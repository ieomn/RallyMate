"""Explicit local-source integrations; no synthetic pass/grade inputs."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.card_evaluation import summarize_card_scores
import rallymate_service.card_execution as execution
from rallymate_service.card_execution import build_card_execution_report, clear_card_execution_cache

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def subset(report, prefix):
    return [card for card in report["cards"] if card["event_code"].startswith(prefix)]


class GSUnifiedWiringContractTests(unittest.TestCase):
    """Media-free wiring tests; mocks do not establish source/decode validity."""

    @classmethod
    def setUpClass(cls):
        from tests.test_card_execution import real_fixture
        sequence, events, timeline = real_fixture()
        cls.fs_baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        cls.gs_contract = json.loads((ROOT / "tests/fixtures/gs-card-service-results-20261022.json").read_text(encoding="utf-8"))["reports"][0]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.report = deepcopy(self.fs_baseline)
        self.gs = deepcopy(self.gs_contract)
        # The production call is explicitly mocked below. This artificial top
        # level identity alignment tests merge plumbing, never source accuracy.
        self.gs["source_run_id"] = self.report["source_run_id"]

    def annotations(self):
        actions = [{"technique_code": code} for code in sorted({row["event_code"] for row in self.gs["cards"] if row["instances"]})]
        (self.directory / "gs-stage-annotations.json").write_text(json.dumps({"actions": actions}), encoding="utf-8")

    def test_without_annotation_never_invokes_gs_and_changes_nothing(self):
        before = deepcopy(self.report)
        with patch("rallymate_service.gs_card_execution.build_gs_card_execution_report") as production:
            execution._apply_optional_gs_annotations(self.report, self.directory)
        production.assert_not_called()
        self.assertEqual(self.report, before)

    def test_gs_validation_failure_keeps_computed_fs_instances(self):
        self.annotations()
        with patch("rallymate_service.gs_card_execution.build_gs_card_execution_report", side_effect=ValueError("source_video_digest_mismatch")):
            execution._apply_optional_gs_annotations(self.report, self.directory)
        self.assertEqual(subset(self.report, "FS"), subset(self.fs_baseline, "FS"))
        self.assertEqual(self.report["summary"], self.fs_baseline["summary"])
        self.assertEqual(self.report["input"]["gs_execution"]["reason_codes"], ["source_video_digest_mismatch"])
        self.assertTrue(all(row["run_status"] == "unavailable" and not row["instances"] for row in subset(self.report, "GS")))

    def test_successful_contract_merge_recounts_and_preserves_source_annotations(self):
        self.annotations()
        with patch("rallymate_service.gs_card_execution.build_gs_card_execution_report", return_value=self.gs) as production:
            execution._apply_optional_gs_annotations(self.report, self.directory)
        production.assert_called_once()
        self.assertEqual(self.report["version"], "card-execution-v1.0.0")
        self.assertEqual(len(self.report["cards"]), 298)
        self.assertEqual(subset(self.report, "FS"), subset(self.fs_baseline, "FS"))
        self.assertEqual(subset(self.report, "GS"), self.gs["cards"])
        instances = [row for card in self.report["cards"] for row in card["instances"]]
        self.assertEqual(self.report["coverage"]["computed_card_instance_count"], len(instances))
        self.assertEqual(self.report["summary"], summarize_card_scores(instances, [row["indicator_id"] for row in self.report["cards"]]))
        self.assertEqual(self.report["coverage"]["run_card_status_counts"], dict(Counter(row["run_status"] for row in self.report["cards"])))
        self.assertEqual(self.report["gs_stage_windows"], self.gs["stage_windows"])
        self.assertTrue(all(row.get("source_annotation") for card in subset(self.report, "GS") for row in card["instances"]))

    def test_unverified_or_cross_run_contract_is_rejected(self):
        for mutation in ("service_eligible", "artifact_files_verified", "decoder_verified_from_original_video", "source_run_id", "rule_version"):
            with self.subTest(mutation=mutation):
                report, gs = deepcopy(self.fs_baseline), deepcopy(self.gs)
                if mutation in gs["input"]:
                    gs["input"][mutation] = False
                else:
                    gs[mutation] = "different-source-or-rule"
                self.annotations()
                with patch("rallymate_service.gs_card_execution.build_gs_card_execution_report", return_value=gs):
                    execution._apply_optional_gs_annotations(report, self.directory)
                self.assertEqual(subset(report, "FS"), subset(self.fs_baseline, "FS"))
                self.assertEqual(report["input"]["gs_execution"]["reason_codes"], ["gs_production_report_binding_mismatch"])
                self.assertTrue(all(not row["instances"] for row in subset(report, "GS")))


class GSUnifiedServiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json").read_text(encoding="utf-8"))
        cls.manifest = {case["id"]: case for case in json.loads((ROOT / "tests/fixtures/card-video-cases.json").read_text(encoding="utf-8"))["cases"]}

    def setUp(self):
        clear_card_execution_cache()
        self.addCleanup(clear_card_execution_cache)

    @contextmanager
    def real_run(self, case):
        original = ROOT / self.manifest[case["case_id"]]["run_dir"]
        files = ("frames.jsonl", "events.jsonl", "primary-player.jsonl", "summary.json")
        if not all((original / name).is_file() for name in files):
            self.skipTest("integration_requires_original_local_source_artifacts:" + case["case_id"])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / case["source_job_id"]
            directory.mkdir()
            for name in files:
                # These linked source files are read only throughout the tests.
                try:
                    os.link(original / name, directory / name)
                except OSError:
                    shutil.copyfile(original / name, directory / name)
            annotations = deepcopy(case["annotations"])
            annotations["source_run_id"] = case["source_job_id"]
            yield directory, annotations

    def write_annotations(self, directory, annotations):
        path = directory / "gs-stage-annotations.json"
        path.write_text(json.dumps(annotations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_three_real_runs_merge_into_298_rows_and_preserve_fs_rows(self):
        counts = Counter()
        for case in self.fixture["cases"]:
            with self.real_run(case) as (directory, annotations):
                baseline = build_card_execution_report(directory)
                self.assertNotIn("gs_execution", baseline["input"])
                self.write_annotations(directory, annotations)
                merged = build_card_execution_report(directory)
                self.assertEqual(merged["version"], "card-execution-v1.0.0")
                self.assertEqual(merged["source_run_id"], directory.name)
                self.assertEqual(len(merged["cards"]), 298)
                self.assertEqual(subset(merged, "FS"), subset(baseline, "FS"))
                self.assertEqual(merged["input"]["gs_execution"]["status"], "executed")
                self.assertTrue(merged["input"]["gs_execution"]["service_eligible"])
                self.assertEqual(merged["coverage"]["gs_executed_card_count"], 5)
                by_annotation = {stage["annotation_id"]: stage for stage in annotations["stages"]}
                for card in subset(merged, "GS"):
                    counts.update(row["status"] for row in card["instances"])
                    for instance in card["instances"]:
                        source = instance["source_annotation"]
                        self.assertEqual(source["binding"], by_annotation[source["annotation_id"]]["binding"])
                        self.assertEqual(source["document_sha256"], merged["input"]["gs_execution"]["annotation_document_sha256"])
                        self.assertEqual(instance["window"]["stage_code"], card["stage_code"])
                        self.assertIsNone(instance["original_grade"])
                self.assertEqual(merged["coverage"]["computed_card_instance_count"], sum(card["instance_count"] for card in merged["cards"]))
                self.assertEqual(merged["summary"]["channels"]["original"]["scored_cards"], 0)
        self.assertEqual(counts, {"proxy_scored": 27, "review_required": 16, "unavailable": 17})

    def test_invalid_gs_annotation_is_isolated_without_changing_fs(self):
        with self.real_run(self.fixture["cases"][0]) as (directory, annotations):
            baseline = build_card_execution_report(directory)
            annotations["stages"][0]["score"] = 100
            self.write_annotations(directory, annotations)
            report = build_card_execution_report(directory)
            self.assertEqual(subset(report, "FS"), subset(baseline, "FS"))
            self.assertEqual(report["summary"], baseline["summary"])
            self.assertEqual(report["input"]["gs_execution"]["status"], "unavailable")
            self.assertTrue(all(card["run_status"] == "unavailable" and not card["instances"] for card in subset(report, "GS")))

    def test_same_size_mtime_preserved_annotation_edit_invalidates_cache(self):
        with self.real_run(self.fixture["cases"][0]) as (directory, annotations):
            path = self.write_annotations(directory, annotations)
            first = build_card_execution_report(directory)
            stat = path.stat()
            before = path.read_bytes(); after = before.replace(b"decoded_frames_2026-10-22", b"decoded_frames_2026-10-23")
            self.assertNotEqual(before, after); self.assertEqual(len(before), len(after))
            path.write_bytes(after); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = build_card_execution_report(directory)
            self.assertNotEqual(first["input"]["gs_execution"]["annotation_document_sha256"], changed["input"]["gs_execution"]["annotation_document_sha256"])
            self.assertEqual(changed["input"]["gs_execution"]["status"], "executed")

    def test_same_size_mtime_preserved_video_edit_cannot_return_cached_grade(self):
        # A private video copy is changed. Never modify a linked or original file.
        with self.real_run(self.fixture["cases"][0]) as (directory, annotations):
            summary_path = directory / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            original_video = Path(summary["input"]["video_path"])
            if not original_video.is_file():
                self.skipTest("integration_requires_original_video")
            copied_video = directory / "video-copy.mp4"; shutil.copyfile(original_video, copied_video)
            # Replace the hardlink with a new local summary before editing it.
            replacement = directory / "summary-private.json"
            summary["input"]["video_path"] = str(copied_video)
            replacement.write_text(json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(replacement, summary_path)
            annotations["input_artifact_sha256"]["summary.json"] = sha(summary_path)
            self.write_annotations(directory, annotations)
            first = build_card_execution_report(directory)
            self.assertEqual(first["input"]["gs_execution"]["status"], "executed")
            stat = copied_video.stat()
            with copied_video.open("r+b") as stream:
                original = stream.read(1); stream.seek(0); stream.write(bytes([original[0] ^ 1]))
            os.utime(copied_video, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = build_card_execution_report(directory)
            self.assertEqual(changed["input"]["gs_execution"]["status"], "unavailable")
            self.assertEqual(changed["input"]["gs_execution"]["reason_codes"], ["source_video_digest_mismatch"])
            self.assertEqual(subset(changed, "FS"), subset(first, "FS"))
            self.assertTrue(all(not row["instances"] for row in subset(changed, "GS")))

    def test_gs_annotation_cannot_hide_a_broken_fs_run_identity(self):
        with self.real_run(self.fixture["cases"][0]) as (directory, annotations):
            wrong = directory.with_name("wrong-request-job")
            self.assertEqual(directory.resolve().parent, wrong.resolve().parent)
            self.assertTrue(directory.resolve().parent.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            directory.rename(wrong)
            annotations["source_run_id"] = wrong.name
            self.write_annotations(wrong, annotations)
            with self.assertRaisesRegex(ValueError, "card_frame_run_binding_mismatch"):
                build_card_execution_report(wrong)


if __name__ == "__main__":
    unittest.main()
