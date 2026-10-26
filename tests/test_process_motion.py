from __future__ import annotations

import json
import math
import unittest

from rallymate_scoring.process_motion import VERSION, analyze_motion_transition, analyze_process_motion
from rallymate_scoring.stroke_candidates import Sample


def samples(*, linear=False):
    result = []
    for i, time in enumerate(range(0, 1501, 50)):
        shoulder = math.radians(5 + 45 * (1 + math.erf((time - 650) / 180)) / 2)
        hip = math.radians(5 + 30 * (1 + math.erf((time - 450) / 180)) / 2)
        wrist_x = time / 1000 if linear else -0.8 + 1.6 * (1 + math.erf((time - 850) / 250)) / 2
        points = {"right_wrist": (wrist_x, 0.5), "left_wrist": (-0.9, 0.5)}
        for joint, angle, radius, y in (("shoulder", shoulder, .3, 0), ("hip", hip, .25, 1)):
            for side, sign in (("left", -1), ("right", 1)):
                points[f"{side}_{joint}"] = (sign * radius * math.cos(angle), y + sign * radius * math.sin(angle))
        result.append(Sample(time, i, 1, (300, 250), 100, points, {"right"}))
    return result


def phases():
    return [{"phase": name, "status": "measured", "start_ms": a, "end_ms": b}
            for name, a, b in (("preparation", 0, 400), ("acceleration", 400, 850), ("follow_through", 850, 1500))]


class ProcessMotionTests(unittest.TestCase):
    def test_known_trajectory_measures_signed_direction_phase_timing_and_peak_order(self):
        result = analyze_process_motion(samples(), phases(), "right")
        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["status"], "measured_2d")
        self.assertEqual([row["duration_ms"] for row in result["phases"]], [400, 450, 650])
        self.assertEqual(result["phases"][1]["wrist"]["speed_trend"], "increasing")
        self.assertEqual(result["phases"][2]["wrist"]["speed_trend"], "decreasing")
        self.assertEqual(result["direction"]["direction_angle_deg"], 0)
        self.assertEqual(result["direction"]["dy_torso"], 0)
        self.assertEqual(result["direction"]["direction_consistency_ratio"], 1)
        self.assertEqual(result["direction"]["reversal_count"], 0)
        self.assertEqual(result["sequence"]["hip_peak_ms"], 450)
        self.assertEqual(result["sequence"]["shoulder_peak_ms"], 650)
        self.assertEqual(result["sequence"]["wrist_peak_ms"], 850)
        self.assertEqual(result["sequence"]["hip_to_shoulder_ms"], 200)
        self.assertTrue(result["sequence"]["ordering_resolved"])
        self.assertLess(result["follow_through"]["end_to_peak_speed_ratio"], .02)
        self.assertGreater(result["phases"][1]["wrist"]["speed_increase_fraction"], .95)
        self.assertGreater(result["phases"][2]["wrist"]["speed_decrease_fraction"], .95)
        self.assertIsNone(result["score"])
        self.assertFalse(result["is_formal_score"])
        json.dumps(result, allow_nan=False)

    def test_mirroring_flips_screen_direction_but_preserves_motion_timing_and_consistency(self):
        original = analyze_process_motion(samples(), phases(), "right")
        mirrored = samples()
        for row in mirrored:
            row.points = {name: (-x, y) for name, (x, y) in row.points.items()}
        result = analyze_process_motion(mirrored, phases(), "right")
        self.assertEqual(result["direction"]["dx_torso"], -original["direction"]["dx_torso"])
        self.assertEqual(abs(result["direction"]["direction_angle_deg"]), 180)
        self.assertEqual(result["sequence"], original["sequence"])
        self.assertEqual(result["direction"]["direction_consistency_ratio"], original["direction"]["direction_consistency_ratio"])

    def test_missing_or_low_confidence_wrist_only_blocks_its_phase_without_interpolation(self):
        for mode in ("missing", "nan", "confidence", "invalid_confidence"):
            data = samples()
            if mode == "missing":
                del data[10].points["right_wrist"]
            elif mode == "nan":
                data[10].points["right_wrist"] = (float("nan"), .5)
            elif mode == "confidence":
                data[10].point_confidences["right_wrist"] = .2
            else:
                data[10].point_confidences["right_wrist"] = 1.2
            result = analyze_process_motion(data, phases(), "right")
            self.assertEqual(result["status"], "partial", mode)
            self.assertIsNone(result["direction"]["dx_torso"])
            self.assertIsNone(result["follow_through"]["end_to_peak_speed_ratio"])
            self.assertEqual([row["status"] for row in result["phases"]], ["measured", "unavailable", "measured"])
            self.assertEqual(result["phases"][2]["duration_ms"], 650)
            self.assertEqual(result["sequence"]["hip_peak_ms"], 450)
            self.assertEqual(result["sequence"]["shoulder_peak_ms"], 650)
            self.assertEqual(result["sequence"]["hip_to_shoulder_ms"], 200)
            self.assertEqual(result["sequence"]["status"], "partial")
            self.assertFalse(result["sequence"]["ordering_resolved"])
            self.assertIsNone(result["sequence"]["wrist_peak_ms"])
            self.assertEqual(result["sequence"]["order"], [])
            self.assertIn("wrist_missing_or_low_confidence", result["follow_through"]["reason_codes"])
            json.dumps(result, allow_nan=False)

    def test_preparation_occlusion_preserves_acceleration_direction_and_follow_through(self):
        data = samples()
        del data[3].points["right_wrist"]
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual([row["status"] for row in result["phases"]], ["unavailable", "measured", "measured"])
        self.assertEqual(result["direction"]["direction_consistency_ratio"], 1)
        # Whole-episode peak ratio remains unavailable; it cannot be estimated
        # from a subset and silently use a different denominator.
        self.assertIsNone(result["follow_through"]["end_to_peak_speed_ratio"])

    def test_missing_shared_phase_boundary_blocks_both_dependent_phases(self):
        data = samples()
        del data[8].points["right_wrist"]
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual([row["status"] for row in result["phases"]], ["unavailable", "unavailable", "measured"])

    def test_reference_identity_frame_and_time_discontinuities_block_full_episode(self):
        for mode in ("track", "selection_epoch", "camera_reference_epoch", "normalization_basis", "camera_compensated", "frame", "time", "missing_sample", "scale"):
            data = samples()
            if mode in {"track", "selection_epoch", "camera_reference_epoch"}:
                setattr(data[10], mode, 7)
            elif mode == "normalization_basis":
                data[10].normalization_basis = "right_shoulder_right_torso"
            elif mode == "camera_compensated":
                data[10].camera_compensated = True
            elif mode == "frame":
                for row in data[10:]:
                    row.frame_index += 1
            elif mode == "time":
                data[10].time = data[9].time
            elif mode == "missing_sample":
                del data[10]
            else:
                data[10].scale *= 2
            result = analyze_process_motion(data, phases(), "right")
            self.assertEqual(result["status"], "unavailable", mode)

    def test_missing_axis_only_blocks_peak_sequence_and_not_wrist_process(self):
        data = samples()
        del data[10].points["left_hip"]
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["direction"]["status"], "measured")
        self.assertEqual(result["sequence"]["status"], "partial")
        self.assertIsNone(result["sequence"]["hip_peak_ms"])
        self.assertEqual(result["sequence"]["order"], [])

    def test_constant_motion_end_speed_is_observed_and_not_manufactured_zero(self):
        result = analyze_process_motion(samples(linear=True), phases(), "right")
        self.assertEqual(result["follow_through"]["end_to_peak_speed_ratio"], 1)
        self.assertEqual(result["phases"][2]["wrist"]["end_speed_torso_per_s"], 1)
        self.assertEqual(result["phases"][2]["wrist"]["speed_trend"], "steady")
        self.assertIsNone(result["phases"][2]["wrist"]["speed_decrease_fraction"])
        self.assertEqual(result["sequence"]["status"], "partial")
        self.assertEqual(result["sequence"]["hip_to_shoulder_ms"], 200)
        self.assertFalse(result["sequence"]["ordering_resolved"])
        self.assertIn("ambiguous_flat_peak", result["sequence"]["reason_codes"])

    def test_static_wrist_is_measured_zero_but_has_no_direction_or_peak_order(self):
        data = samples()
        for row in data:
            row.points["right_wrist"] = (.9, .5)
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual(result["phases"][1]["wrist"]["path_length_torso"], 0)
        self.assertIsNone(result["direction"]["direction_angle_deg"])
        self.assertIsNone(result["direction"]["direction_consistency_ratio"])
        self.assertIsNone(result["follow_through"]["end_to_peak_speed_ratio"])

    def test_partial_phase_remains_null_and_does_not_hide_other_phase_measurements(self):
        stages = phases()
        stages[-1].update(status="unavailable", start_ms=None, end_ms=None)
        result = analyze_process_motion(samples(), stages, "right")
        self.assertEqual(result["phases"][1]["status"], "measured")
        self.assertEqual(result["phases"][2]["status"], "unavailable")
        self.assertIsNone(result["follow_through"]["end_to_peak_speed_ratio"])

    def test_unobserved_and_overlapping_phase_bounds_do_not_produce_measurements(self):
        for mode in ("unobserved", "overlap"):
            stages = phases()
            stages[1]["start_ms"] = 425 if mode == "unobserved" else 350
            result = analyze_process_motion(samples(), stages, "right")
            self.assertEqual(result["phases"][1]["status"], "unavailable", mode)
            self.assertIsNone(result["direction"]["direction_consistency_ratio"])

    def test_empty_short_invalid_side_and_fallback_timestamp_are_serializable_unavailable(self):
        fallback = samples()
        fallback[10].timestamp_source = "fps_fallback"
        for data, side in (([], "right"), (samples()[:3], "right"), (samples(), "other"), (fallback, "right")):
            result = analyze_process_motion(data, phases(), side)
            self.assertEqual(result["status"], "unavailable")
            json.dumps(result, allow_nan=False)

    def test_reversing_path_has_lower_consistency_and_observed_reversals(self):
        data = samples()
        xs = [0, .2, .4, .6, .4, .2, 0, .2, .4, .6]
        for index, x in enumerate(xs, 8):
            data[index].points["right_wrist"] = (x, .5)
        result = analyze_process_motion(data, phases(), "right")
        self.assertLess(result["direction"]["direction_consistency_ratio"], .8)
        self.assertGreaterEqual(result["direction"]["reversal_count"], 2)

    def test_tied_peaks_do_not_claim_resolved_order(self):
        data = samples()
        for row in data:
            angle = math.radians(5 + 30 * (1 + math.erf((row.time - 650) / 180)) / 2)
            for side, sign in (("left", -1), ("right", 1)):
                row.points[f"{side}_hip"] = (sign * .25 * math.cos(angle), 1 + sign * .25 * math.sin(angle))
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual(result["sequence"]["status"], "measured")
        self.assertEqual(result["sequence"]["hip_to_shoulder_ms"], 0)
        self.assertFalse(result["sequence"]["ordering_resolved"])

    def test_transition_uses_intervening_frames_and_rejects_same_track_with_missing_evidence(self):
        previous = {"episode_id": "before", "end_ms": 400, "person_track_id": 1}
        current = {"episode_id": "after", "start_ms": 850, "person_track_id": 1, "racket_hand": "right"}
        observed = analyze_motion_transition(previous, current, samples())
        self.assertEqual(observed["status"], "measured")
        self.assertEqual(observed["interval_ms"], 450)
        for mode in ("missing_frame", "identity_reset", "camera_reset", "bound_missing"):
            data = samples()
            if mode == "missing_frame":
                del data[12]
            elif mode == "identity_reset":
                data[12].selection_epoch = 1
            elif mode == "camera_reset":
                data[12].camera_reference_epoch = 1
            else:
                del data[8]
            unavailable = analyze_motion_transition(previous, current, data)
            self.assertEqual(unavailable["status"], "unavailable", mode)
            self.assertIsNone(unavailable["interval_ms"])

    def test_transition_rejects_overlap_and_a_different_player(self):
        previous = {"episode_id": "before", "end_ms": 400, "person_track_id": 1}
        for current in ({"start_ms": 400, "person_track_id": 1, "racket_hand": "right"},
                        {"start_ms": 850, "person_track_id": 2, "racket_hand": "right"}):
            result = analyze_motion_transition(previous, current, samples())
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["interval_ms"])

    def test_transition_time_does_not_require_visible_wrists(self):
        previous = {"episode_id": "before", "end_ms": 400, "person_track_id": 1}
        current = {"episode_id": "after", "start_ms": 850, "person_track_id": 1, "racket_hand": "right"}
        data = samples()
        for row in data[9:17]:
            del row.points["right_wrist"]
        result = analyze_motion_transition(previous, current, data)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["interval_ms"], 450)

    def test_multiple_separate_peaks_remain_ambiguous(self):
        data = samples()
        for row in data:
            row.points["right_wrist"] = (sum(.7 * (1 + math.erf((row.time - center) / 100)) / 2 for center in (450, 1000)), .5)
        result = analyze_process_motion(data, phases(), "right")
        self.assertIsNone(result["sequence"]["wrist_peak_ms"])
        self.assertIn("ambiguous_multiple_or_broad_peaks", result["sequence"]["reason_codes"])

    def test_extreme_invalid_numbers_fail_closed_without_overflow(self):
        data = samples()
        data[10].points["right_wrist"] = (10 ** 1000, .5)
        result = analyze_process_motion(data, phases(), "right")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["phases"][1]["status"], "unavailable")
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
