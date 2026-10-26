"""Offline, unverified RTMPose diagnostics for generated motion replay videos.

Run with runtime/rtmpose/.venv/Scripts/python.exe. No provider API is contacted.
This is a domain-shift diagnostic, not an approval or ground-truth validator.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_vision.motion_replay import CORE_JOINTS, LIMBS
from rallymate_vision.timebase import SourceVideoClock


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def extract_video_pose(video_path: Path, backend, joint_names: list[str], *, sample_fps: float,
                       max_frames: int, snapshot_times_ms: list[float] | None = None
                       ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Cannot decode input video: {video_path.name}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not math.isfinite(fps) or fps <= 0 or count <= 0:
        capture.release()
        raise ValueError("Video timing metadata is unavailable")
    duration_ms = count * 1000 / fps
    targets = snapshot_times_ms or [0.0, duration_ms / 2, max(0.0, duration_ms - 1000 / sample_fps)]
    snapshots: list[dict[str, Any] | None] = [None, None, None]
    clock, rows = SourceVideoClock(fps), []
    next_due, index, sample_index = 0.0, 0, 0
    started = time.monotonic()
    try:
        while len(rows) < max_frames:
            ok, pixels = capture.read()
            if not ok:
                break
            timestamp, timestamp_source = clock.resolve(index, float(capture.get(cv2.CAP_PROP_POS_MSEC)))
            if timestamp + .5 < next_due:
                index += 1
                continue
            height, width = pixels.shape[:2]
            output = backend.infer([pixels])[0]
            points = []
            if output is not None:
                for name, xy, confidence in zip(joint_names, output.keypoints_xy, output.keypoint_scores):
                    x, y, score = float(xy[0]), float(xy[1]), float(confidence)
                    if (all(math.isfinite(n) for n in (x, y, score)) and .35 <= score <= 1
                            and 0 <= x <= width and 0 <= y <= height):
                        points.append({"name": name, "x": x / width, "y": y / height, "confidence": score})
            row = {"frame_index": index, "timestamp_ms": timestamp, "timestamp_source": timestamp_source,
                   "width": width, "height": height, "keypoints": points}
            rows.append(row)
            for position, target in enumerate(targets):
                current = snapshots[position]
                if current is None or abs(timestamp - target) < abs(current["row"]["timestamp_ms"] - target):
                    snapshots[position] = {"row": row, "pixels": pixels.copy()}
            sample_index += 1
            next_due = sample_index * 1000 / sample_fps
            index += 1
    finally:
        capture.release()
    return {
        "result_kind": "unverified_full_frame_rtmpose_diagnostic", "video_file": video_path.name,
        "video_fps": fps, "video_frame_count": count, "video_duration_ms": duration_ms,
        "requested_sample_fps": sample_fps, "sampled_frame_count": len(rows),
        "is_truncated": index < count, "timebase": clock.summary(),
        "pose_method": "rtmpose_topdown_full_frame_roi_without_person_detector",
        "coordinate_space": "normalized_frame_0_1", "min_keypoint_confidence": .35,
        "elapsed_seconds": round(time.monotonic() - started, 3), "frames": rows,
    }, [snapshot for snapshot in snapshots if snapshot is not None]


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    array = np.asarray(values)
    return {"count": len(values), "mean": float(array.mean()), "median": float(np.median(array)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def _reference_at(trajectory: dict[str, Any], timestamp: float) -> dict[str, Any] | None:
    # Compare with the joints actually encoded into reference.mp4. Raw evidence
    # is retained separately and can contain a short gap or a different person.
    frames = trajectory.get("display_frames", trajectory["frames"])
    times = [row["output_timestamp_ms"] for row in frames]
    # SourceVideoClock rounds decoder PTS to integer milliseconds. Permit only
    # that half-millisecond quantization, never motion-dependent time fitting.
    index = bisect.bisect_right(times, timestamp + .5) - 1
    if index < 0 or timestamp >= trajectory["render"]["encoded_duration_ms"]:
        return None
    return frames[index]


def compare_to_reference(trajectory: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    """Compare in the unchanged reference canvas, without per-frame alignment."""
    width, height = trajectory["render"]["width"], trajectory["render"]["height"]
    body_px = trajectory["render"]["body_scale_source_px"] * trajectory["render"]["transform"]["scale"]
    errors = {name: [] for name in CORE_JOINTS}
    series = {name: [] for name in CORE_JOINTS}
    matched, expected, valid_frame_count, mismatched_aspect = 0, 0, 0, 0
    timing_ages = []
    canvas_transforms = []
    for row in extracted["frames"]:
        reference = _reference_at(trajectory, row["timestamp_ms"])
        if reference is None or row["timestamp_source"] != "decoder_pts":
            continue
        valid_frame_count += 1
        wanted = {point["name"]: np.array([point["x_px"], point["y_px"]]) for point in reference["keypoints"]}
        expected += sum(name in wanted for name in CORE_JOINTS)
        if abs((row["width"] / row["height"]) / (width / height) - 1) > .20:
            mismatched_aspect += 1
            continue
        canvas_scale = min(width / row["width"], height / row["height"])
        offset = np.array([width - row["width"] * canvas_scale,
                           height - row["height"] * canvas_scale]) / 2
        transform = {"input_width": row["width"], "input_height": row["height"],
                     "scale": canvas_scale, "offset_x_px": float(offset[0]), "offset_y_px": float(offset[1])}
        if transform not in canvas_transforms:
            canvas_transforms.append(transform)
        measured = {point["name"]: np.array([point["x"] * row["width"], point["y"] * row["height"]]) * canvas_scale + offset
                    for point in row["keypoints"]}
        timing_ages.append(max(0.0, row["timestamp_ms"] - reference["output_timestamp_ms"]))
        for name in CORE_JOINTS:
            if name not in wanted or name not in measured:
                continue
            matched += 1
            error = float(np.linalg.norm(measured[name] - wanted[name])) / body_px
            errors[name].append(error)
            series[name].append((row["timestamp_ms"], wanted[name] / body_px, measured[name] / body_px))
    movement = {}
    for name, samples in series.items():
        if len(samples) < 3:
            continue
        timestamps = np.array([item[0] for item in samples], dtype=float)
        reference_xy = np.array([item[1] for item in samples])
        measured_xy = np.array([item[2] for item in samples])
        dt = np.diff(timestamps) / 1000
        adjacent = (dt > 0) & (dt <= .16)
        reference_speed = np.linalg.norm(np.diff(reference_xy, axis=0), axis=1) / dt
        measured_speed = np.linalg.norm(np.diff(measured_xy, axis=0), axis=1) / dt
        valid_indexes = np.flatnonzero(adjacent)
        reference_peak, measured_peak = None, None
        if len(valid_indexes):
            reference_peak = int(valid_indexes[np.argmax(reference_speed[adjacent])])
            measured_peak = int(valid_indexes[np.argmax(measured_speed[adjacent])])
        movement[name] = {
            "matched_sample_count": len(samples),
            "reference_extent_body": float(np.linalg.norm(np.ptp(reference_xy, axis=0))),
            "detected_extent_body": float(np.linalg.norm(np.ptp(measured_xy, axis=0))),
            "reference_peak_speed_time_ms": float(timestamps[reference_peak + 1]) if reference_peak is not None else None,
            "detected_peak_speed_time_ms": float(timestamps[measured_peak + 1]) if measured_peak is not None else None,
            "peak_speed_time_delta_ms": float(timestamps[measured_peak + 1] - timestamps[reference_peak + 1])
            if reference_peak is not None and measured_peak is not None else None,
        }
    return {
        "status": "unverified_pending_human_review", "approved": False,
        "reference_frame_source": "display_frames" if "display_frames" in trajectory else "frames",
        "alignment": "fixed_isotropic_canvas_contain_no_pose_fit_no_DTW",
        "canvas_transforms_from_dimensions_only": canvas_transforms,
        "body_unit": "reference_median_shoulder_center_to_hip_center_image_plane_span",
        "matched_joint_observation_count": matched, "expected_joint_observation_count": expected,
        "candidate_sample_count": len(extracted["frames"]),
        "uncompared_sample_count": len(extracted["frames"]) - valid_frame_count,
        "joint_coverage_fraction": matched / expected if expected else 0,
        "compared_frame_count": valid_frame_count, "aspect_mismatch_frame_count": mismatched_aspect,
        "reference_frame_hold_age_ms": _stats(timing_ages),
        "joint_error_body": _stats([value for values in errors.values() for value in values]),
        "per_joint_error_body": {name: _stats(values) for name, values in errors.items()},
        "motion_extent_and_peak_time": movement,
        "limitations": ["Stylized mannequins are outside the validated pose-estimation domain.",
                        "Confidence is not calibrated geometric accuracy; detector error can dominate.",
                        "Peak timing is an image-plane diagnostic, not verified action phase or contact timing.",
                        "No generated video is automatically approved by these measurements."],
    }


def _overlay(pixels: np.ndarray, points: dict[str, tuple[int, int]], color: tuple[int, int, int]) -> None:
    for a, b in LIMBS:
        if a in points and b in points:
            cv2.line(pixels, points[a], points[b], color, 2, cv2.LINE_AA)
    for name, point in points.items():
        if name in CORE_JOINTS:
            cv2.circle(pixels, point, 4, color, -1, cv2.LINE_AA)


def contact_sheet(trajectory, baseline_snapshots, generated_snapshots, output_path: Path) -> None:
    tiles = []
    width, height = trajectory["render"]["width"], trajectory["render"]["height"]
    for baseline, generated in zip(baseline_snapshots, generated_snapshots):
        row_tiles = []
        for title, snapshot, include_detector in (
            ("Reference: encoded joints (green)", baseline, False),
            ("Reference: RTMPose (magenta)", baseline, True),
            ("Generated: desired / RTMPose", generated, True),
        ):
            pixels, row = snapshot["pixels"].copy(), snapshot["row"]
            h, w = pixels.shape[:2]
            reference = _reference_at(trajectory, row["timestamp_ms"])
            if reference is not None:
                scale = min(width / w, height / h)
                offset_x, offset_y = (width - w * scale) / 2, (height - h * scale) / 2
                desired = {p["name"]: (round((p["x_px"] - offset_x) / scale), round((p["y_px"] - offset_y) / scale))
                           for p in reference["keypoints"]}
                _overlay(pixels, desired, (70, 240, 90))
            if include_detector:
                measured = {p["name"]: (round(p["x"] * w), round(p["y"] * h)) for p in row["keypoints"]}
                _overlay(pixels, measured, (230, 60, 240))
            tile = np.full((410, 640, 3), 28, dtype=np.uint8)
            factor = min(640 / w, 360 / h)
            resized = cv2.resize(pixels, (round(w * factor), round(h * factor)))
            y, x = 50 + (360 - resized.shape[0]) // 2, (640 - resized.shape[1]) // 2
            tile[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
            cv2.putText(tile, title, (12, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (245, 245, 245), 1, cv2.LINE_AA)
            cv2.putText(tile, f"t={row['timestamp_ms']/1000:.3f}s | UNVERIFIED", (12, 41), cv2.FONT_HERSHEY_SIMPLEX, .47, (100, 210, 250), 1, cv2.LINE_AA)
            row_tiles.append(tile)
        tiles.append(np.hstack(row_tiles))
    ok, encoded = cv2.imencode(".png", np.vstack(tiles))
    if not ok:
        raise ValueError("Could not encode diagnostic contact sheet")
    output_path.write_bytes(encoded.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-trajectory", type=Path, required=True)
    parser.add_argument("--reference-video", type=Path, required=True)
    parser.add_argument("--generated-video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=12)
    parser.add_argument("--max-frames", type=int, default=144)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.sample_fps <= 24 or not 1 <= args.max_frames <= 144 or args.cpu_threads < 1:
        parser.error("sample-fps must be 1..24, max-frames 1..144, and cpu-threads positive")
    for path in (args.reference_trajectory, args.reference_video, args.generated_video):
        if not path.is_file():
            parser.error(f"Input is missing: {path.name}")
    trajectory = json.loads(args.reference_trajectory.read_text(encoding="utf-8"))
    if trajectory.get("result_kind") not in {"deterministic_2d_motion_replay", "synthetic_motion_reference_test"}:
        parser.error("Reference is not a motion replay trajectory")
    import torch
    from rallymate_vision.pose.metadata import keypoint_schema
    from rallymate_vision.pose.presets import resolve_pose_deployment_preset
    from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend
    torch.set_num_threads(args.cpu_threads)
    cv2.setNumThreads(args.cpu_threads)
    preset = resolve_pose_deployment_preset("rtmpose-m-halpe26-online", ROOT)
    backend = RtmposePoseBackend(preset.model_path, preset.config_path, device=args.device,
                                 runtime=preset.pose_runtime, profile=preset.pose_profile,
                                 native_keypoint_format=preset.native_keypoint_format)
    names = [item["name"] for item in keypoint_schema(preset.native_keypoint_format)["keypoints"]]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_times = [trajectory["render"]["encoded_duration_ms"] * fraction for fraction in (1 / 6, .5, 5 / 6)]
    print("Extracting reference mannequin poses...", flush=True)
    baseline, baseline_snapshots = extract_video_pose(args.reference_video, backend, names,
        sample_fps=args.sample_fps, max_frames=args.max_frames, snapshot_times_ms=snapshot_times)
    _write_json(args.output_dir / "reference-detected-pose.json", baseline)
    print("Extracting generated video poses...", flush=True)
    generated, generated_snapshots = extract_video_pose(args.generated_video, backend, names,
        sample_fps=args.sample_fps, max_frames=args.max_frames, snapshot_times_ms=snapshot_times)
    _write_json(args.output_dir / "generated-detected-pose.json", generated)
    report = {
        "schema_version": "1.0.0", "status": "unverified_pending_human_review", "approved": False,
        "method": "fixed_canvas_full_frame_rtmpose_auxiliary_comparison_v1",
        "model": {"preset_id": preset.preset_id, "native_keypoint_format": preset.native_keypoint_format,
                  "checkpoint_sha256": backend.metadata().model_sha256},
        "reference_video": args.reference_video.name, "generated_video": args.generated_video.name,
        "reference_detector_error_baseline": compare_to_reference(trajectory, baseline),
        "generated_vs_original_observations": compare_to_reference(trajectory, generated),
        "timing": {"reference_duration_ms": baseline["video_duration_ms"],
                   "generated_duration_ms": generated["video_duration_ms"],
                   "duration_delta_ms": generated["video_duration_ms"] - baseline["video_duration_ms"],
                   "time_alignment": "same_elapsed_milliseconds_without_duration_rescaling",
                   "reference_timing_verified": baseline["timebase"]["timing_verified"],
                   "generated_timing_verified": generated["timebase"]["timing_verified"]},
        "artifacts": {"contact_sheet": "comparison.png", "reference_pose": "reference-detected-pose.json",
                      "generated_pose": "generated-detected-pose.json"},
    }
    contact_sheet(trajectory, baseline_snapshots, generated_snapshots, args.output_dir / "comparison.png")
    _write_json(args.output_dir / "evaluation.json", report)
    print(json.dumps({"status": report["status"], "approved": False,
                      "baseline": report["reference_detector_error_baseline"]["joint_error_body"],
                      "generated": report["generated_vs_original_observations"]["joint_error_body"],
                      "coverage": report["generated_vs_original_observations"]["joint_coverage_fraction"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
