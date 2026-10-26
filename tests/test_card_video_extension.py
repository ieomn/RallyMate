"""Small real-output regression; does not pretend to rerun media inference in CI."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from replay_card_video_extension import normalized_sha256, window_metrics


class RealVideoExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest_path = ROOT / "tests/fixtures/card-video-cases-extension-20261022.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.report = json.loads((ROOT / "tests/fixtures/card-video-validation-results-extension-20261022.json").read_text(encoding="utf-8"))

    def test_matching_recomputed_from_real_candidates_preserves_slice_misses(self):
        # Explicit observations: GS04 self-slice 3/5 -> 5/5 review windows;
        # GS05 2/5 -> 3/5 with four wrong-time candidates in either route set.
        for source, result, expected in zip(self.manifest["cases"][:2], self.report["cases"][:2], ((3,5,0),(2,3,4))):
            primary = window_metrics(result["primary_candidates"], source["annotations"])
            combined = window_metrics(result["primary_candidates"] + result["review_candidates"], source["annotations"])
            self.assertEqual(primary, result["primary_window_metrics"])
            self.assertEqual(combined, result["with_review_fallback_metrics"])
            self.assertEqual((primary["matched_count"],combined["matched_count"],combined["false_candidate_count"]),expected)
            self.assertEqual(combined["correct_action_classification_count"],0)
            self.assertEqual(combined["unknown_classification_count"],combined["matched_count"])
        self.assertEqual(self.report["cases"][1]["with_review_fallback_metrics"]["missed_annotation_ids"],
                         ["forehand-slice-3","forehand-slice-5"])

    def test_phase_unknown_and_heldout_do_not_become_card_scores(self):
        for result in self.report["cases"]:
            self.assertIsNone(result["original_card_grade"])
            self.assertEqual(result["original_stage_established_count"],0)
            self.assertFalse(result["tuning_performed"])
            for stage in result["reviewer_stage_observability"]:
                self.assertFalse(stage["original_phase_established"])
                self.assertIsNone(stage["original_grade"])
            for visual in result["visual_window_measurements"]:
                self.assertFalse(visual["annotation"]["allow_card_scoring"])
                self.assertNotIn("card_evaluations",visual)
                self.assertIsNone(visual["original_grade"])
        self.assertEqual(self.manifest["cases"][2]["split"],"held_out_validation")
        self.assertNotIn("primary_window_metrics",self.report["cases"][2])

    def test_complete_original_frame_denominators_and_manifest_binding(self):
        self.assertEqual(self.report["case_manifest_normalized_sha256"],normalized_sha256(self.manifest_path))
        for source,result,count in zip(self.manifest["cases"],self.report["cases"],(479,425,539)):
            clock=result["decoded_clock_verification"]
            self.assertEqual(clock["processed_original_frame_count"],count)
            self.assertEqual(clock["last"]["frame_index"]-clock["first"]["frame_index"]+1,count)
            self.assertEqual(result["source_video_sha256"],source["video_sha256"])
            for endpoint in (clock["first"],clock["last"]):
                self.assertIsInstance(endpoint["pts"],int)
                self.assertGreater(endpoint["time_base_den"],0)

    def test_numeric_geometry_is_kept_without_promoting_it_to_grade(self):
        sweep=self.report["cases"][1]["visual_window_measurements"][0]["measurement"]["measurements"]
        elbow=sweep["right_elbow_angle_deg"]
        self.assertEqual((elbow["valid_frame_count"],elbow["total_frame_count"]),(11,11))
        self.assertAlmostEqual(elbow["statistics"]["median"],146.2429236487035,places=6)
        self.assertFalse(elbow["visibility_verified"])
        self.assertIn("axis_unwrap_requires_continuous_window",sweep["shoulder_axis_deg"]["reason_codes"])

    def test_temporal_repositioning_does_not_hide_slice_misclassification(self):
        back,fore=self.report["cases"][:2]
        self.assertEqual(back["estimated_phase_anchor_metrics"]["incorrect_action_classification_count"],1)
        self.assertEqual(back["estimated_phase_candidates"][0]["classification"]["label"],"forehand")
        self.assertEqual(fore["estimated_phase_anchor_metrics"]["matched_count"],4)
        self.assertEqual(fore["estimated_phase_with_review_metrics"]["matched_count"],5)
        late=next(m for m in fore["estimated_phase_anchor_metrics"]["matches"] if m["annotation_id"]=="forehand-slice-3")
        self.assertEqual(late["anchor_error_ms"],680)
        self.assertEqual(late["overlap_fraction"],.25)
        self.assertEqual(late["classification_status"],"unknown")

    def test_heldout_footwork_review_preserves_wrong_action_and_timing_evidence(self):
        review=self.report["independent_footwork_review"]
        target=self.report["cases"][2]
        self.assertEqual(review["video_sha256"],target["source_video_sha256"])
        self.assertEqual(review["frames_sha256"],target["input_artifact_sha256"]["frames.jsonl"])
        counts=review["sample_specific_matching"]
        self.assertEqual(counts["fs01_evaluable_candidates"],12)
        self.assertEqual(counts["same_action_one_to_one_matches"],6)
        self.assertEqual(counts["wrong_action_candidates"],6)
        self.assertEqual(counts["landing_proxy_within_100_ms_of_manual_anchor"],4)
        self.assertEqual(counts["fs09_deceleration_peaks_inside_manual_interval"],10)
        self.assertFalse(review["thresholds_tuned_from_this_review"])
        self.assertFalse(review["grades_generated"])


if __name__ == "__main__":
    unittest.main()
