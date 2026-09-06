from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts.build_m72_additive_keypoint_fusion_summary import (
    build_summary,
    validate_summary,
)


ROOT = Path(__file__).resolve().parents[1]
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class M72AdditiveKeypointFusionSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_summary(
            m71_summary_path=ROOT / "reports" / "measurement-recovery-m71" / "summary" / "report.json",
            residual_audit_path=ROOT / "reports" / "measurement-recovery-m69" / "residual-audit" / "report.json",
            report_paths=[
                ROOT / "reports" / "measurement-recovery-m72" / "additive-keypoint-fusion-v1" / video_id / "report.json"
                for video_id in VIDEO_IDS
            ],
        )

    def test_real_projection_and_remaining_residual_are_exact(self) -> None:
        projection = self.report["m72_projection"]
        self.assertEqual(46, projection["changed_frame_count"])
        self.assertEqual(79, projection["added_valid_joint_observation_count"])
        self.assertEqual(2346, projection["feature_vector_complete"])
        self.assertEqual(2335, projection["operational_measured"])
        self.assertEqual(2, projection["additional_operational_recovery_over_m71"])
        self.assertEqual(13, projection["non_hard_fail_operational_residual"])
        self.assertEqual(
            {
                "event_observation_coverage_below_feature_contract": 9,
                "required_pose_phase_proxy_not_observed": 2,
                "video_start_boundary_censored_observation": 2,
            },
            self.report["remaining_residual"]["classification_counts"],
        )

    def test_summary_rejects_overwrite_claim_or_hidden_loss(self) -> None:
        bad = copy.deepcopy(self.report)
        bad["fusion_contract"]["baseline_valid_coordinates_overwritten"] = 1
        with self.assertRaises(ValueError):
            validate_summary(bad)
        bad = copy.deepcopy(self.report)
        bad["m72_projection"]["lost_m71_operational_recovery_count"] = 1
        with self.assertRaises(ValueError):
            validate_summary(bad)
        bad = copy.deepcopy(self.report)
        bad["safety"]["accuracy_claim"] = True
        with self.assertRaises(ValueError):
            validate_summary(bad)


if __name__ == "__main__":
    unittest.main()
