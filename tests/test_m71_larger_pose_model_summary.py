from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts.build_m71_larger_pose_model_summary import build_summary, validate_summary


ROOT = Path(__file__).resolve().parents[1]
M70 = ROOT / "reports" / "measurement-recovery-m70"
M71 = ROOT / "reports" / "measurement-recovery-m71"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class M71LargerPoseModelSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_summary(
            m70_summary_path=M70 / "summary" / "report.json",
            report_paths=[
                M71 / "rtmpose-l-384-context-extension-v1" / video_id / "report.json"
                for video_id in VIDEO_IDS
            ],
        )

    def test_real_three_video_projection_is_exact_and_regression_free(self) -> None:
        report = self.report
        self.assertEqual(report["scope"]["target_frame_count"], 258)
        self.assertEqual(report["inference"]["pose_output_produced"], 254)
        projection = report["m71_projection"]
        self.assertEqual(projection["selected_frame_count"], 46)
        self.assertEqual(projection["feature_recovered_from_m68"], 15)
        self.assertEqual(projection["feature_vector_complete"], 2344)
        self.assertEqual(projection["additional_feature_recovery_over_m70"], 6)
        self.assertEqual(projection["operational_recovered_from_m68"], 13)
        self.assertEqual(projection["operational_measured"], 2333)
        self.assertEqual(projection["additional_operational_recovery_over_m70"], 6)
        self.assertEqual(projection["non_hard_fail_operational_residual"], 15)
        self.assertEqual(projection["lost_m70_operational_recovery_count"], 0)

    def test_summary_fails_closed_on_claim_or_regression(self) -> None:
        bad = copy.deepcopy(self.report)
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_summary(bad)
        bad = copy.deepcopy(self.report)
        bad["m71_projection"]["lost_m70_operational_recovery_count"] = 1
        with self.assertRaises(ValueError):
            validate_summary(bad)


if __name__ == "__main__":
    unittest.main()
