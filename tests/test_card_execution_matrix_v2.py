"""Numeric evidence-integrity tests; never synthetic video validation claims."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matrix = load("card_matrix_v2", ROOT / "scripts/build_card_execution_matrix_v2.py")
previous = load("card_matrix_v1_checks", ROOT / "scripts/build_card_execution_matrix.py")


class CardExecutionMatrixV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = matrix.read_json(ROOT / matrix.CATALOGUE)
        # This committed numeric output is mandatory; no silent media skip.
        cls.bundle = matrix.read_json(ROOT / "tests/fixtures/card-execution-results-v2.json")
        cls.document = matrix.read_json(ROOT / "docs/card-implementation-matrix-v2.json")

    def validated(self, bundle):
        return matrix.validate_bundle(bundle, self.catalogue, previous)

    def test_committed_results_reproduce_every_card_and_the_summary(self):
        generated = matrix.build_matrix(self.bundle, self.catalogue, previous)
        self.assertEqual(generated, self.document)
        counts = generated["counts"]
        self.assertEqual(counts["cards"], 298)
        self.assertEqual(counts["instances"], 345)
        self.assertEqual(counts["by_route"], {"temporal": 200, "gs_preparation": 60, "gs_acceleration": 55, "footwork_cycle": 30})
        self.assertEqual(counts["original_automatically_graded_cards"], 0)
        self.assertEqual(counts["distinct_videos_in_inventory"], 11)
        self.assertLess(counts["real_executed_cards"], counts["adapter_present_cards"])
        self.assertLess(counts["real_proxy_scored_cards"], counts["registered_proxy_rule_cards"])

    def test_summaries_are_recounted_not_trusted(self):
        for change in (lambda d: d["summary"].__setitem__("instances", 346),
                       lambda d: d["executions"][0]["status_counts"].__setitem__("proxy_scored", 999)):
            data = deepcopy(self.bundle); change(data)
            with self.assertRaisesRegex(ValueError, "summary_mismatch"):
                self.validated(data)

    def test_unknown_card_and_duplicate_instance_are_rejected(self):
        data = deepcopy(self.bundle)
        data["executions"][0]["instances"][0]["indicator_id"] = "GS99-M01-01"
        with self.assertRaisesRegex(ValueError, "unknown_card"):
            self.validated(data)
        data = deepcopy(self.bundle)
        data["executions"][0]["instances"].append(deepcopy(data["executions"][0]["instances"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate_report_instance"):
            self.validated(data)

    def test_cross_report_repetition_is_rejected_even_with_a_new_group_name(self):
        data = deepcopy(self.bundle)
        duplicate = deepcopy(data["executions"][0]); duplicate["id"] += "-copy"
        data["executions"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate_cross_report_instance"):
            self.validated(data)

    def test_wrong_run_video_stage_and_annotation_are_rejected(self):
        changes = [lambda group: group["instances"][0].__setitem__("video_id", "other"),
            lambda group: group.__setitem__("run_dir", group["run_dir"] + "-wrong"),
            lambda group: group["instances"][0]["window"].__setitem__("video_sha256", "0" * 64),
            lambda group: group["instances"][0]["window"].__setitem__("stage_id", "GS01-M09"),
            lambda group: group["instances"][0]["source_annotation"].__setitem__("document_sha256", "0" * 64)]
        for change in changes:
            data = deepcopy(self.bundle)
            group = next(group for group in data["executions"] if group["route"] == "gs_preparation")
            change(group)
            with self.assertRaisesRegex(ValueError, "mismatch"):
                self.validated(data)

    def test_temporal_group_cannot_swap_video_even_when_other_group_labels_are_coherent(self):
        data = deepcopy(self.bundle)
        group = next(group for group in data["executions"] if group["route"] == "temporal")
        other = next(group for group in data["executions"] if group["route"] == "gs_preparation")
        group["video_sha256"], group["local_video"] = other["video_sha256"], other["local_video"]
        manifest = matrix.read_json(ROOT / self.bundle["input_manifest_path"])
        with self.assertRaisesRegex(ValueError, "declared_source_run_video_binding_mismatch"):
            matrix.validate_input_bindings(data, manifest)

    def test_synthetic_record_only_and_unbound_cycle_projections_are_rejected(self):
        for key, value in (("execution_origin", "synthetic_trajectory"), ("artifact_verified", False),
                           ("original_video_decoder_verified", False)):
            data = deepcopy(self.bundle)
            group = next(group for group in data["executions"] if group["route"] == "footwork_cycle")
            group[key] = value
            with self.assertRaisesRegex(ValueError, "synthetic_or_unverified"):
                self.validated(data)
        data = deepcopy(self.bundle)
        group = next(group for group in data["executions"] if group["route"] == "footwork_cycle")
        group["instances"][0]["evidence_binding"]["step_window_binding"]["method"] = "versioned_detector"
        with self.assertRaisesRegex(ValueError, "cycle_annotation_binding_mismatch"):
            self.validated(data)

    def test_grade_arithmetic_and_missing_are_not_relabelled(self):
        data = deepcopy(self.bundle)
        row = next(row for group in data["executions"] for row in group["instances"] if row["status"] == "proxy_scored")
        row["conditions"][0]["passed"] = not row["conditions"][0]["passed"]
        with self.assertRaisesRegex(ValueError, "condition_pass_flag_mismatch"):
            self.validated(data)
        data = deepcopy(self.bundle)
        row = next(row for group in data["executions"] for row in group["instances"] if row["status"] == "review_required")
        row["score"] = 0; row["proxy_grade"] = "E"
        with self.assertRaisesRegex(ValueError, "unscored_instance_has_grade"):
            self.validated(data)

    def test_three_acceleration_side_gaps_are_separate_from_stage_absence(self):
        by_id = {row["indicator_id"]: row for row in self.document["cards"]}
        for code in ("GS01-M07-01", "GS02-M07-01", "GS03-M07-01"):
            row = by_id[code]
            self.assertTrue(row["proxy_rule_registered"])
            self.assertEqual(row["adapter"], "gs_manual_stage_adapter")
            self.assertTrue(row["real_stage_evidence_available"])
            self.assertIn("trusted_racket_side_input_not_wired_at_baseline", row["dependency_binding_gaps"])
            self.assertFalse(row["real_proxy_scored"])

    def test_recognition_only_inventory_cannot_increase_card_samples(self):
        inventory = self.document["video_inventory"]
        self.assertEqual(sum(row["card_instance_count"] for row in inventory), 345)
        self.assertTrue(any(row["scope"] == "recognition_only_no_card_execution" for row in inventory))
        cycle = next(group for group in self.bundle["executions"] if group["route"] == "footwork_cycle")
        self.assertEqual({row["indicator_id"].split("-")[0] for row in cycle["instances"]}, {"FS04"})
        self.assertEqual(cycle["status_counts"], {"review_required": 30})

    def test_output_contains_no_absolute_machine_paths_or_personal_metadata(self):
        for output in (self.document, self.bundle):
            text = json.dumps(output, ensure_ascii=False)
            self.assertIsNone(re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text), "absolute Windows path leaked")
            self.assertNotIn('"source_ip"', text)
            self.assertNotIn('"original_filename"', text)

    def test_missing_execution_results_cannot_build_a_successful_matrix(self):
        data = deepcopy(self.bundle); data["executions"] = []
        with self.assertRaisesRegex(ValueError, "required_execution_routes_missing"):
            matrix.build_matrix(data, self.catalogue, previous)

    def test_changed_full_report_bytes_are_rejected_before_media_reads(self):
        # Deliberate bad bytes test integrity only; they never enter coverage.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / self.bundle["input_manifest_path"]
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes((ROOT / self.bundle["input_manifest_path"]).read_bytes())
            for name in self.bundle["inventory_manifest_sha256"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            path = root / self.bundle["executions"][0]["full_report_path"]
            path.parent.mkdir(parents=True)
            path.write_text('{"tampered": true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "full_report_digest_mismatch"):
                matrix.verify_bundle_artifacts(root, self.bundle, self.catalogue, previous)

    def test_explicit_text_normalization_allows_only_line_ending_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            original = b'{\r\n  "value": 1\r\n}\r\n'
            path.write_bytes(original)
            raw, normalized = matrix.sha(path), matrix.lf(original)
            path.write_bytes(original.replace(b'\r\n', b'\n'))
            self.assertEqual(matrix.verify_text_artifact(path, raw, normalized, "text_changed"),
                             {"raw_matches_replay": False, "normalized_matches_replay": True})
            path.write_bytes(path.read_bytes().replace(b'1', b'2'))
            with self.assertRaisesRegex(ValueError, "text_changed"):
                matrix.verify_text_artifact(path, raw, normalized, "text_changed")

    def test_inventory_add_remove_or_relabel_cannot_pass_bound_artifact_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in [self.bundle["input_manifest_path"], *self.bundle["inventory_manifest_sha256"]]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            for change in (lambda rows: rows.append({"video_sha256": "0" * 64,
                               "case_ids": ["nonexistent-case"], "source_urls": [], "manifest_paths": []}),
                           lambda rows: rows.pop(),
                           lambda rows: rows[0]["case_ids"].append("invented-source-action")):
                data = deepcopy(self.bundle); change(data["video_inventory"])
                # No full reports or media exist in this temporary directory:
                # source-identity tampering must fail before accessing them.
                with self.assertRaisesRegex(ValueError, "video_inventory_differs_from_source_manifests"):
                    matrix.verify_bundle_artifacts(root, data, self.catalogue, previous)

    def test_changed_checkout_validator_is_rejected_before_it_can_be_imported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = "scripts/build_card_execution_matrix.py"
            path = root / name; path.parent.mkdir(parents=True)
            original = (ROOT / name).read_bytes()
            path.write_bytes(original)
            declaration = self.bundle["runtime"]["files"][name]
            closure = {"files": {name: deepcopy(declaration)}}
            matrix.verify_runtime_checkout(root, closure)
            path.write_bytes(original + b"\nraise RuntimeError('must never execute unverified validator')\n")
            data = deepcopy(self.bundle); data["runtime"] = closure
            args = ["matrix", "build", "--repository", str(root), "--results", str(root / "result.json"),
                    "--runtime-checkout", str(root), "--output", str(root / "out.json")]
            with patch.object(matrix.sys, "argv", args), patch.object(matrix, "read_json", return_value=data), \
                    patch.object(matrix, "verify_closure"), patch.object(matrix, "load_runtime") as import_runtime:
                with self.assertRaisesRegex(ValueError, "runtime_checkout_source_digest_mismatch:scripts/build_card_execution_matrix.py"):
                    matrix.main()
                import_runtime.assert_not_called()

    @unittest.skipUnless(os.environ.get("RALLYMATE_MATRIX_V2_INTEGRATION") == "1",
                         "explicit integration requires local original media and full replay artifacts")
    def test_local_original_artifacts_still_match_the_frozen_replay(self):
        matrix.verify_closure(ROOT, self.bundle["runtime"])
        matrix.verify_bundle_artifacts(ROOT, self.bundle, self.catalogue, previous)


if __name__ == "__main__":
    unittest.main()
