"""Analytic coordinate tests only; these are not real tennis examples."""
from copy import deepcopy
import json
import math
from types import SimpleNamespace
import unittest

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_scoring.gs_recovery_evidence import (
    VERSION, SCORING_REVISION_ID, REFERENCE_VERSION, REFERENCE_SEMANTICS,
    ROTATION_CARDS, STABILITY_CARDS, MANUAL_REQUIREMENTS, SUPPORTED_CARDS,
    ROTATION_CONDITIONS, STABILITY_CONDITIONS, compute_gs_recovery_evidence,
)


def fixture(angles=None, *, translation_speed=0, pts_origin=0, reference_angles=None):
    """A fixed-view 50 px torso with explicit geometry and exact 40 ms PTS."""
    angles = [30, 30, 30, 20, 10, 0, 0, 0, 0, 0] if angles is None else list(angles)
    reference_angles = [0, 0, 0, 0] if reference_angles is None else list(reference_angles)
    start = len(reference_angles)+2
    samples, timeline = [], []
    for i, angle in enumerate(reference_angles + [0, 0] + angles):
        radians = math.radians(angle)
        x = 200+max(0, i-start)*.04*translation_speed*50
        shoulder, hip = (x, 70), (x, 120)
        points = {}
        for pair, center, halfwidth in (("shoulder", shoulder, 15), ("hip", hip, 12.5)):
            for side, sign in (("left", -1), ("right", 1)):
                points[f"{side}_{pair}"] = (center[0]+sign*halfwidth*math.cos(radians), center[1]+sign*halfwidth*math.sin(radians))
        points.update(left_ankle=(x-25, 170), right_ankle=(x+25, 170),
            left_knee=(x-15, 145), right_knee=(x+15, 145),
            left_wrist=(x-5, 100), right_wrist=(x+5, 100))
        clock = {"frame_index": i, "timestamp_ms": i*40, "pts": pts_origin+i*40,
            "time_base_num": 1, "time_base_den": 1000, "video_sha256": "a"*64,
            "selected_person_track_id": 1, "selection_epoch": 2, "derived_selection_epoch": 2,
            "camera_reference_epoch": 0}
        timeline.append(clock)
        samples.append(SimpleNamespace(frame_index=i, time=i*40, track=1, selection_epoch=2,
            camera_reference_epoch=0, camera_compensated=True, timestamp_source="decoder_pts",
            normalization_basis="full_torso", center=shoulder, scale=50,
            reference_points=points, points={k: ((p[0]-shoulder[0])/50, (p[1]-shoulder[1])/50) for k, p in points.items()},
            point_confidences={k: .9 for k in points}, pose_source="synthetic_geometry_only",
            source_binding={**clock, "person_track_id": 1}))
    window = {"video_id": "synthetic-m10", "video_sha256": "a"*64, "event_id": "synthetic-action-stage",
        "event_code": "GS02", "stage_code": "GS02-M10", "start_ms": start*40, "end_ms": (len(samples)-1)*40,
        "person_track_id": 1, "selection_epoch": 2, "derived_selection_epoch": 2,
        "camera_reference_epoch": 0, "camera_compensated": True,
        "stage_binding": {"method": "manual_anchor", "version": "synthetic-only"}}
    rt = timeline[:len(reference_angles)]
    refwindow = {k: v for k, v in window.items() if k not in ("event_id", "event_code", "stage_code", "stage_binding")}
    refwindow.update(start_ms=0, end_ms=(len(reference_angles)-1)*40)
    reference = {"window": refwindow, "samples": samples[:len(reference_angles)], "timeline": rt,
        "continuity_timeline": timeline, "continuity_samples": samples, "action_start_ms": pts_origin+(start-1)*40,
        "binding": {"version": REFERENCE_VERSION, "method": "manual_visual_observation", "semantics": REFERENCE_SEMANTICS,
            "action_id": "synthetic-action", "person_track_id": 1, "video_sha256": "a"*64,
            "start_frame": {k: rt[0][k] for k in ("frame_index", "pts", "time_base_num", "time_base_den")},
            "end_frame": {k: rt[-1][k] for k in ("frame_index", "pts", "time_base_num", "time_base_den")}}}
    return samples[start:], window, timeline[start:], reference


def evaluate(identifier="GS02-M10-02", **kwargs):
    samples, window, timeline, reference = fixture(**kwargs)
    window.update(event_code=identifier[:4], stage_code=identifier.rsplit("-", 1)[0])
    return evaluate_card_window(get_card_rule(identifier), samples, window, timeline, recovery_reference=reference)


class GSRecoveryEvidenceTests(unittest.TestCase):
    def test_thirteen_explicit_contracts_are_distinct_and_match_core(self):
        self.assertEqual(len(SUPPORTED_CARDS), 13)
        for identifier in SUPPORTED_CARDS:
            e = get_card_rule(identifier)["evaluation"]
            self.assertEqual(e["scoring_revision_id"], SCORING_REVISION_ID)
            if identifier in MANUAL_REQUIREMENTS:
                self.assertEqual(e["conditions"], [])
                self.assertEqual(e["required_observables"], list(MANUAL_REQUIREMENTS[identifier][1]))
            else:
                expected = ROTATION_CONDITIONS if identifier in ROTATION_CARDS else STABILITY_CONDITIONS
                self.assertEqual([(c["metric"], c["operator"], c["value"], c["unit"]) for c in e["conditions"]], list(expected))

    def test_analytic_return_uses_both_axes_and_independent_reference(self):
        result = evaluate()
        self.assertEqual(result["status"], "scored", result["reason_codes"])
        self.assertEqual(result["score"], 100)
        values = [c["value"] for c in result["conditions"]]
        for value, expected in zip(values, [30, 30, 0, 0]):
            self.assertAlmostEqual(value, expected)
        self.assertEqual(result["measurement_version"], VERSION)
        self.assertIsNone(result["original_grade"])
        json.dumps(result, allow_nan=False)

    def test_stable_rotated_finish_is_not_restored_orientation(self):
        result = evaluate(angles=[30]*10)
        self.assertEqual([c["passed"] for c in result["conditions"]], [False, False, False, True])
        self.assertEqual(result["score"], 40)
        stable = evaluate("GS02-M10-03", angles=[30]*10)
        self.assertEqual(stable["score"], 100)
        self.assertNotEqual([c["metric"] for c in result["conditions"]], [c["metric"] for c in stable["conditions"]])

    def test_fast_uniform_translation_does_not_pass_low_motion(self):
        result = evaluate("GS02-M10-03", angles=[0]*10, translation_speed=3)
        self.assertAlmostEqual(result["conditions"][0]["value"], 3)
        self.assertFalse(result["conditions"][0]["passed"])
        self.assertEqual(result["score"], 60)
        self.assertAlmostEqual(result["auxiliary_observations"]["measurements"]["hip_speed_std_torso_s"]["statistics"]["value"], 0)

    def test_missing_reference_keeps_actual_axis_auxiliary_but_no_score(self):
        samples, window, timeline, _ = fixture()
        result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline)
        self.assertIsNone(result["score"])
        self.assertTrue(all(c["value"] is None and c["passed"] is None for c in result["conditions"]))
        self.assertGreater(result["auxiliary_observations"]["measurements"]["shoulder_axis_deg"]["statistics"]["range"], 20)
        self.assertIn("independent_ready_reference_missing", result["reason_codes"])

    def test_all_seven_hand_or_complete_ready_cards_remain_unavailable(self):
        for identifier, (reason, missing) in MANUAL_REQUIREMENTS.items():
            result = evaluate(identifier)
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["score"])
            self.assertEqual(result["conditions"], [])
            self.assertIn(reason, result["reason_codes"])
            self.assertEqual(result["missing_required_observables"], list(missing))
            self.assertFalse(result["auxiliary_observations"]["scoring_eligible"])
            self.assertTrue(result["auxiliary_observations"]["measurements"]["wrist_separation_torso"]["statistics"])

    def test_old_duplicate_condition_contract_cannot_score_new_revision(self):
        samples, window, timeline, reference = fixture()
        rule = deepcopy(get_card_rule("GS02-M10-02"))
        rule["evaluation"]["conditions"] = get_card_rule("GS04-M10-02")["evaluation"]["conditions"]
        result = evaluate_card_window(rule, samples, window, timeline, recovery_reference=reference)
        self.assertIsNone(result["score"])
        self.assertIn("recovery_card_contract_mismatch", result["reason_codes"])

    def test_six_frame_stage_and_short_reference_are_not_padded(self):
        for kwargs in ({"angles": [20, 20, 10, 0, 0, 0]}, {"reference_angles": [0, 0]}):
            self.assertIsNone(evaluate(**kwargs)["score"])
        self.assertEqual(evaluate(angles=[20, 20, 20, 0, 0, 0, 0], reference_angles=[0, 0, 0])["score"], 100)

    def test_missing_frame_or_one_required_joint_cannot_shrink_denominator(self):
        for mutation in ("frame", "point", "confidence", "reference_frame"):
            samples, window, timeline, reference = fixture()
            if mutation == "frame":
                samples.pop(4)
            elif mutation == "point":
                del samples[4].points["left_hip"]
            elif mutation == "confidence":
                samples[4].point_confidences["left_hip"] = .2
            else:
                reference["samples"].pop(1)
            result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
            self.assertIsNone(result["score"], mutation)
            self.assertEqual(result["quality"]["total_frame_count"], 10)
            self.assertEqual(len(result["recovery_evidence"]["stage"]["source_geometry_series"]), 10)

    def test_intervening_gap_or_identity_reset_cannot_be_hidden_by_equal_endpoints(self):
        for mutation in ("gap", "person", "primary", "derived", "camera", "association", "model_epoch"):
            samples, window, timeline, reference = fixture()
            bridge = reference["continuity_timeline"]
            if mutation == "gap":
                bridge.pop(4)
            elif mutation == "association":
                bridge[4]["rejected_pose_person_track_id"] = 1
            elif mutation == "model_epoch":
                for i, row in enumerate(bridge):
                    row.update(source_model_selection_epoch=1 if i == 4 else 0,
                        model_epoch_semantics="detector_track_epoch_primary_selection_applied_downstream")
            else:
                key = {"person": "selected_person_track_id", "primary": "selection_epoch", "derived": "derived_selection_epoch", "camera": "camera_reference_epoch"}[mutation]
                bridge[4][key] += 1
            result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
            self.assertIsNone(result["score"], mutation)

    def test_complete_bridge_ledger_cannot_hide_missing_intermediate_sample(self):
        samples, window, timeline, reference = fixture()
        reference["continuity_samples"] = [sample for sample in reference["continuity_samples"] if sample.frame_index != 4]
        result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["score"])
        self.assertIn("ready_reference_intervening_source_sample_missing", result["reason_codes"])
        self.assertTrue(all(row["value"] is None and row["passed"] is None for row in result["conditions"]))
        proof = result["recovery_evidence"]["reference"]
        self.assertEqual(len(proof["continuity_timeline"]), 16)
        self.assertEqual(len(proof["continuity_source_geometry_series"]), 16)
        self.assertEqual(proof["continuity_source_geometry_series"][4],
            {"frame_index": 4, "points": {}, "reference_points": {}, "point_confidences": {}})

    def test_terminal_self_reference_and_wrong_source_are_rejected(self):
        for mutation in ("self", "video", "person", "endpoint"):
            samples, window, timeline, reference = fixture()
            if mutation == "self":
                reference["action_start_ms"] = 80
            elif mutation == "endpoint":
                reference["binding"]["end_frame"]["pts"] += 1
            else:
                reference["binding"]["video_sha256" if mutation == "video" else "person_track_id"] = "b"*64 if mutation == "video" else 2
            result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
            self.assertIsNone(result["score"], mutation)

    def test_nonzero_pts_origin_is_not_mixed_with_video_timestamp(self):
        a, b = evaluate(), evaluate(pts_origin=9000)
        self.assertEqual([c["value"] for c in a["conditions"]], [c["value"] for c in b["conditions"]])
        self.assertEqual(b["score"], 100)

    def test_degenerate_axis_and_unstable_reference_are_unavailable(self):
        self.assertIsNone(evaluate(reference_angles=[0, 20, 0, 0])["score"])
        samples, window, timeline, reference = fixture()
        samples[3].reference_points["left_hip"] = samples[3].reference_points["right_hip"]
        result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
        self.assertIsNone(result["score"])

    def test_180_degree_wrap_does_not_create_a_large_false_change(self):
        result = evaluate(angles=[-65]*3+[-75, -85, 89, 89, 89, 89, 89], reference_angles=[89]*4)
        self.assertEqual(result["score"], 100)
        self.assertAlmostEqual(result["conditions"][0]["value"], 26)

    def test_terminal_oscillation_is_distinct_from_passing_through_target(self):
        result = evaluate(angles=[30]*3+[20, 10, 0, 0, -6, 6, -6])
        self.assertFalse(result["conditions"][-1]["passed"])
        self.assertTrue(result["conditions"][-2]["passed"])

    def test_input_geometry_copies_and_normalization_must_agree(self):
        for field in ("points", "reference_points", "center", "scale"):
            samples, window, timeline, reference = fixture()
            if field in ("points", "reference_points"):
                getattr(samples[4], field)["left_shoulder"] = (0, 0)
            else:
                setattr(samples[4], field, (0, 0) if field == "center" else 3)
            result = evaluate_card_window(get_card_rule("GS02-M10-02"), samples, window, timeline, recovery_reference=reference)
            self.assertIsNone(result["score"], field)

    def test_input_objects_are_not_modified(self):
        samples, window, timeline, reference = fixture()
        before = deepcopy((samples, window, timeline, reference))
        compute_gs_recovery_evidence(samples, window, timeline, kind="rotation", reference=reference)
        self.assertEqual((samples, window, timeline, reference), before)


if __name__ == "__main__":
    unittest.main()
