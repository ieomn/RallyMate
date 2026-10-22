"""Synthetic known trajectories exercise math; none is real step ground truth."""
from copy import deepcopy
import math
import unittest

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_scoring.step_motion_evidence import (
    ANNOTATION_SEMANTICS, ANNOTATION_VERSION, REQUIRED_JOINTS, SUPPORTED_CARDS,
    compute_step_motion_evidence, validate_step_motion_observation,
)
from tests.test_card_evaluation import fixture as base_fixture


def fixture(identifier="FS08-M02", progress=None, direction=(0, 1), left_progress=None):
    progress = [0, 0, 0, .2, .4, .6, .8, .8, .8, .8] if progress is None else progress
    _, samples, window, timeline = base_fixture(len(progress))
    window.update(event_code=identifier.split("-")[0], stage_code=identifier)
    for index, sample in enumerate(samples):
        sample.reference_points = {name: (x*100, y*100) for name, (x, y) in sample.points.items()}
        for side, amount in (("right", progress[index]), ("left", (left_progress or [0]*len(progress))[index])):
            x, y = sample.reference_points[side+"_ankle"]
            sample.reference_points[side+"_ankle"] = (x+100*amount*direction[0], y+100*amount*direction[1])
    window["step_motion_observation"] = {"version": ANNOTATION_VERSION,
        "method": "manual_visual_observation", "moving_foot": "right",
        "source_description": "Synthetic contract trajectory; not a real visual annotation",
        "source_frames": [{key: timeline[3][key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")}],
        "semantics": ANNOTATION_SEMANTICS}
    return get_card_rule(identifier), samples, window, timeline


def evidence(arguments):
    return compute_step_motion_evidence(*arguments[1:])


class StepMotionEvidenceTests(unittest.TestCase):
    def test_pure_vertical_and_diagonal_movements_use_2d_distance_and_unchanged_four_card_formulas(self):
        for direction in ((0, 1), (1, 1)):
            for card in SUPPORTED_CARDS:
                with self.subTest(direction=direction, card=card):
                    args = fixture(card, direction=direction)
                    before = deepcopy(args)
                    raw = evidence(args)
                    row = evaluate_card_window(*args)
                    self.assertEqual(args, before)
                    self.assertTrue(raw["complete_visual_motion_cycle"])
                    self.assertEqual(raw["required_joint_names"], list(REQUIRED_JOINTS))
                    self.assertEqual(raw["endpoint_count"], 3)
                    self.assertEqual([p["duration_ms"] for p in raw["feet"]["right"]["phases"]], [80, 160, 120])
                    self.assertEqual(row["status"], "proxy_scored")
                    self.assertEqual(row["proxy_grade"], "A")
                    self.assertIsNone(row["original_grade"])
                    self.assertFalse(raw["contact_verified"])
                    self.assertFalse(raw["target_direction_verified"])
                    expected = 0 if card == "FS08-M03" else .8*math.hypot(*direction)
                    self.assertAlmostEqual(row["conditions"][0]["value"], expected)

    def test_stationary_out_and_back_truncated_double_movement_and_jump_are_not_steps(self):
        trajectories = ([0]*10, [0, 0, 0, .2, .4, .2, 0, 0, 0, 0],
            [0, .2, .4, .6, .8, .8, .8, .8, .8, .8],
            [0, 0, 0, 0, .2, .4, .6, .8, 1, 1.2],
            [0, 0, 0, .2, .4, .4, .6, .8, .8, .8],
            [0, 0, 0, .8, .8, .8, .8, .8, .8, .8])
        for progress in trajectories:
            with self.subTest(progress=progress):
                args = fixture(progress=progress)
                self.assertFalse(evidence(args)["complete_visual_motion_cycle"])
                result = evaluate_card_window(*args)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["score"])
                self.assertIsNone(result["conditions"][0]["passed"])

    def test_endpoint_twenty_percent_window_is_not_shrunk_to_fit_short_low_phase(self):
        args = fixture(progress=[0, 0, 0] + [.1*i for i in range(1, 15)] + [1.4]*3)
        raw = evidence(args)
        self.assertEqual(raw["endpoint_count"], 4)
        self.assertIn("step_motion_endpoint_window_overlaps_movement", raw["reason_codes"])
        self.assertFalse(raw["complete_visual_motion_cycle"])

    def test_seven_frames_is_possible_but_six_and_79ms_phases_are_not(self):
        args = fixture(progress=[0, 0, 0, .2, .4, .4, .4])
        self.assertTrue(evidence(args)["complete_visual_motion_cycle"])
        short = fixture(progress=[0, 0, .2, .4, .4, .4])
        self.assertIn("continuous_endpoint_window_missing", evidence(short)["reason_codes"])
        args[3][1]["pts"] = 39
        args[3][1]["timestamp_ms"] = args[1][1].time = 39
        args[3][2]["pts"] = 79
        args[3][2]["timestamp_ms"] = args[1][2].time = 79
        self.assertIn("step_motion_phase_too_short", evidence(args)["reason_codes"])

    def test_actual_variable_pts_and_nonzero_origin_control_speed_and_duration(self):
        args = fixture()
        ticks = [10000, 10044, 10090, 10140, 10200, 10255, 10305, 10355, 10400, 10450]
        for sample, row, tick in zip(args[1], args[3], ticks):
            row.update(pts=tick, timestamp_ms=tick-10000)
            sample.time = tick-10000
        args[2]["end_ms"] = 450
        args[2]["step_motion_observation"]["source_frames"][0]["pts"] = ticks[3]
        raw = evidence(args)
        self.assertTrue(raw["complete_visual_motion_cycle"])
        moving = raw["feet"]["right"]["intervals"][2]
        self.assertAlmostEqual(moving["speed_torso_s"], 4)
        self.assertEqual([p["duration_ms"] for p in raw["feet"]["right"]["phases"]], [90, 215, 145])

    def test_path_ratio_uses_full_paths_not_net_displacement(self):
        left = [0, .01, 0, .01, 0, .01, 0, .01, 0, .01]
        args = fixture("FS08-M03", left_progress=left)
        raw = evidence(args)
        self.assertTrue(raw["complete_visual_motion_cycle"])
        row = evaluate_card_window(*args)
        self.assertAlmostEqual(row["conditions"][0]["value"], .09/.8)
        self.assertAlmostEqual(raw["feet"]["left"]["net_displacement_torso"], .01)

    def test_wrong_declared_side_and_competing_cycles_are_never_automatically_swapped(self):
        args = fixture()
        args[2]["step_motion_observation"]["moving_foot"] = "left"
        raw = evidence(args)
        self.assertIn("step_motion_declared_side_conflicts_with_paths", raw["reason_codes"])
        self.assertEqual(raw["moving_foot_observation"]["moving_foot"], "left")
        args = fixture(left_progress=[0, 0, 0, .1, .2, .3, .4, .4, .4, .4])
        self.assertIn("step_motion_competing_ankle_cycle", evidence(args)["reason_codes"])
        self.assertIsNone(evaluate_card_window(*args)["score"])

    def test_curved_declared_path_cannot_authorize_other_ankles_larger_unbound_net(self):
        args = fixture(progress=[0, 0, 0, .4, .8, .6, .4, .4, .4, .4],
            left_progress=[.1*i for i in range(10)])
        raw = evidence(args)
        self.assertTrue(raw["feet"]["right"]["complete_visual_motion_cycle"])
        self.assertFalse(raw["feet"]["left"]["complete_visual_motion_cycle"])
        self.assertAlmostEqual(raw["feet"]["right"]["net_displacement_torso"], .4)
        self.assertAlmostEqual(raw["feet"]["right"]["path_length_torso"], 1.2)
        self.assertAlmostEqual(raw["feet"]["left"]["net_displacement_torso"], .7)
        self.assertAlmostEqual(raw["feet"]["left"]["path_length_torso"], .9)
        self.assertIn("step_motion_declared_side_conflicts_with_paths", raw["reason_codes"])
        self.assertIsNone(evaluate_card_window(*args)["score"])

    def test_missing_joint_intersection_and_original_denominator_are_preserved(self):
        args = fixture()
        for index, joint in enumerate(REQUIRED_JOINTS):
            del args[1][index].points[joint]
        raw = evidence(args)
        self.assertEqual(raw["total_frame_count"], 10)
        self.assertEqual(raw["valid_source_frames"], [6, 7, 8, 9])
        self.assertFalse(raw["geometry_gate_passed"])
        self.assertIsNone(evaluate_card_window(*args)["score"])
        for index in (0, 5, 9):
            args = fixture()
            del args[1][index].points["left_ankle"]
            raw = evidence(args)
            self.assertTrue(raw["geometry_gate_passed"])
            self.assertFalse(raw["complete_visual_motion_cycle"])
            self.assertEqual(raw["total_frame_count"], 10)
            self.assertIsNone(evaluate_card_window(*args)["score"])

    def test_original_normalized_geometry_is_retained_separately_from_camera_reference(self):
        args = fixture("FS08-M03")
        # A changing compensation rotation must never be substituted for the
        # raw normalized plane used by the trunk-tilt condition.
        for index, sample in enumerate(args[1]):
            angle = .03*index
            sample.reference_points = {name: (x*math.cos(angle)-y*math.sin(angle),
                x*math.sin(angle)+y*math.cos(angle)) for name, (x, y) in sample.reference_points.items()}
        raw = evidence(args)
        self.assertEqual(len(raw["source_geometry_series"]), 10)
        self.assertEqual(raw["source_geometry_series"][5]["normalized_points"]["left_shoulder"], [-.3, 0])
        self.assertEqual(raw["source_geometry_series"][5]["point_confidences"]["left_shoulder"], .9)
        self.assertNotEqual(raw["reference_series"][5]["points"]["left_shoulder"], [-30, 0])
        self.assertEqual(evaluate_card_window(*args)["conditions"][1]["value"], 0)

    def test_geometry_series_keeps_low_confidence_and_null_missing_rows_without_rescuing_nan(self):
        args = fixture()
        args[1][0].point_confidences["left_ankle"] = .1
        args[1][1].point_confidences["left_ankle"] = float("nan")
        args[1][1].observation_semantics = "monocular_model_estimate"
        args[1].pop(4)
        raw = evidence(args)
        self.assertEqual(len(raw["source_geometry_series"]), 10)
        self.assertEqual(raw["source_geometry_series"][0]["point_confidences"]["left_ankle"], .1)
        self.assertIsNone(raw["source_geometry_series"][1]["normalized_points"]["left_ankle"])
        self.assertTrue(all(value is None for value in raw["source_geometry_series"][4]["normalized_points"].values()))
        self.assertEqual(raw["source_geometry_series"][4]["frame_index"], 4)
        self.assertFalse(raw["complete_visual_motion_cycle"])
        import json
        json.dumps(raw, allow_nan=False)
        args = fixture()
        del args[3][4]["pts"]
        invalid_clock = evidence(args)
        self.assertEqual(len(invalid_clock["source_geometry_series"]), 10)
        self.assertTrue(all(all(point is None for point in row["normalized_points"].values())
            for row in invalid_clock["source_geometry_series"]))

    def test_identity_camera_and_source_clock_changes_reject_even_with_complete_geometry(self):
        for field, value in (("track", 2), ("selection_epoch", 2), ("camera_reference_epoch", 2),
                             ("camera_compensated", False), ("timestamp_source", "fps")):
            args = fixture()
            setattr(args[1][4], field, value)
            self.assertFalse(evidence(args)["complete_visual_motion_cycle"], field)
            self.assertIsNone(evaluate_card_window(*args)["score"], field)
        args = fixture()
        args[2]["step_motion_observation"]["source_frames"][0]["pts"] += 1
        self.assertIn("step_motion_observation_pts_mismatch", evidence(args)["reason_codes"])

    def test_complete_low_displacement_can_be_e_but_missing_evidence_is_null(self):
        args = fixture("FS02-M04", progress=[0, 0, 0, .03, .06, .09, .12, .12, .12, .12])
        row = evaluate_card_window(*args)
        self.assertEqual(row["proxy_grade"], "E")
        self.assertEqual(row["score"], 20)
        args[2].pop("step_motion_observation")
        args[2]["step_cycle_binding"] = {"method": "manual_anchor", "version": "fake", "depart_ms": 0,
            "widest_ms": 160, "return_ms": 360, "verified": True}
        self.assertIsNone(evaluate_card_window(*args)["score"])
        args[2]["stage_code"] = "M04"
        self.assertIsNone(evaluate_card_window(*args)["score"])

    def test_schema_rejects_external_pass_grade_quality_and_phase_authority(self):
        observation = fixture()[2]["step_motion_observation"]
        for key in ("complete", "passed", "score", "quality_flags", "phase_boundaries", "verified"):
            bad = {**deepcopy(observation), key: True}
            with self.assertRaises(ValueError):
                validate_step_motion_observation(bad, first_frame=0, last_frame=9)
        for change in ({"source_frames": []}, {"moving_foot": "fastest"}, {"source_description": " "},
                       {"method": "versioned_detector"}, {"semantics": "contact_truth"}):
            with self.assertRaises(ValueError):
                validate_step_motion_observation({**deepcopy(observation), **change}, first_frame=0, last_frame=9)
        for field, value in (("frame_index", True), ("frame_index", -1), ("frame_index", 10),
                             ("pts", True), ("time_base_num", 0)):
            bad = deepcopy(observation)
            bad["source_frames"][0][field] = value
            with self.assertRaises(ValueError):
                validate_step_motion_observation(bad, first_frame=0, last_frame=9)


if __name__ == "__main__":
    unittest.main()
