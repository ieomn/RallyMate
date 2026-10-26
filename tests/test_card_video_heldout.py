"""Frozen real heldout output checks; CI does not download or infer videos."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from replay_card_video_extension import normalized_sha256, window_metrics


class RealHeldoutVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "tests/fixtures/card-video-cases-heldout-20261022.json"
        cls.manifest = json.loads(cls.path.read_text(encoding="utf-8"))
        cls.report = json.loads((ROOT / "tests/fixtures/card-video-validation-results-heldout-20261022.json").read_text(encoding="utf-8"))

    def test_all_four_routes_recompute_without_erasing_misses(self):
        routes = (("primary_window_metrics", "primary_candidates"),
                  ("with_review_fallback_metrics", "primary_candidates"),
                  ("estimated_phase_anchor_metrics", "estimated_phase_candidates"),
                  ("estimated_phase_with_review_metrics", "estimated_phase_candidates"))
        for source, result in zip(self.manifest["cases"][:2], self.report["cases"][:2]):
            for key, candidate_key in routes:
                candidates = result[candidate_key] + (result["review_candidates"] if "with_review" in key else [])
                self.assertEqual(window_metrics(candidates, source["annotations"]), result[key])
        atp = self.report["cases"][0]
        for key, _ in routes:
            metrics = atp[key]
            self.assertEqual((metrics["matched_count"], metrics["missed_count"], metrics["candidate_count"]), (0, 8, 0))
            self.assertEqual(metrics["by_action_type"]["forehand"]["missed"], 6)
            self.assertEqual(metrics["by_action_type"]["two_handed_backhand"]["missed"], 2)
            self.assertIsNone(metrics["precision"])

    def test_partial_early_review_is_not_correct_timing_or_technique(self):
        result = self.report["cases"][1]
        metrics = result["with_review_fallback_metrics"]
        self.assertEqual((metrics["matched_count"], metrics["missed_count"], metrics["false_candidate_count"]), (1, 1, 1))
        self.assertEqual(metrics["unknown_classification_count"], 1)
        self.assertEqual(metrics["correct_action_classification_count"], 0)
        match = metrics["matches"][0]
        self.assertEqual(match["anchor_error_ms"], -149)
        self.assertEqual(match["overlap_fraction"], .325)
        self.assertEqual(match["wrist_side_status"], "compatible")
        self.assertEqual(metrics["anchor_within_declared_uncertainty_count"], 0)
        self.assertEqual(metrics["false_candidates"][0]["peak_ms"], 49049)

    def test_wrist_participation_does_not_become_dominant_hand_accuracy(self):
        for case in self.manifest["cases"][:2]:
            for annotation in case["annotations"]:
                self.assertEqual(annotation["eligible_wrist_sides"],
                                 ["left"] if annotation["action_type"] == "forehand" else ["left", "right"])
        for result in self.report["cases"]:
            self.assertEqual(result["dominant_hand_accuracy_status"], "not_estimated_no_confirmed_dominant_hand_outputs")
            self.assertIsNone(result["original_card_grade"])
            self.assertEqual(result["original_stage_established_count"], 0)

    def test_model_observation_failure_is_preserved(self):
        result = self.report["cases"][0]
        self.assertEqual(result["diagnostics"]["valid_pose_samples"], 506)
        rows = result["read_only_miss_observation_diagnostics"]["motion_neighborhood_observations"]
        forehand = [rows[i] for i in (1, 2, 3, 4, 6, 7)]
        self.assertEqual([r["left_racket_support_samples"] for r in forehand], [1, 0, 0, 0, 0, 0])
        self.assertEqual(result["read_only_miss_observation_diagnostics"]["frames_sha256"], result["input_artifact_sha256"]["frames.jsonl"])
        self.assertTrue(result["sampled_primary_identity_review"]["does_not_certify_all_frame_identity"])

    def test_source_clock_and_inference_imports_are_bound(self):
        self.assertEqual(self.report["case_manifest_normalized_sha256"], normalized_sha256(self.path))
        self.assertEqual(self.report["production_source_overlay_count"], 0)
        for source, result, count in zip(self.manifest["cases"], self.report["cases"], (575, 159, 45, 53)):
            clock = result["decoded_clock_verification"]
            self.assertEqual(clock["processed_original_frame_count"], count)
            self.assertEqual(clock["last"]["frame_index"] - clock["first"]["frame_index"] + 1, count)
            self.assertEqual(result["source_video_sha256"], source["video_sha256"])
            evidence = result["first_inference_provenance"]
            self.assertEqual(evidence["head"], self.report["production_runtime_head"])
            self.assertTrue(evidence["all_recorded_modules_verified_against_clean_archive"])
            self.assertEqual(evidence["module_count"], len(source["first_inference_module_sha256"]))
            self.assertEqual(evidence["processing"]["frame_stride"], 1)
            self.assertEqual(evidence["processing"]["source_timing"]["fallback_timestamp_frames"], 0)

    def test_shuffle_title_and_seen_holdout_are_not_validation_upgrades(self):
        for source, result in zip(self.manifest["cases"][2:], self.report["cases"][2:]):
            self.assertEqual(source["annotations"], [])
            self.assertNotIn("primary_window_metrics", result)
            self.assertFalse(result["tuning_performed"])
            self.assertEqual(source["split"], "held_out_validation")
        self.assertIn("become development", self.manifest["validation_use_policy"])

    def test_independent_shuffle_cycle_review_keeps_raw_and_bound_inputs_separate(self):
        review = self.report["independent_footwork_cycle_review"]
        by_id = {c["case_id"]: c for c in self.report["cases"]}
        self.assertFalse(review["scores_or_original_grades_generated"])
        for case in review["cases"]:
            self.assertEqual(case["video_sha256"], by_id[case["case_id"]]["source_video_sha256"])
            self.assertEqual(case["frames_sha256"], by_id[case["case_id"]]["input_artifact_sha256"]["frames.jsonl"])
            self.assertTrue(case["review"]["all_run_frames_viewed"])
            self.assertFalse(case["review"]["complete_pure_FS04_positive_verified"])
            self.assertFalse(case["review"]["thresholds_changed_for_this_case"])
            for card in case["reports"]["unaltered_original_inputs"]["cards"]:
                self.assertEqual(card["measurements"], {})
                self.assertIn("source_clock_subject_binding_mismatch", card["reason_codes"])
            for card in case["reports"]["explicit_decoder_and_derived_epoch_replay"]["cards"]:
                self.assertEqual(card["status"], "needs_review")
                self.assertIsNone(card["score"])
                self.assertIsNone(card["original_grade"])
                self.assertIsNone(card["proxy_grade"])
                self.assertEqual(card["evidence"]["series"]["complete_visual_motion_cycle_count"], 0)
                expected = 0 if case["case_id"].endswith("front-short") else None
                self.assertEqual(card["measurements"]["ankle_order_exchange_count"], expected)
        front = next(c for c in review["cases"] if c["case_id"].endswith("front-short"))
        self.assertAlmostEqual(front["reports"]["explicit_decoder_and_derived_epoch_replay"]["cards"][0]["measurements"]["hip_displacement_torso"], .4758372662970524)


if __name__ == "__main__":
    unittest.main()
