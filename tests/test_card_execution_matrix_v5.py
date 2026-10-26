"""V5 contract tests recompute committed recorded observations, not media IO.

No fixture is relabelled as a fresh decoder run. The replay CLI separately
requires exact committed source, original videos, immutable artifacts and all
13 groups; this suite tests its source/aggregation/retirement boundaries.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import build_card_execution_matrix_v5 as m
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_service.gs_card_execution import compute_gs_card_execution

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return m.read_json(ROOT/"tests/fixtures"/name)


def rows(report):
    return [r for card in report["cards"] for r in card["instances"]]


class MatrixV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = fixture("card-execution-inputs-v5.json")
        cls.rules = {r["indicator_id"]: r for r in load_card_rules()["cards"]}
        cls.raw = fixture("directional-micro-adjustment-native-e420-v1.json")
        cls.applications = {}
        for entry in cls.manifest["executions"][-3:-1]:
            document = m.read_json(ROOT/entry["annotation_path"])
            report = m.recompute_application(entry, cls.raw["records"], cls.raw["primary_timeline"], cls.raw["source_events"],
                cls.raw["decoder_timeline"], document)
            cls.applications[entry["route"]] = (entry, report)
        raw = next(c for c in fixture("gs-card-real-mit-preparation-v1.json")["cases"]
            if c["case_id"] == "ocw-pe.710-2-forehand-220k")
        follow = m.load_follow(ROOT)
        cls.follow = m.json_safe(compute_gs_card_execution(raw["frame_records"], raw["primary_timeline"], raw["decoder_timeline"],
            follow.build_annotations(fixture("gs-follow-through-windows-20261024.json")), source_job_id=raw["source_job_id"]))
        cls.history = fixture("gs-follow-through-results-20261024.json")

    def validate(self, row, entry):
        m.validate_instance(row, self.rules[row["indicator_id"]], video_sha256=entry["video_sha256"], video_id=entry["source_run_id"])

    def group(self, entry, report):
        """Numeric collection only, deliberately not an artifact-verified bundle."""
        group = deepcopy(entry)
        group["instances"] = deepcopy(rows(report))
        for row in group["instances"]:
            row["full_instance_sha256"] = m.canonical(row)
        group["pose_export_id"] = m.pose_export_id(group)
        return group

    def test_manifest_preserves_old_ten_and_adds_exact_three(self):
        # Old media-local annotations are intentionally absent from clean CI.
        # Structural validation still reads committed v3/v4 and audit baselines.
        m.validate_input_manifest(ROOT, self.manifest, verify_sources=False)
        self.assertEqual(len(self.manifest["executions"]), 13)
        self.assertEqual(self.manifest["executions"][:10], fixture("card-execution-inputs-v4.json")["executions"])
        self.assertEqual({r["route"] for r in self.manifest["executions"][10:]}, m.APP_ROUTES | {"gs_follow_through"})
        for entry in self.manifest["executions"][10:]:
            for name, hashes in entry["annotation_source_digests"].items():
                m.v4.verify_text_artifact(ROOT/name, hashes["raw_sha256"], hashes["utf8_lf_sha256"], "new_annotation_changed")

    def test_manifest_mutations_rejected(self):
        for kind in ("old_source", "duplicate", "route", "annotation_digest"):
            value = deepcopy(self.manifest)
            if kind == "old_source": value["executions"][0]["source_run_id"] = "native-substitution"
            if kind == "duplicate": value["executions"][-1]["id"] = value["executions"][0]["id"]
            if kind == "route": value["executions"][-1]["route"] = "automatic_truth"
            if kind == "annotation_digest": value["executions"][-1]["annotation_source_digests"] = {}
            with self.subTest(kind=kind), self.assertRaises(ValueError): m.validate_input_manifest(ROOT, value, verify_sources=False)

    def test_three_real_recorded_applications_recomputed_without_relabelling_manual_catalogue(self):
        for entry, report in self.applications.values():
            self.assertFalse(report["input"]["artifact_files_verified"])
            for row in rows(report):
                self.validate(row, entry)
                self.assertEqual(self.rules[row["indicator_id"]]["evaluation"]["kind"], "manual_review")
                self.assertEqual((row["status"], row["grade"], row["score"]), ("scored", "C", 60))
                self.assertEqual(row["source_window_evidence"]["source_frames"], list(range(112, 137)))
            with self.assertRaisesRegex(ValueError, "record_only_or_research_report_rejected"):
                m.compact_report(report, entry, self.rules)

    def test_recorded_cycle_native_digest_is_checked_without_certifying_media_io(self):
        from tests.test_footwork_cycle_card_evidence import real_fixture
        from rallymate_service.footwork_cycle_execution import compute_footwork_cycle_execution
        sequence, event, timeline, binding = real_fixture()
        report = compute_footwork_cycle_execution(sequence, [event], timeline, [binding], source_run_id=event["video_id"])
        entry = next(e for e in self.manifest["executions"] if e["route"] == "footwork_cycle")
        # Recorded trajectories test the native source schema, not filesystem
        # verification. Do not turn any media-verification flag on here.
        report["input"].update(source_video_sha256=entry["video_sha256"],
            input_artifact_sha256=deepcopy(entry["input_artifact_sha256"]))
        self.assertNotIn("video_sha256", report["input"])
        with self.assertRaisesRegex(ValueError, "record_only_or_research_report_rejected"):
            m.compact_report(report, entry, self.rules)
        for kind in ("missing_native", "wrong_native_with_valid_alias", "missing_raw", "wrong_raw", "extra_raw"):
            changed = deepcopy(report)
            if kind == "missing_native":
                changed["input"]["video_sha256"] = changed["input"].pop("source_video_sha256")
            if kind == "wrong_native_with_valid_alias":
                changed["input"].update(source_video_sha256="0"*64, video_sha256=entry["video_sha256"])
            if kind == "missing_raw": changed["input"]["input_artifact_sha256"].pop("events.jsonl")
            if kind == "wrong_raw": changed["input"]["input_artifact_sha256"]["frames.jsonl"] = "0"*64
            if kind == "extra_raw": changed["input"]["input_artifact_sha256"]["unbound.json"] = "0"*64
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "report_source_artifact_binding_mismatch"):
                m.compact_report(changed, entry, self.rules)

    def test_actual_usta_cycle_adapter_compacts_all_thirty_null_instances(self):
        from rallymate_service.footwork_cycle_artifacts import build_footwork_cycle_artifact_report
        entry = next(e for e in self.manifest["executions"] if e["route"] == "footwork_cycle")
        artifact_root = Path(os.environ.get("CARD_ARTIFACT_ROOT", ROOT))
        directory, video = artifact_root/entry["run_dir"], artifact_root/entry["local_video"]
        if not directory.is_dir() or not video.is_file():
            self.skipTest("physical USTA originals unavailable; recorded cycle schema test still executes")
        before = {name: m.sha(directory/name) for name in entry["input_artifact_sha256"]}
        self.assertEqual(before, entry["input_artifact_sha256"])
        report = build_footwork_cycle_artifact_report(directory,
            m.read_json(artifact_root/entry["annotation_path"]), video_path=video)
        self.assertTrue(all(report["input"][key] is True for key in
            ("artifact_files_verified", "decoder_verified_from_original_video", "service_eligible")))
        self.assertNotIn("video_sha256", report["input"])
        self.assertEqual(report["input"]["source_video_sha256"], entry["video_sha256"])
        compact = m.compact_report(report, entry, self.rules)
        self.assertEqual((compact["instance_count"], compact["status_counts"]), (30, {"unavailable": 30}))
        self.assertTrue(all(row["score"] is None for row in compact["instances"]))
        self.assertEqual(before, {name: m.sha(directory/name) for name in before})

    def test_application_contract_and_arithmetic_mutations_rejected(self):
        entry, report = self.applications["directional_micro"]
        for kind in ("version", "application_version", "metric", "value", "threshold", "passed", "score", "grade", "missing_condition", "source_run"):
            row = deepcopy(rows(report)[0])
            if kind == "version": row["version"] = "old"
            if kind == "application_version": row["application_rule_version"] = "old"
            if kind == "metric": row["conditions"][0]["metric"] = "unknown"
            if kind == "value": row["conditions"][0]["value"] = 999
            if kind == "threshold": row["conditions"][0]["threshold"] = 0
            if kind == "passed": row["conditions"][0]["passed"] = not row["conditions"][0]["passed"]
            if kind == "score": row["score"] = 100
            if kind == "grade": row["grade"] = "A"
            if kind == "missing_condition": row["conditions"].pop()
            if kind == "source_run": row["video_id"] = "0a3df3a3-2629-4dd5-80a1-54e4b11f7341"
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, entry)

    def test_application_source_copies_and_complete_geometry_rejected(self):
        entry, report = self.applications["directional_micro"]
        for kind in ("frame", "clock", "geometry", "contact"):
            row = deepcopy(rows(report)[0]); proof = row["source_window_evidence"]["directional_micro_adjustment"]
            if kind == "frame": proof["source_frames"].pop()
            if kind == "clock": proof["source_frame_bindings"][0]["pts"] += 1
            if kind == "geometry": proof["valid_frame_count"] -= 1
            if kind == "contact": proof["contact_verified"] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, entry)

    def test_whole_raw_recomputation_detects_coordinated_saved_geometry_mutation(self):
        for _, report in self.applications.values():
            m.verify_application_recomputation(report, deepcopy(report))
            changed = deepcopy(report)
            row = rows(changed)[0]
            key = m.application_contracts()[row["indicator_id"]]["measurement_key"]
            row["source_window_evidence"][key]["source_geometry_series"][0]["source_binding"]["processed_index"] += 1
            with self.assertRaisesRegex(ValueError, "full_raw_recomputation"):
                m.verify_application_recomputation(changed, report)

    def test_application_source_ledger_rejects_missing_derived_wrong_pts_or_selected_person(self):
        entry, report = self.applications["directional_micro"]
        entry = {**entry, "route": "fs_card_windows"}
        original = rows(report)[0]
        m.v4.validate_source_ledger(original, entry)
        for kind in ("all_derived", "single_derived", "pts", "selected_person", "camera"):
            row = deepcopy(original); ledger = row["source_window_evidence"]["source_frame_bindings"]
            if kind == "all_derived":
                for bound in ledger: bound.pop("derived_selection_epoch")
            if kind == "single_derived": ledger[0]["derived_selection_epoch"] += 1
            if kind == "pts": ledger[0]["pts"] += 1
            if kind == "selected_person": ledger[0]["selected_person_track_id"] += 1
            if kind == "camera": ledger[0]["camera_reference_epoch"] += 1
            with self.subTest(kind=kind), self.assertRaises(ValueError): m.v4.validate_source_ledger(row, entry)

    def test_saved_summary_is_rechecked_not_used_as_coverage(self):
        entry, report = self.applications["directional_micro"]
        expected = m.v4.formal_summary(rows(report), [r["indicator_id"] for r in report["cards"]])
        self.assertEqual(report["summary"], expected)
        for field in ("formal_scored_instances", "formal_scored_cards", "overall_score"):
            mutated = deepcopy(report); mutated["summary"][field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "summary|overall"):
                m.compact_report(mutated, entry, self.rules)
        with self.assertRaises(ValueError): m.v4.formal_summary(rows(report)+rows(report), [r["indicator_id"] for r in report["cards"]])

    def test_top_application_window_and_document_copies(self):
        entry, report = self.applications["directional_micro"]
        group = self.group(entry, report)
        group.update(report_windows=deepcopy(report["fs_card_windows"]), source_input=deepcopy(report["input"]),
            annotation_canonical_sha256=report["input"]["annotation_document_sha256"])
        m.validate_windows(group)
        for kind in ("window", "input_document", "instance_document", "duplicate"):
            changed = deepcopy(group)
            if kind == "window": changed["report_windows"][0]["source_frames"].pop()
            if kind == "input_document": changed["source_input"]["annotation_document_sha256"] = "0"*64
            if kind == "instance_document": changed["instances"][0]["source_window_annotation"]["document_sha256"] = "0"*64
            if kind == "duplicate": changed["report_windows"].append(deepcopy(changed["report_windows"][0]))
            with self.subTest(kind=kind), self.assertRaises(ValueError): m.validate_windows(changed)

    def test_unknown_card_cannot_enter_source_collection(self):
        entry, report = self.applications["directional_micro"]
        changed = deepcopy(rows(report)); changed[0]["indicator_id"] = "FS99-M99"
        with self.assertRaisesRegex(ValueError, "unknown_card"):
            m.v4.validate_rows(changed, self.rules, entry, m)

    def test_source_identity_distinguishes_same_media_different_export_and_rejects_renamed_duplicate(self):
        entry, report = self.applications["directional_micro"]
        group = self.group(entry, report); other = deepcopy(group)
        other["input_artifact_sha256"]["frames.jsonl"] = "f"*64
        self.assertNotEqual(m.pose_export_id(group), m.pose_export_id(other))
        self.assertNotEqual(m.instance_identity(group, group["instances"][0]), m.instance_identity(other, other["instances"][0]))
        renamed = deepcopy(group); renamed["id"] = "renamed"
        for row in renamed["instances"]: row["event_id"] += "-renamed"
        with self.assertRaisesRegex(ValueError, "duplicate_observation_card_window"):
            m.score_groups([group, renamed])

    def test_same_media_export_track_with_different_continuity_never_averages(self):
        entry, report = self.applications["directional_micro"]
        original = self.group(entry, report)
        original["instances"] = original["instances"][:1]
        for field in ("selection_epoch", "derived_selection_epoch", "camera_reference_epoch"):
            changed = deepcopy(original); changed["id"] += "-other-window"
            row = changed["instances"][0]; row["event_id"] += "-other-window"
            proof = row["source_window_evidence"]
            proof["source_frames"] = [f+1000 for f in proof["source_frames"]]
            for binding in proof["source_frame_bindings"]:
                binding["frame_index"] += 1000; binding[field] += 1
            row["window"][field] += 1
            with self.subTest(field=field): self.assertEqual(len(m.score_groups([original, changed])), 2)

    def test_missing_or_raw_model_epoch_cannot_authorize_cross_window_pooling(self):
        entry, report = self.applications["directional_micro"]
        group = self.group(entry, report)
        for row in group["instances"]:
            for binding in row["source_window_evidence"]["source_frame_bindings"]: binding.pop("derived_selection_epoch", None)
        result = m.score_groups([group])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(g["continuity_scope"]["kind"] == "instance_only_continuity_not_established" for g in result))

    def test_new_primary_artifact_does_not_pool_equal_numeric_track_and_epoch_labels(self):
        entry, report = self.applications["directional_micro"]
        group = self.group(entry, report)
        first, second = deepcopy(group), deepcopy(group)
        first["instances"], second["instances"] = group["instances"][:1], group["instances"][1:]
        second["input_artifact_sha256"]["primary-player.jsonl"] = "a"*64
        self.assertEqual(len(m.score_groups([first, second])), 2)

    def test_retired_two_cards_recomputed_null_and_history_remains_audit_only(self):
        entry = self.manifest["executions"][-1]
        group = self.group(entry, self.follow)
        affected = [r for r in group["instances"] if r["indicator_id"] in m.RETIRED]
        self.assertEqual(len(affected), 8)
        for row in affected:
            self.validate(row, entry)
            self.assertEqual((row["status"], row["score"], row["conditions"]), ("unavailable", None, []))
        audit = m.retirement_audit(self.history, self.manifest["retirement_history"], [group])
        m.validate_retirement_audit(audit, [group])
        self.assertEqual(audit["withdrawn_scored_instances"], 2)
        self.assertEqual(len(audit["rows"]), 8)
        self.assertEqual(len(group["instances"]), 20)
        self.assertTrue(all(r["counted_as_additional_instance"] is False for r in audit["rows"]))
        changed = deepcopy(audit); changed["withdrawn_scored_instances"] += 1
        with self.assertRaises(ValueError): m.validate_retirement_audit(changed, [group])

    def test_retired_old_score_unknown_revision_or_auxiliary_pass_rejected(self):
        entry = self.manifest["executions"][-1]
        original = next(r for r in rows(self.follow) if r["indicator_id"] in m.RETIRED)
        for kind in ("old_score", "revision", "missing_observable", "aux_pass", "aux_score_authorization"):
            row = deepcopy(original)
            if kind == "old_score": row.update(status="scored", score=20, grade="E", proxy_grade="E")
            if kind == "revision": row["scoring_revision_id"] = "unknown"
            if kind == "missing_observable": row["missing_required_observables"] = []
            if kind == "aux_pass": row["auxiliary_observations"]["passed"] = True
            if kind == "aux_score_authorization": row["auxiliary_observations"]["scoring_eligible"] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, entry)

    def test_registry_keeps_298_manual_application_rules_and_retired_aux_distinct(self):
        registry = m.registry_snapshot(load_card_rules())
        self.assertEqual(len(registry), 298)
        self.assertEqual(registry, m.json_safe(registry))
        self.assertEqual({k for k,v in registry.items() if v["application_rule_registered"]}, {"FS05-M02", "FS05-M03", "FS05-M04"})
        for key in m.RETIRED:
            self.assertFalse(registry[key]["scoring_rule_registered"])

    def test_v5_source_closure_requires_new_runner_and_all_json_resources(self):
        needed = {k: {} for k in (*m.RUNNERS, "src/rallymate_service/directional_micro_adjustment.py",
            "src/rallymate_service/micro_adjustment_cycles.py", "src/rallymate_scoring/direction_reference_observables.py")}
        for missing in ("scripts/build_card_execution_matrix_v5.py", "src/rallymate_service/micro_adjustment_cycles.py"):
            partial = deepcopy(needed); partial.pop(missing)
            with patch.object(m.v4, "verify_closure"), self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "incomplete_v5"):
                m.verify_closure(ROOT, {"commit": "fake-contract-only", "files": partial})
        with patch.object(m.v4, "verify_closure"), patch.object(m.v4.subprocess, "check_output", return_value="src/extra.json\n"), \
                self.assertRaisesRegex(ValueError, "packaged_json"):
            m.verify_closure(ROOT, {"commit": "fake-contract-only", "files": needed})


if __name__ == "__main__":
    unittest.main()
