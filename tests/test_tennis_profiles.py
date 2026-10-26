"""Serve profile behavior checks independent of the action detector."""
from copy import deepcopy
import unittest

import numpy as np

from rallymate_service.tennis_profiles import apply_serve_profile, SERVE_METRICS, SERVE_RUBRIC


def serve_rows():
    rows = []
    for i in range(41):
        preparation = i < 14
        release = float(np.clip((i - 14) / 12, 0, 1))
        finish = float(np.clip((i - 26) / 14, 0, 1))
        elbow = np.array([.8 - .3 * release, 1 + .65 * release - .5 * finish, 0.])
        wrist = np.array([.9 - .35 * release - .7 * finish, .8 + 1.4 * release - 1.3 * finish, -.4 + .6 * release + .3 * finish])
        shoulder = np.array([.4, 1., 0.])
        knee = float(55 - 35 * np.clip((i - 10) / 16, 0, 1) + 10 * finish)
        local = {"right_shoulder": shoulder, "right_elbow": elbow, "right_wrist": wrist,
                 "left_shoulder": np.array([-.4, 1., 0.]), "left_elbow": np.array([-.5, 1.6, 0.]),
                 "left_wrist": np.array([-.5, 2.1 if preparation else 1.8 - finish, 0.])}
        reach = float(np.linalg.norm(wrist - shoulder) / (np.linalg.norm(elbow - shoulder) + np.linalg.norm(wrist - elbow)))
        rows.append({"time": float(i * 1000 / 30), "frame": i, "torso": 1., "local": local,
                     "knee": knee, "left_knee_flexion": knee, "right_knee_flexion": knee,
                     "right_reach": reach, "left_reach": .98, "support": .08})
    return rows


def calculate(rows, side="right", recovery_continuity=(.1, 100.)):
    wrist = np.asarray([row["local"].get(side + "_wrist", [np.nan] * 3) for row in rows])
    return apply_serve_profile(rows, prep_end=14, peak=26, side=side, wrist=wrist,
                              recovery_continuity=recovery_continuity,
                              values={"coil": (30., 100.), "coil_release": (.8, 100.),
                                      "preparation_sync": (0., 100.), "head_control": (0., 100.),
                                      "arm_space": (.98, 100.), "release_sequence": (0., 100.),
                                      "support_control": (.08, 100.), "follow_path": (.8, 100.),
                                      "knee_load": (55., 100.), "recovery_control": (0., 100.),
                                      "leg_extension_continuity": (.8, 80.), "follow_deceleration": (.9, 90.)})


class TennisServeProfileTests(unittest.TestCase):
    def test_serve_replaces_baseline_rotation_rules_and_has_five_dimensions(self):
        result = calculate(serve_rows())
        expected = {metric[0] for metrics in SERVE_METRICS.values() for metric in metrics}
        self.assertEqual(set(result), expected)
        self.assertEqual(len(result), 10)
        self.assertEqual(result["leg_extension_continuity"], (.8, 80.))
        self.assertNotIn("follow_deceleration", result)
        self.assertNotIn("coil", result)
        self.assertNotIn("coil_release", result)
        for value, score in result.values():
            self.assertTrue(np.isfinite(value))
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)
        self.assertFalse(SERVE_RUBRIC["ball_toss_quality_assumed"])
        self.assertNotIn("support_control", expected)
        self.assertFalse(SERVE_RUBRIC["stance_width_graded"])

    def test_deep_loading_does_not_receive_an_upper_angle_penalty(self):
        normal, deep = serve_rows(), serve_rows()
        for row in deep[:14]:
            row.update(knee=95., left_knee_flexion=95., right_knee_flexion=95.)
        self.assertEqual(calculate(normal)["knee_load"][1], 100.)
        self.assertEqual(calculate(deep)["knee_load"], (95., 100.))

    def test_loading_integrates_whole_preparation_instead_of_one_best_posture(self):
        rows = serve_rows()
        for i, row in enumerate(rows[:14]):
            knee = 45. if i < 7 else 0.
            row.update(knee=knee, left_knee_flexion=knee, right_knee_flexion=knee)
        value, score = calculate(rows)["knee_load"]
        self.assertAlmostEqual(value, 22.5)
        self.assertAlmostEqual(score, 50.)

    def test_serve_stance_width_and_torso_up_projection_do_not_change_scores(self):
        normal, varied = serve_rows(), serve_rows()
        for i, row in enumerate(varied):
            row["support"] = 20. if i % 2 else None
            row["local"]["left_ankle"] = np.array([-10., -1., 0.])
            row["local"]["right_ankle"] = np.array([10., -1., 0.])
        self.assertEqual(calculate(normal), calculate(varied))

    def test_cramped_trophy_scores_below_open_upper_arm(self):
        normal = serve_rows()
        cramped = deepcopy(normal)
        for row in cramped[:14]:
            row["local"]["right_elbow"] = row["local"]["right_shoulder"] + [0., -.4, 0.]
        self.assertLess(calculate(cramped)["serve_trophy"][1], calculate(normal)["serve_trophy"][1])
        self.assertLess(calculate(cramped)["serve_arm_coordination"][1], calculate(normal)["serve_arm_coordination"][1])

    def test_raising_support_arm_higher_does_not_keep_adding_points(self):
        normal, excessive = serve_rows(), serve_rows()
        for row in excessive[:14]:
            row["local"]["left_wrist"] = np.array([-.5, 4., 0.])
        self.assertEqual(calculate(normal)["serve_arm_coordination"][1], calculate(excessive)["serve_arm_coordination"][1])

    def test_arm_only_action_does_not_receive_leg_extension_credit(self):
        rows = serve_rows()
        normal = calculate(rows)
        for row in rows:
            row.update(knee=55., left_knee_flexion=55., right_knee_flexion=55.)
        actual = calculate(rows)
        self.assertLess(actual["serve_extension"][1], normal["serve_extension"][1])
        self.assertEqual(actual["serve_extension_sequence"][1], 0.)

    def test_lower_body_release_after_arm_rise_reduces_sequence_score(self):
        rows = serve_rows()
        normal = calculate(rows)
        for i, row in enumerate(rows):
            knee = float(55 - 35 * np.clip((i - 22) / 4, 0, 1))
            row.update(knee=knee, left_knee_flexion=knee, right_knee_flexion=knee)
        delayed = calculate(rows)
        self.assertEqual(delayed["serve_extension"][1], normal["serve_extension"][1])
        self.assertLess(delayed["serve_extension_sequence"][1], normal["serve_extension_sequence"][1])

    def test_missing_support_arm_removes_only_unmeasured_coordination(self):
        rows = serve_rows()
        for row in rows[:14]:
            row["local"]["left_wrist"] = None
        result = calculate(rows)
        self.assertNotIn("serve_arm_coordination", result)
        self.assertIn("serve_trophy", result)
        self.assertIn("serve_overhead_position", result)

    def test_different_visible_legs_can_measure_load_but_cannot_fake_extension(self):
        rows = serve_rows()
        for i, row in enumerate(rows):
            row.update(knee=None, left_knee_flexion=55. if i < 14 else None,
                       right_knee_flexion=10. if i >= 14 else None)
        actual = calculate(rows)
        self.assertEqual(actual["knee_load"], (55., 100.))
        self.assertNotIn("serve_extension", actual)
        self.assertNotIn("serve_extension_sequence", actual)
        self.assertIn("serve_recovery_control", actual)

    def test_missing_leg_points_do_not_reuse_old_scores(self):
        rows = serve_rows()
        for row in rows:
            row.update(knee=None, left_knee_flexion=None, right_knee_flexion=None)
        actual = calculate(rows)
        for metric in ("knee_load", "serve_extension", "serve_extension_sequence", "serve_recovery_control"):
            self.assertNotIn(metric, actual)

    def test_recovery_requires_measured_continuity_and_has_saturated_readiness(self):
        rows = serve_rows()
        self.assertEqual(calculate(rows)["serve_recovery_control"], (.1, 100.))
        self.assertEqual(calculate(rows, recovery_continuity=(.7, 20.))["serve_recovery_control"], (.7, 60.))
        for row in rows[34:]:
            row.update(knee=90., left_knee_flexion=90., right_knee_flexion=90.)
        self.assertEqual(calculate(rows)["serve_recovery_control"], (.1, 100.))
        for invalid in (None, (float("nan"), 100.), (.1, 101.), (-1., 100.)):
            self.assertNotIn("serve_recovery_control", calculate(rows, recovery_continuity=invalid))

    def test_core_hand_below_shoulder_is_not_overhead_position(self):
        rows = serve_rows()
        normal = calculate(rows)
        for row in rows[24:29]:
            row["local"]["right_wrist"][1] = .8
        self.assertLess(calculate(rows)["serve_overhead_position"][1], normal["serve_overhead_position"][1])

    def test_left_handed_mirror_preserves_serve_scores(self):
        normal = serve_rows()
        mirrored = deepcopy(normal)
        for row in mirrored:
            original = row["local"]
            row["local"] = {("right" if name.startswith("left") else "left") + name[name.index("_"):]:
                            np.array([-point[0], point[1], point[2]]) for name, point in original.items()}
            row["left_reach"], row["right_reach"] = row["right_reach"], row["left_reach"]
        self.assertEqual(calculate(normal), calculate(mirrored, "left"))

    def test_inputs_are_unchanged_and_invalid_times_do_not_get_credit(self):
        rows = serve_rows()
        original = deepcopy(rows)
        calculate(rows)
        for before, after in zip(original, rows):
            self.assertEqual(before["knee"], after["knee"])
            for name in before["local"]:
                np.testing.assert_equal(before["local"][name], after["local"][name])
        rows[20]["time"] = rows[19]["time"]
        self.assertEqual(calculate(rows), {})


if __name__ == "__main__":
    unittest.main()
