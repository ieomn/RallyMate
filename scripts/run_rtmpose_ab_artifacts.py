from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import cv2
import mmpose

from rallymate_pose_evaluation.baseline import analyze_pose_artifact
from rallymate_vision.contracts import FRAME_SCHEMA_VERSION
from rallymate_vision.pose import PoseEstimator, RtmposePoseBackend
from rallymate_vision.validation import validate_frame_observation


DEFAULT_VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _config_path(candidate: dict) -> Path:
    site_packages = Path(mmpose.__file__).resolve().parent.parent
    return site_packages / candidate["config_package_relative_path"]


def _load_candidates(root: Path) -> list[tuple[dict, RtmposePoseBackend, PoseEstimator]]:
    registry = json.loads(
        (root / "models" / "rtmpose" / "model-candidates.json").read_text(
            encoding="utf-8"
        )
    )
    candidates = []
    for candidate in registry["candidates"]:
        backend = RtmposePoseBackend(
            root / "models" / "rtmpose" / candidate["checkpoint"],
            _config_path(candidate),
            device="0",
            runtime="pytorch",
            profile="realtime",
        )
        candidates.append((candidate, backend, PoseEstimator(backend)))
    return candidates


def run_video(
    root: Path,
    video_id: str,
    candidates: list[tuple[dict, RtmposePoseBackend, PoseEstimator]],
) -> None:
    source_dir = root / "runs" / "full-test" / video_id
    source_frames = source_dir / "frames.jsonl"
    source_summary = json.loads((source_dir / "summary.json").read_text(encoding="utf-8"))
    video_path = root / "FULL-TEST" / f"{video_id}.mp4"
    outputs = {}
    for candidate, backend, _ in candidates:
        output_dir = root / "runs" / "pose-ab" / candidate["candidate_id"] / video_id
        if output_dir.exists():
            raise FileExistsError(
                f"refusing to overwrite A/B output: {output_dir}"
            )
        output_dir.mkdir(parents=True)
        outputs[candidate["candidate_id"]] = {
            "dir": output_dir,
            "handle": (output_dir / "frames.jsonl").open(
                "x", encoding="utf-8", newline="\n"
            ),
            "pose_frames": 0,
            "pose_count": 0,
            "latency_seconds": 0.0,
            "backend": backend,
        }

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")
    processed = 0
    started = time.perf_counter()
    try:
        with source_frames.open("r", encoding="utf-8") as source_handle:
            for line in source_handle:
                record = json.loads(line)
                frame_info = record["frame"]
                expected_index = int(frame_info["index"])
                current_index = int(capture.get(cv2.CAP_PROP_POS_FRAMES))
                if current_index != expected_index:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, expected_index)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(
                        f"could not decode {video_id} frame {expected_index}"
                    )
                players = [
                    detection
                    for detection in record.get("detections", [])
                    if detection.get("class_name") == "player"
                ]
                for candidate, _, estimator in candidates:
                    candidate_id = candidate["candidate_id"]
                    target = outputs[candidate_id]
                    pose_started = time.perf_counter()
                    poses = estimator.estimate(
                        frame,
                        players,
                        max_players=2,
                        timestamp_ms=int(frame_info["timestamp_ms"]),
                    )
                    target["latency_seconds"] += time.perf_counter() - pose_started
                    target["pose_count"] += len(poses)
                    if poses:
                        target["pose_frames"] += 1
                    candidate_record = dict(record)
                    candidate_record["schema_version"] = FRAME_SCHEMA_VERSION
                    candidate_record["poses"] = poses
                    validate_frame_observation(candidate_record)
                    target["handle"].write(
                        json.dumps(candidate_record, ensure_ascii=False) + "\n"
                    )
                processed += 1
                if processed % 500 == 0:
                    print(
                        json.dumps(
                            {
                                "video_id": video_id,
                                "processed": processed,
                                "elapsed_seconds": round(time.perf_counter() - started, 1),
                            }
                        ),
                        flush=True,
                    )
    finally:
        capture.release()
        for target in outputs.values():
            target["handle"].close()

    elapsed = time.perf_counter() - started
    for candidate, backend, _ in candidates:
        candidate_id = candidate["candidate_id"]
        target = outputs[candidate_id]
        output_dir = target["dir"]
        summary = {
            "schema_version": "1.0.0",
            "status": "completed",
            "input": source_summary["input"],
            "models": {
                "detect": source_summary["models"]["detect"],
                "pose_backend": backend.metadata().to_dict(),
                "pose_format": "halpe26",
                "source_detection_artifact": str(source_frames),
            },
            "processing": {
                "processed_frames": processed,
                "elapsed_seconds": round(elapsed, 6),
                "effective_processed_fps": round(processed / max(elapsed, 1e-9), 6),
                "stage_seconds": {
                    "pose_seconds": round(target["latency_seconds"], 6)
                },
                "semantics": "pose_only_replay_over_frozen_detection_ROIs_three_candidates_share_decode_time",
            },
            "coverage": {
                "pose_frame_fraction": round(
                    target["pose_frames"] / max(processed, 1), 6
                )
            },
            "counts": {
                "frames_with_pose": target["pose_frames"],
                "poses": target["pose_count"],
            },
        }
        summary_path = output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        analysis = analyze_pose_artifact(
            output_dir / "frames.jsonl", summary_path, video_path
        )
        (output_dir / "pose-diagnostics.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "video_id": video_id,
                "status": "completed",
                "frames": processed,
                "elapsed_seconds": round(elapsed, 2),
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--video-id", action="append", dest="video_ids")
    args = parser.parse_args()
    root = args.root.resolve()
    candidates = _load_candidates(root)
    for video_id in args.video_ids or DEFAULT_VIDEO_IDS:
        run_video(root, video_id, candidates)


if __name__ == "__main__":
    main()
