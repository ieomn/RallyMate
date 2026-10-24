"""M02-only merge contracts and actual native e420 unified/HTTP replay.

Small contract tests recompute recorded raw trajectories, then explicitly mock
only service verification. The media test does not mock the production adapter.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import rallymate_service.card_execution as execution
import rallymate_service.micro_adjustment_cycles as micro
from rallymate_service.gs_card_execution import _canonical, _sha
from tests.test_card_execution import real_fixture as temporal_fixture
from tests.test_direction_reference_observables import real_fixture as direction_fixture
from tests.test_micro_adjustment_cycles import real_args, compute, document

ANNOTATION_NAME = "micro-adjustment-cycle-annotations.json"
EXECUTION = "micro_adjustment_cycle_execution"
TOP_WINDOWS = "micro_adjustment_cycle_windows"


def others(report):
    return [row for row in report["cards"] if row["indicator_id"] != micro.CARD]


def owned(report):
    return next(row for row in report["cards"] if row["indicator_id"] == micro.CARD)


class MicroCycleUnifiedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sequence, events, timeline = temporal_fixture()
        cls.baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        args = real_args()
        identity = cls.baseline["source_run_id"]
        args[-1]["source_run_id"] = identity
        for record in args[0]: record["job_id"] = identity
        for event in args[2]: event["video_id"] = identity
        for row in args[1]:
            for key in ("job_id", "video_id"):
                if key in row: row[key] = identity
        cls.document = args[-1]
        cls.result = compute(args)
        # This explicit wiring-only mock is not claimed as media verification.
        cls.result["input"].update(service_eligible=True, artifact_files_verified=True, decoder_verified_from_original_video=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.report = deepcopy(self.baseline)
        self.result = deepcopy(type(self).result)
        self.document = deepcopy(type(self).document)
        execution.clear_card_execution_cache()
        self.addCleanup(execution.clear_card_execution_cache)

    def annotate(self):
        path = self.directory / ANNOTATION_NAME
        path.write_text(json.dumps(self.document), encoding="utf8")
        return path

    def merge(self, result=None):
        with patch.object(micro, "build_micro_adjustment_cycle_report", return_value=self.result if result is None else result):
            execution._apply_optional_micro_cycle_annotations(self.report, self.directory)

    def test_absent_annotation_preserves_every_field(self):
        with patch.object(micro, "build_micro_adjustment_cycle_report") as call:
            execution._apply_optional_micro_cycle_annotations(self.report, self.directory)
        call.assert_not_called()
        self.assertEqual(self.report, self.baseline)

    def test_only_m02_replaced_complete_metadata_and_equal_card_mean(self):
        self.annotate(); self.merge()
        self.assertEqual(len(self.report["cards"]), 298)
        self.assertEqual(others(self.report), others(self.baseline))
        row, = owned(self.report)["instances"]
        self.assertEqual((row["status"], row["grade"], row["score"]), ("scored", "C", 60))
        self.assertEqual([c["passed"] for c in row["conditions"]], [False, True])
        self.assertEqual(self.report["input"][EXECUTION]["replaced_card_ids"], [micro.CARD])
        self.assertEqual(self.report[TOP_WINDOWS], self.result["fs_card_windows"])
        self.assertEqual(self.report["coverage"]["micro_cycle_annotated_window_count"], 1)
        self.assertEqual(self.report["coverage"]["micro_cycle_executed_card_count"], 1)
        self.assertEqual(self.report["coverage"]["computed_card_instance_count"], self.baseline["coverage"]["computed_card_instance_count"]+1)
        formal = self.report["summary"]["channels"]["formal"]
        self.assertAlmostEqual(self.report["overall_score"], sum(formal["per_card"].values())/len(formal["per_card"]))
        self.assertEqual(self.report["version"], execution.VERSION)
        self.assertNotIn("independent_one_card_not_unified_api_or_frontend", self.report["limitations"])
        json.dumps(self.report, allow_nan=False)

    def test_failure_clears_previous_score_and_top_windows_only_m02(self):
        self.annotate(); self.merge()
        with patch.object(micro, "build_micro_adjustment_cycle_report", side_effect=ValueError("micro_adjustment_cycle_source_video_digest_mismatch")):
            execution._apply_optional_micro_cycle_annotations(self.report, self.directory)
        self.assertEqual(others(self.report), others(self.baseline))
        self.assertEqual(owned(self.report)["instances"], [])
        self.assertEqual(owned(self.report)["run_status"], "unavailable")
        self.assertNotIn(TOP_WINDOWS, self.report)
        self.assertEqual(self.report["input"][EXECUTION]["status"], "unavailable")
        self.assertEqual(self.report["coverage"]["micro_cycle_executed_card_count"], 0)
        self.assertNotIn(micro.CARD, self.report["summary"]["channels"]["formal"]["per_card"])
        self.assertEqual(self.report["overall_score"], self.baseline["overall_score"])

    def test_invalid_supplied_scores_or_foreign_card_cannot_enter_service(self):
        for kind in ("score", "count", "foreign", "duplicate", "broken_json"):
            with self.subTest(kind=kind):
                self.report = deepcopy(self.baseline)
                self.document = deepcopy(type(self).document)
                if kind == "score": self.document["windows"][0]["score"] = 100
                if kind == "count": self.document["windows"][0]["complete_visual_adjustment_episode_count"] = 8
                if kind == "foreign": self.document["windows"][0]["indicator_id"] = "FS05-M03"
                if kind == "duplicate": self.document["windows"] *= 2
                path = self.annotate()
                if kind == "broken_json": path.write_text("{broken", encoding="utf8")
                with patch.object(micro, "build_micro_adjustment_cycle_report") as call:
                    execution._apply_optional_micro_cycle_annotations(self.report, self.directory)
                call.assert_not_called()
                self.assertEqual(others(self.report), others(self.baseline))
                self.assertEqual(self.report["input"][EXECUTION]["status"], "unavailable")

    def test_unverified_wrong_version_duplicate_instance_or_stale_topwindow_rejected(self):
        kinds = ("source", "version", "policy", "rule", "annotation_version", "hash", "eligible", "inventory",
                 "duplicate", "foreign_instance", "instance_rule", "top_window", "top_frames")
        for kind in kinds:
            with self.subTest(kind=kind):
                self.report = deepcopy(self.baseline); result = deepcopy(self.result)
                if kind == "source": result["source_run_id"] = "foreign"
                if kind == "version": result["version"] = "old"
                if kind == "policy": result["scoring_policy_version"] = "old"
                if kind == "rule": result["input"]["application_rule_version"] = "old"
                if kind == "annotation_version": result["input"]["annotation_version"] = "old"
                if kind == "hash": result["input"]["annotation_document_sha256"] = "0"*64
                if kind == "eligible": result["input"]["service_eligible"] = False
                if kind == "inventory": result["cards"][0]["indicator_id"] = "FS05-M03"
                if kind == "duplicate": result["cards"][0]["instances"] *= 2
                if kind == "foreign_instance": result["cards"][0]["instances"][0]["video_id"] = "foreign"
                if kind == "instance_rule": result["cards"][0]["instances"][0]["application_rule_version"] = "old"
                if kind == "top_window": result["fs_card_windows"][0]["window"]["start_ms"] -= 1
                if kind == "top_frames": result["fs_card_windows"][0]["source_frames"].pop()
                self.annotate(); self.merge(result)
                self.assertEqual(self.report["input"][EXECUTION]["status"], "unavailable")
                self.assertEqual(owned(self.report)["instances"], [])
                self.assertEqual(others(self.report), others(self.baseline))

    def test_empty_valid_document_does_not_replace_existing_card(self):
        self.annotate(); self.merge()
        existing = deepcopy(self.report["cards"])
        self.result = deepcopy(type(self).result)
        self.document["windows"] = []
        self.result["cards"][0]["instances"] = []
        self.result["fs_card_windows"] = []
        self.result["input"]["annotation_document_sha256"] = _canonical(self.document)
        self.annotate(); self.merge()
        self.assertEqual(self.report["cards"], existing)
        self.assertEqual(self.report["input"][EXECUTION]["replaced_card_ids"], [])
        self.assertEqual(self.report["coverage"]["micro_cycle_executed_card_count"], 0)

    def test_cache_content_binding_same_stat_annotation_raw_summary_and_video(self):
        for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl"):
            (self.directory/name).write_text("{ }\n", encoding="utf8")
        video = self.directory/"source.mp4"; video.write_bytes(b"abcd")
        summary = self.directory/"summary.json"
        summary.write_text(json.dumps({"input": {"video_path": str(video)}, "note": "a"}), encoding="utf8")
        annotation = self.annotate()
        for path in (annotation, summary, video, *(self.directory/n for n in ("frames.jsonl", "primary-player.jsonl", "events.jsonl"))):
            with self.subTest(path=path.name):
                before = execution._input_signature(self.directory); stat = path.stat(); data = path.read_bytes()
                changed = data.replace(b"112..136", b"112--136") if path == annotation else (b"abce" if path == video else data.replace(b"a", b"b", 1) if path == summary else data.replace(b"{", b"[").replace(b"}", b"]"))
                self.assertNotEqual(data, changed); self.assertEqual(len(data), len(changed))
                path.write_bytes(changed); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                self.assertNotEqual(before, execution._input_signature(self.directory))
                path.write_bytes(data); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        before = execution._input_signature(self.directory); annotation.unlink()
        self.assertNotEqual(before, execution._input_signature(self.directory))

    def test_runtime_rule_annotation_and_shared_measurement_versions_bind_cache(self):
        import rallymate_scoring.step_motion_evidence as step
        import rallymate_scoring.direction_reference_observables as direction
        import rallymate_scoring.card_kinematics as geometry
        import rallymate_service.fs_card_windows as fs
        self.annotate()
        with patch.object(execution, "_input_signature", return_value=("stable",)), patch.object(execution, "_cached_report", return_value={}) as call:
            execution.build_card_execution_report(self.directory)
            original = call.call_args.args[-1]
            for module, key in ((micro, "VERSION"), (micro, "RULE_VERSION"), (micro, "ANNOTATION_VERSION"),
                                (step, "VERSION"), (direction, "VERSION"), (geometry, "VERSION"), (fs, "VERSION")):
                with self.subTest(module=module.__name__, key=key), patch.object(module, key, "test-new-version"):
                    execution.build_card_execution_report(self.directory)
                    self.assertNotEqual(original, call.call_args.args[-1])


class MicroCycleActualUnifiedIntegration(unittest.TestCase):
    def test_native_original_media_three_cards_297_unchanged_and_http_both_contracts(self):
        data = direction_fixture()
        root = Path(os.environ.get("RALLYMATE_REAL_ARTIFACT_ROOT", Path(__file__).resolve().parents[1]))
        original = root/data["provenance"]["source_run_directory"]
        video = root/data["provenance"]["source_video_relative_path"]
        names = data["provenance"]["input_artifact_sha256"]
        if not video.is_file() or not all((original/name).is_file() for name in names):
            self.skipTest("native e420 actual-media integration requires the original raw files and video")
        from tests.test_card_execution_api import CardExecutionApiTests
        from fastapi.testclient import TestClient
        before = {name: _sha(original/name) for name in names}
        self.assertEqual(before, names)
        video_sha = _sha(video)
        self.assertEqual(video_sha, data["annotation_document"]["video_sha256"])
        execution.clear_card_execution_cache(); self.addCleanup(execution.clear_card_execution_cache)
        with tempfile.TemporaryDirectory() as temporary:
            app, settings, database = CardExecutionApiTests().app(Path(temporary))
            identity = document()["source_run_id"]
            directory = settings.runs_dir/identity; directory.mkdir()
            for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl"):
                try: os.link(original/name, directory/name)
                except OSError: shutil.copyfile(original/name, directory/name)
            summary = json.loads((original/"summary.json").read_text(encoding="utf8"))
            summary["input"]["video_path"] = str(video.resolve())
            (directory/"summary.json").write_text(json.dumps(summary), encoding="utf8")
            direction_doc, micro_doc = deepcopy(data["annotation_document"]), document()
            for doc in (direction_doc, micro_doc): doc["input_artifact_sha256"]["summary.json"] = _sha(directory/"summary.json")
            direction_path = directory/"directional-micro-adjustment-annotations.json"
            direction_path.write_text(json.dumps(direction_doc), encoding="utf8")
            baseline = execution.build_card_execution_report(directory)
            annotation = directory/ANNOTATION_NAME; annotation.write_text(json.dumps(micro_doc), encoding="utf8")
            merged = execution.build_card_execution_report(directory)
            self.assertEqual(len(merged["cards"]), 298)
            self.assertEqual(others(merged), others(baseline))
            self.assertEqual(len(others(merged)), 297)
            self.assertEqual(merged["directional_micro_adjustment_windows"], baseline["directional_micro_adjustment_windows"])
            for identifier in ("FS05-M02", "FS05-M03", "FS05-M04"):
                row, = next(c for c in merged["cards"] if c["indicator_id"] == identifier)["instances"]
                self.assertEqual((row["status"], row["grade"], row["score"]), ("scored", "C", 60))
            self.assertEqual(merged["coverage"]["computed_card_instance_count"], baseline["coverage"]["computed_card_instance_count"]+1)
            self.assertTrue(merged["input"][EXECUTION]["decoder_verified_from_original_video"])
            execution.clear_card_execution_cache()
            self.assertEqual(execution.build_card_execution_report(directory), merged)
            request = settings.requests_dir/(identity+".json"); request.write_text("{}", encoding="utf8")
            database.create_job(identity, video.name, video, request, directory)
            database.claim_next("test-worker", settings.lease_seconds, settings.max_attempts)
            database.mark_succeeded(identity, summary)
            http = {}
            with TestClient(app) as client:
                for contract in ("current", "legacy-v1"):
                    response = client.get(f"/v1/jobs/{identity}/demo-result?report_contract={contract}")
                    self.assertEqual(response.status_code, 200, response.text[:500])
                    http[contract] = response.json()["card_execution"]
                    self.assertEqual(http[contract], json.loads(json.dumps(merged, allow_nan=False)))
            output_name = os.environ.get("MICRO_CYCLE_VALIDATION_OUTPUT")
            if output_name:
                output = Path(output_name); output.mkdir(parents=True, exist_ok=True)
                for name, value in (("direction-only-baseline.json", baseline), ("unified-report.json", merged),
                                    ("http-current.json", http["current"]), ("http-legacy-v1.json", http["legacy-v1"])):
                    (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
                for path in (annotation, direction_path, directory/"summary.json"):
                    (output/path.name).write_bytes(path.read_bytes())
                proof = {"source_run_id": identity, "source_video_sha256": video_sha, "original_artifact_sha256": before,
                    "execution_artifact_sha256": micro_doc["input_artifact_sha256"], "unchanged_other_card_count": 297,
                    "total_card_count": 298, "independent_uncached_rebuild_equal": True,
                    "http_contracts": list(http), "http_exact_report_equal": True,
                    "migration": "Only the copied summary.input.video_path was relocated to the same SHA original media; source records and identities are unchanged",
                    "pose_inference_rerun": False, "raw_media_decoder_executed": True}
                (output/"migration-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
            # Same-size/same-mtime invalid annotation must clear C60, even after
            # a successful cached HTTP request. The other 297 rows stay intact.
            stat = annotation.stat(); raw = annotation.read_bytes()
            bad = raw.replace(b'"person_track_id": 1', b'"person_track_id": 9')
            self.assertNotEqual(bad, raw); self.assertEqual(len(bad), len(raw))
            annotation.write_bytes(bad); os.utime(annotation, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            failed = execution.build_card_execution_report(directory)
            self.assertEqual(others(failed), others(baseline))
            self.assertTrue(all(i["score"] is None and i["grade"] is None for i in owned(failed)["instances"]))
            self.assertEqual(owned(failed)["run_status"], "unavailable")
            self.assertNotIn(micro.CARD, failed["summary"]["channels"]["formal"]["per_card"])
            if output_name:
                (Path(output_name)/"failed-binding-report.json").write_text(json.dumps(failed, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
            annotation.unlink()
            self.assertEqual(execution.build_card_execution_report(directory), baseline)
        self.assertEqual({name: _sha(original/name) for name in names}, before)
        self.assertEqual(_sha(video), video_sha)


if __name__ == "__main__":
    unittest.main()
