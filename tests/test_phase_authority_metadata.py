"""Release/maturity labels cannot invalidate otherwise bound motion phases."""
from copy import deepcopy
import unittest

from rallymate_service.tennis_assessment import analyze_tennis_assessment, _bound_phase_ambiguity, _segments
from tests import test_tennis_candidate_binding as fixtures
from tests.test_tennis_assessment import inputs


class PhaseAuthorityMetadataTests(unittest.TestCase):
    def source_episode(self):
        return fixtures.TennisCandidateBindingTests().source_phased_episode()

    def test_metadata_changes_keep_same_bound_phase_measurements(self):
        timeline, records = inputs()
        original = self.source_episode()
        expected = analyze_tennis_assessment(timeline, records, video_sha256=fixtures.SHA, motion_episodes=[original])
        self.assertEqual(expected["observation_count"], 1)
        for status, allowed, maturity in (("graded", True, "F4"), ("pending", False, "F0"), (None, None, None)):
            episode = deepcopy(original)
            episode["measurement_provenance"].update(assessment_status=status, scoring_allowed=allowed, feasibility_level=maturity)
            with self.subTest(status=status):
                actual = analyze_tennis_assessment(timeline, records, video_sha256=fixtures.SHA, motion_episodes=[episode])
                self.assertEqual(actual["episodes"], expected["episodes"])
                self.assertEqual(actual["overall_score"], expected["overall_score"])
        episode = deepcopy(original)
        del episode["measurement_provenance"]["assessment_status"]
        del episode["measurement_provenance"]["scoring_allowed"]
        actual = analyze_tennis_assessment(timeline, records, video_sha256=fixtures.SHA, motion_episodes=[episode])
        self.assertEqual(actual["episodes"], expected["episodes"])

    def test_mismatched_video_still_rejects_graded_phase_provenance(self):
        timeline, records = inputs()
        episode = self.source_episode()
        episode["measurement_provenance"].update(assessment_status="graded", scoring_allowed=True)
        episode["continuity"]["video_sha256"] = "b" * 64
        result = analyze_tennis_assessment(timeline, records, video_sha256=fixtures.SHA, motion_episodes=[episode])
        self.assertEqual(result["observation_count"], 0)
        self.assertIsNone(result["overall_score"])
        self.assertIn("candidate_source_or_complete_phase_unverified", result["candidate_accounting"]["rejection_counts"])

    def test_ambiguity_reason_uses_geometry_and_source_not_approval_labels(self):
        timeline, records = inputs()
        segments, _ = _segments(timeline, records, fixtures.SHA)
        episode = self.source_episode()
        reason = "unresolved_racket_hand_with_competing_arm_sequence"
        episode["phase_ambiguity"] = {"reason_code": reason, "candidate_wrist": "right", "competing_wrist": "left",
            "candidate_motion_peak_ms": episode["peak_ms"], "competing_motion_peak_ms": episode["peak_ms"] + 160}
        for phase in episode["phases"]:
            phase.update(status="unavailable", start_ms=None, end_ms=None, reason_code=reason)
        for status, allowed in (("measurement_only", False), ("graded", True), ("pending", None)):
            episode["measurement_provenance"].update(assessment_status=status, scoring_allowed=allowed)
            self.assertEqual(_bound_phase_ambiguity(episode, segments, fixtures.SHA), reason)
        episode["continuity"]["video_sha256"] = "b" * 64
        self.assertIsNone(_bound_phase_ambiguity(episode, segments, fixtures.SHA))


if __name__ == "__main__":
    unittest.main()
