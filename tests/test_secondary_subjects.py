"""Person association must reject background-track drift without erasing far players."""
import unittest
import json
from pathlib import Path

import numpy as np

from rallymate_tracking.secondary_subjects import SecondarySubjectSelector


def player(track, box, confidence=.6):
    return {"class_name": "player", "track_id": track, "bbox_px": box, "confidence": confidence}


PRIMARY = player(1, [100, 200, 300, 700], .95)
FAR = player(3, [592, 368, 614, 426], .6)
BACKGROUND = player(3, [597, 393, 613, 422], .26)


class SecondarySubjectsTests(unittest.TestCase):
    def setUp(self):
        self.selector = SecondarySubjectSelector()

    def update(self, timestamp, *others, primary=1, epoch=0, selector=None):
        return (selector or self.selector).update([PRIMARY, *others], primary_track_id=primary,
            timestamp_ms=timestamp, width=1280, height=720, camera_epoch=epoch)

    def establish_far(self):
        for timestamp in (0, 40, 80):
            selected, receipt = self.update(timestamp, FAR)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])
        return receipt

    def test_same_track_cannot_change_into_small_background_object(self):
        self.establish_far()
        for timestamp in range(120, 2121, 40):
            selected, receipt = self.update(timestamp, BACKGROUND)
            self.assertEqual([p["track_id"] for p in selected], [1])
            self.assertEqual(receipt["decisions"][0]["reason"], "body_height_collapsed")
        # Returning person needs renewed support after the missing interval.
        for timestamp in (2160, 2200, 2240):
            selected, _ = self.update(timestamp, FAR)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])

    def test_low_confidence_real_player_keeps_confirmed_identity(self):
        self.establish_far()
        low = player(3, [594, 369, 617, 425], .15035)
        selected, receipt = self.update(120, low)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])
        self.assertEqual(receipt["decisions"][0]["reason"], "body_continuity")

    def test_real_upper_body_is_not_rejected_by_absolute_height(self):
        self.establish_far()
        partial = player(3, [594, 369, 613, 394], .19)
        for timestamp in (120, 160, 200):
            selected, receipt = self.update(timestamp, partial)
            self.assertEqual([p["track_id"] for p in selected], [1, 3])
            self.assertEqual(receipt["decisions"][0]["reason"], "upper_body_continuity")
        # A run of partial detections must not shrink the anchored body size.
        selected, _ = self.update(240, BACKGROUND)
        self.assertEqual([p["track_id"] for p in selected], [1])

    def test_tiny_person_with_strong_evidence_can_be_established(self):
        tiny = player(3, [593, 370, 603, 398], .9)
        for timestamp in (0, 40, 80):
            selected, _ = self.update(timestamp, tiny)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])

    def test_small_far_player_compression_colors_cannot_veto_body_continuity(self):
        for timestamp in (0, 40, 80, 120):
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            frame[368:426, 592:614] = (0, 255, 0) if timestamp < 120 else (0, 0, 255)
            selected, _ = self.selector.update([PRIMARY, FAR], primary_track_id=1,
                timestamp_ms=timestamp, width=1280, height=720, image=frame, camera_epoch=0)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])

    def test_persistent_low_confidence_tiny_object_never_becomes_a_person(self):
        for timestamp in range(0, 2001, 40):
            selected, receipt = self.update(timestamp, BACKGROUND)
            self.assertEqual([p["track_id"] for p in selected], [1])
            self.assertEqual(receipt["decisions"][0]["reason"], "insufficient_new_person_evidence")

    def test_missing_person_slot_is_not_given_to_larger_bystander(self):
        self.establish_far()
        bystander = player(8, [1100, 200, 1200, 500], .9)
        for timestamp in range(120, 3121, 40):
            selected, _ = self.update(timestamp, bystander)
            self.assertEqual([p["track_id"] for p in selected], [1])

    def test_reidentification_needs_coherent_source_time_support(self):
        self.establish_far()
        replacement = player(9, [595, 369, 617, 427], .25)
        for timestamp in (120, 160):
            selected, _ = self.update(timestamp, replacement)
            self.assertEqual([p["track_id"] for p in selected], [1])
        selected, receipt = self.update(200, replacement)
        self.assertEqual([p["track_id"] for p in selected], [1, 9])
        self.assertEqual(receipt["secondary_track_ids"], [9])

    def test_camera_epoch_and_reverse_time_restart_confirmation(self):
        self.establish_far()
        selected, receipt = self.update(120, FAR, epoch=1)
        self.assertEqual([p["track_id"] for p in selected], [1])
        self.assertTrue(receipt["reset"])
        for timestamp in (160, 200):
            selected, _ = self.update(timestamp, FAR, epoch=1)
        self.assertEqual([p["track_id"] for p in selected], [1, 3])
        selected, receipt = self.update(100, FAR, epoch=1)
        self.assertEqual([p["track_id"] for p in selected], [1])
        self.assertTrue(receipt["reset"])

    def test_primary_absence_and_invalid_secondary_do_not_trigger_replacement(self):
        selected, receipt = self.update(0, FAR, primary=None)
        self.assertEqual(selected, [])
        self.assertEqual(receipt["reason"], "primary_not_visible")
        for i, malformed in enumerate(({}, player(3, [0, 0, float("nan"), 80]),
                                        player(3, [0, 0, 90, 80], float("nan")))):
            selected, receipt = self.update(40 * (i + 1), malformed)
            self.assertEqual([p["track_id"] for p in selected], [1])
            self.assertEqual(receipt["decisions"][0]["reason"], "invalid_secondary_detection")

    def test_multiple_slots_are_unique_and_detector_order_independent(self):
        a, b = SecondarySubjectSelector(max_secondaries=2), SecondarySubjectSelector(max_secondaries=2)
        other = player(7, [900, 300, 940, 400], .8)
        for timestamp in (0, 40, 80, 120):
            selected_a, receipt_a = self.update(timestamp, FAR, other, selector=a)
            selected_b, receipt_b = self.update(timestamp, other, FAR, selector=b)
            self.assertEqual(receipt_a, receipt_b)
        self.assertEqual([p["track_id"] for p in selected_a], [1, 7, 3])

    def test_recorded_track_drift_and_upper_body_recovery(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "false-person-regression.json")
                             .read_text(encoding="utf-8"))
        selections = {}
        # This is a contiguous recorded window. Do not invent continuity
        # between the separate windows stored in the larger regression fixture.
        for frame in fixture["frames"]:
            if not 180 <= frame["frame_index"] <= 220:
                continue
            _, receipt = self.selector.update(frame["detections"],
                primary_track_id=frame["primary_track_id"], timestamp_ms=frame["timestamp_ms"],
                width=frame["image_size"][0], height=frame["image_size"][1],
                camera_epoch=frame["camera_reference_epoch"])
            selections[frame["frame_index"]] = receipt["secondary_track_ids"]
        self.assertIn(3, selections[205])
        self.assertNotIn(3, selections[206])
        for index in (214, 217, 219):
            self.assertIn(29, selections[index])


if __name__ == "__main__":
    unittest.main()
