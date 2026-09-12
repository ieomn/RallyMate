from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from rallymate_vision.trajectory import (
    TrajectoryExtractionError,
    build_trajectory_preview,
)


def _frame(
    index: int,
    *,
    ball_x: float | None = None,
    racket_x: float | None = None,
    ball_track: int = 3,
    racket_track: int = 7,
) -> dict:
    detections = []
    if ball_x is not None:
        detections.append(
            {
                "class_name": "ball",
                "confidence": 0.82 + index / 1000,
                "track_id": ball_track,
                "bbox_normalized": [ball_x - 0.01, 0.45, ball_x + 0.01, 0.47],
                "center_normalized": [ball_x, 0.46],
            }
        )
    if racket_x is not None:
        detections.append(
            {
                "class_name": "racket",
                "confidence": 0.71,
                "track_id": racket_track,
                "bbox_normalized": [racket_x, 0.2, racket_x + 0.08, 0.6],
            }
        )
    return {
        "schema_version": "1.1.0",
        "frame": {
            "index": index,
            "processed_index": index,
            "timestamp_ms": index * 100,
            "width": 1000,
            "height": 500,
            "coordinate_origin": "top_left",
        },
        "detections": detections,
    }


class TrajectoryPreviewTests(unittest.TestCase):
    def _write(self, root: Path, rows: list[dict]) -> Path:
        path = root / "frames.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def test_ball_preview_has_observed_points_and_explicit_heuristic_prediction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                Path(directory),
                [
                    _frame(0, ball_x=0.20, racket_x=0.30),
                    _frame(1, ball_x=0.30, racket_x=0.31),
                    _frame(2, ball_x=0.40, racket_x=0.32),
                    _frame(3, ball_x=0.50, racket_x=0.33),
                ],
            )
            result = build_trajectory_preview(
                path, sample_limit=2, prediction_horizon_ms=300
            )

        self.assertEqual(result["schema_version"], "1.0.0")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["source"]["frame_count"], 4)
        self.assertEqual(result["ball"]["track_id"], 3)
        self.assertEqual(result["ball"]["track_count"], 1)
        self.assertEqual(result["ball"]["observed_count"], 4)
        self.assertEqual(len(result["ball"]["observed"]), 2)
        self.assertEqual(result["ball"]["prediction_status"], "heuristic_preview")
        self.assertEqual(result["ball"]["prediction_reason"], None)
        self.assertEqual(result["ball"]["predicted_covered_horizon_ms"], 300)
        self.assertGreater(len(result["ball"]["predicted"]), 0)
        self.assertEqual(
            result["ball"]["predicted"][0]["source"],
            "constant_velocity_extrapolation",
        )
        self.assertAlmostEqual(result["ball"]["velocity"]["vx_normalized_per_s"], 1.0)
        self.assertEqual(result["racket"]["track_id"], 7)
        self.assertEqual(result["racket"]["track_count"], 1)
        self.assertEqual(result["racket"]["geometry_status"], "bbox_only")
        self.assertEqual(result["racket"]["association_status"], "unassociated")
        self.assertEqual(
            result["racket"]["keypoint_status"],
            "not_available_from_current_detection_contract",
        )
        self.assertIn("不等同网球专项轨迹模型", result["limitations"][0])

    def test_missing_ball_is_not_promoted_to_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                Path(directory),
                [_frame(0, racket_x=0.2), _frame(1, racket_x=0.3)],
            )
            result = build_trajectory_preview(path)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["ball"]["track_id_status"], "not_observed")
        self.assertEqual(result["ball"]["prediction_status"], "not_available")
        self.assertEqual(result["ball"]["prediction_reason"], "ball_not_observed")
        self.assertEqual(result["ball"]["predicted"], [])
        self.assertEqual(result["racket"]["observed_count"], 2)

    def test_untracked_observations_are_exposed_without_fake_track_id(self) -> None:
        row = _frame(0, ball_x=0.2)
        row["detections"][0].pop("track_id")
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), [row])
            result = build_trajectory_preview(path)

        self.assertIsNone(result["ball"]["track_id"])
        self.assertEqual(result["ball"]["track_id_status"], "untracked_observations")
        self.assertEqual(result["ball"]["observed"][0]["track_id"], None)

    def test_malformed_frames_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            path.write_text("{not-json}\n", encoding="utf-8")
            with self.assertRaisesRegex(TrajectoryExtractionError, "line 1"):
                build_trajectory_preview(path)

    def test_parameter_bounds_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), [_frame(0, ball_x=0.2)])
            with self.assertRaisesRegex(TrajectoryExtractionError, "sample_limit"):
                build_trajectory_preview(path, sample_limit=0)
            with self.assertRaisesRegex(
                TrajectoryExtractionError, "prediction_horizon_ms"
            ):
                build_trajectory_preview(path, prediction_horizon_ms=5001)

    def test_artifact_limits_fail_closed_before_unbounded_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                Path(directory),
                [_frame(0, ball_x=0.2), _frame(1, ball_x=0.3)],
            )
            with patch("rallymate_vision.trajectory.MAX_FRAMES_ARTIFACT_BYTES", 1):
                with self.assertRaisesRegex(TrajectoryExtractionError, "byte limit"):
                    build_trajectory_preview(path)
            with patch("rallymate_vision.trajectory.MAX_FRAMES_ARTIFACT_LINES", 1):
                with self.assertRaisesRegex(TrajectoryExtractionError, "line limit"):
                    build_trajectory_preview(path)

    def test_prediction_reaches_long_requested_horizon_with_bounded_points(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                Path(directory),
                [_frame(index, ball_x=0.2 + index * 0.02) for index in range(6)],
            )
            result = build_trajectory_preview(path, prediction_horizon_ms=5000)
        self.assertLessEqual(len(result["ball"]["predicted"]), 8)
        self.assertEqual(result["ball"]["predicted_covered_horizon_ms"], 5000)

    def test_preview_matches_versioned_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(
                Path(directory),
                [
                    _frame(0, ball_x=0.20, racket_x=0.30),
                    _frame(1, ball_x=0.30, racket_x=0.31),
                ],
            )
            result = build_trajectory_preview(path)
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "contracts"
            / "trajectory-preview.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
