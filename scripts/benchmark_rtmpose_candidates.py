from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mmpose
import numpy as np
import torch

from rallymate_vision.pose import PoseEstimator, RtmposePoseBackend


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values), percentile)), 6)


def _samples(root: Path, video_id: str, count: int) -> list[dict]:
    frames_path = root / "runs" / "full-test" / video_id / "frames.jsonl"
    records = frames_path.read_text(encoding="utf-8").splitlines()
    indexes = set(
        int(value)
        for value in np.linspace(0, len(records) - 1, num=min(count, len(records)))
    )
    selected = []
    for line in records:
        record = json.loads(line)
        if int(record["frame"]["processed_index"]) not in indexes:
            continue
        players = [
            item
            for item in record.get("detections", [])
            if item.get("class_name") == "player"
        ]
        if players:
            selected.append(
                {
                    "source_frame_index": int(record["frame"]["index"]),
                    "timestamp_ms": int(record["frame"]["timestamp_ms"]),
                    "players": players,
                }
            )
    return selected


def _decode(root: Path, video_id: str, samples: list[dict]) -> list[tuple[dict, np.ndarray]]:
    capture = cv2.VideoCapture(str(root / "FULL-TEST" / f"{video_id}.mp4"))
    if not capture.isOpened():
        raise RuntimeError(f"could not open video {video_id}")
    decoded = []
    for sample in samples:
        capture.set(cv2.CAP_PROP_POS_FRAMES, sample["source_frame_index"])
        ok, frame = capture.read()
        if ok:
            decoded.append((sample, frame))
    capture.release()
    return decoded


def _config_path(candidate: dict) -> Path:
    site_packages = Path(mmpose.__file__).resolve().parent.parent
    return site_packages / candidate["config_package_relative_path"]


def benchmark_candidate(
    root: Path,
    candidate: dict,
    decoded_by_video: dict[str, list[tuple[dict, np.ndarray]]],
    warmup_calls: int,
) -> dict:
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    checkpoint = root / "models" / "rtmpose" / candidate["checkpoint"]
    config = _config_path(candidate)
    backend = RtmposePoseBackend(
        checkpoint,
        config,
        device="0",
        runtime="pytorch",
        profile="realtime",
    )
    estimator = PoseEstimator(backend)
    first_sample, first_frame = next(iter(decoded_by_video.values()))[0]
    for _ in range(warmup_calls):
        estimator.estimate(
            first_frame,
            first_sample["players"],
            max_players=2,
            timestamp_ms=first_sample["timestamp_ms"],
        )
        torch.cuda.synchronize()
    memory_after_warmup = {
        "allocated": int(torch.cuda.memory_allocated()),
        "reserved": int(torch.cuda.memory_reserved()),
    }
    calls = []
    by_video = {}
    for video_id, decoded in decoded_by_video.items():
        video_calls = []
        for sample, frame in decoded:
            torch.cuda.synchronize()
            started = time.perf_counter()
            poses = estimator.estimate(
                frame,
                sample["players"],
                max_players=2,
                timestamp_ms=sample["timestamp_ms"],
            )
            torch.cuda.synchronize()
            latency_ms = (time.perf_counter() - started) * 1000.0
            valid_shape = all(
                pose["keypoint_format"] == "halpe26"
                and len(pose["keypoints"]) == 26
                and all(
                    math.isfinite(float(point[axis]))
                    for point in pose["keypoints"]
                    for axis in ("x_px", "y_px", "confidence")
                )
                for pose in poses
            )
            call = {
                "source_frame_index": sample["source_frame_index"],
                "roi_count": min(len(sample["players"]), 2),
                "pose_count": len(poses),
                "latency_ms": latency_ms,
                "latency_per_roi_ms": latency_ms / max(min(len(sample["players"]), 2), 1),
                "halpe26_shape_and_finite": valid_shape,
            }
            calls.append(call)
            video_calls.append(call)
        by_video[video_id] = {
            "calls": len(video_calls),
            "rois": sum(item["roi_count"] for item in video_calls),
            "poses": sum(item["pose_count"] for item in video_calls),
            "latency_ms": {
                "p50": _percentile([item["latency_ms"] for item in video_calls], 50),
                "p95": _percentile([item["latency_ms"] for item in video_calls], 95),
            },
        }
    result = {
        "candidate_id": candidate["candidate_id"],
        "status": "smoke_passed" if calls and all(item["halpe26_shape_and_finite"] for item in calls) else "failed",
        "metadata": backend.metadata().to_dict(),
        "warmup_calls": warmup_calls,
        "sample_calls": len(calls),
        "sample_rois": sum(item["roi_count"] for item in calls),
        "sample_poses": sum(item["pose_count"] for item in calls),
        "latency_ms": {
            "p50": _percentile([item["latency_ms"] for item in calls], 50),
            "p95": _percentile([item["latency_ms"] for item in calls], 95),
            "mean": round(statistics.mean(item["latency_ms"] for item in calls), 6),
        },
        "latency_per_roi_ms": {
            "p50": _percentile([item["latency_per_roi_ms"] for item in calls], 50),
            "p95": _percentile([item["latency_per_roi_ms"] for item in calls], 95),
        },
        "cuda_memory_bytes": {
            "after_warmup": memory_after_warmup,
            "peak_allocated": int(torch.cuda.max_memory_allocated()),
            "peak_reserved": int(torch.cuda.max_memory_reserved()),
        },
        "by_video": by_video,
    }
    del estimator, backend
    gc.collect()
    torch.cuda.empty_cache()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--samples-per-video", type=int, default=12)
    parser.add_argument("--warmup-calls", type=int, default=3)
    args = parser.parse_args()
    root = args.root.resolve()
    registry_path = root / "models" / "rtmpose" / "model-candidates.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    decoded_by_video = {
        video_id: _decode(root, video_id, _samples(root, video_id, args.samples_per_video))
        for video_id in VIDEO_IDS
    }
    results = [
        benchmark_candidate(root, candidate, decoded_by_video, args.warmup_calls)
        for candidate in registry["candidates"]
    ]
    report = {
        "schema_version": "1.0.0",
        "report_version": "rtmpose-candidate-smoke-2026-08-13.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(item["status"] == "smoke_passed" for item in results) else "failed",
        "semantics": "engineering_smoke_and_sample_performance_not_ground_truth_accuracy",
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "mmpose": mmpose.__version__,
            "numpy": np.__version__,
            "cuda_device": torch.cuda.get_device_name(0),
        },
        "protocol": {
            "videos": list(VIDEO_IDS),
            "samples_per_video_requested": args.samples_per_video,
            "max_players": 2,
            "profile": "realtime",
            "execution": "sequential_ROIs_within_each_frame_v0",
            "ground_truth": "not_used",
        },
        "candidates": results,
    }
    output = root / "reports" / "rtmpose-candidate-smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"], "candidates": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
