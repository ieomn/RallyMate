"""Numeric provenance checks; these do not re-perform visual adjudication."""
from fractions import Fraction
import json
from pathlib import Path
import re
import unittest


FIXTURE = Path(__file__).parent / "fixtures/gs-m01-boundary-triage-20261022.json"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class M01BoundaryTriageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(FIXTURE.read_text(encoding="utf8"))

    def assert_frame_bindings(self, rows, video_sha256):
        self.assertRegex(video_sha256, SHA256)
        self.assertEqual(len(rows), len({r["source_frame_index"] for r in rows}))
        previous = None
        for row in rows:
            binding = row["source_binding"]
            self.assertEqual(binding["frame_index"], row["source_frame_index"])
            self.assertEqual(binding["video_sha256"], video_sha256)
            self.assertIs(type(binding["pts"]), int)
            self.assertGreater(binding["time_base_num"], 0)
            self.assertGreater(binding["time_base_den"], 0)
            time = Fraction(binding["pts"] * binding["time_base_num"], binding["time_base_den"])
            if previous is not None:
                self.assertGreater(time, previous)
            previous = time
            self.assertRegex(row["raw_png_sha256"], SHA256)
            self.assertTrue(row["raw_png_evidence_path"].startswith(".codex_tmp/"))
            self.assertTrue(row["raw_png_evidence_path"].endswith(".png"))

    def test_seven_source_scope_and_actual_time_identity(self):
        expected = {"mit-fh": 16, "mit-thbh": 15, "mit-ohbh": 31, "usta-slice": 12,
                    "rick-slice": 12, "wimbledon": 14, "local-court": 47}
        cases = self.report["initial_cases"]
        self.assertEqual({c["case"]: len(c["frames"]) for c in cases}, expected)
        self.assertEqual(sum(len(c["frames"]) for c in cases), 147)
        for case in cases:
            self.assert_frame_bindings(case["frames"], case["video_sha256"])
            for field in ["stage_window", "local_preload_observation", "score", "original_grade"]:
                self.assertIsNone(case[field])
            self.assertFalse(case["parameter_tuning_performed"])
            self.assertTrue(case["missing"])

    def test_atp_disagreement_cannot_become_complete_m01(self):
        atp = self.report["atp"]
        self.assertEqual([r["source_frame_index"] for r in atp["frames"]], list(range(375, 426)))
        self.assert_frame_bindings(atp["frames"], atp["source"]["video_sha256"])
        self.assert_frame_bindings(atp["context_frames"], atp["source"]["video_sha256"])
        self.assertEqual(atp["first_reviewer_tentative_departure_bracket_frames"], [391, 393])
        self.assertEqual(atp["independent_reviewer_departure_bracket_frames"], [397, 400])
        self.assertEqual(atp["independent_reviewer_landing_bracket_frames"], [397, 400])
        self.assertTrue(atp["reviewer_disagreement_retained"])
        lookup = {r["source_frame_index"]: r["source_binding"] for r in atp["frames"]}
        for prefix in ["first_reviewer_tentative_departure", "first_reviewer_tentative_landing",
                       "independent_reviewer_departure", "independent_reviewer_landing"]:
            self.assertEqual(atp[prefix + "_bracket_source_bindings"],
                             [lookup[i] for i in atp[prefix + "_bracket_frames"]])
        self.assertEqual(Fraction(lookup[397]["pts"], 12800), Fraction(15880, 1000))
        self.assertEqual(Fraction(lookup[400]["pts"], 12800), 16)
        self.assertIsNone(atp["stage_window"])
        self.assertIsNone(atp["local_preload_observation"])
        self.assertIsNone(atp["score"])

    def test_usopen_context_is_verified_but_not_phase_annotation(self):
        case = self.report["usopen"]
        indices = [1395, 1440, 1500, 1540]
        self.assertEqual([r["source_frame_index"] for r in case["frames"]], indices)
        self.assertEqual(case["sequential_decode_png_byte_verification"]["verified_frames"], indices)
        self.assert_frame_bindings(case["frames"], case["source"]["video_sha256"])
        self.assertEqual(case["conclusion"], "original_opponent_departure_boundary_unavailable")
        self.assertIsNone(case["stage_window"])
        self.assertIsNone(case["score"])

    def test_original_and_engineering_boundaries_remain_distinct(self):
        rules = {r["indicator_id"]: r for r in self.report["original_rule_basis"]}
        self.assertEqual(set(rules), {"GS01-M01-03", "GS02-M01-04", "GS03-M01-03",
                                     "GS04-M01-03", "GS05-M01-03"})
        for key in ["GS03-M01-03", "GS04-M01-03"]:
            self.assertFalse(rules[key]["original_start"])
            self.assertFalse(rules[key]["original_end"])
            self.assertNotEqual(rules[key]["effective_stage_window"]["origin"], "source")
        for key in ["GS01-M01-03", "GS02-M01-04", "GS05-M01-03"]:
            self.assertIn("对方拍面", rules[key]["original_start"])
            self.assertIn("垫步落地", rules[key]["original_end"])

    def test_bounded_forehand_all_stage_review_does_not_invent_windows(self):
        review = self.report["mit_first_forehand_all_stage_review"]
        self.assertEqual(review["action_id"], "forehand-1")
        self.assertEqual([r["source_frame_index"] for r in review["frames"]], list(range(9, 81)))
        self.assert_frame_bindings(review["frames"], review["video_sha256"])
        stages = {s["stage_code"]: s for s in review["stages"]}
        self.assertEqual(set(stages), {f"GS01-M{i:02}" for i in range(1, 11)})
        self.assertEqual({k for k, s in stages.items() if s["status"] == "prior_manual_proxy_only"},
                         {"GS01-M03", "GS01-M07"})
        for stage in stages.values():
            self.assertIsNone(stage["new_annotation"])
            self.assertIsNone(stage["new_score"])
            self.assertTrue(stage["source_start"] and stage["source_end"] and stage["reason"])
        independent = review["independent_visual_review"]
        self.assertEqual(independent["non_racket_hand_convergence_bracket_frames"], [41, 43])
        self.assertEqual(independent["visible_ready_posture_bracket_frames"], [60, 62])
        self.assertFalse(review["production_adapter_called"])

    def test_no_model_no_score_no_validation_of_unavailable_link(self):
        execution = self.report["execution"]
        for key in ["new_model_inference", "scoring_or_preload_module_called",
                    "production_stage_adapter_called", "thresholds_changed"]:
            self.assertFalse(execution[key])
        self.assertEqual(execution["new_M01_stage_annotations"], 0)
        self.assertEqual(execution["new_GS_stage_annotations"], 0)
        self.assertIsNone(execution["score"])
        self.assertIsNone(execution["original_grade"])
        self.assertTrue(self.report["policies"]["candidate_brackets_not_stage_annotations"])
        for candidate in self.report["bounded_unprocessed_candidates"]:
            self.assertFalse(candidate["downloaded"])
            self.assertFalse(candidate["visual_reviewed"])
            self.assertFalse(candidate["inference_run"])
            self.assertEqual(candidate["media_fetch_result"], "HTTP404")
        for report in self.report["frozen_input_reports"]:
            self.assertRegex(report["sha256"], SHA256)


if __name__ == "__main__":
    unittest.main()
