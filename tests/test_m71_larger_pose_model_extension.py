from __future__ import annotations

import copy
import unittest

from scripts.run_m71_larger_pose_model_extension import (
    REPORT_VERSION,
    validate_larger_pose_model_extension_report,
)


def _report() -> dict:
    impact = {
        "recovered_indicator_instance_count": 1,
        "regressed_indicator_instance_count": 0,
    }
    return {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "status": "experimental_larger_pose_model_extension_regression_free_requires_truth",
        "inference": {
            "target_frame_count": 2,
            "pose_output_produced": 2,
            "pose_output_missing": 0,
        },
        "router_audit": {
            "target_frame_count": 2,
            "selected_frame_count": 1,
            "rejected_frame_count": 1,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
            "required_joint_validity_regression_allowed": False,
        },
        "comparison_to_m68": {
            "feature_vector": copy.deepcopy(impact),
            "operational_measurement": copy.deepcopy(impact),
        },
        "m70_preservation": {
            "lost_m70_feature_recovery_count": 0,
            "lost_m70_operational_recovery_count": 0,
        },
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
        },
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


class M71LargerPoseModelExtensionTests(unittest.TestCase):
    def test_safe_report_validates(self) -> None:
        validate_larger_pose_model_extension_report(_report())

    def test_hidden_loss_or_accuracy_claim_fails_closed(self) -> None:
        bad = _report()
        bad["m70_preservation"]["lost_m70_operational_recovery_count"] = 1
        with self.assertRaises(ValueError):
            validate_larger_pose_model_extension_report(bad)
        bad = _report()
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_larger_pose_model_extension_report(bad)


if __name__ == "__main__":
    unittest.main()
