from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "build_pose_profile_routing_audit.py"
    spec = importlib.util.spec_from_file_location("build_pose_profile_routing_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(model_sha: str, measured: int) -> dict:
    return {
        "event_counts": {"FS01": 1},
        "score_status_counts": {"calibration_required": measured, "unavailable": 2 - measured},
        "grade_counts": {},
        "indicator_feature_validity": {
            "FS01-M02": {"valid": measured, "total": 2}
        },
        "model_versions": {
            "pose_model_sha256": model_sha,
            "native_keypoint_format": "halpe26",
            "native_keypoint_count": 26,
            "feasibility_registry": "registry-v1",
        },
        "provenance": {
            "feasibility_registry_sha256": "C" * 64,
            "video_sha256": "D" * 64,
        },
    }


def _fixed(current: str, candidate: str, current_sha: str, candidate_sha: str) -> dict:
    model = lambda sha, measured: {
        "model_sha256": sha,
        "indicator_event_pair_count": 2,
        "measured_indicator_event_count": measured,
        "unavailable_indicator_event_count": 2 - measured,
    }
    return {
        "comparison_semantics": {"accuracy_claim": False},
        "assertions": {"accuracy_claim": False, "grade_generated": False},
        "model_order": [current, candidate],
        "models": {current: model(current_sha, 2), candidate: model(candidate_sha, 1)},
        "boundary_source": {
            "event_count": 1,
            "sha256": "E" * 64,
            "truth_status": "candidate_source_not_truth",
        },
        "registry_source": {
            "indicator_count": 1,
            "registry_version": "registry-v1",
            "sha256": "C" * 64,
        },
        "events": [
            {
                "indicators": [
                    {"paired_validity": {"state": "model_a_only"}},
                    {"paired_validity": {"state": "both_valid"}},
                ]
            }
        ],
        "feature_pairs": [
            {"comparison": {"validity_state": "both_valid"}},
            {"comparison": {"validity_state": "model_a_only"}},
        ],
    }


def _event(current: str, candidate: str) -> dict:
    return {
        "semantics": {"accuracy_claim": False, "ground_truth_provided": False},
        "inputs": {"left": {"label": current}, "right": {"label": candidate}},
        "threshold_results": [
            {
                "minimum_segment_iou": 0.3,
                "left_event_count": 2,
                "right_event_count": 2,
                "matched_count": 1,
                "left_to_right_match_rate": 0.5,
                "right_to_left_match_rate": 0.5,
                "mean_segment_iou": 0.8,
                "mean_absolute_differences_ms": {
                    "start_difference_ms": 10,
                    "end_difference_ms": 20,
                    "center_difference_ms": 15,
                    "duration_difference_ms": 10,
                },
            }
        ],
    }


class PoseProfileRoutingAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_fixed_boundary_coverage_retains_primary_without_accuracy_claim(self) -> None:
        current = "current"
        candidate = "candidate"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / f"{name}.json" for name in (current, candidate, "fixed", "event")}
            for path in paths.values():
                path.write_text("{}\n", encoding="utf-8")
            report = self.module.build_pose_profile_routing_audit(
                current_primary=current,
                summary_inputs={
                    current: (paths[current], _summary("A" * 64, 2)),
                    candidate: (paths[candidate], _summary("B" * 64, 2)),
                },
                fixed_inputs=[
                    (
                        paths["fixed"],
                        _fixed(current, candidate, "A" * 64, "B" * 64),
                    )
                ],
                event_inputs={candidate: (paths["event"], _event(current, candidate))},
            )
        self.module.validate_pose_profile_routing_audit(report)
        self.assertEqual(current, report["routing_decision"]["selected_scoring_primary"])
        self.assertEqual(-1, report["comparisons"][candidate]["fixed_boundary_measurement"]["candidate_minus_current"])
        self.assertFalse(report["safety"]["accuracy_claim"])
        self.assertFalse(report["routing_decision"]["per_feature_or_per_event_model_cherry_picking_allowed"])

    def test_validator_rejects_automatic_promotion(self) -> None:
        report = {
            "schema_version": "1.0.0",
            "report_version": "pose-profile-routing-audit-v1.0.0",
            "status": "coverage_audit_complete_ground_truth_required",
            "scope": {"current_primary": "a"},
            "profiles": {"a": {}, "b": {}},
            "comparisons": {"b": {"fixed_boundary_measurement": {"indicator_event_count": 1, "current_primary_measured": 1, "candidate_measured": 1, "candidate_minus_current": 0}}},
            "routing_decision": {
                "selected_scoring_primary": "a",
                "automatic_model_promotion": True,
                "per_feature_or_per_event_model_cherry_picking_allowed": False,
            },
            "safety": {key: False for key in (
                "accuracy_claim",
                "ground_truth_provided",
                "grades_generated",
                "thresholds_generated",
                "maturity_promoted",
                "fixed_boundary_coverage_is_accuracy",
                "self_segmented_counts_used_for_routing",
            )},
        }
        with self.assertRaisesRegex(ValueError, "automatically promote"):
            self.module.validate_pose_profile_routing_audit(report)


if __name__ == "__main__":
    unittest.main()
