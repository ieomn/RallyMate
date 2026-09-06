from __future__ import annotations

import json
import unittest
from pathlib import Path

from rallymate_evaluation.pose_observability_router import (
    EXPERIMENT_VERSION,
    pose_required_joints_from_registry,
    select_required_joint_dominating_candidates,
    select_required_joint_superset_frames,
    validate_pose_observability_router_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _point(name: str, confidence: float = 0.9) -> dict:
    return {
        "name": name,
        "x_normalized": 0.4,
        "y_normalized": 0.5,
        "confidence": confidence,
    }


def _row(index: int, joints: list[str], *, note: str = "same") -> dict:
    return {
        "frame": {"processed_index": index, "timestamp_ms": index * 40},
        "detections": [{"track_id": 7}],
        "note": note,
        "poses": [
            {
                "person_track_id": 7,
                "keypoint_format": "halpe26",
                "keypoints": [_point(name) for name in joints],
            },
            {
                "person_track_id": 9,
                "keypoint_format": "halpe26",
                "keypoints": [_point("left_hip")],
            },
        ],
    }


def _shift_selected_pose(row: dict, delta: float) -> dict:
    shifted = json.loads(json.dumps(row))
    for point in shifted["poses"][0]["keypoints"]:
        point["x_normalized"] += delta
    return shifted


class PoseObservabilityRouterTests(unittest.TestCase):
    def test_real_registry_resolves_exact_pose_joint_scope(self) -> None:
        registry = json.loads(
            (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            pose_required_joints_from_registry(registry),
            (
                "left_ankle",
                "left_hip",
                "left_knee",
                "left_shoulder",
                "right_ankle",
                "right_hip",
                "right_knee",
                "right_shoulder",
            ),
        )

    def test_only_strict_valid_joint_superset_is_selected(self) -> None:
        baseline = [
            _row(0, ["left_hip"]),
            _row(1, ["left_hip", "right_hip"]),
            _row(2, ["left_hip"]),
        ]
        candidate = [
            _row(0, ["left_hip", "right_hip"]),
            _row(1, ["left_hip", "right_hip"]),
            _row(2, ["right_hip", "right_ankle"]),
        ]
        selected, audit = select_required_joint_superset_frames(
            baseline_rows=baseline,
            candidate_rows=candidate,
            primary_timeline=[
                {"processed_index": index, "source_track_id": 7}
                for index in range(3)
            ],
            target_processed_indexes={0, 1, 2},
            required_joints=("left_hip", "right_hip", "right_ankle"),
        )
        self.assertEqual(selected, {0})
        self.assertEqual(audit["selected_frame_count"], 1)
        self.assertEqual(
            audit["decision_reason_counts"],
            {
                "no_additional_required_joint": 1,
                "required_joint_validity_regression": 1,
                "selected_strict_validity_superset": 1,
            },
        )
        self.assertFalse(audit["selection_uses_feature_values"])
        self.assertFalse(audit["required_joint_validity_regression_allowed"])

    def test_candidate_cannot_change_non_pose_data_or_other_people(self) -> None:
        baseline = [_row(0, ["left_hip"])]
        candidate = [_row(0, ["left_hip", "right_hip"], note="changed")]
        with self.assertRaisesRegex(ValueError, "non-Pose"):
            select_required_joint_superset_frames(
                baseline_rows=baseline,
                candidate_rows=candidate,
                primary_timeline=[{"processed_index": 0, "source_track_id": 7}],
                target_processed_indexes={0},
                required_joints=("left_hip", "right_hip"),
            )

    def test_multicandidate_router_selects_unique_largest_valid_superset(self) -> None:
        baseline = [_row(0, ["left_hip"]), _row(1, ["left_hip"])]
        candidate_a = [
            _row(0, ["left_hip", "left_knee"]),
            _row(1, ["left_hip", "left_knee"]),
        ]
        candidate_b = [
            _row(0, ["left_hip", "left_knee", "right_hip"]),
            _shift_selected_pose(
                _row(1, ["left_hip", "left_knee"]), 0.05
            ),
        ]
        selected, audit = select_required_joint_dominating_candidates(
            baseline_rows=baseline,
            candidate_rows_by_name={"a": candidate_a, "b": candidate_b},
            primary_timeline=[
                {"processed_index": index, "source_track_id": 7}
                for index in range(2)
            ],
            target_processed_indexes={0, 1},
            required_joints=("left_hip", "left_knee", "right_hip"),
        )
        self.assertEqual(selected, {0: "b"})
        self.assertEqual(audit["selected_frame_count"], 1)
        self.assertEqual(audit["rejected_frame_count"], 1)
        self.assertEqual(
            audit["decision_reason_counts"][
                "ambiguous_equal_valid_joint_count"
            ],
            1,
        )
        self.assertFalse(audit["selection_uses_feature_values"])
        self.assertTrue(audit["equal_validity_different_pose_keeps_baseline"])

    def test_unknown_registry_feature_fails_closed(self) -> None:
        registry = {
            "indicators": [
                {"indicator_id": "FS01-M02", "required_features": ["unknown"]}
            ]
        }
        with self.assertRaisesRegex(ValueError, "unknown feature"):
            pose_required_joints_from_registry(registry)

    def test_report_cannot_hide_regression_or_enable_production(self) -> None:
        report = {
            "experiment_version": EXPERIMENT_VERSION,
            "status": "experimental_regression_free_observability_candidate_not_production",
            "router_audit": {
                "router_version": "required-joint-validity-superset-v1.0.0",
                "target_frame_count": 2,
                "selected_frame_count": 1,
                "rejected_frame_count": 1,
                "selection_uses_feature_values": False,
                "selection_uses_event_outcomes": False,
                "selection_uses_grades_or_thresholds": False,
                "equal_validity_keeps_baseline": True,
                "required_joint_validity_regression_allowed": False,
            },
            "feature_vector_impact": {"regressed_indicator_instance_count": 0},
            "operational_measurement_impact": {
                "regressed_indicator_instance_count": 0
            },
            "decision": {
                "production_default_changed": False,
                "candidate_promoted": False,
            },
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_enabled": False,
                "automatic_fallback_enabled": False,
                "measurement_gate_modified": False,
                "scoring_gate_modified": False,
                "event_boundaries_modified": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
            },
        }
        validate_pose_observability_router_report(report)
        report["operational_measurement_impact"][
            "regressed_indicator_instance_count"
        ] = 1
        with self.assertRaisesRegex(ValueError, "status"):
            validate_pose_observability_router_report(report)
        report["status"] = "experimental_router_rejected_due_to_regression"
        report["decision"]["production_default_changed"] = True
        with self.assertRaisesRegex(ValueError, "production"):
            validate_pose_observability_router_report(report)


if __name__ == "__main__":
    unittest.main()
