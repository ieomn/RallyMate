from __future__ import annotations

import unittest

from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.tracking import SimpleMultiClassTracker
from rallymate_vision.utils import box_iou


class TrackingTests(unittest.TestCase):
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
