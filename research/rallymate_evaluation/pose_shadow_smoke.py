from __future__ import annotations

import gc
import hashlib
import json
import math
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_VERSION = "rtmpose-shadow-smoke-v1.0.0"
EXPECTED_REGISTRY_VERSION = "rtmpose-shadow-candidates-2026-09-04.1"


class PoseShadowSmokeError(ValueError):
    """Raised when a shadow candidate or its frozen smoke inputs drift."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _inside(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise PoseShadowSmokeError(f"{label} must be a non-empty relative path")
    value = Path(relative)
    if value.is_absolute():
        raise PoseShadowSmokeError(f"{label} must be relative to the workspace")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise PoseShadowSmokeError(f"{label} escapes the workspace")
    return resolved


def _bound_file(
    root: Path,
    relative: Any,
    expected_sha256: Any,
    label: str,
    *,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    path = _inside(root, relative, label)
    if not path.is_file():
        raise PoseShadowSmokeError(f"{label} does not exist: {path}")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != str(expected_sha256 or "").upper():
        raise PoseShadowSmokeError(f"{label} SHA-256 drifted")
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise PoseShadowSmokeError(f"{label} byte size drifted")
    return {
        "path": str(path),
        "relative_path": Path(relative).as_posix(),
        "sha256": observed_sha256,
        "bytes": size,
    }


def load_shadow_registry(
    workspace: str | Path,
    registry_path: str | Path = "models/rtmpose/m95-shadow-candidates.json",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    path = _inside(root, str(registry_path), "shadow registry")
    if not path.is_file():
        raise PoseShadowSmokeError(f"shadow registry does not exist: {path}")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PoseShadowSmokeError(f"shadow registry is invalid JSON: {exc}") from exc
    if not isinstance(registry, dict):
        raise PoseShadowSmokeError("shadow registry must be a JSON object")
    if registry.get("schema_version") != "1.0.0":
        raise PoseShadowSmokeError("unsupported shadow registry schema_version")
    if registry.get("registry_version") != EXPECTED_REGISTRY_VERSION:
        raise PoseShadowSmokeError("unsupported shadow registry version")

    production = registry.get("production_default_binding")
    if not isinstance(production, dict):
        raise PoseShadowSmokeError("production default binding is missing")
    production_binding = _bound_file(
        root,
        production.get("registry_relative_path"),
        production.get("registry_sha256"),
        "M94 deployment registry",
    )
    deployed = json.loads(Path(production_binding["path"]).read_text(encoding="utf-8"))
    if deployed.get("default_preset") != production.get("preset_id"):
        raise PoseShadowSmokeError("bound M94 production default preset drifted")

    safety = registry.get("safety")
    if not isinstance(safety, dict):
        raise PoseShadowSmokeError("shadow registry safety block is missing")
    false_fields = (
        "changes_M94_deployment_registry",
        "changes_production_default",
        "ground_truth_used",
        "RallyMate_accuracy_claim",
        "candidate_promoted",
        "F3_or_F4_promoted",
        "grade_or_threshold_generated",
    )
    if any(safety.get(name) is not False for name in false_fields):
        raise PoseShadowSmokeError("shadow registry contains an unsafe claim")

    protocol = registry.get("development_protocol")
    if not isinstance(protocol, dict) or protocol.get("phase") != "development_smoke_only":
        raise PoseShadowSmokeError("development smoke protocol is missing")
    if protocol.get("ground_truth_used") is not False or protocol.get("accuracy_claim") is not False:
        raise PoseShadowSmokeError("development protocol must not claim accuracy")
    holdout = protocol.get("sealed_holdout")
    if not isinstance(holdout, dict) or holdout.get("use_during_development") is not False:
        raise PoseShadowSmokeError("sealed holdout guard is invalid")
    holdout_id = str(holdout.get("video_id", ""))

    videos = protocol.get("videos")
    if not isinstance(videos, list) or not videos:
        raise PoseShadowSmokeError("development protocol has no videos")
    verified_videos: list[dict[str, Any]] = []
    seen_video_ids: set[str] = set()
    for item in videos:
        if not isinstance(item, dict):
            raise PoseShadowSmokeError("development video entry must be an object")
        video_id = str(item.get("video_id", "")).strip()
        if not video_id or video_id in seen_video_ids or video_id == holdout_id:
            raise PoseShadowSmokeError("development video IDs are empty, duplicate, or include holdout")
        seen_video_ids.add(video_id)
        video = _bound_file(
            root,
            item.get("video_relative_path"),
            item.get("video_sha256"),
            f"development video {video_id}",
        )
        frames = _bound_file(
            root,
            item.get("frozen_frames_relative_path"),
            item.get("frozen_frames_sha256"),
            f"frozen frames {video_id}",
        )
        verified_videos.append(
            {
                "video_id": video_id,
                "declared_frame_count": int(item.get("frame_count", -1)),
                "video": video,
                "frozen_frames": frames,
            }
        )

    candidates = registry.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise PoseShadowSmokeError("shadow registry has no candidates")
    verified_candidates: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise PoseShadowSmokeError("candidate entry must be an object")
        candidate_id = str(item.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in seen_candidate_ids:
            raise PoseShadowSmokeError("candidate IDs must be unique and non-empty")
        seen_candidate_ids.add(candidate_id)
        if (
            item.get("pose_backend") != "rtmpose"
            or item.get("pose_runtime") != "pytorch"
            or item.get("pose_profile") != "analysis"
            or item.get("native_keypoint_format") != "halpe26"
            or item.get("input_size_hw") != [384, 288]
        ):
            raise PoseShadowSmokeError(f"candidate identity drifted: {candidate_id}")
        model = _bound_file(
            root,
            item.get("model_relative_path"),
            item.get("checkpoint_sha256"),
            f"candidate checkpoint {candidate_id}",
            expected_bytes=int(item.get("checkpoint_bytes", -1)),
        )
        config = _bound_file(
            root,
            item.get("config_runtime_relative_path"),
            item.get("config_sha256"),
            f"candidate config {candidate_id}",
        )
        verified_candidates.append({**item, "model_binding": model, "config_binding": config})

    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "registry_version": registry["registry_version"],
        "production_default_binding": production_binding,
        "production_default_preset": production["preset_id"],
        "protocol": protocol,
        "videos": verified_videos,
        "candidates": verified_candidates,
    }


def _read_frozen_samples(
    frames_path: str | Path,
    *,
    video_id: str,
    declared_frame_count: int,
    sample_count: int,
) -> list[dict[str, Any]]:
    if sample_count < 1:
        raise PoseShadowSmokeError("sample_count must be at least 1")
    eligible: list[dict[str, Any]] = []
    seen_processed: set[int] = set()
    line_count = 0
    with Path(frames_path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            line_count += 1
            try:
                record = json.loads(line)
                frame = record["frame"]
                processed = int(frame["processed_index"])
                source = int(frame.get("source_frame_index", frame.get("index")))
                timestamp_ms = int(frame["timestamp_ms"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise PoseShadowSmokeError(
                    f"{video_id} frozen frames line {line_number} is invalid"
                ) from exc
            if processed in seen_processed:
                raise PoseShadowSmokeError(f"{video_id} repeats processed frame {processed}")
            seen_processed.add(processed)
            players = []
            for detection in record.get("detections", []):
                if detection.get("class_name") != "player":
                    continue
                bbox = detection.get("bbox_px")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    raise PoseShadowSmokeError(f"{video_id} has an invalid player bbox")
                values = [float(value) for value in bbox]
                if not all(math.isfinite(value) for value in values) or values[2] <= values[0] or values[3] <= values[1]:
                    raise PoseShadowSmokeError(f"{video_id} has a non-finite or empty player bbox")
                players.append(detection)
            if players:
                eligible.append(
                    {
                        "video_id": video_id,
                        "processed_index": processed,
                        "source_frame_index": source,
                        "timestamp_ms": timestamp_ms,
                        "players": players,
                    }
                )
    if line_count != declared_frame_count:
        raise PoseShadowSmokeError(
            f"{video_id} frame count drifted: {line_count} != {declared_frame_count}"
        )
    if not eligible:
        raise PoseShadowSmokeError(f"{video_id} has no frozen player detections")
    take = min(sample_count, len(eligible))
    if take == 1:
        indexes = [len(eligible) // 2]
    else:
        indexes = sorted(
            {round(index * (len(eligible) - 1) / (take - 1)) for index in range(take)}
        )
    return [eligible[index] for index in indexes]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(float(ordered[lower]), 6)
    weight = position - lower
    return round(float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight), 6)


def run_shadow_smoke(
    *,
    workspace: str | Path,
    output_path: str | Path,
    registry_path: str | Path = "models/rtmpose/m95-shadow-candidates.json",
    candidate_id: str = "rtmpose-x-halpe26-384x288-m95-shadow",
    samples_per_video: int = 2,
    warmup_calls: int = 1,
    device: str = "0",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = Path(output_path)
    output = output.resolve() if output.is_absolute() else (root / output).resolve()
    if not output.is_relative_to(root):
        raise PoseShadowSmokeError("output_path must stay inside the workspace")
    if output.exists():
        raise PoseShadowSmokeError(f"smoke output is immutable and already exists: {output}")
    if warmup_calls < 0:
        raise PoseShadowSmokeError("warmup_calls must be non-negative")

    verified = load_shadow_registry(root, registry_path)
    candidate = next(
        (item for item in verified["candidates"] if item["candidate_id"] == candidate_id),
        None,
    )
    if candidate is None:
        raise PoseShadowSmokeError(f"unknown shadow candidate: {candidate_id}")
    samples_by_video = {
        item["video_id"]: _read_frozen_samples(
            item["frozen_frames"]["path"],
            video_id=item["video_id"],
            declared_frame_count=item["declared_frame_count"],
            sample_count=samples_per_video,
        )
        for item in verified["videos"]
    }

    import cv2
    import mmpose
    import numpy as np
    import torch

    from rallymate_vision.pose import PoseEstimator, RtmposePoseBackend

    if device != "cpu" and not torch.cuda.is_available():
        raise PoseShadowSmokeError("CUDA is unavailable for the requested smoke")
    if device != "cpu":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    backend = RtmposePoseBackend(
        Path(candidate["model_binding"]["path"]),
        Path(candidate["config_binding"]["path"]),
        device=device,
        runtime="pytorch",
        profile="analysis",
        native_keypoint_format="halpe26",
    )
    estimator = PoseEstimator(backend)

    def sync() -> None:
        if device != "cpu":
            torch.cuda.synchronize()

    calls: list[dict[str, Any]] = []
    decoded_cache: dict[tuple[str, int], np.ndarray] = {}
    video_paths = {item["video_id"]: item["video"]["path"] for item in verified["videos"]}
    for video_id, samples in samples_by_video.items():
        capture = cv2.VideoCapture(video_paths[video_id])
        if not capture.isOpened():
            raise PoseShadowSmokeError(f"could not decode development video: {video_id}")
        for sample in samples:
            capture.set(cv2.CAP_PROP_POS_FRAMES, sample["source_frame_index"])
            ok, frame = capture.read()
            if not ok:
                capture.release()
                raise PoseShadowSmokeError(
                    f"could not decode {video_id} frame {sample['source_frame_index']}"
                )
            decoded_cache[(video_id, sample["source_frame_index"])] = frame
        capture.release()

    first_video_id = next(iter(samples_by_video))
    first_sample = samples_by_video[first_video_id][0]
    first_frame = decoded_cache[(first_video_id, first_sample["source_frame_index"])]
    for _ in range(warmup_calls):
        estimator.estimate(
            first_frame,
            first_sample["players"],
            max_players=int(verified["protocol"].get("max_players", 2)),
            timestamp_ms=first_sample["timestamp_ms"],
        )
        sync()

    max_players = int(verified["protocol"].get("max_players", 2))
    for video_id, samples in samples_by_video.items():
        for sample in samples:
            frame = decoded_cache[(video_id, sample["source_frame_index"])]
            sync()
            started = time.perf_counter()
            poses = estimator.estimate(
                frame,
                sample["players"],
                max_players=max_players,
                timestamp_ms=sample["timestamp_ms"],
            )
            sync()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            shape_valid = all(
                pose.get("keypoint_format") == "halpe26"
                and len(pose.get("keypoints", [])) == 26
                and all(
                    math.isfinite(float(point[field]))
                    for point in pose["keypoints"]
                    for field in ("x_px", "y_px", "confidence")
                )
                for pose in poses
            )
            roi_count = min(len(sample["players"]), max_players)
            calls.append(
                {
                    "video_id": video_id,
                    "processed_index": sample["processed_index"],
                    "source_frame_index": sample["source_frame_index"],
                    "timestamp_ms": sample["timestamp_ms"],
                    "roi_count": roi_count,
                    "pose_count": len(poses),
                    "latency_ms": round(elapsed_ms, 6),
                    "latency_per_roi_ms": round(elapsed_ms / max(roi_count, 1), 6),
                    "halpe26_shape_and_finite": shape_valid,
                }
            )

    metadata = backend.metadata().to_dict()
    if metadata.get("input_size") != [384, 288] or metadata.get("native_keypoint_count") != 26:
        raise PoseShadowSmokeError("loaded candidate metadata does not match registry identity")
    latency = [item["latency_ms"] for item in calls]
    per_roi = [item["latency_per_roi_ms"] for item in calls]
    roi_total = sum(item["roi_count"] for item in calls)
    pose_total = sum(item["pose_count"] for item in calls)
    passed = bool(calls) and all(item["halpe26_shape_and_finite"] for item in calls)
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "smoke_passed_ground_truth_required" if passed else "smoke_failed",
        "candidate_id": candidate_id,
        "semantics": "local_load_shape_finiteness_and_sample_performance_not_accuracy",
        "registry": {
            "path": verified["path"],
            "sha256": verified["sha256"],
            "version": verified["registry_version"],
        },
        "production_default": {
            "preset_id": verified["production_default_preset"],
            "unchanged": True,
            "registry": verified["production_default_binding"],
        },
        "candidate": {
            "checkpoint": candidate["model_binding"],
            "config": candidate["config_binding"],
            "official_body8_metrics": candidate["official_body8_metrics"],
            "metadata": metadata,
        },
        "protocol": {
            "phase": "development_smoke_only",
            "samples_per_video_requested": samples_per_video,
            "warmup_calls": warmup_calls,
            "max_players": max_players,
            "profile": "analysis_flip_test",
            "frozen_detection_ROIs": True,
            "sealed_holdout_opened": False,
            "ground_truth_used": False,
            "latency_is_comparative_benchmark": False,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "mmpose": mmpose.__version__,
            "numpy": np.__version__,
            "device": "cpu" if device == "cpu" else torch.cuda.get_device_name(int(device)),
        },
        "inputs": verified["videos"],
        "calls": calls,
        "totals": {
            "sample_calls": len(calls),
            "sample_rois": roi_total,
            "sample_poses": pose_total,
            "pose_return_rate": round(pose_total / roi_total, 6) if roi_total else None,
            "latency_ms": {
                "mean": round(statistics.mean(latency), 6) if latency else None,
                "p50": _percentile(latency, 50),
                "p95": _percentile(latency, 95),
            },
            "latency_per_roi_ms": {
                "p50": _percentile(per_roi, 50),
                "p95": _percentile(per_roi, 95),
            },
            "cuda_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()) if device != "cpu" else None,
            "cuda_peak_reserved_bytes": int(torch.cuda.max_memory_reserved()) if device != "cpu" else None,
        },
        "claims": {
            "official_same_table_metrics_higher_than_current_M_and_L": True,
            "RallyMate_accuracy_improved": False,
            "ground_truth_accuracy_measured": False,
            "candidate_promoted": False,
            "production_default_changed": False,
            "F3_or_F4_promoted": False,
            "grade_or_threshold_generated": False,
        },
    }
    del estimator, backend
    decoded_cache.clear()
    gc.collect()
    if device != "cpu":
        torch.cuda.empty_cache()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return report
