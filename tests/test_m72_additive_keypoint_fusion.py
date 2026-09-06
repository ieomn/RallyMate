from __future__ import annotations

import copy
import unittest

from scripts.run_m72_additive_keypoint_fusion import (
    REPORT_VERSION,
    validate_additive_keypoint_fusion_report,
)


def _report() -> dict:
    impact = {
        "recovered_indicator_instance_count": 1,
        "regressed_indicator_instance_count": 0,
    }
    return {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "status": "experimental_additive_keypoint_fusion_regression_free_requires_truth",
        "fusion_audit": {
            "target_frame_count": 2,
            "changed_frame_count": 1,
            "unchanged_target_frame_count": 1,
            "baseline_valid_coordinates_overwritten": 0,
            "non_target_frames_preserved": True,
            "non_pose_frame_data_preserved": True,
            "non_selected_poses_preserved": True,
            "same_topology_required": True,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
        },
        "comparison_to_m68": {
            "feature_vector": copy.deepcopy(impact),
            "operational_measurement": copy.deepcopy(impact),
        },
        "m71_preservation": {
            "lost_m71_feature_recovery_count": 0,
            "lost_m71_operational_recovery_count": 0,
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
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }


class M72AdditiveKeypointFusionTests(unittest.TestCase):
    def test_safe_report_validates(self) -> None:
        validate_additive_keypoint_fusion_report(_report())

    def test_overwrite_regression_or_accuracy_claim_fails_closed(self) -> None:
        bad = _report()
        bad["fusion_audit"]["baseline_valid_coordinates_overwritten"] = 1
        with self.assertRaises(ValueError):
            validate_additive_keypoint_fusion_report(bad)
        bad = _report()
        bad["m71_preservation"]["lost_m71_operational_recovery_count"] = 1
        with self.assertRaises(ValueError):
            validate_additive_keypoint_fusion_report(bad)
        bad = _report()
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_additive_keypoint_fusion_report(bad)


if __name__ == "__main__":
    unittest.main()
