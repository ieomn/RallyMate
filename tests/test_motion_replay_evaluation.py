from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from rallymate_vision.motion_replay import build_motion_replay
from scripts.evaluate_motion_replay import compare_to_reference
from tests.test_motion_replay import rewrite, write_source


class MotionReplayEvaluationTests(unittest.TestCase):
    def fixture(self, directory):
        frames, primary, _, _ = write_source(directory)
        trajectory = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
        width, height = trajectory["render"]["width"], trajectory["render"]["height"]
        extracted = {"frames": [{"timestamp_ms": frame["output_timestamp_ms"],
                                  "timestamp_source": "decoder_pts", "width": width, "height": height,
                                  "keypoints": [{"name": point["name"], "x": point["x_px"] / width,
                                                 "y": point["y_px"] / height, "confidence": .9}
                                                for point in frame["keypoints"]]}
                                 for frame in trajectory["frames"]]}
        return trajectory, extracted

    def test_fixed_canvas_displacement_is_measured_without_fitting_it_away(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference, observed = self.fixture(Path(temporary))
            exact = compare_to_reference(reference, observed)
            self.assertAlmostEqual(exact["joint_error_body"]["max"], 0, places=10)
            self.assertEqual(exact["joint_coverage_fraction"], 1)
            for frame in observed["frames"]:
                for point in frame["keypoints"]:
                    point["x"] += 20 / reference["render"]["width"]
            shifted = compare_to_reference(reference, observed)
            body_px = reference["render"]["body_scale_source_px"] * reference["render"]["transform"]["scale"]
            self.assertAlmostEqual(shifted["joint_error_body"]["mean"], 20 / body_px)
            self.assertFalse(shifted["approved"])

    def test_timing_shift_and_extra_tail_are_not_normalized_away(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference, observed = self.fixture(Path(temporary))
            for frame in observed["frames"]:
                frame["timestamp_ms"] += 500
            result = compare_to_reference(reference, observed)
            self.assertGreater(result["joint_error_body"]["mean"], .01)
            self.assertEqual(result["uncompared_sample_count"], 12)
            incompatible = copy.deepcopy(observed)
            for frame in incompatible["frames"]:
                frame["height"] *= 2
            rejected = compare_to_reference(reference, incompatible)
            self.assertEqual(rejected["joint_coverage_fraction"], 0)
            self.assertIsNone(rejected["joint_error_body"]["mean"])
            self.assertGreater(rejected["aspect_mismatch_frame_count"], 0)

    def test_short_gap_evaluation_uses_the_joints_encoded_in_reference_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, rows, _ = write_source(Path(temporary))
            next(point for point in rows[25]["poses"][0]["keypoints"]
                 if point["name"] == "right_wrist")["confidence"] = .1
            rewrite(frames, rows)
            trajectory = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            self.assertTrue(any(point.get("source") == "interpolated"
                                for frame in trajectory["display_frames"] for point in frame["keypoints"]))
            width, height = trajectory["render"]["width"], trajectory["render"]["height"]
            detected = {"frames": [{"timestamp_ms": frame["output_timestamp_ms"],
                "timestamp_source": "decoder_pts", "width": width, "height": height,
                "keypoints": [{"name": point["name"], "x": point["x_px"] / width,
                    "y": point["y_px"] / height, "confidence": .9} for point in frame["keypoints"]]}
                for frame in trajectory["display_frames"]]}
            result = compare_to_reference(trajectory, detected)
            self.assertEqual(result["reference_frame_source"], "display_frames")
            self.assertEqual(result["expected_joint_observation_count"], 12 * 48)
            self.assertEqual(result["matched_joint_observation_count"], 12 * 48)
            self.assertAlmostEqual(result["joint_error_body"]["max"], 0, places=10)
            self.assertEqual(trajectory["quality"]["missing_core_joint_frames"]["right_wrist"], 1)

    def test_legacy_trajectory_without_display_frames_keeps_original_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference, detected = self.fixture(Path(temporary))
            reference.pop("display_frames", None)
            result = compare_to_reference(reference, detected)
            self.assertEqual(result["reference_frame_source"], "frames")
            self.assertAlmostEqual(result["joint_error_body"]["max"], 0, places=10)


if __name__ == "__main__":
    unittest.main()
