from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rallymate_pose_evaluation.baseline import analyze_pose_artifact


NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def _keypoints(offset: float = 0.0, missing_left_ankle: bool = False) -> list[dict]:
    coordinates = {
        "left_shoulder": (0.40 + offset, 0.30),
        "right_shoulder": (0.60 + offset, 0.30),
        "left_hip": (0.45 + offset, 0.55),
        "right_hip": (0.55 + offset, 0.55),
        "left_knee": (0.44 + offset, 0.72),
        "right_knee": (0.56 + offset, 0.72),
        "left_ankle": (0.42 + offset, 0.90),
        "right_ankle": (0.58 + offset, 0.90),
    }
    points = []
    for index, name in enumerate(NAMES):
        x, y = coordinates.get(name, (0.50 + offset, 0.20))
        confidence = 0.0 if missing_left_ankle and name == "left_ankle" else 0.9
        points.append(
            {
                "index": index,
                "name": name,
                "x_normalized": x,
                "y_normalized": y,
                "confidence": confidence,
            }
        )
    return points


class PoseBaselineTests(unittest.TestCase):
    def test_nonuniform_timestamps_and_missing_are_diagnostic_not_zero_filled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / "frames.jsonl"
            timestamps = (0, 40, 105, 180)
            with frames.open("w", encoding="utf-8") as handle:
                for index, timestamp in enumerate(timestamps):
                    record = {
                        "frame": {
                            "index": index,
                            "processed_index": index,
                            "timestamp_ms": timestamp,
                        },
                        "detections": [
                            {
                                "class_name": "player",
                                "track_id": 7,
                                "bbox_px": [10, 10, 90, 190],
                            }
                        ],
                        "poses": [
                            {
                                "person_track_id": 7,
                                "confidence": 0.9,
                                "keypoints": _keypoints(
                                    offset=index * 0.01,
                                    missing_left_ankle=index == 2,
                                ),
                            }
                        ],
                    }
                    handle.write(json.dumps(record) + "\n")
            summary = root / "summary.json"
            summary.write_text(
                json.dumps(
                    {
                        "input": {"video": {"fps": 25, "duration_ms": 220}},
                        "processing": {
                            "effective_processed_fps": 20.0,
                            "elapsed_seconds": 0.2,
                            "stage_seconds": {"pose_seconds": 0.1},
                        },
                        "coverage": {"pose_frame_fraction": 1.0},
                    }
                ),
                encoding="utf-8",
            )
            video = root / "video.mp4"
            writer = cv2.VideoWriter(
                str(video), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (16, 16)
            )
            self.assertTrue(writer.isOpened())
            for _ in timestamps:
                writer.write(np.zeros((16, 16, 3), dtype=np.uint8))
            writer.release()
            result = analyze_pose_artifact(frames, summary, video)
            self.assertEqual(result["tracking"]["primary_pose_track_id"], 7)
            self.assertEqual(
                result["keypoints"]["valid_fraction_on_primary_track"]["left_ankle"],
                0.75,
            )
            speed = result["features"]["distributions"]["body_center_speed_body_s"]
            self.assertEqual(speed["valid_count"], 3)
            self.assertGreater(speed["p50"], 0.0)
            stability = result["features"]["distributions"]["stability_duration_ms"]
            self.assertEqual(stability["status"], "unavailable")
            self.assertIsNone(stability["value"])
            self.assertEqual(
                result["ground_truth"]["keypoint_error_status"],
                "ground_truth_required",
            )


if __name__ == "__main__":
    unittest.main()
