"""Real frozen M09 observations; no synthetic trajectory is a video positive."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest

from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_service.gs_card_execution import compute_gs_card_execution, validate_stage_annotations
from scripts.replay_gs_follow_through_validation import (
    FIXED_WINDOWS, FROZEN_SHA, assert_source_windows, build_annotations,
    compact_report, normalized_sha256, replay, source_ms, validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT/"tests/fixtures/gs-follow-through-windows-20261024.json"
RESULTS = ROOT/"tests/fixtures/gs-follow-through-results-20261024.json"


class GSFollowThroughValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(WINDOWS.read_text(encoding="utf-8"))
        cls.expected = json.loads(RESULTS.read_text(encoding="utf-8"))
        cls.raw = next(case for case in json.loads((ROOT/"tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))["cases"]
            if case["case_id"] == cls.manifest["case"]["case_id"])
        cls.document = build_annotations(cls.manifest)
        cls.report = cls.compute(cls.raw, cls.document)

    @staticmethod
    def compute(raw, document):
        return compute_gs_card_execution(raw["frame_records"], raw["primary_timeline"], raw["decoder_timeline"],
            document, source_job_id=raw["source_job_id"])

    def test_frozen_boundaries_parent_revisions_and_historical_inputs(self):
        self.assertEqual(normalized_sha256(WINDOWS.read_bytes()), self.expected["window_manifest_utf8_lf_sha256"])
        self.assertEqual(self.expected["frozen_boundary_sha256"], FROZEN_SHA)
        for name, spec in self.manifest["historical_inputs"].items():
            self.assertEqual(normalized_sha256((ROOT/name).read_bytes()), spec["utf8_lf_sha256"])
        old = {row["action_id"]: row for row in self.raw["annotations"]["actions"]}
        for review, action, stage, (first, last) in zip(self.manifest["frozen_boundary_source"]["document"]["cases"],
                self.document["actions"], self.document["stages"], FIXED_WINDOWS):
            self.assertEqual(old[review["action_id"]], review["old_parent"])
            self.assertNotEqual(action["action_id"], review["action_id"])
            self.assertEqual(action["start_ms"], review["old_parent"]["start_ms"])
            self.assertGreater(action["end_ms"], review["old_parent"]["end_ms"])
            self.assertEqual(action["end_ms"], review["end_bracket"][-1]["timestamp_ms"])
            self.assertEqual(review["source_frames"], list(range(first, last+1)))
            self.assertEqual(stage["start_ms"], source_ms(self.raw["decoder_timeline"][first]))
            self.assertEqual(stage["end_ms"], source_ms(self.raw["decoder_timeline"][last]))
        assert_source_windows(self.manifest, self.report)

    def test_original_observations_recompute_twenty_full_instances(self):
        compact = compact_report(self.manifest, self.report)
        self.assertEqual(compact["instances"], self.expected["instances"])
        self.assertEqual(compact["stage_windows"], self.expected["stage_windows"])
        self.assertEqual(compact["condition_diagnostics"], self.expected["condition_diagnostics"])
        self.assertEqual(Counter(row["status"] for row in compact["instances"]), {"scored": 5, "unavailable": 15})
        self.assertEqual(compact["summary"]["card_instance_count"], 20)
        self.assertFalse(self.report["input"]["artifact_files_verified"])
        for row in compact["instances"]:
            self.assertIsNone(row["original_grade"])
            self.assertFalse(row["source_grade_supported"])
            self.assertEqual(row["source_stage_evidence"]["source_frames"],
                next(window["source_frames"] for window in compact["stage_windows"] if window["annotation_id"] == row["event_id"]))
            if row["status"] == "unavailable":
                self.assertIsNone(row["grade"])
                self.assertIsNone(row["score"])

    def test_metric_specific_sample_gates_do_not_blanket_reject_five_frame_axis_ranges(self):
        cards = {row["indicator_id"]: row["instances"] for row in self.report["cards"]}
        rotation = cards["GS01-M09-01"]
        self.assertEqual([row["score"] for row in rotation], [100, 100, 100, None])
        for row in rotation[:3]:
            self.assertTrue(all(c["value"] is not None and c["passed"] is True for c in row["conditions"]))
        movement = cards["GS01-M09-02"]
        for row in movement:
            self.assertIsNone(row["score"])
            self.assertIsNone(row["conditions"][0]["value"])
            self.assertIn("continuous_endpoint_window_missing", row["conditions"][0]["evidence"]["reason_codes"])
            self.assertIsNotNone(row["conditions"][1]["value"], "measured tilt must survive a missing displacement")
            self.assertIsNone(row["conditions"][1]["passed"], "incomplete card cannot gain a grade")
        approach = cards["GS01-M09-04"]
        self.assertEqual([row["score"] for row in approach], [20, None, None, None])
        self.assertAlmostEqual(approach[0]["conditions"][0]["value"], -.0846946610547554)
        self.assertFalse(approach[0]["conditions"][0]["passed"])
        self.assertEqual([row["conditions"][0]["evidence"]["total_frame_count"] for row in approach], [6, 5, 5, 6])
        self.assertEqual(approach[3]["conditions"][0]["evidence"]["valid_frame_count"], 5)

    def test_original_control_requirement_is_not_proved_by_duplicate_approach_predicate(self):
        rules = {row["indicator_id"]: row for row in load_card_rules()["cards"]}
        approach, control = rules["GS01-M09-04"], rules["GS01-M09-05"]
        self.assertEqual((approach["source"]["table"], control["source"]["table"]), ("T047", "T048"))
        self.assertIn("主动接近", approach["effective"]["definition"])
        self.assertIn("控制", control["effective"]["definition"])
        self.assertEqual(approach["evaluation"]["conditions"], control["evaluation"]["conditions"])
        self.assertEqual(approach["evaluation"]["conditions"][0]["metric"], "wrist_separation_torso.delta")
        cards = {row["indicator_id"]: row["instances"] for row in self.report["cards"]}
        for a, b in zip(cards["GS01-M09-04"], cards["GS01-M09-05"]):
            self.assertEqual(a["conditions"], b["conditions"])
        self.assertFalse(self.expected["summary"]["original_contact_or_control_verified"])
        self.assertFalse(self.expected["summary"]["independent_behavior_count_claimed"])

    def test_no_widening_wrong_source_parent_or_external_grade_authorization(self):
        mutations = [
            lambda m: m["frozen_boundary_source"]["document"]["cases"][0]["source_frames"].append(46),
            lambda m: m["case"].update(video_sha256="a"*64),
            lambda m: m["frozen_boundary_source"]["document"]["cases"][0]["proposed_parent_revision"].update(end_ms=2950),
            lambda m: m["frozen_boundary_source"]["document"]["cases"][0]["end_estimate"].update(pts=271801),
        ]
        for mutate in mutations:
            manifest = deepcopy(self.manifest); mutate(manifest)
            with self.assertRaises(ValueError):
                validate_manifest(manifest)
        document = deepcopy(self.document); document["stages"][0]["score"] = 100
        with self.assertRaises(ValueError):
            validate_stage_annotations(document)
        report = deepcopy(self.report); report["stage_windows"][0]["source_frames"].append(46)
        with self.assertRaises(ValueError):
            assert_source_windows(self.manifest, report)

    def test_missing_boundary_frame_does_not_silently_shrink_the_scored_window(self):
        raw = deepcopy(self.raw)
        raw["frame_records"] = [row for row in raw["frame_records"] if row["frame"]["index"] != 40]
        raw["primary_timeline"] = [row for row in raw["primary_timeline"] if row["source_frame_index"] != 40]
        try:
            report = self.compute(raw, self.document)
        except ValueError:
            return  # Complete source inventory rejection is also conservative.
        first = [i for row in report["cards"] for i in row["instances"] if i["event_id"] == self.document["stages"][0]["annotation_id"]]
        self.assertEqual(len(first), 5)
        self.assertTrue(all(i["score"] is None for i in first))
        self.assertEqual(report["stage_windows"][0]["source_frames"], list(range(40, 46)))

    def test_actual_media_replay_when_external_assets_are_available(self):
        artifact = Path(os.environ.get("CARD_ARTIFACT_ROOT", str(ROOT)))
        if not (artifact/self.manifest["case"]["local_video"]).is_file():
            self.skipTest("original_MIT_media_and_frozen_review_assets_unavailable")
        output = artifact/".codex_tmp/gs-follow-through-validation-20261024/integration-test"
        result = json.loads(replay(WINDOWS, artifact, output).read_text(encoding="utf-8"))
        self.assertEqual(result["instances"], self.expected["instances"])
        self.assertTrue(result["input"]["artifact_files_verified"])
        self.assertTrue(result["input"]["decoder_verified_from_original_video"])
        self.assertTrue(result["runtime"]["all_imported_source_matches_commit"])


if __name__ == "__main__":
    unittest.main()
