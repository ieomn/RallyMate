from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts.build_m69_high_resolution_summary import build_summary, validate_summary


ROOT = Path(__file__).resolve().parents[1]
M69 = ROOT / "reports" / "measurement-recovery-m69"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class M69HighResolutionSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_summary(
            residual_audit_path=M69 / "residual-audit" / "report.json",
            experiment_report_paths=[
                M69 / "high-resolution-384-margin030" / video_id / "report.json"
                for video_id in VIDEO_IDS
            ],
        )

    def test_real_three_video_projection_is_exact_and_regression_free(self) -> None:
        report = self.report
        self.assertEqual(report["inference"]["target_frame_count"], 258)
        self.assertEqual(report["inference"]["pose_output_produced"], 255)
        self.assertEqual(report["inference"]["selected_frame_count"], 40)
        self.assertEqual(report["baseline"]["feature_vector_complete"], 2329)
        self.assertEqual(report["experimental_projection"]["feature_vector_complete"], 2335)
        self.assertEqual(report["experimental_projection"]["feature_vector_regressed"], 0)
        self.assertEqual(report["experimental_projection"]["operational_measured"], 2326)
        self.assertEqual(report["experimental_projection"]["operational_regressed"], 0)
        self.assertEqual(report["experimental_projection"]["remaining_residual_indicator_instances"], 22)

    def test_summary_rejects_unsafe_claim_or_hidden_regression(self) -> None:
        bad = copy.deepcopy(self.report)
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_summary(bad)
        bad = copy.deepcopy(self.report)
        bad["experimental_projection"]["operational_regressed"] = 1
        with self.assertRaises(ValueError):
            validate_summary(bad)


if __name__ == "__main__":
    unittest.main()
