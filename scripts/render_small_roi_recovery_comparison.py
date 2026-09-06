#!/usr/bin/env python3
"""Render a dynamic current-vs-small-ROI pose comparison for audited gap events."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


SKELETON = (
    ("nose", "left_eye"), ("nose", "right_eye"), ("left_eye", "left_ear"), ("right_eye", "right_ear"),
    ("left_shoulder", "right_shoulder"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"), ("left_hip", "right_hip"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ("left_ankle", "left_big_toe"), ("left_ankle", "left_small_toe"), ("left_ankle", "left_heel"),
    ("right_ankle", "right_big_toe"), ("right_ankle", "right_small_toe"), ("right_ankle", "right_heel"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_ffmpeg(explicit: Path | None = None) -> Path:
    """Resolve an ffmpeg executable without assuming it is on PATH."""
    if explicit is not None:
        candidate = explicit.resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"explicit ffmpeg executable does not exist: {candidate}")
        return candidate
    path_candidate = shutil.which("ffmpeg")
    if path_candidate:
        return Path(path_candidate).resolve()
    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
        if bundled.is_file():
            return bundled
    except (ImportError, RuntimeError):
        pass
    raise FileNotFoundError("ffmpeg was not found on PATH and imageio-ffmpeg is unavailable; pass --ffmpeg")


def merge_event_ranges(events: list[dict[str, Any]], padding_ms: int) -> list[dict[str, Any]]:
    ordered = sorted(
        (
            max(0, int(item["start_ms"]) - padding_ms),
            int(item["end_ms"]) + padding_ms,
            str(item["event_id"]),
            str(item["event_code"]),
        )
        for item in events
    )
    merged: list[dict[str, Any]] = []
    for start, end, event_id, event_code in ordered:
        if not merged or start > int(merged[-1]["end_ms"]):
            merged.append({"start_ms": start, "end_ms": end, "event_ids": [event_id], "event_codes": [event_code]})
            continue
        merged[-1]["end_ms"] = max(int(merged[-1]["end_ms"]), end)
        if event_id not in merged[-1]["event_ids"]:
            merged[-1]["event_ids"].append(event_id)
        if event_code not in merged[-1]["event_codes"]:
            merged[-1]["event_codes"].append(event_code)
    return merged


def _pose(frame: dict[str, Any], track_id: Any) -> dict[str, Any] | None:
    return next((item for item in frame.get("poses", []) if item.get("person_track_id") == track_id), None)


def _valid_points(pose: dict[str, Any] | None) -> dict[str, tuple[int, int]]:
    if pose is None:
        return {}
    result = {}
    for point in pose.get("keypoints", []):
        if point.get("in_frame") is False or float(point.get("confidence", 0.0)) < 0.25:
            continue
        result[str(point["name"])] = (int(round(float(point["x_px"]))), int(round(float(point["y_px"]))))
    return result


def _draw_pose(image: np.ndarray, pose: dict[str, Any] | None, color: tuple[int, int, int]) -> int:
    points = _valid_points(pose)
    for first, second in SKELETON:
        if first in points and second in points:
            cv2.line(image, points[first], points[second], color, 2, cv2.LINE_AA)
    for name, point in points.items():
        foot = name.endswith(("ankle", "toe", "heel"))
        cv2.circle(image, point, 4 if foot else 3, (0, 215, 255) if foot else color, -1, cv2.LINE_AA)
    return len(points)


def _bbox_for_track(frame: dict[str, Any], timeline: dict[str, Any]) -> list[float] | None:
    track_id = timeline.get("source_track_id")
    detection = next(
        (item for item in frame.get("detections", []) if item.get("class_name") == "player" and item.get("track_id") == track_id),
        None,
    )
    value = detection.get("bbox_px") if detection is not None else timeline.get("bbox_px")
    return [float(item) for item in value] if isinstance(value, list) and len(value) == 4 else None


def _zoom_crop(image: np.ndarray, bbox: list[float] | None, pose: dict[str, Any] | None, color: tuple[int, int, int]) -> np.ndarray:
    if bbox is None:
        return np.zeros((160, 240, 3), dtype=np.uint8)
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(100.0, max(x2 - x1, y2 - y1) * 3.5)
    h, w = image.shape[:2]
    zx1 = max(0, int(cx - side / 2)); zx2 = min(w, int(cx + side / 2))
    zy1 = max(0, int(cy - side / 2)); zy2 = min(h, int(cy + side / 2))
    annotated = image.copy()
    _draw_pose(annotated, pose, color)
    crop = annotated[zy1:zy2, zx1:zx2]
    return cv2.resize(crop, (240, 160), interpolation=cv2.INTER_CUBIC) if crop.size else np.zeros((160, 240, 3), dtype=np.uint8)


def _panel(
    source: np.ndarray,
    frame_record: dict[str, Any],
    timeline: dict[str, Any],
    *,
    title: str,
    pose: dict[str, Any] | None,
    color: tuple[int, int, int],
    recovered_frame: bool,
    experimental: bool,
    experiment_kind: str,
) -> np.ndarray:
    annotated = source.copy()
    bbox = _bbox_for_track(frame_record, timeline)
    if bbox is not None:
        x1, y1, x2, y2 = [int(round(value)) for value in bbox]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
    valid_count = _draw_pose(annotated, pose, color)
    top = cv2.resize(annotated, (960, 540), interpolation=cv2.INTER_AREA)
    panel = np.full((720, 960, 3), (20, 22, 24), dtype=np.uint8)
    panel[:540] = top
    panel[550:710, 10:250] = _zoom_crop(source, bbox, pose, color)
    cv2.rectangle(panel, (10, 550), (250, 710), color, 2)
    status = (
        "M72 + WHOLEBODY133 MAPPED POINTS (NO INDICATOR GAIN)"
        if experimental and recovered_frame and experiment_kind == "wholebody_mapped_fusion"
        else "M71 + MISSING-POINT ADDITION (EXPERIMENT ONLY)"
        if experimental and recovered_frame and experiment_kind == "additive_keypoint_fusion"
        else "M70 + RTMPOSE-L 384x288 EXTENSION (EXPERIMENT ONLY)"
        if experimental and recovered_frame and experiment_kind == "larger_pose_model_extension"
        else "M69-ANCHORED 384x288 EXTENSION (EXPERIMENT ONLY)"
        if experimental and recovered_frame and experiment_kind == "anchored_profile_extension"
        else
        "384x288 ROUTED POSE (EXPERIMENT ONLY)"
        if experimental and recovered_frame and experiment_kind == "high_resolution_router"
        else "ROUTED POSE (EXPERIMENT ONLY)"
        if experimental and recovered_frame and experiment_kind == "required_joint_router"
        else "EXPERIMENTAL POSE (NOT TRUTH)"
        if experimental and recovered_frame and experiment_kind == "roi_margin"
        else "RECOVERED POSE (EXPERIMENT ONLY)"
        if experimental and recovered_frame
        else "POSE AVAILABLE"
        if pose is not None
        else "POSE SKIPPED BY 32PX ROI GUARD"
        if recovered_frame
        else "POSE UNAVAILABLE"
    )
    cv2.putText(panel, title, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    cv2.putText(panel, status, (270, 585), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2, cv2.LINE_AA)
    cv2.putText(panel, f"valid keypoints >=0.25: {valid_count}/26", (270, 620), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 1, cv2.LINE_AA)
    if bbox is not None:
        cv2.putText(panel, f"detected person box: {bbox[2]-bbox[0]:.1f} x {bbox[3]-bbox[1]:.1f}px", (270, 650), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(panel, "observability demo; not keypoint truth or A-E score", (270, 685), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 190, 255), 1, cv2.LINE_AA)
    return panel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--baseline-frames", type=Path, required=True)
    parser.add_argument("--experimental-frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--gap-audit", type=Path, required=True)
    parser.add_argument("--experiment-report", type=Path, required=True)
    parser.add_argument("--padding-ms", type=int, default=200)
    parser.add_argument("--changed-events-only", action="store_true")
    parser.add_argument("--baseline-title", default="CURRENT: min ROI 32px")
    parser.add_argument("--experimental-title", default="EXPERIMENT: min ROI 8px")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.validation_output.exists():
        parser.error("output already exists")
    gap = json.loads(args.gap_audit.read_text(encoding="utf-8"))
    experiment = json.loads(args.experiment_report.read_text(encoding="utf-8"))
    if experiment.get("safety", {}).get("production_enabled") is not False:
        parser.error("experiment report is not safely non-production")
    experiment_version = experiment.get("experiment_version")
    if experiment_version is None:
        experiment_version = experiment.get("report_version")
    frame_artifact_name = (
        "anchored_frames"
        if experiment_version == "anchored-pose-profile-extension-v1.0.0"
        else "fused_frames"
        if experiment_version in {
            "same-topology-additive-keypoint-fusion-v1.0.0",
            "wholebody133-mapped-keypoint-fusion-v1.0.0",
        }
        else "routed_frames"
        if experiment_version in {
            "pose-observability-router-experiment-v1.0.0",
            "residual-high-resolution-pose-experiment-v1.0.0",
            "larger-pose-model-extension-v1.0.0",
        }
        else "experimental_frames"
    )
    if experiment.get("artifacts", {}).get(frame_artifact_name, {}).get("sha256", "").upper() != _sha256(args.experimental_frames):
        parser.error("experimental frames hash mismatch")
    if experiment_version == "anchored-pose-profile-extension-v1.0.0":
        if experiment.get("sources", {}).get("m69_frames", {}).get("sha256", "").upper() != _sha256(args.baseline_frames):
            parser.error("anchored baseline frames hash mismatch")
    if experiment_version == "larger-pose-model-extension-v1.0.0":
        if experiment.get("sources", {}).get("m70_anchored_frames", {}).get("sha256", "").upper() != _sha256(args.baseline_frames):
            parser.error("M71 baseline frames hash mismatch")
    if experiment_version == "same-topology-additive-keypoint-fusion-v1.0.0":
        if experiment.get("sources", {}).get("m71_routed_frames", {}).get("sha256", "").upper() != _sha256(args.baseline_frames):
            parser.error("M72 baseline frames hash mismatch")
    if experiment_version == "wholebody133-mapped-keypoint-fusion-v1.0.0":
        if experiment.get("sources", {}).get("m72_fused_frames", {}).get("sha256", "").upper() != _sha256(args.baseline_frames):
            parser.error("M73 baseline frames hash mismatch")
    baseline_rows = _read_jsonl(args.baseline_frames)
    experimental_rows = _read_jsonl(args.experimental_frames)
    if len(baseline_rows) != len(experimental_rows):
        parser.error("frame row counts differ")
    baseline = {int(row["frame"]["source_frame_index"] if "source_frame_index" in row["frame"] else row["frame"]["index"]): row for row in baseline_rows}
    experimental = {int(row["frame"]["source_frame_index"] if "source_frame_index" in row["frame"] else row["frame"]["index"]): row for row in experimental_rows}
    timeline = {int(row["source_frame_index"]): row for row in _read_jsonl(args.primary_timeline)}
    experiment_kind = (
        "roi_margin"
        if experiment_version == "roi-margin-pose-recovery-v1.0.0"
        else "required_joint_router"
        if experiment_version == "pose-observability-router-experiment-v1.0.0"
        else "high_resolution_router"
        if experiment_version == "residual-high-resolution-pose-experiment-v1.0.0"
        else "anchored_profile_extension"
        if experiment_version == "anchored-pose-profile-extension-v1.0.0"
        else "larger_pose_model_extension"
        if experiment_version == "larger-pose-model-extension-v1.0.0"
        else "additive_keypoint_fusion"
        if experiment_version == "same-topology-additive-keypoint-fusion-v1.0.0"
        else "wholebody_mapped_fusion"
        if experiment_version == "wholebody133-mapped-keypoint-fusion-v1.0.0"
        else "small_roi"
    )
    if experiment_kind == "roi_margin":
        recovered = {
            int(item["processed_index"])
            for item in experiment.get("inference", {}).get("frame_results", [])
            if item.get("pose_output_produced") is True
        }
    elif experiment_kind == "small_roi":
        recovered = set(
            int(item)
            for item in experiment["inference"]["recovered_processed_indices"]
        )
    else:
        recovered = {
            int(base["frame"]["processed_index"])
            for source_index, base in baseline.items()
            if _canonical(_pose(base, timeline[source_index].get("source_track_id")))
            != _canonical(
                _pose(
                    experimental[source_index],
                    timeline[source_index].get("source_track_id"),
                )
            )
        }
    if gap.get("report_version") == "pose-observability-residual-audit-v1.0.0":
        selected_events_by_id = {}
        for item in gap.get("residual_items", []):
            if item.get("video_id") is not None and str(item.get("video_id")) != str(
                experiment.get("video_id")
            ):
                continue
            selected_events_by_id.setdefault(
                str(item["event_id"]),
                {
                    "event_id": str(item["event_id"]),
                    "event_code": str(item["event_code"]),
                    "start_ms": int(item["start_ms"]),
                    "end_ms": int(item["end_ms"]),
                },
            )
        selected_events = list(selected_events_by_id.values())
    else:
        selected_events = gap["event_failures"]
    if args.changed_events_only:
        if experiment_version == "wholebody133-mapped-keypoint-fusion-v1.0.0":
            changed_timestamps = {
                int(timeline[source_index]["timestamp_ms"])
                for source_index, base in baseline.items()
                if int(base["frame"]["processed_index"]) in recovered
                and source_index in timeline
            }
            selected_events = [
                event
                for event in selected_events
                if any(
                    int(event["start_ms"]) <= timestamp <= int(event["end_ms"])
                    for timestamp in changed_timestamps
                )
            ]
            if not selected_events:
                parser.error("M73 has no residual event containing a changed frame")
        elif experiment_version in {
            "larger-pose-model-extension-v1.0.0",
            "same-topology-additive-keypoint-fusion-v1.0.0",
        }:
            preservation_key = (
                "m71_preservation"
                if experiment_version == "same-topology-additive-keypoint-fusion-v1.0.0"
                else "m70_preservation"
            )
            changed_event_ids = {
                str(item["event_id"])
                for item in experiment.get(preservation_key, {}).get(
                    "additional_operational_recoveries", []
                )
            }
        else:
            impact_root = (
            experiment.get("comparison_to_m68", {})
            if experiment_version == "anchored-pose-profile-extension-v1.0.0"
            else experiment
            )
            changed_event_ids = {
                str(item["event_id"])
                for section in (
                    ("feature_vector" if experiment_version == "anchored-pose-profile-extension-v1.0.0" else "feature_vector_impact"),
                    ("operational_measurement" if experiment_version == "anchored-pose-profile-extension-v1.0.0" else "operational_measurement_impact"),
                )
                for field in (
                    "recovered_indicator_instances",
                    "regressed_indicator_instances",
                )
                for item in impact_root.get(section, {}).get(field, [])
            }
        if experiment_version != "wholebody133-mapped-keypoint-fusion-v1.0.0":
            selected_events = [
                event
                for event in selected_events
                if str(event["event_id"]) in changed_event_ids
            ]
            if not selected_events:
                parser.error("experiment has no changed events to render")
    ranges = merge_event_ranges(selected_events, args.padding_ms)
    selected_source_frames = [
        index for index, row in baseline.items()
        if any(item["start_ms"] <= int(row["frame"]["timestamp_ms"]) <= item["end_ms"] for item in ranges)
    ]
    if not selected_source_frames:
        parser.error("no video frames fall inside merged event ranges")

    capture = cv2.VideoCapture(str(args.video))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    ffmpeg_executable = resolve_ffmpeg(args.ffmpeg)
    ffmpeg = subprocess.Popen(
        [str(ffmpeg_executable), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1920x720", "-r", f"{fps:.8f}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output)],
        stdin=subprocess.PIPE,
    )
    selected = set(selected_source_frames)
    written = 0
    source_index = 0
    last = max(selected)
    while source_index <= last:
        ok, image = capture.read()
        if not ok:
            break
        if source_index in selected:
            base = baseline[source_index]; exp = experimental[source_index]; tl = timeline[source_index]
            track = tl.get("source_track_id")
            processed = int(base["frame"]["processed_index"])
            recovered_frame = processed in recovered
            left = _panel(image, base, tl, title=args.baseline_title, pose=_pose(base, track), color=(40, 100, 255), recovered_frame=recovered_frame, experimental=False, experiment_kind=experiment_kind)
            right = _panel(image, exp, tl, title=args.experimental_title, pose=_pose(exp, track), color=(60, 220, 80), recovered_frame=recovered_frame, experimental=True, experiment_kind=experiment_kind)
            canvas = np.hstack([left, right])
            timestamp = int(base["frame"]["timestamp_ms"])
            active = next(item for item in ranges if item["start_ms"] <= timestamp <= item["end_ms"])
            label = f"source t={timestamp/1000:.3f}s | {'/'.join(active['event_codes'])} gap window | fixed candidate boundaries, no truth"
            cv2.rectangle(canvas, (0, 0), (1920, 50), (0, 0, 0), -1)
            cv2.putText(canvas, label, (25, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
            assert ffmpeg.stdin is not None
            ffmpeg.stdin.write(canvas.tobytes())
            written += 1
        source_index += 1
    capture.release()
    assert ffmpeg.stdin is not None
    ffmpeg.stdin.close()
    return_code = ffmpeg.wait()
    if return_code != 0 or written == 0:
        raise RuntimeError(f"ffmpeg failed or no frames were written: {return_code}, {written}")

    check = cv2.VideoCapture(str(args.output))
    decoded = []
    for index in (0, written // 2, written - 1):
        check.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, _ = check.read(); decoded.append({"frame_index": index, "decoded": bool(ok)})
    codec = int(check.get(cv2.CAP_PROP_FOURCC))
    codec_name = "".join(chr((codec >> 8 * i) & 0xFF) for i in range(4))
    width = int(check.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(check.get(cv2.CAP_PROP_FRAME_HEIGHT))
    check.release()
    validation = {
        "schema_version": "1.0.0",
        "status": "passed" if all(item["decoded"] for item in decoded) and codec_name.lower() in {"h264", "avc1"} else "failed",
        "video": {"path": str(args.output.resolve()), "sha256": _sha256(args.output), "codec": codec_name, "frame_count": written, "fps": round(fps, 6), "duration_seconds": round(written / fps, 6), "width": width, "height": height, "sample_decode": decoded},
        "source": {"video_sha256": _sha256(args.video), "baseline_frames_sha256": _sha256(args.baseline_frames), "experimental_frames_sha256": _sha256(args.experimental_frames), "gap_audit_sha256": _sha256(args.gap_audit), "experiment_report_sha256": _sha256(args.experiment_report), "ffmpeg_path": str(ffmpeg_executable), "ffmpeg_sha256": _sha256(ffmpeg_executable)},
        "segments": ranges,
        "experiment_kind": experiment_kind,
        "safety": {"accuracy_claim": False, "ground_truth_provided": False, "production_enabled": False, "grade_generated": False, "threshold_generated": False},
    }
    args.validation_output.write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if validation["status"] != "passed":
        raise RuntimeError(f"rendered video validation failed: {validation}")
    print(json.dumps({"video": str(args.output), "validation": str(args.validation_output), "frames": written, "duration_seconds": validation["video"]["duration_seconds"], "sha256": validation["video"]["sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
