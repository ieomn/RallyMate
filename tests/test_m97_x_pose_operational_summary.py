from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from rallymate_evaluation.pose_x_operational import (
    PoseXOperationalError,
    build_multivideo_summary,
    load_x_operational_protocol,
    validate_multivideo_summary,
)


ROOT = Path(__file__).resolve().parents[1]
M97 = ROOT / "reports" / "measurement-recovery-m97"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class M97XOperationalSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verified = load_x_operational_protocol(ROOT)
        cls.report_paths = [
            M97 / "rtmpose-x-384-context-extension-v1" / video_id / "report.json"
            for video_id in VIDEO_IDS
        ]
        cls.report = build_multivideo_summary(
            verified_protocol=cls.verified,
            report_paths=cls.report_paths,
        )

    def test_real_258_frame_projection_and_reference_differences_are_exact(self) -> None:
        report = self.report
        self.assertEqual(report["scope"]["target_frame_count"], 258)
        self.assertEqual(report["inference"]["pose_output_produced"], 254)
        self.assertEqual(report["inference"]["pose_output_missing"], 4)
        # Three targets have no uniquely selected source detection; the remaining
        # 255 frames are timed inference attempts (one returns no pose).
        self.assertEqual(report["inference"]["latency_ms"]["count"], 255)

        projection = report["X_projection"]
        self.assertEqual(projection["selected_frame_count"], 56)
        self.assertEqual(projection["feature_recovered_from_m68"], 13)
        self.assertEqual(projection["feature_regressed_from_m68"], 0)
        self.assertEqual(projection["feature_vector_complete"], 2342)
        self.assertEqual(projection["operational_recovered_from_m68"], 11)
        self.assertEqual(projection["operational_regressed_from_m68"], 0)
        self.assertEqual(projection["operational_measured"], 2331)

        m70_feature = report["comparison_to_M70"]["feature_vector"]
        m70_operational = report["comparison_to_M70"]["operational_measurement"]
        self.assertEqual(m70_feature["additional_recovery_count"], 4)
        self.assertEqual(m70_operational["additional_recovery_count"], 4)
        self.assertEqual(m70_feature["lost_reference_recovery_count"], 0)
        self.assertEqual(m70_operational["lost_reference_recovery_count"], 0)

        m71_feature = report["comparison_to_M71"]["feature_vector"]
        m71_operational = report["comparison_to_M71"]["operational_measurement"]
        self.assertEqual(m71_feature["additional_recovery_count"], 1)
        self.assertEqual(m71_operational["additional_recovery_count"], 1)
        self.assertEqual(m71_feature["lost_reference_recovery_count"], 3)
        self.assertEqual(m71_operational["lost_reference_recovery_count"], 3)
        self.assertEqual(
            report["status"],
            "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery",
        )
        self.assertTrue(report["decision"]["M68_regression_free"])
        self.assertTrue(report["decision"]["M70_recovered_sets_preserved"])
        self.assertFalse(report["decision"]["M71_recovered_sets_preserved"])

    def test_saved_summary_is_valid_and_reconstructs_the_same_metrics(self) -> None:
        saved = json.loads((M97 / "summary" / "report.json").read_text(encoding="utf-8"))
        validate_multivideo_summary(saved)
        for field in (
            "scope",
            "candidate",
            "comparability",
            "inference",
            "baseline_M68",
            "baseline_M70",
            "baseline_M71",
            "X_projection",
            "comparison_to_M70",
            "comparison_to_M71",
            "by_video",
            "decision",
            "field_semantics",
            "safety",
            "not_completed",
        ):
            self.assertEqual(saved[field], self.report[field])

    def test_summary_fails_closed_on_hidden_m71_loss_or_accuracy_claim(self) -> None:
        bad = copy.deepcopy(self.report)
        bad["decision"]["M71_recovered_sets_preserved"] = True
        with self.assertRaises(PoseXOperationalError):
            validate_multivideo_summary(bad)

        bad = copy.deepcopy(self.report)
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(PoseXOperationalError):
            validate_multivideo_summary(bad)


if __name__ == "__main__":
    unittest.main()
