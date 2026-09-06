from __future__ import annotations

import unittest

import numpy as np

from rallymate_evaluation.feature_errors import evaluate_feature_errors
from rallymate_evaluation.ground_truth import (
    apply_keypoint_corrections,
    validate_keypoint_annotation,
)
from rallymate_features.schemas import PoseSequence


def _sequence(knee_x: float) -> PoseSequence:
    timestamps = np.asarray([0, 40, 95, 165, 250, 350, 470], dtype=np.int64)
    names = (
        "left_shoulder",
        "right_shoulder",
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
    )
    base = {
        "left_shoulder": (0.40, 0.30),
        "right_shoulder": (0.60, 0.30),
        "left_hip": (0.45, 0.55),
        "right_hip": (0.55, 0.55),
        "left_knee": (knee_x, 0.72),
        "right_knee": (0.56, 0.72),
        "left_ankle": (0.42, 0.90),
        "right_ankle": (0.58, 0.90),
    }
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size),
        keypoints_xy={
            name: np.asarray(
                [[x + timestamp / 1000 * 0.05, y] for timestamp in timestamps]
            )
            for name, (x, y) in base.items()
        },
        confidence={name: np.full(timestamps.size, 0.9) for name in names},
    )


def _direction_sequence(dx_per_second: float, dy_per_second: float) -> PoseSequence:
    timestamps = np.asarray([0, 40, 95, 165, 250, 350, 470], dtype=np.int64)
    names = (
        "left_shoulder",
        "right_shoulder",
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
    )
    base = {
        "left_shoulder": (0.40, 0.30),
        "right_shoulder": (0.60, 0.30),
        "left_hip": (0.45, 0.55),
        "right_hip": (0.55, 0.55),
        "left_knee": (0.44, 0.72),
        "right_knee": (0.56, 0.72),
        "left_ankle": (0.42, 0.90),
        "right_ankle": (0.58, 0.90),
    }
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size),
        keypoints_xy={
            name: np.asarray(
                [
                    [
                        x + timestamp / 1000.0 * dx_per_second,
                        y + timestamp / 1000.0 * dy_per_second,
                    ]
                    for timestamp in timestamps
                ]
            )
            for name, (x, y) in base.items()
        },
        confidence={name: np.full(timestamps.size, 0.9) for name in names},
    )


def _target_semantic(*, observable: bool = True) -> dict:
    return {
        "schema_version": "1.0.0",
        "annotation_id": "semantic-target-1",
        "video_id": "video",
        "event_id": "truth-fs02",
        "indicator_id": "FS02-M02",
        "semantic_key": "target_direction",
        "semantic_type": "direction",
        "observable": observable,
        "value": (
            {"direction_deg": 0.0, "coordinate_frame": "image_plane"}
            if observable
            else None
        ),
        "null_reason": None if observable else "tactical_intent_not_visible",
        "annotation_confidence": 0.95,
        "annotator_id": "coach-target",
        "reviewer_id": "reviewer-target",
        "adjudication_status": "accepted",
    }


class FeatureEvaluationTests(unittest.TestCase):
    def test_target_alignment_error_uses_manual_semantic_and_corrected_pose(self) -> None:
        prediction = {
            "event_id": "pred-fs02",
            "video_id": "video",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
        }
        truth = {
            **prediction,
            "event_id": "truth-fs02",
            "view_group": "side",
        }
        result = evaluate_feature_errors(
            _direction_sequence(0.20, 0.0),
            _direction_sequence(0.0, -0.20),
            [prediction],
            [truth],
            {"FS02": ["launch_direction_deg"]},
            context_feature_names_by_event={
                "FS02": ["target_direction_alignment_error_deg"]
            },
            semantic_ground_truth=[_target_semantic()],
        )
        metric = result["feature_metrics"][
            "target_direction_alignment_error_deg"
        ]
        self.assertEqual(metric["unit"], "deg")
        self.assertEqual(metric["overall"]["eligible_count"], 1)
        self.assertEqual(metric["overall"]["valid_count"], 1)
        self.assertAlmostEqual(metric["overall"]["mae"], 90.0, places=5)
        self.assertAlmostEqual(metric["overall"]["bias"], -90.0, places=5)
        detail = next(
            item
            for item in result["details"]
            if item["feature_name"] == "target_direction_alignment_error_deg"
        )
        self.assertEqual(detail["model_value"], 0.0)
        self.assertAlmostEqual(detail["ground_truth_value"], 90.0, places=5)
        self.assertTrue(detail["semantic_ground_truth"]["available"])
        self.assertEqual(
            detail["semantic_ground_truth"]["annotation_id"],
            "semantic-target-1",
        )
        budget = result["error_budget"]["features"][
            "target_direction_alignment_error_deg"
        ]
        self.assertEqual(budget["pose_error"]["valid_rate"], 1.0)
        self.assertEqual(budget["smoothing_error"]["valid_rate"], 1.0)
        self.assertEqual(budget["smoothing_error"]["status"], "evaluated")
        self.assertEqual(
            detail["smoothing_counterfactual_reason"],
            "computed_from_launch_direction_raw_counterfactual",
        )

    def test_target_alignment_truth_missing_is_incomplete_not_zero(self) -> None:
        prediction = {
            "event_id": "pred-fs02",
            "video_id": "video",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
        }
        truth = {**prediction, "event_id": "truth-fs02", "view_group": "side"}
        result = evaluate_feature_errors(
            _direction_sequence(0.20, 0.0),
            _direction_sequence(0.20, 0.0),
            [prediction],
            [truth],
            {"FS02": ["launch_direction_deg"]},
            context_feature_names_by_event={
                "FS02": ["target_direction_alignment_error_deg"]
            },
            semantic_ground_truth=[],
        )
        metric = result["feature_metrics"][
            "target_direction_alignment_error_deg"
        ]["overall"]
        self.assertEqual(result["status"], "evaluated_with_incomplete_feature_truth")
        self.assertEqual(metric["eligible_count"], 1)
        self.assertEqual(metric["valid_count"], 0)
        self.assertIsNone(metric["mae"])
        detail = next(
            item
            for item in result["details"]
            if item["feature_name"] == "target_direction_alignment_error_deg"
        )
        self.assertIsNone(detail["model_value"])
        self.assertEqual(
            detail["semantic_ground_truth"]["reason"],
            "manual_target_direction_missing",
        )

    def test_target_alignment_truth_requires_independent_reviewer(self) -> None:
        prediction = {
            "event_id": "pred-fs02",
            "video_id": "video",
            "event_code": "FS02",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
        }
        truth = {**prediction, "event_id": "truth-fs02", "view_group": "side"}
        semantic = _target_semantic()
        semantic["reviewer_id"] = semantic["annotator_id"]
        with self.assertRaisesRegex(ValueError, "independent reviewer"):
            evaluate_feature_errors(
                _direction_sequence(0.20, 0.0),
                _direction_sequence(0.20, 0.0),
                [prediction],
                [truth],
                {"FS02": ["launch_direction_deg"]},
                context_feature_names_by_event={
                    "FS02": ["target_direction_alignment_error_deg"]
                },
                semantic_ground_truth=[semantic],
            )

    def test_without_truth_returns_null_metrics(self) -> None:
        result = evaluate_feature_errors(
            _sequence(0.44), None, [], None, {"FS09": ["left_knee_flexion_deg"]}
        )
        self.assertEqual(result["status"], "ground_truth_required")
        self.assertIsNone(result["event_metrics"]["event_f1"])
        self.assertIsNone(result["error_budget"]["pose_error"])

    def test_feature_metrics_and_error_budget_use_matched_manual_event(self) -> None:
        prediction = {
            "event_id": "pred",
            "video_id": "video",
            "event_code": "FS09",
            "person_track_id": 1,
            "start_ms": 40,
            "end_ms": 470,
        }
        truth = {
            "event_id": "truth",
            "video_id": "video",
            "event_code": "FS09",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
            "view_group": "side",
        }
        result = evaluate_feature_errors(
            _sequence(0.44),
            _sequence(0.50),
            [prediction],
            [truth],
            {"FS09": ["left_knee_flexion_deg", "body_center_speed_body_s"]},
        )
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["event_metrics"]["event_f1"], 1.0)
        knee = result["feature_metrics"]["left_knee_flexion_deg"]
        self.assertEqual(knee["overall"]["valid_rate"], 1.0)
        self.assertGreater(knee["overall"]["mae"], 0.0)
        self.assertIn("side", knee["by_view"])
        self.assertIn(
            "pose_error",
            result["error_budget"]["features"]["left_knee_flexion_deg"],
        )
        self.assertEqual(
            result["error_budget"]["features"]["left_knee_flexion_deg"][
                "pose_error"
            ]["eligible_count"],
            1,
        )
        self.assertEqual(
            result["error_budget"]["features"]["left_knee_flexion_deg"][
                "pose_error"
            ]["valid_rate"],
            1.0,
        )
        self.assertEqual(
            result["error_budget"]["method"],
            "one_factor_counterfactual_differences_not_additive_shapley_decomposition",
        )

    def test_invisible_joint_requires_null_coordinates(self) -> None:
        record = {
            "schema_version": "1.0.0",
            "video_id": "video",
            "source_frame_index": 1,
            "timestamp_ms": 40,
            "primary_player_id": 1,
            "annotator_id": "coach-1",
            "view_group": "side",
            "joints": {
                "left_ankle": {
                    "visible": False,
                    "x_normalized": 0.0,
                    "y_normalized": 0.0,
                }
            },
        }
        with self.assertRaisesRegex(ValueError, "never zero-fill"):
            validate_keypoint_annotation(record)

    def test_partial_manual_truth_never_reuses_unannotated_model_points(self) -> None:
        model = _sequence(0.44)
        annotation = {
            "schema_version": "1.0.0",
            "video_id": "video",
            "source_frame_index": 1,
            "timestamp_ms": 40,
            "primary_player_id": 1,
            "annotator_id": "adjudicated-truth",
            "view_group": "side",
            "joints": {
                "left_knee": {
                    "visible": True,
                    "x_normalized": 0.51,
                    "y_normalized": 0.73,
                }
            },
        }
        truth = apply_keypoint_corrections(model, [annotation])
        self.assertTrue(np.isnan(truth.keypoints_xy["left_knee"][0]).all())
        self.assertTrue(np.isnan(truth.keypoints_xy["right_knee"]).all())
        np.testing.assert_allclose(truth.keypoints_xy["left_knee"][1], [0.51, 0.73])

    def test_duplicate_manual_truth_requires_adjudication(self) -> None:
        model = _sequence(0.44)
        annotation = {
            "schema_version": "1.0.0",
            "video_id": "video",
            "source_frame_index": 1,
            "timestamp_ms": 40,
            "primary_player_id": 1,
            "annotator_id": "coach-1",
            "view_group": "side",
            "joints": {
                "left_knee": {
                    "visible": True,
                    "x_normalized": 0.51,
                    "y_normalized": 0.73,
                }
            },
        }
        with self.assertRaisesRegex(ValueError, "adjudicate multiple annotators"):
            apply_keypoint_corrections(model, [annotation, {**annotation, "annotator_id": "coach-2"}])

    def test_sparse_truth_is_reported_as_insufficient_not_evaluated(self) -> None:
        model = _sequence(0.44)
        annotation = {
            "schema_version": "1.0.0",
            "video_id": "video",
            "source_frame_index": 1,
            "timestamp_ms": 40,
            "primary_player_id": 1,
            "annotator_id": "adjudicated-truth",
            "view_group": "side",
            "joints": {
                "left_knee": {
                    "visible": True,
                    "x_normalized": 0.51,
                    "y_normalized": 0.73,
                }
            },
        }
        truth_sequence = apply_keypoint_corrections(model, [annotation])
        prediction = {
            "event_id": "pred",
            "video_id": "video",
            "event_code": "FS09",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
        }
        truth_event = {
            "event_id": "truth",
            "video_id": "video",
            "event_code": "FS09",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 470,
            "view_group": "side",
        }
        result = evaluate_feature_errors(
            model,
            truth_sequence,
            [prediction],
            [truth_event],
            {"FS09": ["left_knee_flexion_deg"]},
        )
        self.assertEqual(result["status"], "insufficient_keypoint_ground_truth_coverage")
        self.assertEqual(result["ground_truth_coverage"]["valid_feature_event_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
