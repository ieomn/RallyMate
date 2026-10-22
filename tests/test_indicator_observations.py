from __future__ import annotations

import json
import math
import unittest

from rallymate_scoring.indicator_observations import analyze_indicator_observations
from rallymate_scoring.stroke_candidates import Sample


def samples(n=11):
    rows = []
    for i in range(n):
        t = i / (n - 1)
        points = {
            "left_shoulder": (-.3, 0), "right_shoulder": (.3, 0),
            "left_hip": (-.25, 1), "right_hip": (.25, 1),
            "left_knee": (-.4, 1.6), "right_knee": (.4, 1.6),
            "left_ankle": (-.5, 2), "right_ankle": (.5, 2),
            "left_elbow": (-.6, .3), "right_elbow": (.6, .3),
            "left_wrist": (-.9 + .2 * t, .5), "right_wrist": (.9 - .2 * t, .5),
            "nose": (0, -.3),
        }
        rows.append(Sample(i * 50, i, 1, (200, 100), 100, points, {"right"}))
    return rows


def run(data):
    return analyze_indicator_observations(data, [{"phase": "preparation", "status": "measured", "start_ms": 0,
                                                "end_ms": (len(data) - 1) * 50}], "right")["phases"][0]["measurements"]


class IndicatorObservationTests(unittest.TestCase):
    def test_known_geometry_and_observed_change(self):
        result = run(samples())
        self.assertEqual(result["ankle_distance_over_hip_width"]["median"], 2)
        self.assertEqual(result["hip_inside_ankle_support_fraction"]["mean"], 1)
        self.assertEqual(result["hip_to_ankle_midpoint_torso"]["median"], 0)
        self.assertEqual(result["trunk_tilt_deg"]["median"], 0)
        self.assertAlmostEqual(result["wrist_separation_torso"]["delta"], -.4)
        self.assertEqual(len(result), 13)
        self.assertTrue(all(row["status"] == "measured" for row in result.values()))
        json.dumps(result, allow_nan=False)

    def test_occluded_wrist_never_discards_knee_and_never_bridges_delta(self):
        data = samples()
        del data[5].points["right_wrist"]
        result = run(data)
        wrist = result["wrist_separation_torso"]
        self.assertEqual(wrist["status"], "partial")
        self.assertIsNone(wrist["delta"])
        self.assertEqual([(w["start_ms"], w["end_ms"]) for w in wrist["windows"]], [(0, 200), (300, 500)])
        self.assertEqual(result["right_knee_flexion_deg"]["status"], "measured")
        self.assertEqual(result["shoulder_axis_deg"]["status"], "measured")

    def test_seventy_percent_is_joint_specific_strictly_less_than(self):
        for missing in (3, 4):
            data = samples(10)
            for row in data[-missing:]:
                del row.points["right_wrist"]
            result = run(data)["wrist_separation_torso"]
            self.assertEqual(result["coverage"], (10 - missing) / 10)
            self.assertEqual("required_joint_coverage_below_70_percent" in result["reason_codes"], missing == 4)
            self.assertEqual(result["median"] is None, missing == 4)
            self.assertIsNone(result["delta"])

    def test_identity_reference_and_frame_gaps_split_local_windows(self):
        for mode in ("track", "camera_reference_epoch", "frame"):
            data = samples()
            for row in data[5:]:
                if mode == "frame":
                    row.frame_index += 5
                else:
                    setattr(row, mode, 2)
            result = run(data)["left_knee_flexion_deg"]
            self.assertEqual(result["status"], "partial", mode)
            self.assertIsNone(result["delta"])
            self.assertEqual(len(result["windows"]), 2)
            if mode != "frame":
                self.assertIsNone(result["median"], mode)
                self.assertIsNone(result["mean"], mode)
                self.assertNotEqual(result["windows"][0]["reference"], result["windows"][1]["reference"])
                self.assertIn("subject_or_coordinate_reference_changed_no_phase_aggregate", result["reason_codes"])

    def test_changes_of_normalization_or_selection_do_not_mix_scalar_values(self):
        for mode in ("normalization_basis", "selection_epoch", "camera_compensated"):
            data = samples()
            for row in data[5:]:
                setattr(row, mode, "left_shoulder_left_torso" if mode == "normalization_basis" else 1)
                row.points["right_wrist"] = (1.5, .5)
            result = run(data)["wrist_separation_torso"]
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["median"])
            self.assertIsNone(result["range"])
            self.assertEqual(len(result["windows"]), 2)

    def test_mirror_preserves_width_angles_and_support(self):
        original, mirrored = samples(), samples()
        for row in mirrored:
            row.points = {k: (-x, y) for k, (x, y) in row.points.items()}
        first, second = run(original), run(mirrored)
        for key in ("ankle_distance_over_hip_width", "left_knee_flexion_deg", "trunk_tilt_deg", "hip_inside_ankle_support_fraction"):
            self.assertEqual(first[key]["median"], second[key]["median"])

    def test_axis_angle_unwraps_without_inventing_180_degree_rotation(self):
        data = samples()
        for i, row in enumerate(data):
            angle = math.radians(85 + i)
            row.points["left_shoulder"] = (-.4 * math.cos(angle), -.4 * math.sin(angle))
            row.points["right_shoulder"] = (.4 * math.cos(angle), .4 * math.sin(angle))
        axis = run(data)["shoulder_axis_deg"]
        self.assertEqual(axis["delta"], 10)
        self.assertEqual(axis["range"], 10)

    def test_degenerate_projection_nonfinite_and_invalid_confidence_are_rejected_locally(self):
        for mode in ("width", "nan", "confidence"):
            data = samples()
            for row in data:
                if mode == "width":
                    row.points["right_hip"] = (-.25, 1.01)
                elif mode == "nan":
                    row.points["right_ankle"] = (float("nan"), 2)
                else:
                    row.point_confidences["right_ankle"] = 1.5
            result = run(data)
            self.assertEqual(result["ankle_distance_over_hip_width"]["status"], "unavailable")
            self.assertEqual(result["wrist_separation_torso"]["status"], "measured")
            json.dumps(result, allow_nan=False)

    def test_unobserved_phases_stay_explicit(self):
        result = analyze_indicator_observations(samples(), [], "right")
        self.assertTrue(all(row["status"] == "unavailable" for p in result["phases"] for row in p["measurements"].values()))
        self.assertTrue(all(row["reason_codes"] == ["phase_not_observed"] for p in result["phases"] for row in p["measurements"].values()))


if __name__ == "__main__":
    unittest.main()
