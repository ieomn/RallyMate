from dataclasses import replace
import unittest

import numpy as np

from rallymate_vision.contracts import CourtConfig
from rallymate_vision.manual_court import FixedManualCourtObservation


def config():
    return CourtConfig(mode="manual", manual_polygon_role="court_outer_doubles_corners",
                       manual_polygon_normalized=[[.1, .2], [.9, .2], [.8, .9], [.2, .9]])


def camera(status="reference", epoch=0, dx=0):
    return {"status": status, "reference_epoch": epoch, "compensation_valid": True,
            "matrix_to_reference": [[1, 0, dx], [0, 1, 0]]}


class FixedManualCourtTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((100, 200, 3), dtype=np.uint8)

    def test_explicit_manual_corners_are_projected_but_never_score_usable(self):
        detector = FixedManualCourtObservation(config())
        for state in (camera(), camera("stationary")):
            result = detector.observe(self.frame, state)
            self.assertEqual(result["status"], "calibrated")
            self.assertTrue(result["calibration_usable"])
            self.assertFalse(result["scoring_usable"])
            self.assertEqual(result["purpose"], "display_only")
            self.assertEqual(result["polygon_px"][0], [20, 20])

    def test_default_auto_and_visible_region_stay_disabled(self):
        for item in (CourtConfig(), replace(config(), mode="auto"),
                     replace(config(), manual_polygon_role="visible_region")):
            result = FixedManualCourtObservation(item).observe(self.frame, camera())
            self.assertEqual(result["status"], "disabled")
            self.assertFalse(result["calibration_usable"])

    def test_motion_missing_reference_epoch_and_drift_permanently_invalidate(self):
        for state in (camera("moving"), camera("unavailable"), camera("stationary", epoch=1),
                      camera("stationary", dx=2), {**camera("stationary"), "compensation_valid": False}):
            detector = FixedManualCourtObservation(config())
            self.assertTrue(detector.observe(self.frame, camera())["calibration_usable"])
            self.assertFalse(detector.observe(self.frame, state)["calibration_usable"])
            # Returning to a stationary status cannot restore stale pixels.
            self.assertFalse(detector.observe(self.frame, camera("stationary"))["calibration_usable"])

    def test_resolution_change_invalidates_instead_of_rescaling_old_geometry(self):
        detector = FixedManualCourtObservation(config())
        detector.observe(self.frame, camera())
        result = detector.observe(np.zeros((100, 400, 3), dtype=np.uint8), camera("stationary"))
        self.assertFalse(result["calibration_usable"])
        self.assertEqual(result["reason"], "manual_calibration_resolution_changed")

    def test_first_processed_frame_requires_actual_reference(self):
        result = FixedManualCourtObservation(config()).observe(self.frame, camera("stationary"))
        self.assertFalse(result["calibration_usable"])

    def test_invalid_self_intersecting_and_degenerate_polygons_are_not_calibrated(self):
        for polygon in ([[.1, .1], [.9, .9], [.9, .1], [.1, .9]], [[.1, .1]] * 4,
                        [[-.1, .1], [.9, .1], [.9, .9], [.1, .9]]):
            result = FixedManualCourtObservation(replace(config(), manual_polygon_normalized=polygon)).observe(self.frame, camera())
            self.assertFalse(result["calibration_usable"])


if __name__ == "__main__":
    unittest.main()
