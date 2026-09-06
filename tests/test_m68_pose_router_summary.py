from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_m68_pose_router_summary",
    ROOT / "scripts" / "build_m68_pose_router_summary.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class M68PoseRouterSummaryTests(unittest.TestCase):
    def test_validator_rejects_regression_or_unsafe_selection(self) -> None:
        report = {
            "report_version": MODULE.REPORT_VERSION,
            "status": "regression_free_observability_router_candidate_requires_truth",
            "scope": {"video_count": 3, "indicator_instances": 10},
            "router": {
                "candidate_margin_target_frames": 5,
                "selected_margin_frames": 2,
                "rejected_margin_frames": 3,
                "selection_uses_feature_values": False,
                "selection_uses_event_outcomes": False,
                "selection_uses_grades_or_thresholds": False,
            },
            "baseline": {
                "feature_vector_complete": 8,
                "feature_vector_incomplete": 2,
                "operational_measured": 7,
                "operational_unavailable": 3,
            },
            "routed_projection": {
                "feature_vector_complete": 9,
                "feature_vector_incomplete": 1,
                "operational_measured": 8,
                "operational_unavailable": 2,
                "feature_vector_regressed": 0,
                "operational_regressed": 0,
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
                "current_three_video_regression_free_is_not_independent_validation": True,
            },
        }
        MODULE.validate_summary(report)
        report["routed_projection"]["operational_regressed"] = 1
        with self.assertRaisesRegex(ValueError, "regression-free"):
            MODULE.validate_summary(report)
        report["routed_projection"]["operational_regressed"] = 0
        report["router"]["selection_uses_feature_values"] = True
        with self.assertRaisesRegex(ValueError, "selection"):
            MODULE.validate_summary(report)


if __name__ == "__main__":
    unittest.main()
