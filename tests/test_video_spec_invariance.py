from __future__ import annotations

from dataclasses import replace
import unittest

import cv2
import numpy as np

from rallymate_vision.timebase import SourceVideoClock
from rallymate_vision.camera_motion import CameraMotionGuard
from rallymate_vision.quality import VideoMetadata, metadata_warnings
from rallymate_events.rules import detect_pose_events, _local_activity_thresholds
from rallymate_scoring.stroke_candidates import _sample
from test_events import _motion_sequence


class VideoSpecInvarianceTests(unittest.TestCase):
    def test_actual_variable_frame_intervals_are_preserved(self):
        clock = SourceVideoClock(30)
        values = [clock.resolve(i, value) for i, value in enumerate([150, 190, 270, 290, 410])]
        self.assertEqual([value for value, _ in values], [0, 40, 120, 140, 260])
        self.assertTrue(all(source == "decoder_pts" for _, source in values))
        self.assertTrue(clock.summary()["timing_verified"])

    def test_broken_decoder_time_is_explicit_and_monotonic(self):
        clock = SourceVideoClock(30)
        values = [clock.resolve(i, value) for i, value in enumerate([0, 0, float("nan"), -1, 133])]
        self.assertEqual([value for value, _ in values], [0, 33, 67, 100, 133])
        self.assertEqual(clock.summary()["fallback_timestamp_frames"], 3)
        self.assertFalse(clock.summary()["timing_verified"])
        with self.assertRaises(ValueError):
            clock.resolve(4, 160)

    def test_portrait_and_landscape_quality_spec_warning_matches(self):
        self.assertEqual(metadata_warnings(VideoMetadata(1920, 1080, 30, 100, 3333)),
                         metadata_warnings(VideoMetadata(1080, 1920, 30, 100, 3333)))

    def test_background_guard_distinguishes_pan_and_player_motion(self):
        rng = np.random.default_rng(17)
        gray = rng.integers(0, 256, (240, 320), dtype=np.uint8)
        frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        guard = CameraMotionGuard()
        self.assertEqual(guard.update(frame, [], 0)["status"], "reference")
        self.assertEqual(guard.update(frame, [], 40)["status"], "stationary")
        foreground = frame.copy()
        foreground[70:190, 120:200] = 0
        self.assertEqual(guard.update(foreground, [[120, 70, 200, 190]], 80)["status"], "stationary")
        pan = cv2.warpAffine(foreground, np.float32([[1, 0, 4], [0, 1, 0]]), (320, 240))
        moved = guard.update(pan, [[124, 70, 204, 190]], 120)
        self.assertEqual(moved["status"], "moving")
        self.assertTrue(moved["compensation_valid"])
        corrected = np.asarray(moved["matrix_to_reference"]) @ np.array([104.0, 100.0, 1.0])
        np.testing.assert_allclose(corrected, [100.0, 100.0], atol=0.2)

    def test_featureless_background_is_unavailable_not_stable(self):
        guard = CameraMotionGuard()
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        guard.update(frame, [], 0)
        result = guard.update(frame, [], 40)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["fixed_camera_supported"])
        self.assertFalse(result["compensation_valid"])

    def test_camera_motion_blocks_translation_bouts(self):
        sequence = _motion_sequence()
        fixed = replace(sequence, camera_motion_status=("stationary",) * len(sequence.timestamp_ms))
        moving = replace(sequence, camera_motion_status=("moving",) * len(sequence.timestamp_ms))
        unknown = replace(sequence, camera_motion_status=("unavailable",) * len(sequence.timestamp_ms))
        self.assertTrue(detect_pose_events(fixed, source_id="fixed-camera"))
        self.assertEqual(detect_pose_events(moving, source_id="pan-camera"), [])
        self.assertEqual(detect_pose_events(unknown, source_id="unknown-camera"), [])

    def test_stroke_sample_rejects_unverified_motion_time(self):
        self.assertIsNone(_sample({}, {"timestamp_source": "fps_fallback"}))
        self.assertIsNone(_sample({}, {"camera_motion_status": "moving"}))

    def test_local_threshold_is_independent_of_remote_padding(self):
        timestamps = np.arange(0, 10001, 40)
        values = np.sin(timestamps / 400) ** 2
        valid = np.ones(len(timestamps), dtype=bool)
        baseline = _local_activity_thresholds(timestamps, values, valid)
        altered = values.copy()
        altered[(timestamps < 2000) | (timestamps > 8000)] = 1000
        actual = _local_activity_thresholds(timestamps, altered, valid)
        middle = (timestamps >= 3600) & (timestamps <= 6400)
        np.testing.assert_allclose(actual[middle], baseline[middle])

    def test_local_threshold_is_stable_across_regular_frame_rates(self):
        outputs = []
        for fps in (25, 50, 100):
            timestamps = np.arange(0, 5001, 1000 / fps).astype(int)
            values = timestamps / 1000
            thresholds = _local_activity_thresholds(timestamps, values, np.ones(len(values), dtype=bool))
            outputs.append(thresholds[np.argmin(abs(timestamps - 2500))])
        self.assertLess(max(outputs) - min(outputs), 0.04)


if __name__ == "__main__":
    unittest.main()
