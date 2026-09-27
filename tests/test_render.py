from __future__ import annotations

import copy
import unittest

import numpy as np

from rallymate_vision.render import annotate_frame


class AnnotateFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = np.full((240, 360, 3), 24, dtype=np.uint8)
        points = [{"x_px": 0.0, "y_px": 0.0, "confidence": 0.0} for _ in range(17)]
        points[5] = {"x_px": 120.0, "y_px": 120.0, "confidence": 0.9}
        points[6] = {"x_px": 150.0, "y_px": 120.0, "confidence": 0.9}
        self.poses = [{"keypoints": points}]
        self.detections = [
            {"class_name": "player", "bbox_px": [40, 80, 100, 180], "center_px": [70, 130], "track_id": 1, "confidence": 0.95},
            {"class_name": "ball", "center_px": [260, 110], "track_id": 2, "confidence": 0.8},
            {"class_name": "racket", "center_px": [305, 120], "track_id": 3, "confidence": 0.75},
            {"class_name": "ball", "center_px": [60, 210], "track_id": 4, "confidence": 0.3},
        ]

    def render(self, **options) -> np.ndarray:
        return annotate_frame(self.frame, self.detections, self.poses, {"status": "unknown"}, 1, 100, {"status": "ok"}, **options)

    def test_default_replay_is_clean_and_preserves_inference_evidence(self) -> None:
        detections = copy.deepcopy(self.detections)
        poses = copy.deepcopy(self.poses)
        rendered = self.render()
        self.assertTrue(np.array_equal(rendered, self.frame))
        self.assertIsNot(rendered, self.frame)
        self.assertEqual(self.detections, detections)
        self.assertEqual(self.poses, poses)

    def test_detection_overlay_is_opt_in_and_filters_low_confidence(self) -> None:
        rendered = self.render(show_detections=True)
        self.assertTrue(np.array_equal(rendered[110, 260], [40, 220, 255]))
        self.assertTrue(np.array_equal(rendered[120, 305], [255, 140, 40]))
        self.assertTrue(np.array_equal(rendered[130, 70], self.frame[130, 70]))
        self.assertTrue(np.array_equal(rendered[210, 60], self.frame[210, 60]))
        self.assertTrue(np.array_equal(rendered[120, 120], self.frame[120, 120]))
        self.assertTrue(np.array_equal(rendered[:38], self.frame[:38]))

    def test_pose_overlay_can_be_enabled_without_detection_points_or_status(self) -> None:
        rendered = self.render(show_pose=True)
        self.assertTrue(np.array_equal(rendered[120, 120], [255, 255, 255]))
        self.assertTrue(np.array_equal(rendered[120, 135], [255, 90, 210]))
        self.assertTrue(np.array_equal(rendered[110, 260], self.frame[110, 260]))
        self.assertTrue(np.array_equal(rendered[:38], self.frame[:38]))

    def test_status_banner_is_opt_in(self) -> None:
        rendered = self.render(show_status=True)
        self.assertFalse(np.array_equal(rendered[:38], self.frame[:38]))
        self.assertTrue(np.array_equal(rendered[39:], self.frame[39:]))


if __name__ == "__main__":
    unittest.main()
