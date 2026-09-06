from __future__ import annotations

import copy
import unittest

from scripts.run_residual_pose_profile_ablation import (
    EXPERIMENT_VERSION,
    validate_pose_profile_ablation_report,
)


def _impact() -> dict:
    return {
        "status_transitions": {"measured_to_measured": 2},
        "recovered_indicator_instance_count": 0,
        "regressed_indicator_instance_count": 0,
        "recovered_indicator_instances": [],
        "regressed_indicator_instances": [],
    }


def _router(*, multi: bool = False) -> dict:
    result = {
        "target_frame_count": 2,
        "selected_frame_count": 1,
        "rejected_frame_count": 1,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
        "required_joint_validity_regression_allowed": False,
    }
    if multi:
        result["equal_validity_different_pose_keeps_baseline"] = True
    return result


def _report() -> dict:
    pair = {"feature_vector": _impact(), "operational_measurement": _impact()}
    return {
        "schema_version": "1.0.0",
        "experiment_version": EXPERIMENT_VERSION,
        "generated_at": "2026-08-22T00:00:00+00:00",
        "status": "experimental_multicandidate_regression_free_not_production",
        "video_id": "video",
        "sources": {},
        "artifacts": {},
        "settings": {},
        "crop_context_replay": {
            "target_frame_count": 2,
            "source_frame_count_by_policy": {"baseline": 2},
            "classification_uses_feature_values": False,
            "classification_uses_event_outcomes": False,
            "classification_uses_grades_or_thresholds": False,
        },
        "inference": {
            "target_frame_count": 2,
            "pose_output_produced": 2,
            "pose_output_missing": 0,
        },
        "router_audits": {"profile_only": _router(), "multicandidate": _router(multi=True)},
        "impacts": {"profile_only": copy.deepcopy(pair), "uniform_context_combined": copy.deepcopy(pair), "multicandidate": copy.deepcopy(pair)},
        "counts": {},
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
            "crop_context_or_pose_profile_accuracy_claimed": False,
        },
    }


class PoseProfileAblationTests(unittest.TestCase):
    def test_safe_regression_free_ablation_validates(self) -> None:
        validate_pose_profile_ablation_report(_report())

    def test_hidden_regression_or_accuracy_claim_fails_closed(self) -> None:
        report = _report()
        report["impacts"]["multicandidate"]["operational_measurement"]["regressed_indicator_instance_count"] = 1
        with self.assertRaisesRegex(ValueError, "status"):
            validate_pose_profile_ablation_report(report)
        report = _report()
        report["safety"]["crop_context_or_pose_profile_accuracy_claimed"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_pose_profile_ablation_report(report)


if __name__ == "__main__":
    unittest.main()
