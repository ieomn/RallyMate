from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import cv2

from rallymate_vision.contracts import FRAME_SCHEMA_VERSION
from rallymate_vision.pose import PoseEstimator, RtmposePoseBackend
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.validation import validate_frame_observation


DEFAULT_VIDEO_ID = "850cb0006b406c7176eeda8d711cd065"
DEFAULT_START_FRAME = 930  # 31.0 s at the source video's 30 fps
DEFAULT_MAX_FRAMES = 600   # 20.0 s
MODEL_RELATIVE_PATH = Path(
    "models/rtmpose/rtmpose-m_coco-wholebody133_256x192.pth"
)
CONFIG_RELATIVE_PATH = Path(
    "runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/"
    "wholebody_2d_keypoint/rtmpose/coco-wholebody/"
    "rtmpose-m_8xb64-270e_coco-wholebody-256x192.py"
)
GROUP_RANGES = {
    "body": (0, 17),
    "foot": (17, 23),
    "face": (23, 91),
    "left_hand": (91, 112),
    "right_hand": (112, 133),
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Replay frozen player detections through the official RTMPose-M "
            "COCO-WholeBody133 checkpoint. No points are interpolated or synthesized."
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--video-id", default=DEFAULT_VIDEO_ID)
    parser.add_argument("--start-frame", type=int, default=DEFAULT_START_FRAME)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--device", default="0")
    parser.add_argument("--confidence-threshold", type=float, default=0.25)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.start_frame < 0 or args.max_frames <= 0:
        raise ValueError("start-frame must be >= 0 and max-frames must be > 0")
    if not 0.0 <= args.confidence_threshold <= 1.0:
        raise ValueError("confidence-threshold must be in 0..1")

    root = args.root.resolve()
    video_id = args.video_id
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else root
        / "runs"
        / "pose-wholebody133"
        / f"{video_id}-{args.start_frame}-{args.start_frame + args.max_frames}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_output = output_dir / "frames.jsonl"
    partial_output = output_dir / "frames.jsonl.partial"
    metadata_output = output_dir / "inference-metadata.json"
    if frames_output.exists() or partial_output.exists() or metadata_output.exists():
        raise FileExistsError(
            f"refusing to overwrite existing WholeBody133 artifact: {output_dir}"
        )

    source_frames_path = root / "runs" / "full-test" / video_id / "frames.jsonl"
    timeline_path = (
        root
        / "reports"
        / "pose-scoring-ab"
        / "rtmpose-m-halpe26-256x192"
        / video_id
        / "primary-player.jsonl"
    )
    video_path = root / "FULL-TEST" / f"{video_id}.mp4"
    model_path = root / MODEL_RELATIVE_PATH
    config_path = root / CONFIG_RELATIVE_PATH
    source_records = {
        int(item["frame"]["processed_index"]): item
        for item in _jsonl(source_frames_path)
    }
    timeline = {
        int(item["processed_index"]): item for item in _jsonl(timeline_path)
    }

    backend = RtmposePoseBackend(
        model_path,
        config_path,
        device=args.device,
        runtime="pytorch",
        profile="realtime",
        native_keypoint_format="coco_wholebody133",
    )
    estimator = PoseEstimator(backend)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise FileNotFoundError(video_path)

    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    pose_frames = 0
    inference_seconds = 0.0
    group_valid_totals: Counter[str] = Counter()
    group_fully_valid_frames: Counter[str] = Counter()
    missing_primary_detection_frames: list[int] = []
    started = time.perf_counter()
    written = 0
    try:
        with partial_output.open("x", encoding="utf-8", newline="\n") as handle:
            for processed_index in range(
                args.start_frame, args.start_frame + args.max_frames
            ):
                record = source_records.get(processed_index)
                if record is None:
                    break
                source_frame_index = int(record["frame"]["index"])
                current_index = int(capture.get(cv2.CAP_PROP_POS_FRAMES))
                if current_index != source_frame_index:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, source_frame_index)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(
                        f"could not decode source frame {source_frame_index}"
                    )
                timeline_item = timeline.get(processed_index)
                source_track_id = (
                    timeline_item.get("source_track_id") if timeline_item else None
                )
                primary = next(
                    (
                        detection
                        for detection in record.get("detections", [])
                        if detection.get("class_name") == "player"
                        and detection.get("track_id") == source_track_id
                    ),
                    None,
                )
                poses = []
                if primary is None:
                    missing_primary_detection_frames.append(source_frame_index)
                else:
                    inference_started = time.perf_counter()
                    poses = estimator.estimate(
                        frame,
                        [primary],
                        max_players=1,
                        timestamp_ms=int(record["frame"]["timestamp_ms"]),
                    )
                    inference_seconds += time.perf_counter() - inference_started
                if poses:
                    pose = poses[0]
                    if len(pose["keypoints"]) != 133:
                        raise RuntimeError("backend did not return 133 native points")
                    pose_frames += 1
                    for group, (start, end) in GROUP_RANGES.items():
                        valid = sum(
                            float(point["confidence"])
                            >= args.confidence_threshold
                            for point in pose["keypoints"][start:end]
                        )
                        group_valid_totals[group] += valid
                        group_fully_valid_frames[group] += valid == end - start
                candidate = dict(record)
                candidate["schema_version"] = FRAME_SCHEMA_VERSION
                candidate["poses"] = poses
                validate_frame_observation(candidate)
                handle.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                written += 1
                if written % 100 == 0:
                    print(
                        json.dumps(
                            {
                                "processed": written,
                                "pose_frames": pose_frames,
                                "elapsed_seconds": round(
                                    time.perf_counter() - started, 2
                                ),
                            }
                        ),
                        flush=True,
                    )
    finally:
        capture.release()

    if written == 0:
        raise RuntimeError("WholeBody133 inference produced no frame records")
    partial_output.replace(frames_output)
    metadata = {
        "schema_version": "1.0.0",
        "artifact_version": "wholebody133-real-inference/1.0.0",
        "status": "completed_real_model_inference",
        "true_model_inference": True,
        "synthetic_or_interpolated_points": False,
        "command": [sys.executable, *sys.argv],
        "video_id": video_id,
        "source": {
            "video_path": str(video_path),
            "video_sha256": sha256_file(video_path),
            "frozen_detection_frames": str(source_frames_path),
            "frozen_detection_frames_sha256": sha256_file(source_frames_path),
            "primary_timeline": str(timeline_path),
            "primary_timeline_sha256": sha256_file(timeline_path),
            "start_processed_frame": args.start_frame,
            "end_processed_frame_exclusive": args.start_frame + written,
            "fps": fps,
        },
        "model": backend.metadata().to_dict(),
        "topology_groups": {
            group: end - start for group, (start, end) in GROUP_RANGES.items()
        },
        "confidence_threshold": args.confidence_threshold,
        "processing": {
            "frames": written,
            "pose_frames": pose_frames,
            "pose_frame_rate": round(pose_frames / written, 6),
            "inference_seconds": round(inference_seconds, 6),
            "inference_fps": round(pose_frames / max(inference_seconds, 1e-9), 6),
            "wall_seconds": round(time.perf_counter() - started, 6),
            "missing_primary_detection_source_frames": missing_primary_detection_frames,
        },
        "validity": {
            group: {
                "valid_points": group_valid_totals[group],
                "eligible_points": pose_frames * (end - start),
                "valid_rate": round(
                    group_valid_totals[group]
                    / max(pose_frames * (end - start), 1),
                    6,
                ),
                "fully_valid_frame_rate": round(
                    group_fully_valid_frames[group] / max(pose_frames, 1), 6
                ),
            }
            for group, (start, end) in GROUP_RANGES.items()
        },
        "output": str(frames_output),
        "claims": {
            "keypoint_coverage_is_accuracy": False,
            "scoring_promoted": False,
            "coach_calibrated": False,
        },
    }
    metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
