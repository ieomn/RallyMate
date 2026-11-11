import unittest
from copy import deepcopy
import numpy as np

from rallymate_features.gs_features import joint_angle, summarize_series, measure_joint_kinematics
from tests.test_tennis_assessment import inputs, calculate


class PhaseKinematicsTests(unittest.TestCase):
    def test_known_angle_and_degenerate_bone(self):
        self.assertAlmostEqual(joint_angle([1, 0, 0], [0, 0, 0], [0, 1, 0]), 90)
        self.assertIsNone(joint_angle([0, 0, 0], [0, 0, 0], [0, 1, 0]))

    def test_angular_speed_uses_pts_and_never_bridges_missing_joint(self):
        result = summarize_series([0, 10, 30], [0, 100, 300], [0, 1, 2], unit="deg")
        self.assertEqual(result["angular_speed_p90_deg_s"], 100)
        result = summarize_series([0, None, 30, 40], [0, 100, 200, 300], [0, 1, 2, 3], unit="deg")
        self.assertEqual(result["source_frames"], [0, 2, 3])
        self.assertEqual(result["angular_speed_p90_deg_s"], 100)

    def test_all_observed_windows_publish_angles_and_ten_phase_candidates(self):
        report = calculate(*inputs())
        self.assertGreater(len(report["episodes"]), 0)
        for episode in report["episodes"]:
            evidence = episode["joint_kinematics"]
            self.assertEqual(len(evidence["phases"]), 10)
            for phase in evidence["phases"]:
                self.assertLessEqual(episode["start_ms"], phase["start_ms"])
                self.assertLessEqual(phase["start_ms"], phase["end_ms"])
                self.assertLessEqual(phase["end_ms"], episode["end_ms"])
                self.assertFalse(phase["physical_event_verified"])
                self.assertIn(phase["source_frame"], phase["metrics"]["shoulder_hip_separation_deg"]["source_frames"])
            self.assertEqual(evidence["metrics"]["right_elbow_flexion_deg"]["status"], "measured")

    def test_camera_rotation_and_scale_do_not_change_joint_angles(self):
        timeline, records = inputs()
        before = calculate(timeline, records)
        rotation = np.array([[0., 0., 1.], [0., 1., 0.], [-1., 0., 0.]])
        transformed = deepcopy(records)
        for i, row in enumerate(transformed):
            row["keypoints_3d"] = {k: (3 * rotation @ np.asarray(v) + np.array([i, 4., -2.])).tolist()
                                   for k, v in row["keypoints_3d"].items()}
        after = calculate(timeline, transformed)
        for a, b in zip(before["episodes"], after["episodes"]):
            self.assertEqual(a["joint_kinematics"]["metrics"], b["joint_kinematics"]["metrics"])

    def test_bad_clock_rejected(self):
        with self.assertRaises(ValueError):
            measure_joint_kinematics([{"time": 100}, {"time": 100}])

    def test_signed_angle_wrap_does_not_create_spurious_rotation(self):
        result = summarize_series([179, -179, -177], [0, 100, 200], [0, 1, 2], unit="deg", circular=True)
        self.assertAlmostEqual(result["angular_speed_p90_deg_s"], 20)
        self.assertAlmostEqual(result["range"], 4)

    def test_missing_joint_does_not_create_a_false_full_rotation(self):
        result = summarize_series([178, 179, None, -179, -178], [0, 33, 66, 99, 132], [0, 1, 2, 3, 4], unit="deg", circular=True)
        self.assertAlmostEqual(result["range"], 4)
        self.assertLess(result["angular_speed_p90_deg_s"], 31)


if __name__ == "__main__":
    unittest.main()
