"""Integrity checks for source reconstruction; synthetic values never add coverage."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matrix = load("card_matrix_v3", ROOT / "scripts/build_card_execution_matrix_v3.py")
previous = load("card_matrix_v3_arithmetic", ROOT / "scripts/build_card_execution_matrix.py")
acceleration = load("card_matrix_v3_m07_source", ROOT / "scripts/replay_gs_acceleration_validation.py")


class CardExecutionMatrixV3SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = matrix.read_json(ROOT / "tests/fixtures/gs-acceleration-windows-20261022.json")
        cls.side = matrix.read_json(ROOT / "tests/fixtures/gs-racket-side-observations-20261022.json")
        cls.inputs = matrix.read_json(ROOT / "tests/fixtures/card-execution-inputs-v3.json")

    def source_pair(self):
        case = self.base["cases"][0]
        annotation = acceleration.build_annotations(case, self.base["annotation_version"])
        side = next(c for c in self.side["cases"] if c["case_id"] == case["case_id"])
        return annotation, deepcopy(side)

    def test_side_binding_merges_without_adding_or_extending_any_existing_window(self):
        before_total = after_total = 0
        for case in self.base["cases"]:
            original = acceleration.build_annotations(case, self.base["annotation_version"])
            side = next(c for c in self.side["cases"] if c["case_id"] == case["case_id"])
            merged = matrix.merge_racket_side(original, self.side, side, self.base)
            self.assertEqual(merged["stages"], original["stages"])
            self.assertEqual([{k: v for k, v in action.items() if k != "racket_side_observation"}
                              for action in merged["actions"]], original["actions"])
            self.assertEqual(sum("racket_side_observation" in a for a in merged["actions"]), 1)
            before_total += len(original["stages"])
            after_total += len(merged["stages"])
        self.assertEqual(before_total, after_total)
        self.assertEqual(before_total, 11)

    def test_side_observation_cannot_widen_action_or_stage_for_minimum_frames(self):
        original, side = self.source_pair()
        for collection in ("actions", "stages"):
            changed = deepcopy(side)
            changed["annotations"][collection][0]["end_ms"] += 500
            with self.assertRaisesRegex(ValueError, "racket_side_(action|stage)_window_changed"):
                matrix.merge_racket_side(original, self.side, changed, self.base)

    def test_side_source_run_person_and_video_are_bound_to_original_action(self):
        original, side = self.source_pair()
        for key in ("source_run_id", "video_sha256"):
            changed = deepcopy(side); changed["annotations"][key] = "other"
            with self.assertRaisesRegex(ValueError, "racket_side_document_source_mismatch"):
                matrix.merge_racket_side(original, self.side, changed, self.base)
        side["annotations"]["actions"][0]["person_track_id"] += 1
        with self.assertRaisesRegex(ValueError, "racket_side_action_window_changed"):
            matrix.merge_racket_side(original, self.side, side, self.base)

    def test_wrong_canonical_base_manifest_cannot_attach_observation(self):
        original, side = self.source_pair()
        changed = deepcopy(self.side); changed["base_stage_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "racket_side_base_manifest_mismatch"):
            matrix.merge_racket_side(original, changed, side, self.base)

    def test_duplicate_side_actions_and_extra_stages_are_rejected(self):
        original, side = self.source_pair()
        side["annotations"]["actions"].append(deepcopy(side["annotations"]["actions"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate_racket_side_action"):
            matrix.merge_racket_side(original, self.side, side, self.base)
        original, side = self.source_pair()
        extra = deepcopy(side["annotations"]["stages"][0]); extra["annotation_id"] += "-new"
        side["annotations"]["stages"].append(extra)
        with self.assertRaisesRegex(ValueError, "racket_side_stage_window_changed"):
            matrix.merge_racket_side(original, self.side, side, self.base)

    def test_frozen_sources_reconstruct_three_side_bound_documents(self):
        for entry in self.inputs["executions"]:
            if entry["route"] != "gs_acceleration":
                continue
            annotation, sources = matrix.source_annotation(ROOT, entry, acceleration, verify_media=False)
            self.assertEqual(sources, [entry["annotation_manifest"], entry["racket_side_manifest"]])
            self.assertEqual(sum("racket_side_observation" in action for action in annotation["actions"]), 1)

    def test_real_three_fs_windows_are_preserved_exactly(self):
        entry = next(e for e in self.inputs["executions"] if e["route"] == "fs_card_windows")
        document, sources = matrix.source_annotation(ROOT, entry, acceleration, verify_media=False)
        fixture = matrix.read_json(ROOT / entry["annotation_fixture"])
        self.assertEqual(document, fixture["annotation_document"])
        self.assertEqual(len(document["windows"]), 3)
        self.assertEqual(sources, [entry["annotation_fixture"]])
        self.assertTrue(all("step_motion_observation" not in row for row in document["windows"]))

    def test_renamed_same_source_window_has_same_identity(self):
        bundle = matrix.read_json(ROOT / "tests/fixtures/card-execution-results-v2.json")
        group = next(g for g in bundle["executions"] if g["route"] == "gs_acceleration")
        row = deepcopy(group["instances"][0])
        expected = matrix.source_interval_identity(group, row)
        row["event_id"] += "-renamed"
        row["window"]["event_id"] = row["event_id"]
        row["source_annotation"]["annotation_id"] = row["event_id"]
        self.assertEqual(matrix.source_interval_identity(group, row), expected)
        row["window"]["start_ms"] += 1
        self.assertNotEqual(matrix.source_interval_identity(group, row), expected)

    def test_unlocated_temporal_attempt_is_not_an_invented_measurement_window(self):
        bundle = matrix.read_json(ROOT / "tests/fixtures/card-execution-results-v2.json")
        group = bundle["executions"][0]
        row = next(deepcopy(r) for r in group["instances"] if not r["evidence_binding"])
        self.assertEqual(matrix.source_interval_identity(group, row)[-1][0], "unlocated_event_attempt")
        row["status"] = "proxy_scored"
        with self.assertRaisesRegex(ValueError, "source_interval_evidence_missing"):
            matrix.source_interval_identity(group, row)

    def test_auxiliary_measurement_never_authorizes_score(self):
        # Small mutation specimens test the guard only, never execution coverage.
        row = {"indicator_id": "GS01-M01-03", "status": "review_required", "auxiliary_evidence": {"status": "measured_auxiliary",
            "original_grade": None, "proxy_grade": None, "score": None, "source_grade_supported": False,
            "split_step_confirmed": False, "auxiliary_observations": [{"passed": None, "scoring_eligible": False}],
            "source_annotation": {"document_sha256": "a" * 64}}}
        rule = {"evaluation": {"kind": "manual_review"}}
        group = {"annotation_canonical_sha256": "a" * 64}
        matrix.validate_auxiliary(row, rule, group)
        for key, value in (("score", 0), ("proxy_grade", "E"), ("split_step_confirmed", True)):
            changed = deepcopy(row); changed["auxiliary_evidence"][key] = value
            with self.assertRaisesRegex(ValueError, "manual_auxiliary_cannot_authorize_grade"):
                matrix.validate_auxiliary(changed, rule, group)
        row["auxiliary_evidence"]["auxiliary_observations"][0]["passed"] = True
        with self.assertRaisesRegex(ValueError, "auxiliary_observation_claims_grade"):
            matrix.validate_auxiliary(row, rule, group)

    def test_changed_checkout_is_rejected_before_runtime_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "scripts/build_card_execution_matrix.py"
            path = root / name; path.parent.mkdir(parents=True)
            path.write_text("raise RuntimeError('unverified execution')", encoding="utf-8")
            bundle = {"runtime": {"files": {name: {"utf8_lf_sha256": "0" * 64}}}}
            args = ["matrix", "build", "--repository", str(root), "--results", str(root / "results.json"),
                    "--runtime-checkout", str(root), "--output", str(root / "out.json")]
            with patch.object(matrix.sys, "argv", args), patch.object(matrix, "read_json", return_value=bundle), \
                    patch.object(matrix, "verify_closure"), patch.object(matrix, "load_runtime") as imported:
                with self.assertRaisesRegex(ValueError, "runtime_checkout_source_digest_mismatch"):
                    matrix.main()
                imported.assert_not_called()

    def test_text_normalization_does_not_accept_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_bytes(b'{\r\n"value": 1\r\n}\r\n')
            raw, normalized = matrix.sha(path), matrix.lf(path.read_bytes())
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
            matrix.verify_text_artifact(path, raw, normalized, "changed")
            path.write_bytes(path.read_bytes().replace(b"1", b"2"))
            with self.assertRaisesRegex(ValueError, "changed"):
                matrix.verify_text_artifact(path, raw, normalized, "changed")

    def temporary_git_source(self, directory):
        """Real tiny Git source; never uses the shared workspace index."""
        repository = Path(directory) / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True, capture_output=True)
        for name, content in {"src/example.py": "VALUE = 3\n",
                **{name: "# source-only validation dependency\n" for name in matrix.RUNNER_DEPENDENCIES}}.items():
            target = repository / name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "src", "scripts"], cwd=repository, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Matrix fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--quiet", "-m", "Isolated source fixture"], cwd=repository, check=True, capture_output=True)
        return repository

    def test_real_git_archive_and_preimport_check_are_exercised_without_mocks(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.temporary_git_source(directory)
            checkout = Path(directory) / "checkout"
            commit = matrix.archive_runtime(repository, "HEAD", checkout)
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
            self.assertEqual((checkout / "src/example.py").read_text(encoding="utf-8"), "VALUE = 3\n")
            matrix.verify_import_surface(repository, checkout, commit)
            with self.assertRaisesRegex(ValueError, "runtime_checkout_must_be_new"):
                matrix.archive_runtime(repository, commit, checkout)

    def test_all_source_files_are_verified_even_if_saved_closure_omits_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.temporary_git_source(directory)
            checkout = Path(directory) / "checkout"
            commit = matrix.archive_runtime(repository, "HEAD", checkout)
            # This file is absent from the invented saved closure, but cannot
            # evade the independent full Git-source check before import.
            matrix.verify_runtime_checkout(checkout, {"files": {}})
            (checkout / "src/example.py").write_text("raise RuntimeError('changed import')\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "archived_runtime_source_mismatch:src/example.py"):
                matrix.verify_import_surface(repository, checkout, commit)

    def test_extra_source_shadow_file_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.temporary_git_source(directory)
            checkout = Path(directory) / "checkout"
            commit = matrix.archive_runtime(repository, "HEAD", checkout)
            (checkout / "src/extra.py").write_text("raise RuntimeError('shadow module')\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime_import_surface_inventory_mismatch"):
                matrix.verify_import_surface(repository, checkout, commit)


class CardExecutionMatrixV3FrozenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = matrix.read_json(ROOT / matrix.CATALOGUE)
        # The committed real numeric snapshot is mandatory in ordinary CI.
        cls.bundle = matrix.read_json(ROOT / "tests/fixtures/card-execution-results-v3.json")
        cls.document = matrix.read_json(ROOT / "docs/card-implementation-matrix-v3.json")

    def validated(self, bundle):
        return matrix.validate_bundle(bundle, self.catalogue, previous)

    def test_every_card_and_total_are_recomputed_from_the_real_snapshot(self):
        self.assertEqual(matrix.build_matrix(self.bundle, self.catalogue, previous), self.document)
        counts = self.document["counts"]
        self.assertEqual(counts["cards"], 298)
        self.assertEqual(counts["adapter_present_cards"], 298)
        self.assertEqual(counts["registered_proxy_rule_cards"], 158)
        self.assertEqual(counts["manual_external_observation_cards"], 140)
        self.assertEqual(counts["instances"], 348)
        self.assertEqual(counts["status_counts"], {"proxy_scored": 140, "review_required": 157, "unavailable": 51})
        self.assertEqual(counts["by_route"], {"temporal": 200, "gs_preparation": 60, "gs_acceleration": 55,
                                              "footwork_cycle": 30, "fs_card_windows": 3})
        self.assertEqual(counts["real_executed_cards"], 43)
        self.assertEqual(counts["real_proxy_scored_cards"], 15)
        self.assertEqual(counts["original_automatically_graded_cards"], 0)
        self.assertEqual(counts["distinct_videos_in_inventory"], 11)

    def test_new_contracts_and_actual_observation_counts_stay_distinct(self):
        counts = self.document["counts"]
        self.assertEqual(counts["racket_side_observation_instances"], 15)
        self.assertEqual(counts["racket_side_stage_bound_instances"], 15)
        self.assertEqual(counts["manual_auxiliary_contract_cards"], 5)
        self.assertEqual(counts["local_preload_auxiliary_status_counts"], {})
        self.assertEqual(counts["step_motion_contract_cards"], 4)
        self.assertEqual(counts["source_step_motion_observation_instances"], 0)
        self.assertEqual(counts["complete_visual_step_cycle_instances"], 0)
        for row in self.document["cards"]:
            if row["indicator_id"] in matrix.STEP_MOTION_CARDS:
                self.assertEqual(row["adapter"], "manual_card_window_adapter")
                self.assertTrue(row["declared_dependency_contracts"]["manual_source_step_motion_observation"])
                self.assertFalse(row["real_executed"])
            if row["indicator_id"] in matrix.PRELOAD_CARDS:
                self.assertEqual(row["rule_kind"], "manual_review")
                self.assertFalse(row["proxy_rule_registered"])

    def test_source_side_does_not_relax_short_m07_wrist_windows(self):
        for group in self.bundle["executions"]:
            if group["route"] != "gs_acceleration":
                continue
            wrists = [r for r in group["instances"] if r["indicator_id"].endswith("-M07-01")]
            self.assertTrue(wrists)
            self.assertTrue(all(r["status"] == "unavailable" and r["score"] is None for r in wrists))
        self.assertEqual(sum(g["instance_count"] for g in self.bundle["executions"] if g["route"] == "gs_acceleration"), 55)

    def test_source_window_renaming_cannot_duplicate_samples_across_groups(self):
        data = deepcopy(self.bundle)
        duplicate = deepcopy(next(g for g in data["executions"] if g["route"] == "gs_preparation"))
        duplicate["id"] += "-renamed"
        for row in duplicate["instances"]:
            row["event_id"] += "-renamed"
            row["window"]["event_id"] = row["event_id"]
            row["source_annotation"]["annotation_id"] = row["event_id"]
        data["executions"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate_source_card_window"):
            self.validated(data)

    def test_same_original_frames_deduplicate_despite_timestamp_label_rounding(self):
        group = next(g for g in self.bundle["executions"] if g["route"] == "fs_card_windows")
        row = deepcopy(group["instances"][0])
        identity = matrix.source_interval_identity(group, row)
        row["event_id"] += "-new-label"
        row["window"]["start_ms"] += .1
        row["window"]["end_ms"] += .1
        self.assertEqual(matrix.source_interval_identity(group, row), identity)

    def test_summaries_unknown_cards_and_repeated_instances_fail(self):
        mutations = [
            (lambda d: d["summary"].__setitem__("instances", 349), "bundle_summary_mismatch"),
            (lambda d: d["executions"][0]["instances"][0].__setitem__("indicator_id", "GS99-M01-01"), "unknown_card"),
            (lambda d: d["executions"][0]["instances"].append(deepcopy(d["executions"][0]["instances"][0])), "duplicate_report_instance")]
        for mutate, reason in mutations:
            data = deepcopy(self.bundle); mutate(data)
            with self.assertRaisesRegex(ValueError, reason):
                self.validated(data)

    def test_grade_math_and_missing_never_become_zero_or_e(self):
        data = deepcopy(self.bundle)
        row = next(r for g in data["executions"] for r in g["instances"] if r["status"] == "proxy_scored")
        row["conditions"][0]["passed"] = not row["conditions"][0]["passed"]
        with self.assertRaisesRegex(ValueError, "condition_pass_flag_mismatch"):
            self.validated(data)
        data = deepcopy(self.bundle)
        row = next(r for g in data["executions"] for r in g["instances"] if r["status"] == "unavailable")
        row["score"], row["proxy_grade"] = 0, "E"
        with self.assertRaisesRegex(ValueError, "unscored_instance_has_grade"):
            self.validated(data)

    def test_fs_source_frame_pts_and_document_tampering_fail(self):
        for mutate, reason in [
            (lambda r: r["source_window_evidence"]["source_frames"].pop(), "fs_window_original_frame_inventory_mismatch"),
            (lambda r: r["source_window_evidence"]["source_pts"][0].__setitem__("pts", 0), "fs_window_original_pts_binding_mismatch"),
            (lambda r: r["source_window_annotation"].__setitem__("document_sha256", "0" * 64), "fs_window_annotation_binding_mismatch"),
            (lambda r: r["source_window_evidence"].__setitem__("step_cycle_bound", True), "fs_cycle_claim_without_computed_cycle")]:
            data = deepcopy(self.bundle)
            group = next(g for g in data["executions"] if g["route"] == "fs_card_windows")
            mutate(group["instances"][0])
            with self.assertRaisesRegex(ValueError, reason):
                self.validated(data)

    def test_side_observation_cannot_change_to_continuous_or_dominant_hand_proof(self):
        for key in ("dominant_hand_established", "continuous_racket_hold_established"):
            data = deepcopy(self.bundle)
            row = next(r for g in data["executions"] for r in g["instances"] if r.get("window", {}).get("racket_side_binding"))
            row["window"]["racket_side_binding"][key] = True
            row["source_annotation"]["racket_side_observation"][key] = True
            with self.assertRaisesRegex(ValueError, "racket_side_source_proof_mismatch"):
                self.validated(data)

    def test_synthetic_reports_and_empty_execution_bundles_are_rejected(self):
        for key, value in (("execution_origin", "synthetic_trajectory"), ("artifact_verified", False),
                           ("original_video_decoder_verified", False)):
            data = deepcopy(self.bundle); data["executions"][0][key] = value
            with self.assertRaisesRegex(ValueError, "synthetic_or_unverified_execution_rejected"):
                self.validated(data)
        data = deepcopy(self.bundle); data["executions"] = []
        with self.assertRaisesRegex(ValueError, "required_execution_routes_missing"):
            self.validated(data)

    def test_manifest_preserves_run_video_and_all_annotation_source_links(self):
        manifest = matrix.read_json(ROOT / self.bundle["input_manifest_path"])
        matrix.validate_input_bindings(self.bundle, manifest)
        for mutate, reason in [
            (lambda g: g.__setitem__("video_sha256", "0" * 64), "declared_source_run_video_binding_mismatch"),
            (lambda g: g["annotation_sources"].pop(), "declared_source_annotation_binding_mismatch")]:
            data = deepcopy(self.bundle)
            group = next(g for g in data["executions"] if g["route"] == "gs_acceleration")
            mutate(group)
            with self.assertRaisesRegex(ValueError, reason):
                matrix.validate_input_bindings(data, manifest)

    def test_inventory_is_reconstructed_before_any_saved_report_or_video_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [self.bundle["input_manifest_path"], *self.bundle["inventory_manifest_sha256"]]:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            for mutate in (lambda rows: rows.pop(), lambda rows: rows[0]["case_ids"].append("invented-case"),
                           lambda rows: rows.append({"video_sha256": "0" * 64, "case_ids": ["invented"],
                               "manifest_paths": [], "source_urls": []})):
                data = deepcopy(self.bundle); mutate(data["video_inventory"])
                with self.assertRaisesRegex(ValueError, "video_inventory_differs_from_source_manifests"):
                    matrix.verify_bundle_artifacts(root, data, self.catalogue, previous)

    def test_changed_full_report_is_rejected_before_original_media_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [self.bundle["input_manifest_path"], *self.bundle["inventory_manifest_sha256"]]:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            path = root / self.bundle["executions"][0]["full_report_path"]
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "full_report_digest_mismatch"):
                matrix.verify_bundle_artifacts(root, self.bundle, self.catalogue, previous)

    def test_snapshot_has_no_machine_paths_and_counts_no_recognition_only_samples(self):
        for document in (self.bundle, self.document):
            self.assertIsNone(re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", json.dumps(document, ensure_ascii=False)))
        inventory = self.document["video_inventory"]
        self.assertEqual(sum(row["card_instance_count"] for row in inventory), 348)
        self.assertEqual(sum(row["card_instance_count"] > 0 for row in inventory), 5)
        self.assertTrue(any(row["scope"] == "recognition_only_no_card_execution" for row in inventory))

    @unittest.skipUnless(os.environ.get("RALLYMATE_MATRIX_V3_INTEGRATION") == "1",
                         "explicit integration needs local original media and the clean replay checkout")
    def test_original_artifact_and_runtime_closure_integration(self):
        checkout = ROOT / Path(self.bundle["executions"][0]["full_report_path"]).parent / "checkout"
        # A separate process ensures helper imports also use the verified
        # archived runtime, not modules imported by this test process.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rebuilt.json"
            process = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_card_execution_matrix_v3.py"),
                "build", "--repository", str(ROOT), "--results", str(ROOT / "tests/fixtures/card-execution-results-v3.json"),
                "--runtime-checkout", str(checkout), "--output", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(matrix.read_json(output), self.document)


if __name__ == "__main__":
    unittest.main()
