from __future__ import annotations

import unittest

from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.tracking import SimpleMultiClassTracker
from rallymate_vision.utils import box_iou


class TrackingTests(unittest.TestCase):
    @staticmethod
    def _ball(x: float = 50, scale: float = 1) -> dict:
        return {"class_name": "ball", "bbox_px": [value * scale for value in [x - 5, 45, x + 5, 55]], "confidence": .8}

    def test_ball_identity_expires_by_source_time_at_different_fps(self) -> None:
        for fps in [15, 30, 60, 120]:
            with self.subTest(fps=fps):
                tracker = SimpleMultiClassTracker()
                first = tracker.update([self._ball()], 640, 480, timestamp_ms=0)[0]["track_id"]
                for index in range(1, round(fps * .3)):
                    tracker.update([], 640, 480, timestamp_ms=round(index * 1000 / fps))
                at_boundary = tracker.update([self._ball(52)], 640, 480, timestamp_ms=300)[0]["track_id"]
                self.assertEqual(first, at_boundary, "same 300 ms evidence gap must retain identity at every FPS")
                expired = tracker.update([self._ball(54)], 640, 480, timestamp_ms=601)[0]["track_id"]
                self.assertNotEqual(first, expired, "no empty update calls are required to expire a stale track")

    def test_ball_tracking_gate_is_invariant_to_uniform_resolution_scale(self) -> None:
        sequences = []
        for scale in [.5, 1, 2]:
            tracker = SimpleMultiClassTracker()
            sequences.append([tracker.update([self._ball(x, scale)], int(640 * scale), int(480 * scale), timestamp_ms=time)[0]["track_id"]
                              for time, x in [(0, 50), (100, 80), (200, 400), (600, 401)]])
        self.assertEqual(sequences[0], sequences[1])
        self.assertEqual(sequences[1], sequences[2])
        self.assertEqual(sequences[0][0], sequences[0][1])
        self.assertNotEqual(sequences[0][1], sequences[0][2])

    def test_simultaneous_balls_keep_separate_ids_and_timestamp_rewind_clears_tracks(self) -> None:
        tracker = SimpleMultiClassTracker()
        initial = tracker.update([self._ball(50), self._ball(300)], 640, 480, timestamp_ms=1000)
        self.assertEqual(len({item["track_id"] for item in initial}), 2)
        after_seek = tracker.update([self._ball(50)], 640, 480, timestamp_ms=0)
        self.assertNotIn(after_seek[0]["track_id"], {item["track_id"] for item in initial})

    def test_timestamp_input_is_validated_without_changing_player_gap_policy(self) -> None:
        tracker = SimpleMultiClassTracker()
        for invalid in [True, -1, 1.5]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tracker.update([], 640, 480, timestamp_ms=invalid)
        player = {"class_name": "player", "bbox_px": [100, 100, 300, 500], "confidence": .9}
        first = tracker.update([dict(player)], 640, 480, timestamp_ms=0)[0]["track_id"]
        resumed = tracker.update([dict(player)], 640, 480, timestamp_ms=2000)[0]["track_id"]
        self.assertEqual(first, resumed)

    def test_iou(self) -> None:
        self.assertAlmostEqual(box_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
        self.assertEqual(box_iou([0, 0, 2, 2], [3, 3, 4, 4]), 0.0)

    def test_track_id_is_stable_for_small_motion(self) -> None:
        tracker = SimpleMultiClassTracker()
        first = tracker.update(
            [
                {
                    "class_name": "player",
                    "bbox_px": [100, 100, 300, 500],
                    "confidence": 0.9,
                }
            ],
            1920,
            1080,
        )
        second = tracker.update(
            [
                {
                    "class_name": "player",
                    "bbox_px": [108, 102, 308, 502],
                    "confidence": 0.88,
                }
            ],
            1920,
            1080,
        )
        self.assertEqual(first[0]["track_id"], second[0]["track_id"])

    def test_classes_do_not_share_track_ids(self) -> None:
        tracker = SimpleMultiClassTracker()
        detections = tracker.update(
            [
                {
                    "class_name": "player",
                    "bbox_px": [0, 0, 100, 100],
                    "confidence": 0.9,
                },
                {
                    "class_name": "ball",
                    "bbox_px": [45, 45, 55, 55],
                    "confidence": 0.8,
                },
            ],
            640,
            480,
        )
        self.assertNotEqual(detections[0]["track_id"], detections[1]["track_id"])

    def test_near_identical_player_boxes_are_deduplicated(self) -> None:
        detections = [
            {
                "class_name": "player",
                "bbox_px": [100, 100, 500, 900],
                "confidence": 0.95,
            },
            {
                "class_name": "player",
                "bbox_px": [102, 101, 501, 901],
                "confidence": 0.83,
            },
        ]
        result = Yolo26Perception._deduplicate(detections)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["confidence"], 0.95)

    def test_nested_player_duplicate_is_deduplicated(self) -> None:
        detections = [
            {
                "class_name": "player",
                "bbox_px": [750, 140, 1380, 1080],
                "confidence": 0.61,
            },
            {
                "class_name": "player",
                "bbox_px": [770, 135, 1675, 1045],
                "confidence": 0.23,
            },
        ]
        result = Yolo26Perception._deduplicate(detections)
        self.assertEqual(len(result), 1)

    def test_player_track_survives_longer_detection_gap(self) -> None:
        tracker = SimpleMultiClassTracker(max_missed=3)
        first = tracker.update(
            [
                {
                    "class_name": "player",
                    "bbox_px": [100, 100, 300, 500],
                    "confidence": 0.9,
                }
            ],
            640,
            480,
        )
        for _ in range(12):
            tracker.update([], 640, 480)
        resumed = tracker.update(
            [
                {
                    "class_name": "player",
                    "bbox_px": [110, 105, 310, 505],
                    "confidence": 0.88,
                }
            ],
            640,
            480,
        )
        self.assertEqual(first[0]["track_id"], resumed[0]["track_id"])


if __name__ == "__main__":
    unittest.main()
