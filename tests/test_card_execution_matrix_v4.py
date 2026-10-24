"""Current policy validation from freshly evaluated recorded source trajectories.

The CI inputs contain real observations, not new model inference. A separate
opt-in source-adapter check verifies and decodes physical original media. No old
saved status is relabelled to manufacture a current result.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts import build_card_execution_matrix_v4 as matrix
from rallymate_features.schemas import PoseSequence
from rallymate_scoring.card_rule_catalog import load_card_rules
from rallymate_service.card_execution import build_card_execution_from_sequence
from rallymate_service.gs_card_execution import compute_gs_card_execution
from rallymate_service.fs_card_windows import compute_fs_card_windows, build_fs_card_window_report

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return matrix.read_json(ROOT / "tests/fixtures" / name)


def instances(report):
    return [r for card in report["cards"] for r in card["instances"]]


class MatrixV4SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load("card-execution-inputs-v4.json")
        cls.rules = {c["indicator_id"]: c for c in load_card_rules()["cards"]}
        raw = next(c for c in load("gs-card-real-mit-preparation-v1.json")["cases"]
                   if c["case_id"] == "ocw-pe.710-2-forehand-220k")
        cls.gs = compute_gs_card_execution(raw["frame_records"], raw["primary_timeline"], raw["decoder_timeline"],
            raw["annotations"], source_job_id=raw["source_job_id"])
        cls.gs_entry = deepcopy(next(e for e in cls.manifest["executions"] if e["id"] == "gs-preparation-ocw-pe.710-2-forehand-220k"))
        # This older record-only fixture declares the research case label as
        # video_id while source_job_id separately binds the original raw rows.
        # Preserve it here; it is explicitly rejected from physical bundles.
        cls.gs_entry["source_run_id"] = raw["annotations"]["source_run_id"]
        cls.step_fixture = load("fs-step-real-mit-development-v1.json")
        cls.step = compute_fs_card_windows(raw["frame_records"], raw["primary_timeline"],
            cls.step_fixture["case"]["source_events"], raw["decoder_timeline"], cls.step_fixture["annotation_document"],
            source_job_id=raw["source_job_id"])
        cls.step_entry = cls.manifest["executions"][-1]
        payload = load("temporal-card-real-0a3.json")
        data = payload["sequence"]
        for key in ("timestamp_ms", "source_frames", "frame_dimensions_px", "frame_transforms_to_reference"):
            data[key] = np.asarray(data[key])
        for key in ("keypoints_xy", "confidence", "model_estimate_masks"):
            data[key] = {n: np.asarray(v, dtype=bool if key == "model_estimate_masks" else float) for n, v in data[key].items()}
        for key in ("camera_motion_status", "camera_reference_epochs", "model_estimate_bindings"):
            data[key] = tuple(data[key])
        data["source_time_bindings"] = {int(k): v for k, v in data["source_time_bindings"].items()}
        cls.temporal = build_card_execution_from_sequence(PoseSequence(**data), payload["events"], payload["timeline"],
            source_run_id=payload["events"][0]["video_id"])
        cls.temporal_entry = cls.manifest["executions"][0]
        # Validate the actual JSON contract, including tuple-to-array bounds.
        cls.gs, cls.step, cls.temporal = json.loads(json.dumps([cls.gs, cls.step, cls.temporal], allow_nan=False))

    def validate(self, row, entry):
        matrix.validate_instance(row, self.rules[row["indicator_id"]], video_sha256=entry["video_sha256"], video_id=entry["source_run_id"])
        matrix.validate_source_ledger(row, entry)

    def test_real_recorded_observations_are_recomputed_and_independently_validated(self):
        for report, entry in ((self.gs, self.gs_entry), (self.step, self.step_entry), (self.temporal, self.temporal_entry)):
            rows = instances(report)
            self.assertTrue(rows)
            for row in rows:
                self.validate(row, entry)
            self.assertEqual(report["summary"], matrix.formal_summary(rows, [c["indicator_id"] for c in report["cards"]]))
        self.assertTrue(any(r["status"] == "scored" for r in instances(self.gs)))
        self.assertTrue(any(r["status"] == "scored" for r in instances(self.temporal)))
        row, = instances(self.step)
        self.assertEqual((row["indicator_id"], row["status"], row["score"]), ("FS07-M02", "unavailable", None))
        self.assertFalse(row["source_window_evidence"]["step_cycle_bound"])
        self.assertEqual(row["source_window_evidence"]["source_frames"], list(range(29, 39)))

    def test_record_only_report_cannot_claim_original_video_execution(self):
        with self.assertRaisesRegex(ValueError, "record_only_or_research_report_rejected"):
            matrix.compact_report(self.gs, self.gs_entry, self.rules, matrix)

    def test_old_status_or_missing_policy_is_not_implicitly_upgraded(self):
        original = next(r for r in instances(self.gs) if r["status"] == "scored")
        for kind in ("old_status", "missing_policy", "wrong_policy", "source_grade"):
            row = deepcopy(original)
            if kind == "old_status": row["status"] = "proxy_scored"
            if kind == "missing_policy": del row["scoring_policy_version"]
            if kind == "wrong_policy": row["scoring_policy_version"] = "automatic-formal-card-scoring-v0"
            if kind == "source_grade": row["original_grade"] = "A"
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, self.gs_entry)

    def test_condition_and_grade_arithmetic_cannot_be_forged(self):
        original = next(r for r in instances(self.gs) if r["status"] == "scored")
        for kind in ("value", "pass", "threshold", "grade", "score", "fraction", "missing_condition", "duplicate"):
            row = deepcopy(original)
            if kind == "value":
                condition = row["conditions"][0]
                threshold, passed = condition["threshold"], condition["passed"]
                condition["value"] = {"gte": lambda: threshold - 1 if passed else threshold + 1,
                    "lte": lambda: threshold + 1 if passed else threshold - 1,
                    "eq": lambda: threshold + 1 if passed else threshold,
                    "between": lambda: threshold[1] + 1 if passed else sum(threshold) / 2}[condition["operator"]]()
            if kind == "pass": row["conditions"][0]["passed"] = not row["conditions"][0]["passed"]
            if kind == "threshold": row["conditions"][0]["threshold"] = -100
            if kind == "grade": row["grade"] = "E"
            if kind == "score": row["score"] = 0
            if kind == "fraction": row["condition_completion_fraction"] = -.1
            if kind == "missing_condition": row["conditions"].pop()
            if kind == "duplicate": row["conditions"].append(deepcopy(row["conditions"][0]))
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, self.gs_entry)

    def test_nonfinite_boolean_and_string_values_are_not_measurements(self):
        for value in (float("nan"), float("inf"), True, "100"):
            row = deepcopy(next(r for r in instances(self.gs) if r["status"] == "scored"))
            row["conditions"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.validate(row, self.gs_entry)

    def test_boolean_zero_or_one_threshold_cannot_use_python_numeric_equality(self):
        # Contract arithmetic only: these rows are not claimed as real inputs.
        for expected, changed in ((0, False), (1, True), ([0, 1], [False, 1]), ([0, 1], [0, True])):
            operator = "between" if isinstance(expected, list) else "eq"
            rule = {"evaluation": {"conditions": [{"metric": "contract", "operator": operator,
                "unit": "unit", **({"bounds": expected} if isinstance(expected, list) else {"value": expected})}]}}
            row = {"status": "unavailable", "reason_codes": ["contract-only"], "conditions": [{"metric": "contract",
                "operator": operator, "unit": "unit", "threshold": changed, "value": None, "passed": None}]}
            with self.assertRaisesRegex(ValueError, "condition_contract_mismatch"):
                matrix.validate_condition_measurements(row, rule)

    def test_unavailable_retains_nulls_not_zero_e_or_pass(self):
        for kind in ("grade", "score", "passed"):
            row = deepcopy(instances(self.step)[0])
            if kind == "grade": row["grade"] = "E"
            elif kind == "score": row["score"] = 0
            else: row["conditions"][0]["passed"] = False
            with self.assertRaises(ValueError): self.validate(row, self.step_entry)

    def test_full_original_ledger_cannot_be_deleted_shortened_or_rebound(self):
        original = next(r for r in instances(self.gs) if r["status"] == "scored")
        for kind in ("all_derived", "one_derived", "window_derived", "epoch", "person", "pts", "hash", "frame"):
            row = deepcopy(original); proof = row["source_stage_evidence"]; bindings = proof["source_frame_bindings"]
            if kind == "all_derived":
                for b in bindings: del b["derived_selection_epoch"]
            elif kind == "one_derived": del bindings[0]["derived_selection_epoch"]
            elif kind == "window_derived": del row["window"]["derived_selection_epoch"]
            elif kind == "epoch": bindings[0]["derived_selection_epoch"] += 1
            elif kind == "person": bindings[0]["selected_person_track_id"] += 1
            elif kind == "pts": bindings[0]["pts"] += 1
            elif kind == "hash": bindings[0]["video_sha256"] = "0" * 64
            else: bindings.pop()
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, self.gs_entry)

    def test_temporal_uses_its_own_native_original_clock_ledger(self):
        row = deepcopy(next(r for r in instances(self.temporal) if r["status"] == "scored"))
        self.validate(row, self.temporal_entry)
        self.assertTrue(row["evidence"]["source_frame_bindings"])
        self.assertNotIn("derived_selection_epoch", row["evidence"]["source_frame_bindings"][0])
        del row["evidence"]["source_frame_bindings"]
        with self.assertRaisesRegex(ValueError, "temporal_source_ledger_inventory_mismatch"):
            self.validate(row, self.temporal_entry)

    def test_temporal_primary_epochs_and_consistently_changed_clock_are_rejected(self):
        original = next(r for r in instances(self.temporal) if r["status"] == "scored")
        for kind in ("primary", "epoch", "camera", "clock", "clock_reverse", "model_epoch_bool"):
            row = deepcopy(original); bindings = row["evidence"]["source_frame_bindings"]; b = bindings[1]
            if kind == "primary": b["primary_selection"]["primary_player_id"] += 1
            elif kind == "epoch":
                b["selection_epoch"] += 1
                b["model_geometry_source"]["source_binding"]["selection_epoch"] += 1
            elif kind == "camera":
                b["camera_reference_epoch"] += 1
                b["model_geometry_source"]["source_binding"]["camera_reference_epoch"] += 1
            elif kind == "model_epoch_bool": b["model_geometry_source"]["source_binding"]["selection_epoch"] = False
            else:
                b["pts"] += 10 if kind == "clock" else -100
                b["source_timestamp_ms"] = float(matrix.exact_time(b))
                b["model_geometry_source"]["source_binding"]["pts"] = b["pts"]
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.validate(row, self.temporal_entry)

    def test_recorded_usta_cycle_keeps_its_native_ledger_and_negative_result(self):
        from tests.test_footwork_cycle_card_evidence import real_fixture
        from rallymate_service.footwork_cycle_execution import compute_footwork_cycle_execution
        sequence, event, timeline, binding = real_fixture()
        report = compute_footwork_cycle_execution(sequence, [event], timeline, [binding], source_run_id=event["video_id"])
        rows = json.loads(json.dumps(instances(report), allow_nan=False))
        entry = {"route": "footwork_cycle", "video_sha256": binding["video_sha256"], "source_run_id": event["video_id"]}
        for row in rows:
            self.validate(row, entry)
            self.assertEqual((row["status"], row["score"]), ("unavailable", None))
        self.assertEqual(len(rows), 5)
        rows[0]["evidence"]["source_frame_bindings"].pop()
        with self.assertRaisesRegex(ValueError, "cycle_source_ledger_inventory_mismatch"):
            self.validate(rows[0], entry)

    def test_same_source_frames_deduplicate_even_if_event_label_changes(self):
        row = deepcopy(instances(self.step)[0]); first = matrix.source_interval_identity(self.step_entry, row)
        row["event_id"] = "invented-second-event"
        row["window"]["start_ms"] += .1
        self.assertEqual(first, matrix.source_interval_identity(self.step_entry, row))

    def test_duplicate_instances_are_rejected_before_aggregation(self):
        row = instances(self.gs)[0]
        with self.assertRaisesRegex(ValueError, "duplicate_or_outside_summary_instance"):
            matrix.formal_summary([row, deepcopy(row)], [row["indicator_id"]])

    def test_formal_summary_and_quality_claims_are_checked_independently(self):
        report = deepcopy(self.gs)
        report["summary"]["channels"]["formal"]["score"] += 1
        with self.assertRaisesRegex(ValueError, "report_summary_mismatch"):
            matrix.compact_report(report, self.gs_entry, self.rules, matrix)
        for field, value in (("valid_frame_count", True), ("total_frame_count", 0), ("geometry_gate_passed", False)):
            row = deepcopy(next(r for r in instances(self.gs) if r["status"] == "scored"))
            row["quality"][field] = value
            with self.assertRaisesRegex(ValueError, "scored_quality_gate_mismatch"):
                self.validate(row, self.gs_entry)

    def test_raw_model_epoch_is_independent_of_primary_and_cannot_be_relabelled(self):
        # Explicit synthetic contract-only inputs; never added to real counts.
        from tests.test_card_formal_scoring_inputs import model_records
        from tests.test_fs_step_motion_windows import fixture
        for mixed in (False, True):
            arguments = fixture()
            model_records(arguments, {8} if mixed else None)
            for record in arguments[0]:
                for pose in record["poses"]:
                    if pose.get("source") == "sam3d": pose["source_binding"]["selection_epoch"] = 0
            for primary in arguments[1]: primary["selection_epoch"] = 1
            report = compute_fs_card_windows(*arguments, source_job_id=arguments[-1]["source_run_id"])
            row, = json.loads(json.dumps(instances(report), allow_nan=False))
            entry = {"route": "fs_card_windows", "video_sha256": arguments[-1]["video_sha256"],
                     "source_run_id": arguments[-1]["source_run_id"]}
            self.assertEqual(row["status"], "scored")
            self.validate(row, entry)
            geometry = row["source_window_evidence"]["step_motion"]["source_geometry_series"]
            source = next(g for g in geometry if g.get("observation_semantics") == "monocular_model_estimate")
            self.assertEqual(source["source_binding"]["selection_epoch"], 0)
            self.assertEqual(source["selection_epoch"], 1)
            source["source_binding"]["selection_epoch"] = 1
            with self.assertRaisesRegex(ValueError, "step_model_namespace_mismatch"):
                self.validate(row, entry)

    def test_summary_equal_card_weights_and_missing_coverage(self):
        rows = [deepcopy(r) for r in instances(self.gs) if r["status"] == "scored"]
        ids = sorted({r["indicator_id"] for r in rows}) + ["unobserved-test-card"]
        expected = matrix.formal_summary(rows, ids)
        per_card = expected["channels"]["formal"]["per_card"]
        self.assertEqual(expected["overall_score"], sum(per_card.values()) / len(per_card))
        self.assertEqual(expected["channels"]["formal"]["applicable_cards"], len(ids))
        self.assertLess(expected["channels"]["formal"]["coverage"], 1)
        self.assertIsNone(matrix.formal_summary([], ids)["overall_score"])

    def test_input_scope_preserves_nine_and_adds_one_same_video_negative(self):
        matrix.validate_input_manifest(ROOT, self.manifest, verify_sources=False)
        self.assertEqual(len(self.manifest["executions"]), 10)
        self.assertEqual(len({e["video_sha256"] for e in self.manifest["executions"]}), 5)
        for target in ("original", "added", "hash"):
            m = deepcopy(self.manifest)
            if target == "original": m["executions"][0]["run_dir"] += "-renamed"
            elif target == "added": m["executions"][-1]["video_sha256"] = "0" * 64
            else: m["executions"][1]["input_artifact_sha256"].pop("frames.jsonl")
            with self.assertRaises(ValueError): matrix.validate_input_manifest(ROOT, m, verify_sources=False)

    def test_added_fixture_cannot_reselect_window_or_anatomical_side(self):
        matrix.verify_fs_fixture(self.step_fixture, ROOT, verify_media=False)
        for kind in ("frame", "side", "freeze"):
            data = deepcopy(self.step_fixture)
            if kind == "frame": data["annotation_document"]["windows"][0]["end_source_frame"] += 1
            elif kind == "side": data["annotation_document"]["windows"][0]["step_motion_observation"]["moving_foot"] = "right"
            else: data["visual_observation"]["new_step_motion_numeric_output_read_or_executed_before_freeze"] = True
            with self.assertRaises(ValueError): matrix.verify_fs_fixture(data, ROOT, verify_media=False)

    def test_cohorts_do_not_inflate_added_video_or_hide_negative(self):
        groups = [{**self.gs_entry, "instances": instances(self.gs)}, {**self.step_entry, "instances": instances(self.step)}]
        result = matrix.cohort_summary(groups)
        added = result[matrix.ADDED_COHORT]
        self.assertEqual(added["new_video_count"], 0)
        self.assertEqual(added["new_executed_card_ids"], ["FS07-M02"])
        self.assertEqual(added["scored_card_ids"], [])
        self.assertEqual(added["instances"], 1)

    @unittest.skipUnless(os.environ.get("CARD_ARTIFACT_ROOT"), "physical original media is opt-in; recorded trajectories always execute")
    def test_physical_original_mit_source_adapter_not_a_saved_report(self):
        root = Path(os.environ["CARD_ARTIFACT_ROOT"])
        entry = self.step_entry
        report = build_fs_card_window_report(root / entry["run_dir"], self.step_fixture["annotation_document"],
            video_path=root / entry["local_video"])
        compact = matrix.compact_report(report, entry, self.rules, matrix)
        self.assertEqual(compact["instance_count"], 1)
        self.assertEqual(compact["status_counts"], {"unavailable": 1})
        self.assertTrue(compact["original_video_decoder_verified"])
        self.validate(compact["instances"][0], entry)


class MatrixV4ArchiveTests(unittest.TestCase):
    def test_build_checks_source_digest_before_importing_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            path = root / "src/source.py"; path.write_text("# changed source\n")
            bundle = {"runtime": {"files": {"src/source.py": {"utf8_lf_sha256": "0" * 64}}}}
            arguments = ["matrix", "build", "--repository", str(root), "--results", str(root / "results.json"),
                         "--runtime-checkout", str(root), "--output", str(root / "matrix.json")]
            with patch("sys.argv", arguments), patch.object(matrix, "read_json", return_value=bundle), \
                    patch.object(matrix, "verify_closure"), patch.object(matrix, "load_runtime") as imported:
                with self.assertRaisesRegex(ValueError, "runtime_checkout_source_digest_mismatch"):
                    matrix.main()
                imported.assert_not_called()

    def test_archive_and_preimport_inventory_are_real_git_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"; repository.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True, capture_output=True)
            for name in ["src/example.py", *matrix.RUNNER_DEPENDENCIES]:
                path = repository / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("# exact archive\n")
            subprocess.run(["git", "add", "src", "scripts"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Matrix fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "--quiet", "-m", "Isolated fixture"], cwd=repository, check=True, capture_output=True)
            checkout = Path(directory) / "checkout"
            commit = matrix.archive_runtime(repository, "HEAD", checkout)
            matrix.verify_import_surface(repository, checkout, commit)
            (checkout / "src/example.py").write_text("# changed\n")
            with self.assertRaisesRegex(ValueError, "archived_runtime_source_mismatch"):
                matrix.verify_import_surface(repository, checkout, commit)
            (checkout / "src/extra.py").write_text("# import shadow\n")
            with self.assertRaisesRegex(ValueError, "runtime_import_surface_inventory_mismatch"):
                matrix.verify_import_surface(repository, checkout, commit)
            with self.assertRaisesRegex(ValueError, "runtime_checkout_must_be_new"):
                matrix.archive_runtime(repository, commit, checkout)

    def test_json_rejects_duplicate_keys_nonfinite_and_semantic_digest_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
                path.write_text(text)
                with self.assertRaises(ValueError): matrix.read_json(path)
            path.write_bytes(b'{\r\n"a":1\r\n}\r\n')
            raw, normalized = matrix.sha(path), matrix.lf(path.read_bytes())
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
            matrix.verify_text_artifact(path, raw, normalized, "changed")
            path.write_bytes(path.read_bytes().replace(b"1", b"2"))
            with self.assertRaisesRegex(ValueError, "changed"):
                matrix.verify_text_artifact(path, raw, normalized, "changed")


if __name__ == "__main__":
    unittest.main()
