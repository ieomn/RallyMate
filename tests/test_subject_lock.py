from __future__ import annotations

import unittest
from unittest.mock import patch
import numpy as np

from rallymate_tracking.subject_lock import SubjectLock, clothing_descriptor, appearance_distance
from rallymate_tracking.primary_player import select_primary_player_timeline
from tests.test_primary_player import _record
from tests.test_primary_player import _sam_record


def person(track=1, box=(40, 30, 90, 180)):
    return {"class_name": "player", "track_id": track, "bbox_px": list(box), "confidence": .9}


def image_with_people(people, colors):
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    for player, color in zip(people, colors):
        x1, y1, x2, y2 = [int(v) for v in player["bbox_px"]]
        image[y1:y2, x1:x2] = color
    return image


class SubjectLockTests(unittest.TestCase):
    def test_explicit_source_pixel_failure_cannot_fall_back_to_geometry_identity(self):
        records = [_record(0, [(1, [20, 20, 60, 110], True)])]
        with patch('rallymate_tracking.primary_player._source_images', return_value=iter([None])):
            row = select_primary_player_timeline(records, source_video_path='requested.mp4')["timeline"][0]
        self.assertEqual(row["subject_identity"]["reason_code"], "source_pixels_unavailable")
        self.assertIsNone(row["source_track_id"])
        self.assertEqual(select_primary_player_timeline(records)["timeline"][0]["source_track_id"], 1)

    def test_epochs_are_copied_only_from_verified_native_request_binding(self):
        record = _sam_record(0)
        record["sam3d_requests"][0]["selection_epoch"] = 7
        record["poses"][0]["source_binding"]["selection_epoch"] = 7
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["selection_epoch"], 7)
        self.assertEqual(row["camera_reference_epoch"], 0)
        record["sam3d_requests"][0]["selection_epoch"] = 8
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertIsNone(row["selection_epoch"])
        self.assertIsNone(row["camera_reference_epoch"])

    def update(self, lock, timestamp, people, colors=None, epoch=0):
        return lock.update(people, timestamp_ms=timestamp, width=320, height=240,
                           image=image_with_people(people, colors) if colors else None,
                           camera_epoch=epoch)

    def test_long_dropout_does_not_borrow_different_person_or_recycled_id(self):
        lock = SubjectLock()
        self.assertEqual(self.update(lock, 0, [person()])["source_track_id"], 1)
        for track in (2, 1):
            unknown = self.update(lock, 1000, [person(track, (200, 30, 250, 180))])
            self.assertEqual(unknown["status"], "unknown")
            self.assertIsNone(unknown["subject_id"])

    def test_immediate_tracklet_overlap_preserves_subject_without_changing_native_ids(self):
        rows = [_record(0, [(1, [30, 15, 100, 115], True)]),
                _record(1, [(9, [31, 15, 101, 115], True)])]
        timeline = select_primary_player_timeline(rows)["timeline"]
        self.assertEqual([r["source_track_id"] for r in timeline], [1, 9])
        self.assertEqual({r["subject_identity"]["subject_id"] for r in timeline}, {"subject-1"})
        self.assertEqual(rows[1]["poses"][0]["person_track_id"], 9)

    def test_different_clothing_vetoes_overlap_and_same_track_id_switch(self):
        for track in (1, 2):
            lock = SubjectLock()
            self.update(lock, 0, [person()], [(30, 40, 210)])
            result = self.update(lock, 40, [person(track)], [(210, 210, 210)])
            self.assertEqual(result["status"], "unknown")

    def test_distinctive_target_reentry_requires_temporal_evidence(self):
        lock = SubjectLock()
        target_color = (20, 200, 200)
        self.update(lock, 0, [person()], [target_color])
        for t in (1000, 1040, 1080):
            result = self.update(lock, t, [person(2)], [(220, 220, 220)])
            self.assertIsNone(result["subject_id"])
        returned = person(9, (210, 30, 260, 180))
        for t in (2000, 2040):
            self.assertEqual(self.update(lock, t, [returned], [target_color], epoch=2)["status"], "unknown")
        result = self.update(lock, 2080, [returned], [target_color], epoch=2)
        self.assertEqual(result["status"], "reidentified")
        self.assertEqual(result["source_track_id"], 9)
        self.assertEqual(result["subject_id"], "subject-1")

    def test_same_clothes_on_two_people_are_not_unique_reidentification(self):
        lock = SubjectLock()
        color = (20, 200, 200)
        self.update(lock, 0, [person()], [color])
        choices = [person(2, (140, 30, 190, 180)), person(3, (230, 30, 280, 180))]
        for t in (1000, 1040, 1080):
            result = self.update(lock, t, choices, [color, color])
            self.assertEqual(result["reason_code"], "appearance_match_not_unique")

    def test_sparse_reentry_observations_do_not_form_continuous_support(self):
        lock = SubjectLock()
        color = (20, 200, 200)
        self.update(lock, 0, [person()], [color])
        for t in (1000, 2000, 3000):
            self.assertEqual(self.update(lock, t, [person(9)], [color])["status"], "unknown")

    def test_clothing_color_is_stable_under_light_intensity_change(self):
        p = person()
        bright = clothing_descriptor(image_with_people([p], [(10, 210, 210)]), p["bbox_px"])
        dim = clothing_descriptor(image_with_people([p], [(5, 105, 105)]), p["bbox_px"])
        white = clothing_descriptor(image_with_people([p], [(210, 210, 210)]), p["bbox_px"])
        self.assertLess(appearance_distance(bright, dim), .1)
        self.assertGreater(appearance_distance(bright, white), .9)

    def test_gradual_same_track_appearance_updates_do_not_require_the_first_pose_forever(self):
        lock = SubjectLock()
        p = person()
        for step in range(12):
            image = image_with_people([p], [(20, 200, 200)])
            # A changing visible shirt/pants fraction, as bbox shape changes,
            # must be learned gradually rather than compared only to frame 0.
            split = 30 + int(150 * step / 12)
            image[30:split,40:90] = (210,210,210)
            result = lock.update([p],timestamp_ms=step*200,width=320,height=240,image=image,camera_epoch=0)
            self.assertEqual(result["status"], "locked")
        self.assertGreater(len(lock.gallery), 4)

    def test_crowded_initial_frame_needs_athlete_evidence(self):
        people = [person(i + 1, (10 + i * 50, 30, 45 + i * 50, 170)) for i in range(6)]
        lock = SubjectLock()
        result = self.update(lock, 0, people, [(0, 100, 200)] * 6)
        self.assertEqual(result["status"], "unknown")

    def test_truncated_remainder_cannot_extend_target_to_the_edge_of_a_camera_pan(self):
        lock = SubjectLock()
        color = (20, 200, 200)
        self.update(lock, 0, [person()], [color])
        result = self.update(lock, 40, [person(1, (40, 195, 90, 240))], [color])
        self.assertEqual(result["status"], "unknown")

    def test_feet_at_image_edge_do_not_discard_visible_clothing_identity(self):
        lock = SubjectLock()
        color = (20, 200, 200)
        self.update(lock, 0, [person(1, (40, 60, 90, 230))], [color])
        result = self.update(lock, 40, [person(1, (40, 70, 90, 240))], [color])
        self.assertEqual(result["status"], "locked")

    def test_saved_identity_cannot_claim_a_missing_or_conflicting_subject(self):
        record = _record(0, [(1, [20, 20, 60, 110], True)])
        record["subject_identity"] = {"version": "subject-lock-v1", "subject_id": "subject-1",
                                      "status": "locked", "source_track_id": 9, "evidence": ["continuous_track_and_box"], "reason_code": None}
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertIsNone(row["source_track_id"])
        self.assertEqual(row["subject_identity"]["reason_code"], "saved_subject_identity_invalid")

    def test_saved_preinference_lock_is_authoritative_even_when_other_box_is_larger(self):
        record = _record(0, [(1, [20, 20, 60, 110], True), (2, [0, 0, 190, 119], True)])
        record["subject_identity"] = {"version": "subject-lock-v1", "subject_id": "subject-1",
                                      "status": "locked", "source_track_id": 1, "evidence": ["continuous_track_and_box"], "reason_code": None}
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["source_track_id"], 1)
        self.assertIsNone(row["selection_best_score"])
        record["subject_identity"].update(subject_id=None, source_track_id=None, status="unknown", reason_code="target_identity_unmatched")
        row = select_primary_player_timeline([record])["timeline"][0]
        self.assertEqual(row["selection_status"], "missing")
        self.assertIsNone(row["source_track_id"])


if __name__ == '__main__':
    unittest.main()
