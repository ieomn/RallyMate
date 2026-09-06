from __future__ import annotations

import copy
import unittest

from scripts.run_m73_wholebody_mapped_keypoint_fusion import (
    REPORT_VERSION,
    validate_wholebody_mapped_keypoint_fusion_report,
)


def _report() -> dict:
    return {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "status": "experimental_wholebody_mapped_fusion_regression_free_requires_truth",
        "inference": {
            "target_frame_count": 2,
            "pose_output_produced": 2,
            "pose_output_missing": 0,
        },
        "fusion_audit": {
            "baseline_keypoint_format": "halpe26",
            "candidate_keypoint_format": "coco_wholebody133",
            "target_to_candidate_joint_names": {
                "left_ankle": "left_ankle",
                "left_big_toe": "left_big_toe",
            },
            "mapping_is_one_to_one": True,
            "changed_frame_count": 1,
            "unchanged_target_frame_count": 1,
            "baseline_valid_coordinates_overwritten": 0,
            "baseline_topology_preserved": True,
            "non_target_frames_preserved": True,
            "non_pose_frame_data_preserved": True,
            "non_selected_poses_preserved": True,
            "explicit_topology_map_required": True,
            "selection_uses_feature_values": False,
            "selection_uses_event_outcomes": False,
            "selection_uses_grades_or_thresholds": False,
        },
        "comparison_to_m68": {
            "feature_vector": {"regressed_indicator_instance_count": 0},
            "operational_measurement": {"regressed_indicator_instance_count": 0},
        },
        "m72_preservation": {
            "lost_m72_feature_recovery_count": 0,
            "lost_m72_operational_recovery_count": 0,
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


class M73WholeBodyMappedFusionTests(unittest.TestCase):
    def test_safe_report_validates(self) -> None:
        validate_wholebody_mapped_keypoint_fusion_report(_report())

    def test_hidden_regression_mapping_or_accuracy_claim_fails_closed(self) -> None:
        report = _report()
        report["m72_preservation"]["lost_m72_operational_recovery_count"] = 1
        with self.assertRaisesRegex(ValueError, "hides a regression"):
            validate_wholebody_mapped_keypoint_fusion_report(report)
        report = _report()
        report["fusion_audit"]["target_to_candidate_joint_names"] = {
            "left_ankle": "right_ankle"
        }
        with self.assertRaisesRegex(ValueError, "same-name"):
            validate_wholebody_mapped_keypoint_fusion_report(report)
        report = copy.deepcopy(_report())
        report["safety"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "accuracy_claim"):
            validate_wholebody_mapped_keypoint_fusion_report(report)


if __name__ == "__main__":
    unittest.main()
