from __future__ import annotations

import json
import unittest
from pathlib import Path

from rallymate_scoring.blocker_taxonomy import (
    SCORING_BLOCKER_TAXONOMY_VERSION,
    blocker_group_for_flag,
    known_typed_reason_codes,
    scoring_block_details,
    taxonomy_document,
    truth_requirement_for_flag,
    typed_reason_for_flag,
    typed_reason_supports_flag,
)


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "scoring-blocker-taxonomy.json"
COVERAGE = (
    ROOT
    / "reports"
    / "multivideo-indicator-calculation-coverage"
    / "m63-halpe26-three-video-fs02-m05-first-step-evidence-v1"
    / "coverage.json"
)


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ScoringBlockerTaxonomyTests(unittest.TestCase):
    def test_checked_in_machine_taxonomy_matches_the_python_source(self) -> None:
        payload = json.loads(TAXONOMY.read_text(encoding="utf-8"))
        self.assertEqual(taxonomy_document(), payload)
        self.assertEqual(SCORING_BLOCKER_TAXONOMY_VERSION, payload["taxonomy_version"])
        self.assertEqual(
            len(payload["exact_entries"]),
            len({item["flag"] for item in payload["exact_entries"]}),
        )
        self.assertFalse(any(payload["safety"].values()))

    def test_each_known_flag_has_one_typed_reason_truth_requirement_and_group(self) -> None:
        expected = {
            "keypoint_jump_candidates_present": (
                "keypoint_jump_diagnostic_unverified",
                "full_timeline_manual_keypoint_jump_truth",
                "pose_diagnostic",
            ),
            "left_right_swap_candidates_present": (
                "left_right_assignment_unverified",
                "full_timeline_manual_left_right_swap_truth",
                "pose_diagnostic",
            ),
            "tactical_target_direction_not_observed": (
                "tactical_target_direction_required",
                "manual_target_direction_semantics",
                "tactical_context",
            ),
            "source_track_switch_candidates_present": (
                "event_identity_continuity_unverified",
                "manual_primary_identity_continuity_truth",
                "identity_continuity",
            ),
            "phase_proxy_right_censored_peak:landing_proxy_ms": (
                "event_phase_proxy_right_censored_unverified",
                "manual_landing_phase_boundary",
                "event_phase",
            ),
        }
        for flag, values in expected.items():
            with self.subTest(flag=flag):
                reason, requirement, group = values
                self.assertEqual(reason, typed_reason_for_flag(flag))
                self.assertEqual(requirement, truth_requirement_for_flag(flag))
                self.assertEqual(group, blocker_group_for_flag(flag))
                self.assertTrue(typed_reason_supports_flag(reason, flag))

    def test_jump_target_and_identity_reasons_cannot_be_conflated(self) -> None:
        reasons, messages = scoring_block_details(
            [
                "keypoint_jump_candidates_present",
                "tactical_target_direction_not_observed",
            ]
        )
        self.assertIn("keypoint_jump_diagnostic_unverified", reasons)
        self.assertIn("tactical_target_direction_required", reasons)
        self.assertNotIn("event_identity_continuity_unverified", reasons)
        self.assertTrue(any("关键点跳变" in message for message in messages))
        self.assertTrue(any("目标方向" in message for message in messages))

    def test_phase_feedback_precedes_lead_foot_feedback_for_compatibility(self) -> None:
        reasons, messages = scoring_block_details(
            [
                "keypoint_jump_candidates_present",
                "phase_proxy_low_sample_peak:first_step_slowdown_proxy_ms",
                "lead_foot_side_proxy_ambiguous",
            ]
        )
        self.assertLess(
            reasons.index("event_phase_proxy_low_sample_unverified"),
            reasons.index("lead_foot_side_assignment_unverified"),
        )
        self.assertLess(
            next(index for index, message in enumerate(messages) if "两样本" in message),
            next(index for index, message in enumerate(messages) if "左右脚活动峰接近" in message),
        )

    def test_all_real_three_video_score_blocks_are_exactly_typed(self) -> None:
        coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
        known_reasons = set(known_typed_reason_codes())
        record_count = 0
        flag_count = 0
        for bindings in coverage["source"]["source_reports"].values():
            for row in _jsonl(Path(bindings["scores"]["path"])):
                record_count += 1
                flags = [
                    str(value)
                    for value in row["quality_gate"].get(
                        "scoring_block_flags", []
                    )
                ]
                reasons = {str(value) for value in row.get("reason_codes", [])}
                for flag in flags:
                    flag_count += 1
                    expected = typed_reason_for_flag(flag)
                    self.assertIsNotNone(expected, flag)
                    self.assertIn(expected, reasons, (row["event_id"], row["indicator_id"], flag))
                for reason in reasons.intersection(known_reasons):
                    self.assertTrue(
                        any(typed_reason_supports_flag(reason, flag) for flag in flags),
                        (row["event_id"], row["indicator_id"], reason),
                    )
        self.assertEqual(2366, record_count)
        self.assertGreater(flag_count, 0)


if __name__ == "__main__":
    unittest.main()
