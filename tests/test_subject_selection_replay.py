from copy import deepcopy
import unittest

from scripts.rebuild_subject_selection import reselected_row
from tests.test_primary_player import _sam_record


class SubjectSelectionReplayTests(unittest.TestCase):
    def test_replay_cannot_supply_missing_native_pose_or_new_epoch(self):
        record = _sam_record(0)
        original = {"source_frame_index": 0, "source_track_id": 1, "selection_epoch": 99}
        identity = {"version": "subject-lock-v1", "subject_id": "subject-1", "status": "locked",
                    "source_track_id": 1, "evidence": ["foreground_clothing"], "reason_code": None}
        actual = reselected_row(original, record, identity)
        self.assertEqual(actual["selection_epoch"], record["sam3d_requests"][0]["selection_epoch"])
        self.assertNotEqual(actual["selection_epoch"], 99)
        no_pose = deepcopy(record)
        no_pose["poses"] = []
        missing = reselected_row(original, no_pose, identity)
        self.assertIsNone(missing["source_track_id"])
        self.assertFalse(missing["pose_present"])
        self.assertIsNone(missing["selection_epoch"])
        self.assertEqual(original["selection_epoch"], 99)

    def test_rejected_identity_clears_old_player_and_pose(self):
        record = _sam_record(0)
        original = {"source_frame_index": 0, "source_track_id": 1, "pose_present": True, "selection_epoch": 3}
        result = reselected_row(original, record, {"status": "unknown", "subject_id": None,
                                                  "source_track_id": None, "reason_code": "target_identity_unmatched"})
        self.assertIsNone(result["source_track_id"])
        self.assertIsNone(result["selection_epoch"])
        self.assertFalse(result["pose_present"])


if __name__ == "__main__":
    unittest.main()
