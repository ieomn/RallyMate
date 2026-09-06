from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "audit_scoring_blockers.py"
    spec = importlib.util.spec_from_file_location("audit_scoring_blockers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(*, event: str, indicator: str, status: str, valid: bool, flags: list[str], reasons: list[str], hard: bool = False) -> dict:
    return {
        "event_id": event,
        "indicator_id": indicator,
        "status": status,
        "grade": None,
        "threshold_version": None,
        "feature": {"items": [{"feature_name": "f", "valid": valid}]},
        "reason_codes": reasons,
        "quality_gate": {
            "hard_fail": hard,
            "scoring_allowed": not hard and not flags,
            "scoring_block_flags": flags,
        },
    }


class ScoringBlockerAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_decomposition_and_priority_stop_at_calibration_required(self) -> None:
        rows = [
            _row(event="e1", indicator="I1", status="calibration_required", valid=True, flags=[], reasons=["coach_calibration_missing"]),
            _row(event="e2", indicator="I1", status="unavailable", valid=True, flags=["keypoint_jump_candidates_present"], reasons=["event_scoring_quality_blocked", "keypoint_jump_diagnostic_unverified"]),
            _row(event="e3", indicator="I1", status="unavailable", valid=False, flags=[], reasons=["required_feature_unavailable"]),
            _row(event="e4", indicator="I1", status="unavailable", valid=False, flags=["left_right_swap_candidates_present"], reasons=["required_feature_unavailable", "left_right_assignment_unverified"]),
            _row(event="e5", indicator="I1", status="unavailable", valid=False, flags=[], reasons=["event_quality_hard_fail"], hard=True),
        ]
        summary = {
            "video_id": "video",
            "loop_version": "loop",
            "score_status_counts": {"calibration_required": 1, "unavailable": 4},
            "model_versions": {"feasibility_registry": "registry", "quality_policy": "quality"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scores = root / "scores.jsonl"
            summary_path = root / "summary.json"
            scores.write_text("{}\n", encoding="utf-8")
            summary_path.write_text("{}\n", encoding="utf-8")
            report = self.module.audit_scoring_blockers(
                scores_path=scores,
                summary_path=summary_path,
                score_rows=rows,
                summary=summary,
            )
        self.module.validate_scoring_blocker_audit(report)
        self.assertEqual(1, report["counts"]["complete_feature_score_only_recoverable_to_calibration_required_if_all_truth_clears_count"])
        self.assertEqual("keypoint_jump_candidates_present", report["human_review_priority"][0]["flag"])
        self.assertTrue(report["assertions"]["recoverable_means_calibration_required_not_scored"])
        self.assertEqual(
            report["reason_code_counts"]["keypoint_jump_diagnostic_unverified"],
            1,
        )
        self.assertNotIn(
            "event_identity_continuity_unverified",
            report["reason_code_counts"],
        )
        jump_metric = next(
            item
            for item in report["typed_reason_metrics"]
            if item["reason_code"] == "keypoint_jump_diagnostic_unverified"
        )
        self.assertEqual(jump_metric["supported_indicator_record_count"], 1)

    def test_typed_reason_misattribution_is_rejected(self) -> None:
        rows = [
            _row(event="e1", indicator="I1", status="unavailable", valid=True, flags=["tactical_target_direction_not_observed"], reasons=["event_identity_continuity_unverified"])
        ]
        summary = {
            "video_id": "video",
            "loop_version": "loop",
            "score_status_counts": {"unavailable": 1},
            "model_versions": {"feasibility_registry": "registry", "quality_policy": "quality"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scores = root / "scores.jsonl"; scores.write_text("{}\n", encoding="utf-8")
            summary_path = root / "summary.json"; summary_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "typed score blocker reasons"):
                self.module.audit_scoring_blockers(scores_path=scores, summary_path=summary_path, score_rows=rows, summary=summary)

    def test_extra_identity_reason_without_identity_flag_is_rejected(self) -> None:
        rows = [
            _row(
                event="e1",
                indicator="I1",
                status="unavailable",
                valid=True,
                flags=["tactical_target_direction_not_observed"],
                reasons=[
                    "event_scoring_quality_blocked",
                    "tactical_target_direction_required",
                    "event_identity_continuity_unverified",
                ],
            )
        ]
        summary = {
            "video_id": "video",
            "loop_version": "loop",
            "score_status_counts": {"unavailable": 1},
            "model_versions": {
                "feasibility_registry": "registry",
                "quality_policy": "quality",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scores = root / "scores.jsonl"
            summary_path = root / "summary.json"
            scores.write_text("{}\n", encoding="utf-8")
            summary_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "typed score blocker reasons"):
                self.module.audit_scoring_blockers(
                    scores_path=scores,
                    summary_path=summary_path,
                    score_rows=rows,
                    summary=summary,
                )


if __name__ == "__main__":
    unittest.main()
