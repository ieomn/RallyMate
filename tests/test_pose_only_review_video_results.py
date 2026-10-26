"""Recompute frozen experimental evidence, without downloading or inferring media."""
import json
import math
from fractions import Fraction
from pathlib import Path
import statistics
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from replay_card_video_extension import normalized_sha256, window_metrics
from rallymate_scoring.pose_only_swing_review import POLICY


class PoseOnlyRealEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest_path = ROOT / "tests/fixtures/pose-only-review-cases-20261022.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.report = json.loads((ROOT / "tests/fixtures/pose-only-review-results-20261022.json").read_text(encoding="utf-8"))
        cls.cases = {c["case_id"]: c for c in cls.report["cases"]}

    def test_policy_and_input_digests_are_frozen(self):
        self.assertEqual(self.report["policy"], POLICY)
        self.assertEqual(self.manifest["policy_freeze_record"]["policy"], POLICY)
        self.assertEqual(self.report["case_manifest_normalized_sha256"], normalized_sha256(self.manifest_path))
        self.assertEqual(self.report["experiment_module_normalized_sha256"], normalized_sha256(ROOT / "src/rallymate_scoring/pose_only_swing_review.py"))
        self.assertEqual(self.report["replay_script_normalized_sha256"], normalized_sha256(ROOT / "scripts/replay_pose_only_review.py"))
        for name, digest in self.report["replay_helper_normalized_sha256"].items():
            self.assertEqual(digest, normalized_sha256(ROOT / "scripts" / name))
        self.assertEqual(len(self.cases), 8)
        self.assertEqual(sum(c["source_frame_count"] for c in self.cases.values()), 2624)

    def test_every_match_and_unmatched_candidate_recomputes(self):
        for source in self.manifest["cases"]:
            result = self.cases[source["id"]]
            primary, old, new = result["primary_candidates"], result["racket_review_candidates"], result["pose_only_report"]["candidates"]
            for key, rows in (("primary_metrics", primary), ("previous_combined_metrics", primary+old),
                              ("pose_only_metrics", new), ("new_combined_metrics", primary+old+new)):
                if source["annotations"]:
                    self.assertEqual(result[key], window_metrics(rows, source["annotations"]))
                else:
                    self.assertNotIn(key, result)
            self.assertEqual(len({c["candidate_id"] for c in new}), len(new))

    def test_atp_still_misses_all_eight_development_actions(self):
        result = self.cases["atp-nadal-left-forehand"]
        self.assertEqual(result["split"], "observed_development")
        for name in ("previous_combined_metrics", "pose_only_metrics", "new_combined_metrics"):
            metric = result[name]
            self.assertEqual((metric["matched_count"], metric["missed_count"], metric["candidate_count"]), (0, 8, 0))
            self.assertEqual(metric["by_action_type"]["forehand"]["missed"], 6)
            self.assertEqual(metric["by_action_type"]["two_handed_backhand"]["missed"], 2)

    def test_independent_validation_adds_wrong_side_duplicates_not_recall(self):
        result = self.cases["wimbledon-konta-new-validation"]
        self.assertEqual(result["split"], "frozen_independent_validation_for_pose_only_v1")
        old, new, combined = (result[k] for k in ("previous_combined_metrics", "pose_only_metrics", "new_combined_metrics"))
        self.assertEqual((old["matched_count"], old["false_candidate_count"]), (3, 1))
        self.assertEqual((new["matched_count"], new["false_candidate_count"], new["incompatible_wrist_side_count"]), (3, 3, 3))
        self.assertEqual(new["unknown_classification_count"], 3)
        self.assertEqual([m["anchor_error_ms"] for m in new["matches"]], [-200., -200., -120.])
        self.assertEqual(new["median_absolute_anchor_error_ms"], 200.)
        self.assertEqual((combined["matched_count"], combined["false_candidate_count"]), (3, 7))

    def test_development_gain_has_explicit_false_candidate_cost(self):
        result = self.cases["gs05-macci-forehand-slice"]
        old, combined = result["previous_combined_metrics"], result["new_combined_metrics"]
        self.assertEqual((old["matched_count"], old["false_candidate_count"]), (3, 4))
        self.assertEqual((combined["matched_count"], combined["false_candidate_count"]), (5, 10))
        self.assertEqual(result["pose_only_metrics"]["incompatible_wrist_side_count"], 1)
        mit = self.cases["ocw-pe.710-2-forehand-220k"]
        self.assertEqual(mit["pose_only_metrics"]["incompatible_wrist_side_count"], 3)
        self.assertEqual(mit["new_combined_metrics"]["false_candidate_count"], 14)

    def test_negative_peaks_and_overlaps_remain_separate(self):
        for result in self.cases.values():
            candidates = result["pose_only_report"]["candidates"]
            for row in result["negative_window_results"]:
                start, end = row["start_ms"], row["end_ms"]
                self.assertEqual(row["new_candidate_peaks_inside"], [c["candidate_id"] for c in candidates if start <= c["peak_ms"] <= end])
                self.assertEqual(row["new_candidate_windows_overlap"], [c["candidate_id"] for c in candidates if max(start,c["start_ms"]) < min(end,c["end_ms"])])
            self.assertIn("exhaustive", result["negative_annotation_scope"])
        usta = self.cases["fs-usta-return-footwork-heldout"]
        self.assertEqual(usta["negative_new_peak_count"], 1)
        self.assertEqual(usta["negative_window_results"][3]["new_candidate_peaks_inside"], ["c5e8589a12fb9030e442"])
        self.assertEqual(self.cases["rick-explanation-negative"]["pose_only_report"]["candidates"], [])
        for key, expected in (("ocw-pe.710-2-forehand-220k", 4), ("ocw-pe.710-2-hand_backhand-220k", 3), ("wimbledon-konta-new-validation", 2)):
            result = self.cases[key]
            self.assertEqual(result["negative_new_peak_count"], 0)
            self.assertEqual(len({i for row in result["negative_window_results"] for i in row["new_candidate_windows_overlap"]}), expected)

    def test_filter_scale_clock_and_values_recompute_from_numeric_evidence(self):
        count = 0
        for result in self.cases.values():
            report = result["pose_only_report"]
            self.assertTrue(report["experimental"])
            self.assertFalse(report["registered_in_production"])
            self.assertTrue(result["performance_semantic_comparison"]["all_output_fields_identical"])
            for candidate in report["candidates"]:
                count += 1
                for key in ("original_grade", "proxy_grade", "score", "technique_label", "dominant_hand"):
                    self.assertIsNone(candidate[key])
                self.assertFalse(candidate["automatic_scoring_allowed"])
                self.assertFalse(candidate["contact_confirmed"])
                evidence, measured = candidate["evidence"], candidate["measurements"]
                context = evidence["filter_context"]
                offset, length = context["window_offset"], context["window_count"]
                for joint in ("wrist", "elbow"):
                    raw = context["raw_body_relative_"+joint]
                    rebuilt = [[statistics.median(p[axis] for p in raw[max(0,i-1):i+2]) for axis in (0,1)] for i in range(offset, offset+length)]
                    self.assertEqual(rebuilt, evidence["filtered_body_relative_"+joint])
                    self.assertEqual(raw[offset:offset+length], evidence["raw_body_relative_"+joint])
                    self.assertAlmostEqual(math.dist(rebuilt[0], rebuilt[-1]), measured[joint+"_net_excursion_torso"])
                normal = evidence["segment_normalization"]
                self.assertEqual(normal["source_frame_indexes"], list(range(normal["start_frame"], normal["end_frame"]+1)))
                self.assertEqual(statistics.median(normal["torso_scale_reference_px_values"]), evidence["segment_fixed_torso_scale_reference_px"])
                self.assertEqual(context["source_frames"][offset:offset+length], evidence["source_frames"])
                for row in context["source_frames"]:
                    self.assertEqual(row["video_sha256"], result["video_sha256"])
                for a,b in zip(context["source_frames"], context["source_frames"][1:]):
                    self.assertEqual(b["frame_index"], a["frame_index"]+1)
                    self.assertGreater(b["pts"], a["pts"])
                angles = evidence["image_shoulder_axis_deg"]
                self.assertAlmostEqual(max(angles)-min(angles), measured["shoulder_axis_range_deg"])
                wrist, elbow = (evidence["filtered_body_relative_"+joint] for joint in ("wrist", "elbow"))
                wnet, enet = math.dist(wrist[0],wrist[-1]), math.dist(elbow[0],elbow[-1])
                path = sum(math.dist(a,b) for a,b in zip(wrist,wrist[1:]))
                self.assertAlmostEqual(wnet/path, measured["wrist_path_efficiency"])
                cosine = sum((elbow[-1][i]-elbow[0][i])*(wrist[-1][i]-wrist[0][i]) for i in (0,1))/(wnet*enet)
                self.assertAlmostEqual(cosine, measured["elbow_wrist_net_cosine"])
                times = [Fraction(row["pts"]*row["time_base_num"],row["time_base_den"]) for row in evidence["source_frames"]]
                speeds = [None]+[math.dist(wrist[i-1],wrist[i+1])/float(times[i+1]-times[i-1]) for i in range(1,length-1)]+[None]
                peak = candidate["peak_frame"]-candidate["start_frame"]
                self.assertAlmostEqual(speeds[peak], measured["peak_wrist_speed_torso_per_second"])
                self.assertEqual([max(speeds[1:3]), max(speeds[-3:-1])], measured["endpoint_wrist_speeds_torso_per_second"])
        self.assertEqual(count, 39)

    def test_new_inferences_have_actual_clean_imports_and_configuration(self):
        for key, frames in (("wimbledon-konta-new-validation", 204), ("rick-explanation-negative", 250)):
            result = self.cases[key]
            proof = result["inference_provenance"]
            self.assertEqual(proof["head"], "3a3c5f8e500adac2ccd386b62f54b2630c7a2c45")
            self.assertTrue(proof["all_imports_from_clean_archive"])
            self.assertEqual(len(proof["imports"]), 101)
            self.assertEqual(proof["configuration"]["frame_stride"], 1)
            self.assertEqual(proof["model_configuration"], {"device":"cpu", "detect_imgsz":640, "pose_imgsz":640})
            self.assertEqual(result["recorded_inference_configuration"]["processing"]["processed_frames"], frames)
            self.assertEqual(result["weight_file_sha256_at_replay"]["yolo26n-pose.pt"], result["pose_model_sha256"].lower())


if __name__ == "__main__":
    unittest.main()
