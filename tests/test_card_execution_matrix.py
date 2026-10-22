"""Evidence-integrity tests; these tests do not create video-validation coverage."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("card_execution_matrix", ROOT / "scripts/build_card_execution_matrix.py")
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


class CardExecutionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = matrix.read_json(ROOT / matrix.RULE_PATH)
        cls.rules = {row["indicator_id"]: row for row in cls.catalogue["cards"]}
        cls.video = matrix.read_json(ROOT / "tests/fixtures/card-video-validation-results-20261022.json")
        cls.manifest = matrix.read_json(ROOT / "tests/fixtures/card-video-cases.json")

    def validate_video(self, report):
        return matrix.validate_video_payload(report, self.manifest, self.rules)

    def scored_instance(self):
        for case in self.video["cases"]:
            for stage in case.get("stage_measurements", []):
                for row in stage["card_evaluations"]:
                    if row["status"] == "proxy_scored":
                        return deepcopy(row), deepcopy(stage), case
        self.fail("committed_real_replay_fixture_has_no_scored_instance")

    def check_instance(self, row, stage, case):
        return matrix.validate_instance(row, self.rules[row["indicator_id"]],
            video_sha256=case["source_video_sha256"], video_id=case["case_id"], stage=stage)

    def test_committed_real_video_claims_have_exactly_sixty_card_instances(self):
        rows, cases = self.validate_video(self.video)
        self.assertEqual(len(cases), 5)
        self.assertEqual(len(rows), 60)
        self.assertEqual(dict(matrix.Counter(row["status"] for row in rows)),
                         {"proxy_scored": 27, "review_required": 16, "unavailable": 17})

    def test_tampered_recognition_summary_rejected(self):
        report = deepcopy(self.video)
        report["cases"][0]["primary_window_metrics"]["matched_count"] += 1
        with self.assertRaisesRegex(ValueError, "summary_numeric_mismatch"):
            self.validate_video(report)

    def test_unknown_card_rejected(self):
        report = deepcopy(self.video)
        report["cases"][0]["stage_measurements"][0]["card_evaluations"][0]["indicator_id"] = "GS99-M01-01"
        with self.assertRaisesRegex(ValueError, "unknown_or_missing_stage_card"):
            self.validate_video(report)

    def test_duplicate_card_instance_rejected(self):
        report = deepcopy(self.video)
        rows = report["cases"][0]["stage_measurements"][0]["card_evaluations"]
        rows.append(deepcopy(rows[0]))
        with self.assertRaisesRegex(ValueError, "unknown_or_missing_stage_card"):
            self.validate_video(report)

    def test_duplicate_stage_and_case_rejected(self):
        report = deepcopy(self.video)
        stages = report["cases"][0]["stage_measurements"]
        stages[1] = deepcopy(stages[0])
        with self.assertRaisesRegex(ValueError, "duplicate_or_unknown_stage_window"):
            self.validate_video(report)
        report = deepcopy(self.video)
        report["cases"].append(deepcopy(report["cases"][0]))
        with self.assertRaisesRegex(ValueError, "video_case_inventory_mismatch"):
            self.validate_video(report)

    def test_wrong_source_video_rejected(self):
        report = deepcopy(self.video)
        report["cases"][0]["source_video_sha256"] = report["cases"][1]["source_video_sha256"]
        with self.assertRaisesRegex(ValueError, "video_case_source_binding_mismatch"):
            self.validate_video(report)

    def test_wrong_source_run_rejected(self):
        row, stage, case = self.scored_instance()
        row["video_id"] = "different-source-run"
        with self.assertRaisesRegex(ValueError, "source_run_binding_mismatch"):
            self.check_instance(row, stage, case)
        with self.assertRaisesRegex(ValueError, "temporal_source_run_binding_mismatch"):
            matrix.validate_temporal_payload({"version": "card-execution-v1.0.0", "source_run_id": "wrong"},
                                             self.rules, {}, {"id": "expected"}, {})

    def test_different_person_cannot_reuse_stage_evidence(self):
        row, stage, case = self.scored_instance()
        row["person_track_id"] += 1
        with self.assertRaisesRegex(ValueError, "stage_instance_person_binding_mismatch"):
            self.check_instance(row, stage, case)

    def test_manifest_run_directory_swap_rejected_before_source_reads(self):
        first, second = self.manifest["cases"][:2]
        altered = deepcopy(first)
        altered["run_dir"] = second["run_dir"]
        with self.assertRaisesRegex(ValueError, "source_run_directory_binding_mismatch"):
            matrix.verify_source_artifacts(ROOT, altered, self.video["cases"][0]["input_artifact_sha256"])

    def test_changed_grade_or_pass_flag_rejected(self):
        row, stage, case = self.scored_instance()
        row["conditions"][0]["passed"] = not row["conditions"][0]["passed"]
        with self.assertRaisesRegex(ValueError, "condition_pass_flag_mismatch"):
            self.check_instance(row, stage, case)
        row, stage, case = self.scored_instance()
        row["score"] += 1
        with self.assertRaisesRegex(ValueError, "proxy_grade_math_mismatch"):
            self.check_instance(row, stage, case)

    def test_dropped_condition_and_changed_threshold_rejected(self):
        row, stage, case = self.scored_instance()
        row["conditions"].pop()
        with self.assertRaisesRegex(ValueError, "condition_inventory_mismatch"):
            self.check_instance(row, stage, case)
        row, stage, case = self.scored_instance()
        row["conditions"][0]["threshold"] = -999
        with self.assertRaisesRegex(ValueError, "condition_contract_mismatch"):
            self.check_instance(row, stage, case)

    def test_original_grade_claim_and_quality_denominator_rejected(self):
        row, stage, case = self.scored_instance()
        row["original_grade"] = "A"
        with self.assertRaisesRegex(ValueError, "unsupported_original_grade_claim"):
            self.check_instance(row, stage, case)
        row, stage, case = self.scored_instance()
        row["quality"]["total_frame_count"] = row["quality"]["valid_frame_count"] * 2
        with self.assertRaisesRegex(ValueError, "scored_quality_gate_mismatch"):
            self.check_instance(row, stage, case)

    def test_single_instance_cannot_be_counted_twice(self):
        row, stage, case = self.scored_instance()
        output, seen = [], set()
        arguments = (output, seen, row, self.rules[row["indicator_id"]], case["source_video_sha256"], case["case_id"], "unit_negative_test", "/example")
        matrix._add_instance(*arguments, stage=stage)
        with self.assertRaisesRegex(ValueError, "duplicate_evidence_instance"):
            matrix._add_instance(*arguments, stage=stage)

    def test_raw_canonical_and_normalized_digests_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rules.json"
            path.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
            data = {"a": 1}
            raw, canonical, normalized = matrix.sha256(path), matrix.canonical_digest(data), matrix.normalized_digest(path)
            self.assertEqual(len({raw, canonical, normalized}), 3)
            matrix.verify_catalogue_digest(raw, matrix.RAW, path, data)
            matrix.verify_catalogue_digest(canonical, matrix.CANONICAL, path, data)
            with self.assertRaisesRegex(ValueError, "catalogue_digest_mismatch"):
                matrix.verify_catalogue_digest(raw, matrix.CANONICAL, path, data)
            with self.assertRaisesRegex(ValueError, "unknown_catalogue_digest_kind"):
                matrix.verify_catalogue_digest(raw, "guessed", path, data)
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
            result = matrix.verify_catalogue_digest(raw, matrix.RAW, path, data, normalized)
            self.assertTrue(result["normalized_equivalence_used"])
            self.assertNotEqual(result["tested_sha256"], result["current_sha256"])

    def test_module_line_endings_are_explicit_not_a_raw_hash_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "source.py"
            path.write_bytes(b'x = 1\r\n')
            raw, normalized = matrix.sha256(path), matrix.normalized_digest(path)
            path.write_bytes(b'x = 1\n')
            result = matrix.verify_module_digests(root, {"source.py": raw}, {"source.py": normalized})
            self.assertFalse(result["source.py"]["current_raw_matches_tested"])
            path.write_bytes(b'x = 2\n')
            with self.assertRaisesRegex(ValueError, "normalized_runtime_module_mismatch"):
                matrix.verify_module_digests(root, {"source.py": raw}, {"source.py": normalized})

    def test_missing_evidence_does_not_produce_success(self):
        process = subprocess.run([sys.executable, str(ROOT / "scripts/build_card_execution_matrix.py")],
                                 capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("required", process.stderr)
        with self.assertRaisesRegex(ValueError, "missing_or_oversized"):
            matrix.read_json(ROOT / "tests/fixtures/absent-card-evidence.json")

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            for text in ('{"count": 1, "count": 2}', '{"score": NaN}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    matrix.read_json(path)

    def test_all_proxy_conditions_registered_but_not_claimed_as_runtime_evidence(self):
        _, temporal = matrix.temporal_contract(ROOT)
        registered = 0
        for rule in self.rules.values():
            if rule["evaluation"]["kind"] == "visual_proxy":
                for condition in rule["evaluation"]["conditions"]:
                    self.assertNotEqual(matrix.metric_registration(condition, rule["indicator_id"], temporal), "unregistered_condition")
                registered += 1
        self.assertEqual(registered, 158)
        self.assertEqual(len(temporal), 5)

    def test_committed_matrix_recounts_and_tampered_summary_fails(self):
        document = matrix.read_json(ROOT / "docs/card-implementation-matrix-v1.json")
        matrix.validate_matrix_invariants(document)
        self.assertEqual(document["counts"]["original_automatically_graded_cards"], 0)
        self.assertEqual(document["counts"]["real_card_instance_count"], 260)
        self.assertLess(document["counts"]["real_proxy_scored_card_count"], 158)
        self.assertTrue(all(not row["per_card_unit_execution_claimed"] for row in document["cards"]))
        document["counts"]["real_instance_status_counts"]["proxy_scored"] += 1
        with self.assertRaisesRegex(ValueError, "summary_numeric_mismatch"):
            matrix.validate_matrix_invariants(document)

    def test_matrix_does_not_publish_machine_paths_or_private_metadata(self):
        document = matrix.read_json(ROOT / "docs/card-implementation-matrix-v1.json")
        text = json.dumps(document, ensure_ascii=False)
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]")
        self.assertNotIn('"source_ip"', text)
        self.assertNotIn('"original_filename"', text)
        self.assertNotIn('"source_root"', text)
        self.assertEqual(document["counts"]["real_video_source_count"], 5)
        self.assertEqual(document["counts"]["real_video_sources_with_card_instances"], 4)


if __name__ == "__main__":
    unittest.main()
