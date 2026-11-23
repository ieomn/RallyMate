from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


KEYPOINT_CONFIDENCE_MIN = 0.25
BASELINE_ANALYZER_VERSION = "0.1.0"
TARGET_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
SYMMETRIC_PAIRS = (
    ("left_shoulder", "right_shoulder"),
    ("left_elbow", "right_elbow"),
    ("left_wrist", "right_wrist"),
    ("left_hip", "right_hip"),
    ("left_knee", "right_knee"),
    ("left_ankle", "right_ankle"),
)
FEATURE_NAMES = (
    "hip_center_y_body",
    "body_center_speed_body_s",
    "left_knee_flexion_deg",
    "right_knee_flexion_deg",
    "torso_lean_deg",
    "stance_width_body",
    "hip_center_relative_to_ankle_support",
    "body_center_deceleration_body_s2",
    "stability_duration_ms",
    "shoulder_hip_angular_velocity",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_percentile(values: Iterable[float], percentile: float) -> float | None:
    array = np.asarray(list(values), dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return None
    return round(float(np.percentile(array, percentile)), 6)


def _distribution(values: Iterable[float], eligible_count: int) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=np.float64)
    array = array[np.isfinite(array)]
    valid_count = int(array.size)
    if valid_count == 0:
        return {
            "status": "unavailable",
            "valid_count": 0,
            "eligible_count": int(max(eligible_count, 0)),
            "valid_fraction": 0.0,
            "min": None,
            "p05": None,
            "p50": None,
            "p95": None,
            "max": None,
            "mean": None,
        }
    return {
        "status": "diagnostic_only",
        "valid_count": valid_count,
        "eligible_count": int(max(eligible_count, 0)),
        "valid_fraction": round(valid_count / max(eligible_count, 1), 6),
        "min": round(float(array.min()), 6),
        "p05": _safe_percentile(array, 5),
        "p50": _safe_percentile(array, 50),
        "p95": _safe_percentile(array, 95),
        "max": round(float(array.max()), 6),
        "mean": round(float(array.mean()), 6),
    }


def _point(pose: dict[str, Any], name: str) -> np.ndarray | None:
    point = pose["keypoints"].get(name)
    if point is None or point[2] < KEYPOINT_CONFIDENCE_MIN:
        return None
    value = np.asarray(point[:2], dtype=np.float64)
    return value if np.isfinite(value).all() else None


def _center(*points: np.ndarray | None) -> np.ndarray | None:
    if any(point is None for point in points):
        return None
    return np.mean(np.stack(points), axis=0)


def _angle_3pt(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float | None:
    first = a - b
    second = c - b
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-12:
        return None
    cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _body_scale(pose: dict[str, Any]) -> float | None:
    left_shoulder = _point(pose, "left_shoulder")
    right_shoulder = _point(pose, "right_shoulder")
    left_hip = _point(pose, "left_hip")
    right_hip = _point(pose, "right_hip")
    shoulder_center = _center(left_shoulder, right_shoulder)
    hip_center = _center(left_hip, right_hip)
    candidates = []
    for first, second in (
        (left_shoulder, right_shoulder),
        (left_hip, right_hip),
        (shoulder_center, hip_center),
    ):
        if first is not None and second is not None:
            distance = float(np.linalg.norm(first - second))
            if distance > 1e-6:
                candidates.append(distance)
    return float(statistics.median(candidates)) if candidates else None


def _wrap_180(value: float) -> float:
    return ((value + 90.0) % 180.0) - 90.0


def _max_missing_run(indexes: set[int], start: int, end: int) -> int:
    longest = 0
    current = 0
    for index in range(start, end + 1):
        if index in indexes:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _load_pose_tracks(frames_path: Path) -> tuple[
    int,
    dict[int, int],
    dict[int, list[dict[str, Any]]],
    int,
]:
    frame_count = 0
    timestamps: dict[int, int] = {}
    tracks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    largest_bbox_track: int | None = None
    largest_bbox_switches = 0
    with frames_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            frame = record["frame"]
            processed_index = int(frame["processed_index"])
            timestamp_ms = int(frame["timestamp_ms"])
            timestamps[processed_index] = timestamp_ms
            frame_count += 1
            players = [
                detection
                for detection in record.get("detections", [])
                if detection.get("class_name") == "player"
            ]
            if players:
                largest = max(
                    players,
                    key=lambda item: (
                        (item["bbox_px"][2] - item["bbox_px"][0])
                        * (item["bbox_px"][3] - item["bbox_px"][1])
                    ),
                )
                current = int(largest["track_id"])
                if largest_bbox_track is not None and current != largest_bbox_track:
                    largest_bbox_switches += 1
                largest_bbox_track = current
            for raw_pose in record.get("poses", []):
                track_id = raw_pose.get("person_track_id")
                if not isinstance(track_id, int):
                    continue
                points = {}
                for point in raw_pose.get("keypoints", []):
                    name = point.get("name")
                    if not isinstance(name, str):
                        continue
                    points[name] = (
                        float(point.get("x_normalized", math.nan)),
                        float(point.get("y_normalized", math.nan)),
                        float(point.get("confidence", 0.0)),
                    )
                tracks[track_id].append(
                    {
                        "processed_index": processed_index,
                        "source_frame_index": int(frame["index"]),
                        "timestamp_ms": timestamp_ms,
                        "confidence": float(raw_pose.get("confidence", 0.0)),
                        "keypoints": points,
                    }
                )
    return frame_count, timestamps, tracks, largest_bbox_switches


def _joint_temporal_diagnostics(
    poses: list[dict[str, Any]], scale_ref: float
) -> tuple[dict[str, Any], list[int]]:
    names = sorted({name for pose in poses for name in pose["keypoints"]})
    results: dict[str, Any] = {}
    jump_frames: set[int] = set()
    for name in names:
        joint_jump_frames: set[int] = set()
        steps: list[float] = []
        speeds: list[float] = []
        step_frames: list[int] = []
        jitters: list[float] = []
        valid_indexes = {
            int(pose["processed_index"])
            for pose in poses
            if _point(pose, name) is not None
        }
        for previous, current in zip(poses, poses[1:]):
            if current["processed_index"] != previous["processed_index"] + 1:
                continue
            p0 = _point(previous, name)
            p1 = _point(current, name)
            dt = (current["timestamp_ms"] - previous["timestamp_ms"]) / 1000.0
            if p0 is None or p1 is None or dt <= 0:
                continue
            step = float(np.linalg.norm(p1 - p0) / scale_ref)
            steps.append(step)
            speeds.append(step / dt)
            step_frames.append(int(current["processed_index"]))
        for before, center, after in zip(poses, poses[1:], poses[2:]):
            if not (
                center["processed_index"] == before["processed_index"] + 1
                and after["processed_index"] == center["processed_index"] + 1
            ):
                continue
            p0, p1, p2 = (_point(item, name) for item in (before, center, after))
            if p0 is None or p1 is None or p2 is None:
                continue
            t0, t1, t2 = (
                float(item["timestamp_ms"]) for item in (before, center, after)
            )
            if t2 <= t0:
                continue
            alpha = (t1 - t0) / (t2 - t0)
            expected = p0 + alpha * (p2 - p0)
            jitters.append(float(np.linalg.norm(p1 - expected) / scale_ref))
        if speeds:
            median = float(np.median(speeds))
            mad = float(np.median(np.abs(np.asarray(speeds) - median)))
            robust_sigma = 1.4826 * mad
            threshold = median + 6.0 * robust_sigma
            for step, speed, frame in zip(steps, speeds, step_frames):
                if step >= 0.20 and speed > threshold:
                    joint_jump_frames.add(frame)
                    jump_frames.add(frame)
        else:
            threshold = None
        start = int(poses[0]["processed_index"])
        end = int(poses[-1]["processed_index"])
        results[name] = {
            "normalized_step_body": _distribution(steps, max(len(poses) - 1, 0)),
            "speed_body_s": _distribution(speeds, max(len(poses) - 1, 0)),
            "jitter_residual_body": _distribution(jitters, max(len(poses) - 2, 0)),
            "jump_candidate_count": len(joint_jump_frames),
            "jump_candidate_frames": sorted(joint_jump_frames),
            "jump_rule": {
                "min_normalized_step_body": 0.20,
                "robust_speed_sigma_multiplier": 6.0,
                "speed_threshold_body_s": (
                    round(float(threshold), 6) if threshold is not None else None
                ),
                "semantics": "diagnostic_heuristic_not_accuracy",
            },
            "longest_missing_frames_within_track_span": _max_missing_run(
                valid_indexes, start, end
            ),
        }
    return results, sorted(jump_frames)


def _left_right_swap_diagnostics(
    poses: list[dict[str, Any]], scale_ref: float
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    candidate_frames: set[int] = set()
    for previous, current in zip(poses, poses[1:]):
        if current["processed_index"] != previous["processed_index"] + 1:
            continue
        for left_name, right_name in SYMMETRIC_PAIRS:
            previous_left = _point(previous, left_name)
            previous_right = _point(previous, right_name)
            current_left = _point(current, left_name)
            current_right = _point(current, right_name)
            if any(
                value is None
                for value in (
                    previous_left,
                    previous_right,
                    current_left,
                    current_right,
                )
            ):
                continue
            direct = float(
                np.linalg.norm(current_left - previous_left)
                + np.linalg.norm(current_right - previous_right)
            ) / scale_ref
            swapped = float(
                np.linalg.norm(current_left - previous_right)
                + np.linalg.norm(current_right - previous_left)
            ) / scale_ref
            if swapped < direct * 0.80 and direct - swapped >= 0.05:
                pair = f"{left_name}|{right_name}"
                counts[pair] += 1
                candidate_frames.add(int(current["processed_index"]))
    return {
        "candidate_count": int(sum(counts.values())),
        "candidate_frame_count": len(candidate_frames),
        "by_pair": dict(sorted(counts.items())),
        "candidate_frames": sorted(candidate_frames),
        "rule": {
            "swapped_cost_ratio_max": 0.80,
            "direct_minus_swapped_min_body": 0.05,
            "semantics": "diagnostic_heuristic_not_confirmed_id_switch",
        },
    }


def _feature_diagnostics(
    poses: list[dict[str, Any]], scale_ref: float
) -> dict[str, Any]:
    static_values: dict[str, list[float]] = defaultdict(list)
    centers: list[tuple[float, np.ndarray, int]] = []
    separation_angles: list[tuple[float, float, int]] = []
    for pose in poses:
        left_shoulder = _point(pose, "left_shoulder")
        right_shoulder = _point(pose, "right_shoulder")
        left_hip = _point(pose, "left_hip")
        right_hip = _point(pose, "right_hip")
        left_knee = _point(pose, "left_knee")
        right_knee = _point(pose, "right_knee")
        left_ankle = _point(pose, "left_ankle")
        right_ankle = _point(pose, "right_ankle")
        shoulder_center = _center(left_shoulder, right_shoulder)
        hip_center = _center(left_hip, right_hip)
        body_center = _center(shoulder_center, hip_center)
        if hip_center is not None:
            static_values["hip_center_y_body"].append(hip_center[1] / scale_ref)
        if body_center is not None:
            centers.append(
                (
                    float(pose["timestamp_ms"]) / 1000.0,
                    body_center / scale_ref,
                    int(pose["processed_index"]),
                )
            )
        for side, hip, knee, ankle in (
            ("left", left_hip, left_knee, left_ankle),
            ("right", right_hip, right_knee, right_ankle),
        ):
            if hip is not None and knee is not None and ankle is not None:
                internal = _angle_3pt(hip, knee, ankle)
                if internal is not None:
                    static_values[f"{side}_knee_flexion_deg"].append(
                        180.0 - internal
                    )
        if shoulder_center is not None and hip_center is not None:
            torso = shoulder_center - hip_center
            if np.linalg.norm(torso) > 1e-9:
                static_values["torso_lean_deg"].append(
                    math.degrees(math.atan2(float(torso[0]), float(-torso[1])))
                )
        if left_ankle is not None and right_ankle is not None:
            ankle_axis = right_ankle - left_ankle
            width = float(np.linalg.norm(ankle_axis))
            if width > 1e-9:
                static_values["stance_width_body"].append(width / scale_ref)
                if hip_center is not None:
                    static_values[
                        "hip_center_relative_to_ankle_support"
                    ].append(
                        float(np.dot(hip_center - left_ankle, ankle_axis))
                        / float(np.dot(ankle_axis, ankle_axis))
                    )
        if all(
            point is not None
            for point in (left_shoulder, right_shoulder, left_hip, right_hip)
        ):
            shoulder_axis = right_shoulder - left_shoulder
            hip_axis = right_hip - left_hip
            if np.linalg.norm(shoulder_axis) > 1e-9 and np.linalg.norm(hip_axis) > 1e-9:
                shoulder_angle = math.degrees(
                    math.atan2(float(-shoulder_axis[1]), float(shoulder_axis[0]))
                )
                hip_angle = math.degrees(
                    math.atan2(float(-hip_axis[1]), float(hip_axis[0]))
                )
                separation_angles.append(
                    (
                        float(pose["timestamp_ms"]) / 1000.0,
                        _wrap_180(shoulder_angle - hip_angle),
                        int(pose["processed_index"]),
                    )
                )

    speeds: list[tuple[float, float, int]] = []
    for previous, current in zip(centers, centers[1:]):
        if current[2] != previous[2] + 1:
            continue
        dt = current[0] - previous[0]
        if dt > 0:
            speeds.append(
                (
                    (current[0] + previous[0]) / 2.0,
                    float(np.linalg.norm(current[1] - previous[1]) / dt),
                    current[2],
                )
            )
    static_values["body_center_speed_body_s"] = [item[1] for item in speeds]
    decelerations = []
    for previous, current in zip(speeds, speeds[1:]):
        if current[2] != previous[2] + 1:
            continue
        dt = current[0] - previous[0]
        if dt > 0:
            decelerations.append(-(current[1] - previous[1]) / dt)
    static_values["body_center_deceleration_body_s2"] = decelerations

    angular_velocities = []
    if separation_angles:
        unwrapped = np.rad2deg(
            np.unwrap(
                np.deg2rad([item[1] * 2.0 for item in separation_angles])
            )
        ) / 2.0
        for index in range(1, len(separation_angles)):
            previous = separation_angles[index - 1]
            current = separation_angles[index]
            if current[2] != previous[2] + 1:
                continue
            dt = current[0] - previous[0]
            if dt > 0:
                angular_velocities.append(
                    float((unwrapped[index] - unwrapped[index - 1]) / dt)
                )
    static_values["shoulder_hip_angular_velocity"] = angular_velocities

    eligible = len(poses)
    result = {
        name: _distribution(
            static_values.get(name, []),
            max(eligible - (2 if "deceleration" in name else 1 if "speed" in name or "velocity" in name else 0), 0),
        )
        for name in FEATURE_NAMES
        if name != "stability_duration_ms"
    }
    result["stability_duration_ms"] = {
        "status": "unavailable",
        "valid_count": 0,
        "eligible_count": eligible,
        "valid_fraction": 0.0,
        "reason": "event_boundary_and_validated_stability_envelope_required",
        "value": None,
    }
    return result


def analyze_pose_artifact(
    frames_path: str | Path,
    summary_path: str | Path,
    video_path: str | Path,
) -> dict[str, Any]:
    frames = Path(frames_path)
    summary_file = Path(summary_path)
    video = Path(video_path)
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    frame_count, timestamps, tracks, largest_bbox_switches = _load_pose_tracks(frames)
    if not tracks:
        raise ValueError(f"no pose tracks found in {frames}")
    primary_track_id = max(tracks, key=lambda track_id: len(tracks[track_id]))
    poses = sorted(tracks[primary_track_id], key=lambda item: item["processed_index"])
    scales = [value for pose in poses if (value := _body_scale(pose)) is not None]
    if not scales:
        raise ValueError(f"primary pose track has no valid body scale in {frames}")
    scale_ref = float(statistics.median(scales))
    joint_valid: Counter[str] = Counter()
    mean_confidence_frames: list[tuple[float, int, int]] = []
    for pose in poses:
        confidences = []
        for name, point in pose["keypoints"].items():
            if point[2] >= KEYPOINT_CONFIDENCE_MIN:
                joint_valid[name] += 1
            if math.isfinite(point[2]):
                confidences.append(point[2])
        mean_confidence_frames.append(
            (
                statistics.mean(confidences) if confidences else 0.0,
                int(pose["processed_index"]),
                int(pose["source_frame_index"]),
            )
        )
    temporal, jump_frames = _joint_temporal_diagnostics(poses, scale_ref)
    swaps = _left_right_swap_diagnostics(poses, scale_ref)
    feature_diagnostics = _feature_diagnostics(poses, scale_ref)
    primary_indexes = {int(pose["processed_index"]) for pose in poses}
    duration_ms = int(summary.get("input", {}).get("video", {}).get("duration_ms", 0))
    longest_pose_missing_frames = _max_missing_run(primary_indexes, 0, frame_count - 1)
    fps = float(summary.get("input", {}).get("video", {}).get("fps", 0.0))
    annotation_candidates: dict[int, set[str]] = defaultdict(set)
    for _, processed_index, _ in sorted(mean_confidence_frames)[:10]:
        annotation_candidates[processed_index].add("low_mean_keypoint_confidence")
    for processed_index in jump_frames[:10]:
        annotation_candidates[processed_index].add("jump_candidate")
    if poses:
        spread_indexes = np.linspace(0, len(poses) - 1, num=min(10, len(poses)), dtype=int)
        for pose_index in spread_indexes:
            annotation_candidates[int(poses[pose_index]["processed_index"])].add(
                "temporal_coverage"
            )
    pose_by_index = {int(pose["processed_index"]): pose for pose in poses}
    candidates = []
    for processed_index, reasons in sorted(annotation_candidates.items()):
        pose = pose_by_index.get(processed_index)
        candidates.append(
            {
                "processed_index": processed_index,
                "source_frame_index": (
                    int(pose["source_frame_index"]) if pose is not None else None
                ),
                "timestamp_ms": timestamps.get(processed_index),
                "person_track_id": primary_track_id,
                "selection_reasons": sorted(reasons),
            }
        )
    return {
        "analyzer_version": BASELINE_ANALYZER_VERSION,
        "status": "diagnostic_baseline_not_accuracy",
        "video": {
            "path": str(video),
            "sha256": sha256_file(video),
            "frame_count": frame_count,
            "fps": fps,
            "duration_ms": duration_ms,
        },
        "artifacts": {
            "frames_jsonl": str(frames),
            "frames_sha256": sha256_file(frames),
            "summary_json": str(summary_file),
            "summary_sha256": sha256_file(summary_file),
        },
        "existing_run": {
            "effective_processed_fps": summary.get("processing", {}).get(
                "effective_processed_fps"
            ),
            "elapsed_seconds": summary.get("processing", {}).get("elapsed_seconds"),
            "pose_stage_seconds": summary.get("processing", {})
            .get("stage_seconds", {})
            .get("pose_seconds"),
            "pose_frame_fraction": summary.get("coverage", {}).get(
                "pose_frame_fraction"
            ),
            "measurement_semantics": "historical_full_pipeline_run",
        },
        "tracking": {
            "primary_pose_track_id": primary_track_id,
            "primary_pose_track_frames": len(poses),
            "primary_pose_track_fraction": round(len(poses) / max(frame_count, 1), 6),
            "pose_track_count": len(tracks),
            "largest_bbox_track_switches": largest_bbox_switches,
            "largest_bbox_switch_semantics": "framewise_largest_detection_track_change_not_ground_truth_id_switch",
            "longest_primary_pose_missing_frames_over_video": longest_pose_missing_frames,
            "longest_primary_pose_missing_ms_approx": (
                round(longest_pose_missing_frames / fps * 1000)
                if fps > 0
                else None
            ),
        },
        "keypoints": {
            "confidence_threshold": KEYPOINT_CONFIDENCE_MIN,
            "valid_fraction_on_primary_track": {
                name: round(joint_valid[name] / max(len(poses), 1), 6)
                for name in sorted({name for pose in poses for name in pose["keypoints"]})
            },
            "temporal_diagnostics": temporal,
            "jump_diagnostics": {
                "candidate_frame_count": len(jump_frames),
                "candidate_frames": jump_frames,
                "semantics": "union_of_per_joint_diagnostic_candidates_not_accuracy",
            },
            "left_right_swap_diagnostics": swaps,
            "body_scale_reference_normalized": round(scale_ref, 8),
        },
        "features": {
            "status": "whole_track_raw_diagnostic_not_event_feature",
            "coordinate_space": "normalized_frame_divided_by_primary_track_body_scale",
            "smoothing": "none",
            "timebase": "frames_jsonl.timestamp_ms",
            "distributions": feature_diagnostics,
        },
        "ground_truth": {
            "keypoint_error_status": "ground_truth_required",
            "keypoint_mae": None,
            "keypoint_p95": None,
            "annotation_candidates": candidates,
            "requested_joints": list(TARGET_JOINTS),
        },
    }


def _benchmark_samples(
    frames_path: Path, sample_count: int
) -> list[dict[str, Any]]:
    records = []
    total_lines = sum(1 for _ in frames_path.open("r", encoding="utf-8"))
    target_indexes = set(
        int(value)
        for value in np.linspace(0, max(total_lines - 1, 0), num=min(sample_count, total_lines))
    )
    with frames_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            processed_index = int(record["frame"]["processed_index"])
            if processed_index not in target_indexes:
                continue
            players = [
                detection
                for detection in record.get("detections", [])
                if detection.get("class_name") == "player"
            ]
            if players:
                records.append(
                    {
                        "source_frame_index": int(record["frame"]["index"]),
                        "timestamp_ms": int(record["frame"]["timestamp_ms"]),
                        "players": players,
                    }
                )
    return records


def benchmark_yolo_pose(
    specs: list[dict[str, str | Path]],
    detect_model: str | Path,
    pose_model: str | Path,
    *,
    device: str = "0",
    sample_count_per_video: int = 60,
    warmup_calls: int = 5,
) -> dict[str, Any]:
    import torch

    from rallymate_vision.inference import Yolo26Perception

    load_started = time.perf_counter()
    perception = Yolo26Perception(
        detect_model=Path(detect_model),
        pose_model=Path(pose_model),
        device=device,
        detect_imgsz=960,
        pose_imgsz=640,
        detect_confidence=0.15,
        pose_confidence=0.25,
    )
    load_seconds = time.perf_counter() - load_started
    cuda = bool(torch.cuda.is_available() and device != "cpu")
    if cuda:
        torch.cuda.synchronize()
        allocated_after_load = int(torch.cuda.memory_allocated())
        reserved_after_load = int(torch.cuda.memory_reserved())
        torch.cuda.reset_peak_memory_stats()
    else:
        allocated_after_load = None
        reserved_after_load = None

    all_calls = []
    by_video = {}
    warmed = False
    allocated_after_warmup = None
    reserved_after_warmup = None
    for spec in specs:
        video_path = Path(spec["video"])
        frames_path = Path(spec["frames"])
        samples = _benchmark_samples(frames_path, sample_count_per_video)
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"could not open benchmark video: {video_path}")
        calls = []
        decoded = []
        for sample in samples:
            capture.set(cv2.CAP_PROP_POS_FRAMES, sample["source_frame_index"])
            ok, frame = capture.read()
            if ok:
                decoded.append((sample, frame))
        capture.release()
        if not decoded:
            raise RuntimeError(f"no benchmark frames decoded from {video_path}")
        if not warmed:
            sample, frame = decoded[0]
            for _ in range(warmup_calls):
                perception.estimate_poses(frame, sample["players"], 2)
                if cuda:
                    torch.cuda.synchronize()
            if cuda:
                allocated_after_warmup = int(torch.cuda.memory_allocated())
                reserved_after_warmup = int(torch.cuda.memory_reserved())
            warmed = True
        for sample, frame in decoded:
            roi_count = min(len(sample["players"]), 2)
            if cuda:
                torch.cuda.synchronize()
            started = time.perf_counter()
            predictions = perception.estimate_poses(frame, sample["players"], 2)
            if cuda:
                torch.cuda.synchronize()
            latency_ms = (time.perf_counter() - started) * 1000.0
            call = {
                "source_frame_index": sample["source_frame_index"],
                "timestamp_ms": sample["timestamp_ms"],
                "roi_count": roi_count,
                "prediction_count": len(predictions),
                "latency_ms": latency_ms,
                "latency_per_roi_ms": latency_ms / max(roi_count, 1),
            }
            calls.append(call)
            all_calls.append(call)
        by_video[str(spec["id"])] = {
            "sample_count": len(calls),
            "roi_count": sum(item["roi_count"] for item in calls),
            "pose_call_latency_ms": {
                "p50": _safe_percentile((item["latency_ms"] for item in calls), 50),
                "p95": _safe_percentile((item["latency_ms"] for item in calls), 95),
                "mean": round(statistics.mean(item["latency_ms"] for item in calls), 6),
            },
            "latency_per_roi_ms": {
                "p50": _safe_percentile((item["latency_per_roi_ms"] for item in calls), 50),
                "p95": _safe_percentile((item["latency_per_roi_ms"] for item in calls), 95),
            },
        }
    if cuda:
        torch.cuda.synchronize()
        peak_allocated = int(torch.cuda.max_memory_allocated())
        peak_reserved = int(torch.cuda.max_memory_reserved())
    else:
        peak_allocated = None
        peak_reserved = None
    return {
        "benchmark_version": BASELINE_ANALYZER_VERSION,
        "backend": "current_yolo_pose_cascade",
        "runtime": "ultralytics_pytorch_cuda" if cuda else "ultralytics_pytorch_cpu",
        "device": device,
        "sample_count_per_video_requested": sample_count_per_video,
        "warmup_calls": warmup_calls,
        "load_seconds": round(load_seconds, 6),
        "load_semantics": "Yolo26Perception_combined_detect_and_pose_load",
        "pose_call_latency_ms": {
            "p50": _safe_percentile((item["latency_ms"] for item in all_calls), 50),
            "p95": _safe_percentile((item["latency_ms"] for item in all_calls), 95),
            "mean": round(statistics.mean(item["latency_ms"] for item in all_calls), 6),
        },
        "latency_per_roi_ms": {
            "p50": _safe_percentile((item["latency_per_roi_ms"] for item in all_calls), 50),
            "p95": _safe_percentile((item["latency_per_roi_ms"] for item in all_calls), 95),
        },
        "cuda_memory_bytes": {
            "allocated_after_combined_model_load": allocated_after_load,
            "reserved_after_combined_model_load": reserved_after_load,
            "allocated_after_pose_warmup": allocated_after_warmup,
            "reserved_after_pose_warmup": reserved_after_warmup,
            "peak_allocated_during_pose_benchmark": peak_allocated,
            "peak_reserved_during_pose_benchmark": peak_reserved,
            "semantics": "current_process_pytorch_allocator_not_total_GPU_board_usage",
        },
        "by_video": by_video,
    }
