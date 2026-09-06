from __future__ import annotations

import unittest

import cv2
import numpy as np

from rallymate_vision.court import CourtDetector


class CourtDetectorTests(unittest.TestCase):
    def test_manual_polygon_maps_to_pixels(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        detector = CourtDetector(
            mode="manual",
            manual_polygon_role="court_outer_doubles_corners",
            manual_polygon_normalized=[
                [0.1, 0.2],
                [0.9, 0.2],
                [0.8, 0.9],
                [0.2, 0.9],
            ],
        )
        result = detector.detect(frame)
        self.assertEqual(result["status"], "calibrated")
        self.assertEqual(result["polygon_px"][0], [20, 20])
        self.assertEqual(result["polygon_px"][2], [160, 90])

    def test_disabled_detector_is_explicit(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = CourtDetector(mode="disabled").detect(frame)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["polygon_px"], [])
        self.assertFalse(result["region_usable"])

    def test_manual_polygon_is_calibration_usable(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = CourtDetector(
            mode="manual",
            manual_polygon_role="court_outer_doubles_corners",
            manual_polygon_normalized=[
                [0.1, 0.1],
                [0.9, 0.1],
                [0.9, 0.9],
                [0.1, 0.9],
            ],
        ).detect(frame)
        self.assertTrue(result["region_usable"])
        self.assertTrue(result["calibration_usable"])
        self.assertEqual(
            len(result["homography_image_to_court_normalized"]),
            3,
        )
        source = np.asarray(
            [[[10.0, 10.0], [90.0, 10.0], [90.0, 90.0], [10.0, 90.0]]],
            dtype=np.float32,
        )
        matrix = np.asarray(
            result["homography_image_to_court_normalized"],
            dtype=np.float32,
        )
        mapped = cv2.perspectiveTransform(source, matrix)[0]
        np.testing.assert_allclose(
            mapped,
            np.asarray([[0, 0], [1, 0], [1, 1], [0, 1]]),
            atol=1e-5,
        )

    def test_manual_visible_region_is_not_metric_calibration(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = CourtDetector(
            mode="manual",
            manual_polygon_normalized=[
                [0.1, 0.1],
                [0.9, 0.1],
                [0.9, 0.9],
                [0.1, 0.9],
            ],
        ).detect(frame)
        self.assertEqual(result["status"], "detected")
        self.assertTrue(result["region_usable"])
        self.assertFalse(result["calibration_usable"])
        self.assertIsNone(
            result["homography_image_to_court_normalized"]
        )

    def test_close_player_prevents_auto_geometry_promotion(self) -> None:
        frame = np.zeros((600, 1000, 3), dtype=np.uint8)
        for y in (280, 360, 500):
            cv2.line(frame, (50, y), (950, y), (255, 255, 255), 8)
        cv2.line(frame, (100, 550), (450, 240), (255, 255, 255), 8)
        cv2.line(frame, (900, 550), (550, 240), (255, 255, 255), 8)
        result = CourtDetector(mode="auto").detect(
            frame,
            player_boxes=[[200, 50, 800, 590]],
        )
        self.assertNotEqual(result["status"], "detected")
        self.assertFalse(result["region_usable"])
        for point in result["polygon_normalized"]:
            self.assertTrue(all(0.0 <= value <= 1.0 for value in point))


if __name__ == "__main__":
    unittest.main()
