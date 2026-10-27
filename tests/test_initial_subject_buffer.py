from __future__ import annotations

from copy import deepcopy
import unittest
import numpy as np

from rallymate_tracking.initial_subject_buffer import InitialSubjectBuffer
from rallymate_tracking.subject_lock import SubjectLock, valid_subject_identity


def observation(index, *, known=False, track=2, color=(20, 30, 200), camera=0):
    image = np.zeros((140, 160, 3), np.uint8)
    image[10:130, 30:80] = color
    identity = ({"version": "subject-lock-v1", "subject_id": "subject-1", "status": "locked",
                 "source_track_id": track, "evidence": ["uniquely_associated_racket_with_activity"], "reason_code": None}
                if known else SubjectLock.unknown("athlete_role_not_established"))
    return {"image": image, "timestamp_ms": index * 50, "timestamp_source": "decoder_pts",
            "source_frame_index": index, "frame_stride": 1,
            "decoded_binding": {"pts": index, "time_base_num": 1, "time_base_den": 20},
            "camera_motion": {"reference_epoch": camera, "status": "stationary", "compensation_valid": True},
            "detections": [{"class_name": "player", "track_id": track, "bbox_px": [30, 10, 80, 130], "confidence": .9}],
            "subject_identity": identity}


class InitialSubjectBufferTests(unittest.TestCase):
    def test_later_role_confirmation_retains_each_original_pixel_binding_and_order(self):
        buffer = InitialSubjectBuffer()
        source = [observation(i, known=i == 6) for i in range(8)]
        released = []
        for item in source:
            released.extend(buffer.push(item))
        self.assertEqual([i["source_frame_index"] for i in released], list(range(8)))
        for i, item in enumerate(released):
            self.assertIs(item["image"], source[i]["image"])
            self.assertEqual(item["decoded_binding"]["pts"], i)
            self.assertTrue(valid_subject_identity(item["subject_identity"], {2}))
            if i < 6:
                proof = item["subject_identity"]["initialization_confirmation"]
                self.assertEqual(proof["original_reason_code"], "athlete_role_not_established")
                self.assertEqual(proof["confirmation_source_frame_index"], 6)
        self.assertFalse(buffer.pending)

    def test_identity_pixel_camera_missing_frame_and_overlap_break_prefix(self):
        for field in ("different_clothes", "track", "camera", "frame_gap", "ambiguous"):
            with self.subTest(field=field):
                source = [observation(i, known=i == 4) for i in range(5)]
                if field == "different_clothes":
                    source[2] = observation(2, color=(200, 30, 20))
                elif field == "track":
                    source[2]["detections"][0]["track_id"] = 99
                elif field == "camera":
                    source[2]["camera_motion"]["reference_epoch"] = 1
                elif field == "frame_gap":
                    source[2]["source_frame_index"] = 1
                else:
                    other = deepcopy(source[2]["detections"][0]); other["track_id"] = 99
                    source[2]["detections"].append(other)
                buffer = InitialSubjectBuffer(); released = []
                for item in source:
                    released.extend(buffer.push(item))
                self.assertEqual([i["subject_identity"]["status"] for i in released[:3]], ["unknown"] * 3)
                self.assertEqual(released[3]["subject_identity"]["status"], "locked")

    def test_memory_time_and_frame_caps_flush_unknown_without_dropping_frames(self):
        for options in ({"max_frames": 2}, {"max_duration_ms": 60}, {"max_bytes": 140 * 160 * 3 * 2}):
            with self.subTest(options=options):
                buffer = InitialSubjectBuffer(**options); released = []
                for i in range(8):
                    released.extend(buffer.push(observation(i)))
                    self.assertLessEqual(len(buffer.pending), 2)
                released.extend(buffer.finish())
                self.assertEqual([i["source_frame_index"] for i in released], list(range(8)))
                self.assertTrue(all(i["subject_identity"]["status"] == "unknown" for i in released))
                self.assertEqual(buffer.bytes, 0)

    def test_eof_and_clip_end_before_confirmation_preserve_unknown(self):
        buffer = InitialSubjectBuffer()
        self.assertEqual(buffer.push(observation(0)), [])
        self.assertEqual(buffer.push(observation(1)), [])
        result = buffer.finish()
        self.assertEqual(len(result), 2)
        self.assertEqual([i["subject_identity"]["status"] for i in result], ["unknown", "unknown"])

    def test_oversized_single_frame_is_released_without_lookahead(self):
        buffer = InitialSubjectBuffer(max_bytes=1)
        item = observation(0)
        self.assertEqual(buffer.push(item), [item])
        self.assertFalse(buffer.pending)
        self.assertEqual(buffer.bytes, 0)

    def test_buffer_does_not_reopen_after_first_lock(self):
        buffer = InitialSubjectBuffer()
        self.assertEqual(len(buffer.push(observation(0, known=True))), 1)
        item = observation(1)
        self.assertEqual(buffer.push(item), [item])
        self.assertEqual(item["subject_identity"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
