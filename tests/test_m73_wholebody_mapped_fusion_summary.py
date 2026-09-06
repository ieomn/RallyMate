from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.build_m73_wholebody_mapped_fusion_summary import (
    validate_summary,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "measurement-recovery-m73" / "summary" / "report.json"


class M73WholeBodyMappedFusionSummaryTests(unittest.TestCase):
    def test_real_projection_preserves_m72_and_records_zero_indicator_gain(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        validate_summary(report)
        projection = report["m73_projection"]
        self.assertEqual(254, projection["candidate_pose_output_count"])
        self.assertEqual(49, projection["changed_frame_count"])
        self.assertEqual(101, projection["added_valid_joint_observation_count"])
        self.assertEqual(2346, projection["feature_vector_complete"])
        self.assertEqual(2335, projection["operational_measured"])
        self.assertEqual(13, projection["non_hard_fail_operational_residual"])
        self.assertFalse(report["decision"]["additional_indicator_recovery_observed"])

    def test_summary_rejects_hidden_gain_or_accuracy_claim(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        report["m73_projection"]["additional_operational_recovery_over_m72"] = 1
        with self.assertRaisesRegex(ValueError, "observed zero gain"):
            validate_summary(report)
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        report["safety"]["accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "accuracy_claim"):
            validate_summary(report)


if __name__ == "__main__":
    unittest.main()
