"""Canonical primary players and detector tracks are distinct bound identities."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import model_geometry_evidence
from rallymate_scoring.batch import score_indicator_records
from rallymate_scoring.loop import run_minimum_scoring_loop
from rallymate_scoring.scoring import bind_primary_player_score_trace
from tests.test_direct_scoring_rules import evaluate, feature, rule
from tests.test_sam_footwork import sam_motion_fixture


ROOT = Path(__file__).resolve().parents[1]


class ScoringIdentityNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.records, self.timeline = sam_motion_fixture()
        proof = model_geometry_evidence(pose_sequence_from_records(self.records, self.timeline), range(4))
        self.feature = {**feature(), **proof, "confidence": None}
        self.record = {"video_id": "source-video", "event_id": "event", "event_code": "FS01",
                       "person_track_id": 1, "indicator_id": "FS01-M02", "features": [self.feature],
                       "feature_status": "measured", **deepcopy(proof),
                       "track_diagnostics": {"primary_player_id": 1, "source_track_ids": [7],
                                             "primary_identity_ambiguous": False}}

    def batch(self, record=None, **options):
        rows, _ = score_indicator_records([record or self.record], calibrations={"FS01-M02": rule()},
                                          model_versions={}, allow_test_only=True, **options)
        return rows[0]

    def test_batch_preserves_canonical_identity_and_binds_detector_track(self):
        result = self.batch()
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["grade"], "B")
        self.assertEqual(result["person_track_id"], 1)
        trace = result["evidence"][0]
        self.assertEqual(trace["person_track_id"], 1)
        self.assertEqual(trace["identity_namespace"], "primary_player")
        self.assertEqual(trace["primary_player_id"], 1)
        self.assertEqual(trace["source_track_id"], 7)
        self.assertNotIn("geometry_mask", trace["source_identity_binding"])
        self.assertNotIn("features", trace["source_identity_binding"])

    def test_no_primary_player_number_is_a_bypass(self):
        self.assertEqual(evaluate(self.feature, rule(), evidence=[{"person_track_id": 1}])["status"], "unavailable")
        self.assertEqual(evaluate(self.feature, rule(), evidence=[{"person_track_id": 7}])["grade"], "B")
        for change in ({"track_diagnostics": {}}, {"person_track_id": 2},
                       {"track_diagnostics": {"primary_player_id": 1, "source_track_ids": [8]}},
                       {"track_diagnostics": {"primary_player_id": 1, "source_track_ids": [7, 8]}},
                       {"track_diagnostics": {"primary_player_id": 1, "source_track_ids": [7], "primary_identity_ambiguous": True}}):
            with self.subTest(change=change):
                self.assertEqual(self.batch({**self.record, **change})["status"], "unavailable")

    def test_explicit_source_alias_must_match_in_every_namespace(self):
        for namespace in (None, "source_track"):
            trace = {"person_track_id": 7, "source_track_id": 7}
            if namespace is not None:
                trace["identity_namespace"] = namespace
            self.assertEqual(evaluate(self.feature, rule(), evidence=[trace])["grade"], "B")
            for source in (8, True, "7", None):
                with self.subTest(namespace=namespace, source=source):
                    self.assertEqual(evaluate(self.feature, rule(), evidence=[{**trace, "source_track_id": source}])["status"], "unavailable")

    def test_feature_cannot_rewrite_the_independent_event_source(self):
        record = deepcopy(self.record)
        window = record["features"][0]["source_window"]
        window["person_track_id"] = 8
        for binding in window["source_bindings"]:
            binding["person_track_id"] = 8
        result = self.batch(record)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["evidence"][0]["source_track_id"], 7)

    def test_authoritative_event_wins_over_conflicting_persisted_mapping(self):
        record = deepcopy(self.record)
        record["track_diagnostics"]["source_track_ids"] = [8]
        result = self.batch(record, authoritative_events=[deepcopy(self.record)])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["evidence"][0]["source_track_id"], 7)
        wrong_event = {**self.record, "person_track_id": 2}
        self.assertEqual(self.batch(authoritative_events=[wrong_event])["status"], "unavailable")
        other_event = {**self.record, "event_id": "unrelated-event"}
        self.assertEqual(self.batch(authoritative_events=[other_event])["status"], "unavailable")
        self.assertEqual(self.batch(authoritative_events=[])["status"], "unavailable")

    def test_transport_rejects_modified_identity_event_and_frame_proof(self):
        trace = self.batch()["evidence"][0]
        mutations = (
            lambda t: t.update(primary_player_id=2),
            lambda t: t.update(source_track_id=8),
            lambda t: t.update(event_id="other-event"),
            lambda t: t.update(video_id="other-video"),
            lambda t: t.update(identity_namespace="invented"),
            lambda t: t["source_identity_binding"].update(primary_player_id=2),
            lambda t: t["source_identity_binding"]["source_window"].update(selection_epoch=99),
            lambda t: t["source_identity_binding"]["source_window"]["source_bindings"][0].update(pts=999),
        )
        for mutate in mutations:
            broken = deepcopy(trace)
            mutate(broken)
            with self.subTest(trace=broken):
                self.assertEqual(evaluate(self.feature, rule(), evidence=[broken])["status"], "unavailable")

    def test_oversize_mapping_window_is_not_transported(self):
        oversized = deepcopy(self.record)
        oversized["source_window"]["source_frames"] = list(range(20001))
        trace = bind_primary_player_score_trace({"person_track_id": 1}, oversized)
        self.assertNotIn("source_identity_binding", trace)

    def test_real_loop_and_batch_rescore_keep_primary_one_source_seven(self):
        for row in self.timeline:
            row["selection_algorithm_version"] = "primary-player-v0.3.0"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            frames, timeline = folder / "frames.jsonl", folder / "timeline.jsonl"
            for destination, rows in ((frames, self.records), (timeline, self.timeline)):
                destination.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = run_minimum_scoring_loop(frames_path=frames, primary_timeline_path=timeline,
                output_dir=folder, feasibility_registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
                source_id="namespace-fixture", video_id="namespace-fixture", pose_model={"backend": "test", "runtime": "cpu"})
            first = next(row for row in output["indicator_records"] if row["scoring_status"] == "calibration_required")
            measured = first.get("scoring_features", first["features"])[0]
            definition = {**rule(), "indicator_id": first["indicator_id"], "primary_feature": measured["feature_name"],
                          "primary_feature_version": measured["feature_version"], "unit": measured["unit"]}
            graded = run_minimum_scoring_loop(frames_path=frames, primary_timeline_path=timeline,
                output_dir=folder, feasibility_registry_path=ROOT / "metric-feasibility-pose-wave-v2.json",
                source_id="namespace-fixture", video_id="namespace-fixture", pose_model={"backend": "test", "runtime": "cpu"},
                calibrations={first["indicator_id"]: definition})
            selected = next(row for row in graded["scores"] if row["indicator_id"] == first["indicator_id"])
            self.assertEqual(selected["status"], "scored")
            self.assertEqual(selected["evidence"][0]["primary_player_id"], 1)
            self.assertEqual(selected["evidence"][0]["source_track_id"], 7)
        self.assertTrue(output["events"])
        eligible = [row for row in output["indicator_records"] if row["scoring_status"] == "calibration_required"]
        self.assertTrue(eligible)
        for row in eligible:
            self.assertEqual(row["person_track_id"], 1)
            self.assertEqual(row["source_window"]["person_track_id"], 7)
        rescored, _ = score_indicator_records(output["indicator_records"], calibrations={}, model_versions={})
        self.assertEqual([r["status"] for r in rescored], [r["status"] for r in output["scores"]])
        for row in rescored:
            self.assertFalse(any(reason.startswith("model_primary_source_") for reason in row["reason_codes"]))


if __name__ == "__main__":
    unittest.main()
