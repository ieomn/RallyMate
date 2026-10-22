"""Unknown or mixed diagnostic joint names never clear an original warning."""
from copy import deepcopy
import unittest

from rallymate_scoring.footwork_cycle_card_evidence import _quality_evidence, CORE_JOINTS
from rallymate_scoring.joint_quality_scope import known_joint_names
from rallymate_scoring.temporal_card_evidence import _result, CARD_CONDITIONS


class JointWarningScopeTests(unittest.TestCase):
    def test_only_registered_names_prove_a_known_landmark_identity(self):
        self.assertTrue(known_joint_names(["left_wrist", "right_wrist"]))
        for names in ([], ["J999"], ["left_wrist", "unrecognized_joint"], [None], [True], "left_wrist"):
            self.assertFalse(known_joint_names(names))

    def test_unknown_jump_and_swap_names_keep_warning_in_both_runtime_paths(self):
        examples = [
            ("keypoint_jump_candidates_present", "keypoint_jump_candidate_joints", ["unrecognized_joint"]),
            ("keypoint_jump_candidates_present", "keypoint_jump_candidate_joints", ["left_wrist", "J999"]),
            ("left_right_swap_candidates_present", "left_right_swap_candidate_joint_pairs", [{"left_joint": "J999", "right_joint": "J998"}]),
            ("left_right_swap_candidates_present", "left_right_swap_candidate_joint_pairs", [{"left_joint": "left_wrist", "right_joint": "J998"}]),
        ]
        for flag, field, entries in examples:
            with self.subTest(flag=flag, entries=entries):
                event = {"event_id": "unit-only", "quality_flags": [flag], "track_diagnostics": {field: entries}}
                before = deepcopy(event)
                cycle, reason = _quality_evidence(event, CORE_JOINTS)
                self.assertIsNotNone(reason)
                self.assertIn(flag, cycle["review_flags"])
                self.assertFalse(cycle["irrelevant_joint_flags"])
                measurements = {metric: 1 for metric, *_ in CARD_CONDITIONS["FS01-M02"]}
                temporal = _result("FS01-M02", event, {}, measurements, None)
                self.assertEqual(temporal["status"], "needs_review")
                self.assertTrue(all(condition["passed"] is None for condition in temporal["conditions"]))
                self.assertIn(flag, temporal["evidence"]["event_quality"]["scoring_block_flags"])
                self.assertEqual(event, before)

    def test_known_unrelated_wrist_warning_stays_local_and_required_ankle_still_blocks(self):
        for joint, blocked in (("left_wrist", False), ("left_ankle", True)):
            event = {"event_id": "unit-only", "quality_flags": ["keypoint_jump_candidates_present"],
                "track_diagnostics": {"keypoint_jump_candidate_joints": [joint]}}
            cycle, reason = _quality_evidence(event, CORE_JOINTS)
            self.assertEqual(reason is not None, blocked)
            temporal = _result("FS01-M02", event, {}, {metric: 1 for metric, *_ in CARD_CONDITIONS["FS01-M02"]}, None)
            self.assertEqual(temporal["status"] == "needs_review", blocked)


if __name__ == "__main__":
    unittest.main()
