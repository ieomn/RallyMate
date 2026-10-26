"""The two hand/racket requirements cannot be scored from wrist convergence."""
from copy import deepcopy
import unittest

from rallymate_scoring.card_evaluation import (
    HAND_RACKET_AUXILIARY_VERSION, HAND_RACKET_SCORING_REVISION, evaluate_card_window,
)
from rallymate_scoring.card_kinematics import measure_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_service.gs_card_execution import _unavailable
from tests.test_card_evaluation import fixture

TARGETS = ("GS01-M09-04", "GS01-M09-05")
JOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_wrist", "right_wrist")


def inputs(identifier=TARGETS[0], count=10):
    _, samples, window, timeline = fixture(count)
    window["stage_code"] = "GS01-M09"
    for i, sample in enumerate(samples):
        spread = .8 - .6*i/(count-1)
        sample.points["left_wrist"] = (-spread/2, .5)
        sample.points["right_wrist"] = (spread/2, .5)
    return get_card_rule(identifier), samples, window, timeline


class GSHandRacketObservabilityTests(unittest.TestCase):
    def assert_unscored(self, result):
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["conditions"], [])
        for key in ("grade", "score", "proxy_grade", "original_grade"):
            self.assertIsNone(result.get(key), key)
        self.assertFalse(result["source_grade_supported"])
        self.assertEqual(result["scoring_revision_id"], HAND_RACKET_SCORING_REVISION)

    def test_distinct_source_requirements_and_no_replacement_condition(self):
        approach, control = [get_card_rule(key) for key in TARGETS]
        self.assertEqual([rule["source"]["table"] for rule in (approach, control)], ["T047", "T048"])
        self.assertIn("主动接近球拍", approach["effective"]["definition"])
        self.assertIn("非持拍手控制", control["effective"]["definition"])
        self.assertNotEqual(approach["evaluation"]["required_observables"], control["evaluation"]["required_observables"])
        self.assertNotEqual(approach["evaluation"]["score_block_reason_code"], control["evaluation"]["score_block_reason_code"])
        for rule in (approach, control):
            self.assertEqual(rule["evaluation"]["kind"], "manual_review")
            self.assertEqual(rule["evaluation"]["conditions"], [])
            self.assertEqual(rule["evaluation"]["scoring_revision_id"], HAND_RACKET_SCORING_REVISION)
            aux = rule["evaluation"]["auxiliary_observations"][0]
            self.assertFalse(aux["scoring_eligible"])
            self.assertNotIn("operator", aux)
            self.assertNotIn("value", aux)

    def test_complete_strong_wrist_convergence_never_authorizes_either_card(self):
        for identifier in TARGETS:
            args = inputs(identifier)
            rule, samples, window, timeline = args
            expected = measure_card_window(samples, window, timeline, required_joint_names=JOINTS)["measurements"]["wrist_separation_torso"]
            result = evaluate_card_window(*args)
            self.assert_unscored(result)
            self.assertEqual(result["missing_required_observables"], rule["evaluation"]["required_observables"])
            self.assertEqual(result["reason_codes"], [rule["evaluation"]["score_block_reason_code"]])
            aux = result["auxiliary_observations"]
            self.assertEqual(aux["version"], HAND_RACKET_AUXILIARY_VERSION)
            self.assertEqual(aux["status"], "measured_auxiliary")
            self.assertFalse(aux["scoring_eligible"])
            self.assertEqual(aux["measurements"], {"wrist_separation_torso": expected})
            self.assertLess(expected["statistics"]["delta"], -.15)
            self.assertTrue({"passed", "grade", "score"}.isdisjoint(aux))
            self.assertTrue({"passed", "grade", "score"}.isdisjoint(expected))

    def test_six_frame_gate_preserves_three_frame_endpoint_medians(self):
        result = evaluate_card_window(*inputs(count=6))
        self.assert_unscored(result)
        aux = result["auxiliary_observations"]
        self.assertEqual(aux["status"], "measured_auxiliary")
        stats = aux["measurements"]["wrist_separation_torso"]["statistics"]
        self.assertAlmostEqual(stats["start"], .68)
        self.assertAlmostEqual(stats["end"], .32)
        self.assertAlmostEqual(stats["delta"], -.36)

    def test_five_frames_keep_static_measurement_but_do_not_create_delta(self):
        result = evaluate_card_window(*inputs(count=5))
        self.assert_unscored(result)
        aux = result["auxiliary_observations"]
        measurement = aux["measurements"]["wrist_separation_torso"]
        self.assertEqual(aux["status"], "unavailable")
        self.assertEqual(measurement["source_frames"], list(range(5)))
        self.assertNotIn("delta", measurement["statistics"])
        self.assertAlmostEqual(measurement["statistics"]["median"], .5)
        self.assertIn("partial_window_cannot_establish_change", aux["reason_codes"])

    def test_partial_joint_or_source_inventory_keeps_full_denominator(self):
        for missing in ("left_wrist", "right_shoulder", "sample"):
            rule, samples, window, timeline = inputs()
            if missing == "sample":
                samples.pop(4)
            else:
                del samples[4].points[missing]
            result = evaluate_card_window(rule, samples, window, timeline)
            self.assert_unscored(result)
            aux = result["auxiliary_observations"]
            measurement = aux["measurements"]["wrist_separation_torso"]
            self.assertEqual(measurement["total_frame_count"], 10)
            self.assertEqual(measurement["valid_frame_count"], 9)
            self.assertNotIn("delta", measurement["statistics"])
            self.assertEqual(aux["status"], "unavailable")

    def test_identity_camera_source_clock_corruption_cannot_create_auxiliary(self):
        for field, value in (("track", 2), ("selection_epoch", 1), ("camera_reference_epoch", 1),
                             ("timestamp_source", "fps_fallback"), ("time", 43)):
            args = inputs()
            setattr(args[1][1], field, value)
            result = evaluate_card_window(*args)
            self.assert_unscored(result)
            aux = result["auxiliary_observations"]
            self.assertEqual(aux["status"], "unavailable", field)
            self.assertEqual(aux["measurements"]["wrist_separation_torso"]["statistics"], {})
            self.assertTrue(aux["reason_codes"])

    def test_stage_binding_failure_retains_distinct_missing_requirements(self):
        for mutation in ({"event_code": "GS02"}, {"stage_code": "M08"}, {"stage_binding": {}}):
            rule, samples, window, timeline = inputs()
            window.update(mutation)
            result = evaluate_card_window(rule, samples, window, timeline)
            self.assert_unscored(result)
            self.assertEqual(result["auxiliary_observations"]["measurements"], {})
            self.assertEqual(result["auxiliary_observations"]["reason_codes"], ["source_stage_binding_missing_or_mismatched"])

    def test_saved_score_or_pass_does_not_substitute_for_required_observables(self):
        rule, samples, window, timeline = inputs()
        window.update(score=100, passed=True, auxiliary_observations={"score": 100}, wrist_separation_torso=-100)
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assert_unscored(result)
        self.assertAlmostEqual(result["auxiliary_observations"]["measurements"]["wrist_separation_torso"]["statistics"]["delta"], -7/15)

    def test_source_adapter_early_unavailable_cannot_lose_revision_or_make_measurement(self):
        rule, _, window, _ = inputs()
        result = _unavailable(rule, window, ["required_source_frame_missing"])
        self.assert_unscored(result)
        self.assertIn("required_source_frame_missing", result["reason_codes"])
        self.assertIn(rule["evaluation"]["score_block_reason_code"], result["reason_codes"])
        self.assertNotIn("auxiliary_observations", result)

    def test_non_target_scoring_and_manual_contracts_are_unchanged(self):
        args = fixture()
        self.assertEqual(evaluate_card_window(*args)["score"], 100)
        rule = deepcopy(args[0]); rule["evaluation"]["kind"] = "manual_review"
        result = evaluate_card_window(rule, *args[1:])
        self.assertNotIn("scoring_revision_id", result)
        self.assertNotIn("auxiliary_observations", result)


if __name__ == "__main__":
    unittest.main()
