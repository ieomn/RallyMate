import unittest
from copy import deepcopy

from rallymate_service.level_assessment import build_level_assessments
from tests.test_tennis_assessment import inputs, calculate


class LevelAssessmentIntegrationTests(unittest.TestCase):
    def test_real_motion_keeps_five_level_scores_with_source_identity(self):
        report = calculate(*inputs())
        self.assertEqual(len(report["level_assessments"]), 5)
        for level in report["level_assessments"]:
            self.assertIsNotNone(level["overall_score"])
            self.assertEqual(level["source_video_sha256"], report["source_video_sha256"])
            self.assertEqual(level["subject_id"], report["subject_scope"]["selected_subject_id"])
            self.assertEqual(level["domains"]["FS"]["score_source"], "observed_family")
            self.assertEqual(level["domains"]["FS"]["coverage"], 0)
            self.assertIn("ball_control_consistency_ok", level["gatekeepers"]["advanced"]["missing_evidence"])

    def test_serve_family_does_not_fabricate_flat_spin_class(self):
        report = calculate(*inputs())
        report = deepcopy(report)
        for episode in report["episodes"]:
            episode["family"] = "serve"
            episode["motion_pattern"]["profile_id"] = "serve"
        levels = build_level_assessments(report)
        for level in levels:
            self.assertEqual(level["domains"]["SS"]["measured_techniques"], [])
            self.assertIsNotNone(level["domains"]["SS"]["score"])
            self.assertEqual(level["domains"]["SS"]["score_source"], "observed_family")


if __name__ == "__main__":
    unittest.main()
