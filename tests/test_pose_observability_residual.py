from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from rallymate_evaluation.pose_observability_residual import (
    build_pose_observability_residual_audit,
    validate_pose_observability_residual_audit,
    validate_pose_observability_residual_audit_sources,
    operational_transitions_with_preserved_source_gate,
)
from scripts.run_residual_high_resolution_pose_experiment import (
    validate_residual_high_resolution_experiment_report,
)


ROOT = Path(__file__).resolve().parents[1]
ROUTER_ROOT = ROOT / "reports" / "measurement-recovery-m68" / "required-joint-superset"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class PoseObservabilityResidualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_pose_observability_residual_audit(
            registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
            router_report_paths=[ROUTER_ROOT / video_id / "report.json" for video_id in VIDEO_IDS],
        )

    def test_real_m68_residual_is_exact_and_source_replayable(self) -> None:
        report = self.report
        self.assertEqual(report["counts"]["feature_vector_complete"], 2329)
        self.assertEqual(report["counts"]["feature_vector_incomplete"], 37)
        self.assertEqual(report["counts"]["operational_measured"], 2320)
        self.assertEqual(report["counts"]["operational_unavailable"], 46)
        self.assertEqual(report["counts"]["measurement_hard_fail"], 18)
        self.assertEqual(report["counts"]["non_hard_fail_feature_incomplete"], 28)
        self.assertEqual(report["counts"]["residual_event_count"], 13)
        self.assertEqual(report["counts"]["invalid_feature_occurrence_count"], 73)
        self.assertEqual(report["counts"]["candidate_frame_count"], 258)
        self.assertEqual(
            report["by_reason"],
            {
                "bilateral_rise_proxy_not_observable": 3,
                "required_keypoints_unavailable": 5,
                "required_phase_proxy_unavailable": 2,
                "valid_fraction_below_quality_gate": 63,
            },
        )
        validate_pose_observability_residual_audit_sources(report)

    def test_tampered_counts_or_safety_fail_closed(self) -> None:
        bad = copy.deepcopy(self.report)
        bad["counts"]["non_hard_fail_feature_incomplete"] -= 1
        with self.assertRaises(ValueError):
            validate_pose_observability_residual_audit(bad)

    def test_second_stage_transition_preserves_source_measurement_gate(self) -> None:
        comparison = {
            "events": [
                {
                    "boundary": {"event_id": "event-1"},
                    "indicators": [
                        {
                            "indicator_id": "FS01-M02",
                            "models": {
                                "current": {"measurement_status": "unavailable"},
                                "candidate": {"measurement_status": "measured"},
                            },
                        },
                        {
                            "indicator_id": "FS01-M03",
                            "models": {
                                "current": {"measurement_status": "unavailable"},
                                "candidate": {"measurement_status": "measured"},
                            },
                        },
                    ],
                }
            ]
        }
        rows = [
            {
                "event_id": "event-1",
                "indicator_id": "FS01-M02",
                "quality_gate": {"status": "pass", "measurement_allowed": True},
            },
            {
                "event_id": "event-1",
                "indicator_id": "FS01-M03",
                "quality_gate": {"status": "hard_fail", "measurement_allowed": False},
            },
        ]
        impact = operational_transitions_with_preserved_source_gate(
            comparison, "current", "candidate", rows
        )
        self.assertEqual(impact["recovered_indicator_instance_count"], 1)
        self.assertEqual(
            impact["status_transitions"],
            {"unavailable_to_measured": 1, "unavailable_to_unavailable": 1},
        )

    def test_high_resolution_report_cannot_hide_regression_or_enable_production(self) -> None:
        report = {
            "schema_version": "1.0.0",
            "experiment_version": "residual-high-resolution-pose-experiment-v1.0.0",
            "status": "experimental_regression_free_high_resolution_candidate_not_production",
            "inference": {"target_frame_count": 2, "pose_output_produced": 2, "pose_output_missing": 0},
            "router_audit": {
                "selected_frame_count": 1,
                "rejected_frame_count": 1,
                "selection_uses_feature_values": False,
                "selection_uses_event_outcomes": False,
                "selection_uses_grades_or_thresholds": False,
                "required_joint_validity_regression_allowed": False,
            },
            "feature_vector_impact": {"regressed_indicator_instance_count": 0},
            "operational_measurement_impact": {"regressed_indicator_instance_count": 0},
            "decision": {"production_default_changed": False, "candidate_promoted": False},
            "safety": {
                "accuracy_claim": False,
                "ground_truth_provided": False,
                "production_enabled": False,
                "automatic_profile_fallback_enabled": False,
                "feature_gate_modified": False,
                "measurement_gate_modified": False,
                "event_boundaries_modified": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
            },
        }
        validate_residual_high_resolution_experiment_report(report)
        bad = copy.deepcopy(report)
        bad["operational_measurement_impact"]["regressed_indicator_instance_count"] = 1
        with self.assertRaises(ValueError):
            validate_residual_high_resolution_experiment_report(bad)
        bad = copy.deepcopy(report)
        bad["safety"]["production_enabled"] = True
        with self.assertRaises(ValueError):
            validate_residual_high_resolution_experiment_report(bad)
        bad = copy.deepcopy(self.report)
        bad["safety"]["production_enabled"] = True
        with self.assertRaises(ValueError):
            validate_pose_observability_residual_audit(bad)


if __name__ == "__main__":
    unittest.main()
