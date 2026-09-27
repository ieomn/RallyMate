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

    def test_reconstruction_keeps_multiple_segments_and_interpolates_short_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = [
                _frame(0, ball_x=0.20, ball_track=1),
                _frame(1, ball_x=0.25, ball_track=1),
                # 200 ms is a short, bounded gap and is interpolated.
                _frame(3, ball_x=0.35, ball_track=1),
                # Long gap starts a new segment instead of drawing across it.
                _frame(20, ball_x=0.40, ball_track=1),
                _frame(21, ball_x=0.44, ball_track=2),
            ]
            path = self._write(Path(directory), rows)
            result = build_trajectory_preview(path)
        reconstruction = result["ball"]["reconstruction"]
        self.assertGreaterEqual(reconstruction["summary"]["segment_count"], 2)
        self.assertGreater(reconstruction["summary"]["interpolated_count"], 0)
        self.assertTrue(
            all(point["source"] in {"observed", "interpolated"}
                for segment in reconstruction["segments"]
                for point in segment["points"])
        )

    def test_partial_reader_ignores_only_unterminated_last_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            path.write_bytes((json.dumps(_frame(0, ball_x=0.2)) + "\n{\"frame\":" ).encode())
            with self.assertRaises(TrajectoryExtractionError):
                build_trajectory_preview(path)
            result = build_trajectory_preview(path, allow_partial=True)
        self.assertTrue(result["source"]["is_partial"])
        self.assertEqual(result["source"]["frame_count"], 1)

    def test_all_tracks_are_kept_and_coverage_counts_frames_once(self) -> None:
        rows = []
        for index in range(5):
            row = _frame(index, ball_x=0.2 + index * 0.01, ball_track=1)
            row["detections"].extend(_frame(index, ball_x=0.8, ball_track=2)["detections"])
            rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            result = build_trajectory_preview(self._write(Path(directory), rows))
        reconstruction = result["ball"]["reconstruction"]
        self.assertEqual(reconstruction["summary"]["observed_count"], 10)
        self.assertEqual(reconstruction["summary"]["observed_frame_count"], 5)
        self.assertEqual(reconstruction["summary"]["coverage_fraction"], 1)
        self.assertEqual(reconstruction["summary"]["returned_point_count"], 10)
        self.assertEqual(reconstruction["summary"]["interpolated_count"], 0)
        self.assertEqual(result["source"]["start_timestamp_ms"], 0)
        self.assertEqual(result["source"]["end_timestamp_ms"], 400)

    def test_point_budget_preserves_long_track_endpoints_and_short_tracks(self) -> None:
        rows = [_frame(i, ball_x=0.4, ball_track=1) for i in range(400)]
        rows.extend([_frame(500, ball_x=0.2, ball_track=2), _frame(501, ball_x=0.3, ball_track=2)])
        with tempfile.TemporaryDirectory() as directory:
            result = build_trajectory_preview(self._write(Path(directory), rows), sample_limit=1)
        reconstruction = result["ball"]["reconstruction"]
        self.assertLessEqual(reconstruction["summary"]["returned_point_count"], 240)
        first, last = reconstruction["segments"]
        self.assertEqual(first["points"][0]["timestamp_ms"], 0)
        self.assertEqual(first["points"][-1]["timestamp_ms"], 39900)
        self.assertEqual(len(last["points"]), 2)
        self.assertEqual(reconstruction["summary"]["observed_count"], 402)

    def test_two_sided_motion_bridges_medium_gap_without_changing_observations(self) -> None:
        rows = [_frame(i, ball_x=0.1 + i * 0.04, ball_track=1 if i < 4 else 2)
                for i in [0, 1, 2, 7, 8, 9]]
        with tempfile.TemporaryDirectory() as directory:
            result = build_trajectory_preview(self._write(Path(directory), rows))
        reconstruction = result["ball"]["reconstruction"]
        self.assertEqual(reconstruction["version"], "1.1.0")
        self.assertEqual(reconstruction["summary"]["segment_count"], 1)
        self.assertEqual(reconstruction["summary"]["extended_bridge_count"], 1)
        segment = reconstruction["segments"][0]
        self.assertEqual(segment["track_ids"], [1, 2])
        self.assertEqual(segment["interpolation_intervals"], [
            {"start_ms": 200, "end_ms": 700, "method": "bounded_hermite"}
        ])
        self.assertEqual(segment["interpolated_count"], 4)
        observed = [p for p in segment["points"] if p["source"] == "observed"]
        self.assertEqual([p["frame_index"] for p in observed], [0, 1, 2, 7, 8, 9])
        self.assertEqual([p["x"] for p in observed], [0.1, 0.14, 0.18, 0.38, 0.42, 0.46])
        interpolated = [p for p in segment["points"] if p["source"] == "interpolated"]
        self.assertTrue(all(p["frame_index"] is None and p["processed_index"] is None for p in interpolated))
        self.assertTrue(all(0.18 < p["x"] < 0.38 for p in interpolated))
        self.assertEqual(reconstruction["summary"]["observed_count"], 6)

    def test_unsupported_gaps_remain_disconnected(self) -> None:
        cases = {
            "long_gap": [(0, .1), (1, .14), (2, .18), (9, .46), (10, .50)],
            "direction_reversal": [(0, .1), (1, .14), (2, .18), (7, .38), (8, .34)],
            "static_false_positive": [(0, .1), (1, .1), (2, .1), (7, .12), (8, .12)],
            "single_endpoint": [(0, .1), (1, .14), (2, .18), (7, .38)],
            "camera_jump": [(0, .1), (1, .14), (2, .18), (7, .85), (8, .89)],
        }
        for name, coordinates in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                rows = [_frame(i, ball_x=x, ball_track=1 if i < 3 else 2) for i, x in coordinates]
                reconstruction = build_trajectory_preview(self._write(Path(directory), rows))["ball"]["reconstruction"]
                self.assertEqual(reconstruction["summary"]["segment_count"], 2)
                self.assertEqual(reconstruction["summary"]["extended_bridge_count"], 0)

    def test_competing_ball_fragments_are_not_arbitrarily_joined(self) -> None:
        rows = [_frame(i, ball_x=.1 + i * .04, ball_track=1) for i in [0, 1, 2]]
        for i in [7, 8, 9]:
            row = _frame(i, ball_x=.1 + i * .04, ball_track=2)
            row["detections"].extend(_frame(i, ball_x=.105 + i * .04, ball_track=3)["detections"])
            rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            reconstruction = build_trajectory_preview(self._write(Path(directory), rows))["ball"]["reconstruction"]
        self.assertEqual(reconstruction["summary"]["segment_count"], 3)
        self.assertEqual(reconstruction["summary"]["extended_bridge_count"], 0)

    def test_simultaneously_observed_ids_cannot_be_stitched_later(self) -> None:
        rows = [_frame(i, ball_x=.1 + i * .04, ball_track=1) for i in [0, 1, 2]]
        rows[0]["detections"].extend(_frame(0, ball_x=.8, ball_track=2)["detections"])
        rows.extend(_frame(i, ball_x=.1 + i * .04, ball_track=2) for i in [7, 8, 9])
        with tempfile.TemporaryDirectory() as directory:
            reconstruction = build_trajectory_preview(self._write(Path(directory), rows))["ball"]["reconstruction"]
        self.assertEqual(reconstruction["summary"]["segment_count"], 3)
        self.assertEqual(reconstruction["summary"]["extended_bridge_count"], 0)

    def test_sampling_preserves_interpolation_interval_provenance(self) -> None:
        # A short physical gap can disappear from a heavily sampled payload.
        # Its provenance must survive independently of returned point source.
        rows = [_frame(i, ball_x=.1 + i * .0005, ball_track=1) for i in range(600) if i != 302]
        with tempfile.TemporaryDirectory() as directory:
            reconstruction = build_trajectory_preview(self._write(Path(directory), rows), sample_limit=1)["ball"]["reconstruction"]
        segment = reconstruction["segments"][0]
        self.assertTrue(segment["sampling"]["is_sampled"])
        self.assertEqual(segment["sampling"]["original_point_count"], 600)
        self.assertEqual(segment["sampling"]["returned_point_count"], 240)
        self.assertEqual(segment["interpolation_intervals"], [
            {"start_ms": 30100, "end_ms": 30300, "method": "linear"}
        ])
        self.assertEqual(segment["points"][0]["timestamp_ms"], 0)
        self.assertEqual(segment["points"][-1]["timestamp_ms"], 59900)

    def test_interval_budget_marks_incomplete_provenance_explicitly(self) -> None:
        rows = [_frame(i, ball_x=.1 + i * .01) for i in range(0, 20, 2)]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), rows)
            with patch("rallymate_vision.trajectory.MAX_RECONSTRUCTION_POINTS", 4):
                reconstruction = build_trajectory_preview(path)["ball"]["reconstruction"]
        segment = reconstruction["segments"][0]
        self.assertFalse(segment["interpolation_intervals_complete"])
        self.assertEqual(len(segment["interpolation_intervals"]), 4)
        self.assertEqual(segment["bridged_gap_count"], 9)
        self.assertLessEqual(len(segment["points"]), 4)


if __name__ == "__main__":
    unittest.main()
