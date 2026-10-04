from __future__ import annotations

import json
import unittest

from rallymate_scoring.rotation_analysis import ANALYSIS_VERSION, analyze_rotation
from tests.test_stroke_analysis import rotation_samples


def phase(name, start, end, status="measured"):
    return {"phase": name, "label_zh": name, "start_ms": start, "end_ms": end, "status": status}


class IndependentRotationWindowTests(unittest.TestCase):
    def test_local_windows_do_not_promote_fragmented_episode_summary(self):
        samples = rotation_samples()
        del samples[10].points["left_shoulder"]
        result = analyze_rotation(samples)
        self.assertIsNone(result["metrics"]["shoulder_line_change_deg"])
        self.assertEqual(result["metrics"]["hip_line_change_deg"], 20)
        shoulder_windows = [item for item in result["local_windows"] if item["axis"] == "shoulder"]
        self.assertEqual([(item["start_ms"], item["end_ms"]) for item in shoulder_windows], [(0, 450), (550, 1000)])
        self.assertEqual([item["metrics"]["shoulder_line_change_deg"] for item in shoulder_windows], [18, 18])
        for item in shoulder_windows:
            evidence = item["metric_evidence"]["shoulder_line_change_deg"]
            self.assertEqual(evidence["scope"], "continuous_local_window")
            self.assertEqual(evidence["source_version"], ANALYSIS_VERSION)
            self.assertFalse(evidence["is_3d_rotation"])
            self.assertEqual(evidence["coverage_fraction"], 1)
            self.assertLess(evidence["episode_sample_fraction"], .8)
        self.assertIsNone(result["score"])

    def test_phase_measurement_uses_only_corresponding_observed_window(self):
        samples = rotation_samples()
        del samples[10].points["left_shoulder"]
        result = analyze_rotation(samples, phases=[phase("preparation", 0, 450), phase("follow_through", 550, 1000)])
        self.assertIsNone(result["metrics"]["shoulder_line_change_deg"])
        for item in result["phase_measurements"]:
            self.assertEqual(item["status"], "measured_2d")
            self.assertEqual(item["metrics"]["shoulder_line_change_deg"], 18)
            self.assertEqual(item["metrics"]["hip_line_change_deg"], 9)
            self.assertFalse(item["anchor_is_contact"])

    def test_phase_bounds_do_not_hide_clipped_or_missing_samples(self):
        samples = rotation_samples()
        result = analyze_rotation(samples, phases=[phase("acceleration", 0, 2000)])
        phase_result = result["phase_measurements"][0]
        self.assertEqual(phase_result["status"], "unavailable")
        evidence = phase_result["metric_evidence"]["shoulder_line_change_deg"]
        self.assertEqual(evidence["time_coverage_fraction"], .5)
        self.assertIn("phase_coverage_insufficient", evidence["reason_codes"])
        del samples[10].points["left_shoulder"]
        result = analyze_rotation(samples, phases=[phase("acceleration", 0, 1000)])
        self.assertIsNone(result["phase_measurements"][0]["metrics"]["shoulder_line_change_deg"])
        self.assertEqual(result["phase_measurements"][0]["metrics"]["hip_line_change_deg"], 20)

    def test_short_and_unobserved_phases_keep_null_values(self):
        for entry in [phase("preparation", 0, 200), phase("preparation", 0, 500, "unavailable"),
                      phase("acceleration", 500, 200), phase("acceleration", False, 500),
                      phase("acceleration", 0, float("nan"))]:
            with self.subTest(entry=entry):
                result = analyze_rotation(rotation_samples(), phases=[entry])
                self.assertEqual(result["phase_measurements"][0]["status"], "unavailable")
                self.assertTrue(all(value is None for value in result["phase_measurements"][0]["metrics"].values()))
                json.dumps(result, allow_nan=False)

    def test_camera_epoch_and_normalization_changes_split_local_windows(self):
        for key in ("camera_reference_epoch", "normalization_basis"):
            with self.subTest(key=key):
                samples = rotation_samples()
                for sample in samples[:10]:
                    setattr(sample, key, 0 if key.endswith("epoch") else "bilateral_torso")
                for sample in samples[10:]:
                    setattr(sample, key, 1 if key.endswith("epoch") else "right_torso")
                result = analyze_rotation(samples)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["quality"]["temporal_break_count"], 1)
                self.assertEqual(result["local_measurement_status"], "measured_2d")
                self.assertFalse(any(item["start_ms"] < 500 < item["end_ms"] for item in result["local_windows"]))

    def test_local_window_keeps_minimum_duration_and_sample_count(self):
        for samples in (rotation_samples()[:6], rotation_samples([0, 20, 40, 60, 80, 100, 120])):
            result = analyze_rotation(samples)
            self.assertEqual(result["local_windows"], [])
            self.assertEqual(result["local_measurement_status"], "unavailable")

    def test_reasons_distinguish_missing_axis_from_foreshortening(self):
        samples = rotation_samples()
        for sample in samples:
            sample.points.pop("left_shoulder")
        result = analyze_rotation(samples)
        self.assertEqual(result["metrics"]["hip_line_change_deg"], 20)
        self.assertIn("axis_points_missing", result["metric_evidence"]["shoulder_line_change_deg"]["reason_codes"])
        samples = rotation_samples()
        for sample in samples:
            sample.points["left_shoulder"] = sample.points["right_shoulder"]
        result = analyze_rotation(samples)
        self.assertIn("axis_projection_too_short", result["metric_evidence"]["shoulder_line_change_deg"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
