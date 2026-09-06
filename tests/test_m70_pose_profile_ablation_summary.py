from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts.build_m70_anchored_pose_extension import (
    validate_anchored_pose_extension_report,
)
from scripts.build_m70_pose_profile_ablation_summary import build_summary, validate_summary


ROOT = Path(__file__).resolve().parents[1]
M69 = ROOT / "reports" / "measurement-recovery-m69"
M70 = ROOT / "reports" / "measurement-recovery-m70"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class M70PoseProfileAblationSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_summary(
            residual_audit_path=M69 / "residual-audit" / "report.json",
            ablation_report_paths=[
                M70 / "pose-profile-context-ablation-v1" / video_id / "report.json"
                for video_id in VIDEO_IDS
            ],
            anchored_report_paths=[
                M70 / "m69-anchored-extension-v1" / video_id / "report.json"
                for video_id in VIDEO_IDS
            ],
        )

    def test_real_three_video_ablation_and_anchored_projection_are_exact(self) -> None:
        report = self.report
        self.assertEqual(report["context_replay"]["target_frame_count"], 258)
        self.assertEqual(report["context_replay"]["pose_output_produced"], 254)
        self.assertEqual(
            report["context_replay"]["source_frame_count_by_policy"],
            {"baseline": 179, "roi_margin_candidate": 28, "small_roi_min8": 51},
        )
        strategies = report["strategies"]
        self.assertEqual(strategies["context_matched_profile_only"]["operational_recovered"], 2)
        self.assertEqual(strategies["uniform_context_m69"]["operational_recovered"], 6)
        self.assertEqual(strategies["naive_multicandidate"]["operational_recovered"], 6)
        self.assertEqual(
            strategies["naive_multicandidate"]["lost_uniform_operational_recovery_count"],
            1,
        )
        self.assertFalse(strategies["naive_multicandidate"]["preserves_uniform_recovered_sets"])
        anchored = strategies["m69_anchored_extension"]
        self.assertEqual(anchored["extension_selected_frame_count"], 15)
        self.assertEqual(anchored["feature_vector_complete"], 2338)
        self.assertEqual(anchored["operational_measured"], 2327)
        self.assertEqual(anchored["additional_operational_recovery_over_m69"], 1)
        self.assertEqual(anchored["lost_m69_operational_recovery_count"], 0)
        self.assertEqual(anchored["non_hard_fail_feature_incomplete"], 21)

    def test_anchored_validator_and_summary_fail_closed(self) -> None:
        anchored_path = (
            M70
            / "m69-anchored-extension-v1"
            / VIDEO_IDS[0]
            / "report.json"
        )
        import json

        anchored = json.loads(anchored_path.read_text(encoding="utf-8"))
        validate_anchored_pose_extension_report(anchored)
        bad = copy.deepcopy(anchored)
        bad["m69_preservation"]["lost_m69_operational_recovery_count"] = 1
        with self.assertRaises(ValueError):
            validate_anchored_pose_extension_report(bad)

        bad_summary = copy.deepcopy(self.report)
        bad_summary["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_summary(bad_summary)


if __name__ == "__main__":
    unittest.main()
