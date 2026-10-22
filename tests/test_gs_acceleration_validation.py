"""Frozen real M07 windows: sampling failures remain evidence, never zero grades."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_service.gs_card_execution import compute_gs_card_execution
from scripts.replay_gs_acceleration_validation import build_annotations, compact_case, source_ms

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "tests/fixtures/gs-acceleration-windows-20261022.json"
RESULTS = ROOT / "tests/fixtures/gs-acceleration-results-20261022.json"


class GSAccelerationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(WINDOWS.read_text(encoding="utf-8"))
        cls.result = json.loads(RESULTS.read_text(encoding="utf-8"))
        # The existing committed real fixture already retains the entire source
        # clips. Reuse its unchanged observations, not saved preparation grades.
        cls.raw = {case["case_id"]: case for case in json.loads((ROOT /
            "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))["cases"]}
        cls.expected = {case["case_id"]: case for case in cls.result["cases"]}

    def test_review_inventory_was_frozen_and_all_twelve_actions_remain(self):
        self.assertEqual(hashlib.sha256(WINDOWS.read_bytes()).hexdigest(), self.result["window_manifest_sha256"])
        reviews = [review for case in self.manifest["cases"] for review in case["reviews"]]
        self.assertEqual(len(reviews), 12)
        self.assertEqual(Counter(row["decision"] for row in reviews),
            {"manual_source_stage_candidate": 11, "phase_unresolved": 1})
        self.assertEqual(sum(len(row["inspection"]["source_frames"]) for row in reviews), 195)
        self.assertTrue(self.manifest["annotation_frozen_before_scoring"])
        self.assertFalse(self.manifest["policy"]["threshold_tuning"])
        self.assertFalse(self.manifest["policy"]["generic_phase_relabeling"])
        for row in reviews:
            self.assertFalse(row["contact_truth"])
            self.assertFalse(row["source_grade_supported"])
            self.assertEqual(row["reviewer_kind"], "nonexpert_agent_visual_estimate")

    def test_source_stage_definitions_and_engineering_revisions_are_preserved(self):
        stages = {row["stage_code"]: row for row in load_card_rules()["stages"]}
        for case in self.manifest["cases"]:
            stage = stages[case["stage_code"]]
            self.assertTrue(case["stage_code"].endswith("-M07"))
            self.assertEqual(case["source_stage"]["source_start"], stage["source"]["start"])
            self.assertEqual(case["source_stage"]["source_end"], stage["source"]["end"])
            self.assertEqual(case["source_stage"]["effective"], stage["effective"])
            self.assertEqual(case["source_stage"]["boundary_origin"], stage["boundary_origin"])
            self.assertEqual(case["source_stage"]["revision_id"], stage["revision_id"])
            if case["technique_code"] == "GS01":
                for review in case["reviews"]:
                    self.assertIn("predicted_hitting_trajectory_convergence", review["unverified_source_requirements"])

    def test_every_boundary_refers_to_original_decoder_pts_and_short_windows_stay_short(self):
        for case in self.manifest["cases"]:
            original = self.raw[case["case_id"]]
            self.assertEqual(case["video_sha256"], original["video_sha256"])
            self.assertEqual(case["source_run_id"], original["source_job_id"])
            document = build_annotations(case, self.manifest["annotation_version"])
            accepted = [row for row in case["reviews"] if row["decision"] == "manual_source_stage_candidate"]
            self.assertEqual(len(document["stages"]), len(accepted))
            for review, stage in zip(accepted, document["stages"]):
                self.assertIn(len(review["source_frames"]), (2, 3, 5))
                for key, edge in (("start_boundary", "start_ms"), ("end_boundary", "end_ms")):
                    sample = review[key]["estimate"]
                    raw = original["decoder_timeline"][sample["frame_index"]]
                    for field in ("frame_index", "pts", "time_base_num", "time_base_den", "video_sha256"):
                        self.assertEqual(sample[field], raw[field])
                    self.assertEqual(stage[edge], source_ms(raw))
                self.assertEqual(stage["binding"]["uncertainty_ms"], review["uncertainty_ms"])

    def test_original_observations_reproduce_all_fifty_five_new_instances(self):
        all_instances = []
        for case in self.manifest["cases"]:
            raw = self.raw[case["case_id"]]
            document = build_annotations(case, self.manifest["annotation_version"])
            report = compute_gs_card_execution(raw["frame_records"], raw["primary_timeline"], raw["decoder_timeline"],
                document, source_job_id=raw["source_job_id"])
            actual = compact_case(case, report)
            expected = self.expected[case["case_id"]]
            self.assertFalse(report["input"]["artifact_files_verified"])
            self.assertFalse(report["input"]["service_eligible"])
            self.assertTrue(expected["input"]["artifact_files_verified"])
            self.assertTrue(expected["input"]["decoder_verified_from_original_video"])
            for key in ("instances", "stage_windows", "coverage", "status_counts", "reason_counts", "executed_card_ids", "summary"):
                self.assertEqual(actual[key], expected[key], (case["case_id"], key))
            all_instances.extend(actual["instances"])
        self.assertEqual(len(all_instances), 55)
        self.assertEqual(Counter(row["status"] for row in all_instances),
            {"proxy_scored": 4, "review_required": 18, "unavailable": 33})
        for row in all_instances:
            self.assertIsNone(row["original_grade"])
            self.assertFalse(row["source_grade_supported"])
            if row["status"] != "proxy_scored":
                self.assertIsNone(row["score"])
                self.assertIsNone(row["proxy_grade"])

    def test_no_new_wrist_acceleration_score_is_implied_by_balance_proxy(self):
        rows = [row for case in self.result["cases"] for row in case["instances"]]
        acceleration = [row for row in rows if row["indicator_id"].endswith("-01")]
        self.assertEqual(len(acceleration), 11)
        self.assertTrue(all(row["status"] == "unavailable" and row["score"] is None for row in acceleration))
        scored = [row for row in rows if row["status"] == "proxy_scored"]
        self.assertEqual({row["indicator_id"] for row in scored}, {"GS01-M07-04"})
        for row in scored:
            self.assertEqual({condition["metric"] for condition in row["conditions"]},
                {"hip_inside_ankle_support_fraction.mean", "trunk_tilt_deg.range"})
            self.assertEqual(row["quality"]["total_frame_count"], 5)
            self.assertEqual(row["quality"]["valid_frame_count"], 5)

    def test_unresolved_end_is_excluded_instead_of_padded_or_relabelled(self):
        case = self.manifest["cases"][1]
        document = build_annotations(case, self.manifest["annotation_version"])
        rejected = case["reviews"][-1]
        self.assertEqual(rejected["action_id"], "backhand-4")
        self.assertIsNone(rejected["end_boundary"])
        self.assertNotIn(rejected["annotation_id"], {row["annotation_id"] for row in document["stages"]})
        self.assertEqual(len(document["actions"]), 4)
        self.assertEqual(len(document["stages"]), 3)
        self.assertEqual(self.result["summary"]["phase_unresolved_count"], 1)

    def test_tampered_pts_window_and_understated_uncertainty_are_rejected(self):
        for target in ("time", "frames", "uncertainty"):
            case = deepcopy(self.manifest["cases"][0])
            row = case["reviews"][0]
            if target == "time": row["start_boundary"]["estimate"]["timestamp_ms"] += 1
            if target == "frames": row["source_frames"].pop()
            if target == "uncertainty": row["uncertainty_ms"] = 1
            with self.subTest(target=target), self.assertRaises(ValueError):
                build_annotations(case, self.manifest["annotation_version"])

    def test_actual_production_results_are_bound_to_full_committed_module_closure(self):
        runtime = self.result["runtime"]
        self.assertEqual(runtime["base_commit"], self.manifest["runtime_base_commit"])
        self.assertTrue(runtime["all_imported_source_matches_commit"])
        self.assertEqual(len(runtime["modules"]), 79)
        for name in ("src/rallymate_service/gs_card_execution.py", "src/rallymate_scoring/card_kinematics.py",
                     "src/rallymate_scoring/card_evaluation.py", "src/rallymate_scoring/quality_policy.py",
                     "src/rallymate_scoring/data/card_rules_v1.json"):
            self.assertIn(name, runtime["modules"])
        for digest in self.result["full_report_sha256"].values():
            self.assertRegex(digest, "^[0-9a-f]{64}$")
        self.assertEqual(self.result["summary"]["original_graded_instance_count"], 0)
        self.assertIsNone(self.result["summary"]["overall_score"])


if __name__ == "__main__":
    unittest.main()
