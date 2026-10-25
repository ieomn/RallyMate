"""FS05 merge contracts and optional real-media API roundtrip.

Mocked production results below exercise wiring only. The separate media test
uses the original e420 records and video; it does not rename a 0a3 pose run.
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
from rallymate_service.directional_micro_adjustment import SUPPORTED_CARDS
from rallymate_service.gs_card_execution import _canonical, _sha
from tests.test_card_execution import real_fixture as temporal_fixture
from tests.test_direction_reference_observables import real_fixture, real_args, compute

ANNOTATION_NAME = "directional-micro-adjustment-annotations.json"


def other_cards(report, owned=SUPPORTED_CARDS):
    return [row for row in report["cards"] if row["indicator_id"] not in owned]


def owned_instances(report):
    return [instance for card in report["cards"] if card["indicator_id"] in SUPPORTED_CARDS for instance in card["instances"]]


class DirectionalUnifiedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sequence, events, timeline = temporal_fixture()
        cls.baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        cls.data = real_fixture()
        # This explicitly mocked service identity is not real media validation.
        args = real_args(deepcopy(cls.data))
        identity = cls.baseline["source_run_id"]
        args[-1]["source_run_id"] = identity
        for record in args[0]: record["job_id"] = identity
        for event in args[2]: event["video_id"] = identity
        for row in args[1]:
            for key in ("job_id", "video_id"):
                if key in row: row[key] = identity
        cls.document = args[-1]
        cls.result = compute(args)
        cls.result["input"].update(service_eligible=True, artifact_files_verified=True, decoder_verified_from_original_video=True)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.report = deepcopy(self.baseline)
        self.result = deepcopy(self.result)
        self.document = deepcopy(self.document)

    def annotate(self):
        path = self.directory / ANNOTATION_NAME
        path.write_text(json.dumps(self.document), encoding="utf8")
        return path

    def merge(self, result=None):
        with patch("rallymate_service.directional_micro_adjustment.build_directional_micro_adjustment_report", return_value=result or self.result):
            execution._apply_optional_directional_annotations(self.report, self.directory)

    def test_absence_preserves_every_existing_value_and_never_calls_consumer(self):
        with patch("rallymate_service.directional_micro_adjustment.build_directional_micro_adjustment_report") as call:
            execution._apply_optional_directional_annotations(self.report, self.directory)
        call.assert_not_called()
        self.assertEqual(self.report, self.baseline)

    def test_two_cards_replace_only_their_rows_and_recompute_equal_card_mean(self):
        self.annotate(); self.merge()
        self.assertEqual(len(self.report["cards"]), 298)
        self.assertEqual(other_cards(self.report), other_cards(self.baseline))
        self.assertEqual(self.report["version"], execution.VERSION)
        self.assertEqual([i["score"] for i in owned_instances(self.report)], [200 / 3, 200 / 3])
        self.assertEqual(self.report["coverage"]["computed_card_instance_count"], self.baseline["coverage"]["computed_card_instance_count"]+2)
        formal = self.report["summary"]["channels"]["formal"]
        self.assertAlmostEqual(self.report["overall_score"], sum(formal["per_card"].values())/len(formal["per_card"]))
        self.assertEqual(self.report["directional_micro_adjustment_windows"], self.result["fs_card_windows"])
        json.loads(json.dumps(self.report, allow_nan=False))

    def test_partial_document_preserves_unannotated_partner_and_bad_attachment_clears_only_declared(self):
        self.document["windows"] = self.document["windows"][:1]
        self.result["input"]["annotation_document_sha256"] = _canonical(self.document)
        self.result["cards"][1]["instances"] = []
        self.result["fs_card_windows"] = self.result["fs_card_windows"][:1]
        self.annotate(); self.merge()
        self.assertEqual(other_cards(self.report, {"FS05-M03"}), other_cards(self.baseline, {"FS05-M03"}))
        with patch("rallymate_service.directional_micro_adjustment.build_directional_micro_adjustment_report", side_effect=ValueError("direction_reference_invalid")):
            execution._apply_optional_directional_annotations(self.report, self.directory)
        self.assertEqual(other_cards(self.report, {"FS05-M03"}), other_cards(self.baseline, {"FS05-M03"}))
        card = next(c for c in self.report["cards"] if c["indicator_id"] == "FS05-M03")
        self.assertEqual(card["instances"], [])
        self.assertEqual(card["reason_codes"], ["direction_reference_invalid"])

    def test_foreign_unverified_duplicate_or_injected_card_result_never_merges(self):
        for kind in ("source", "policy", "hash", "eligible", "inventory", "duplicate", "foreign_instance"):
            self.report = deepcopy(self.baseline); result = deepcopy(self.result)
            if kind == "source": result["source_run_id"] = "another"
            if kind == "policy": result["scoring_policy_version"] = "another"
            if kind == "hash": result["input"]["annotation_document_sha256"] = "0"*64
            if kind == "eligible": result["input"]["service_eligible"] = False
            if kind == "inventory": result["cards"][1]["indicator_id"] = "FS05-M02"
            if kind == "duplicate": result["cards"][0]["instances"] *= 2
            if kind == "foreign_instance": result["cards"][0]["instances"][0]["video_id"] = "another"
            self.annotate(); self.merge(result)
            with self.subTest(kind=kind):
                self.assertEqual(self.report["input"]["directional_micro_adjustment_execution"]["status"], "unavailable")
                self.assertFalse(owned_instances(self.report))
                self.assertEqual(other_cards(self.report), other_cards(self.baseline))

    def test_same_stat_annotation_or_video_edit_invalidates_signature(self):
        for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl"):
            (self.directory/name).write_text("{}\n")
        video = self.directory/"source.mp4"; video.write_bytes(b"abcd")
        (self.directory/"summary.json").write_text(json.dumps({"input": {"video_path": str(video)}}))
        annotation = self.annotate()
        for path in (annotation, video):
            old = execution._input_signature(self.directory); stat = path.stat()
            raw = path.read_bytes()
            changed = raw.replace(b"112..136", b"112--136") if path == annotation else b"abce"
            self.assertNotEqual(raw, changed); self.assertEqual(len(raw), len(changed))
            path.write_bytes(changed); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(old, execution._input_signature(self.directory))


class DirectionalRealUnifiedIntegration(unittest.TestCase):
    def test_e420_original_media_unified_and_api_export_keep_all_298_rows(self):
        data = real_fixture()
        artifact_root = Path(os.environ.get("RALLYMATE_REAL_ARTIFACT_ROOT", Path(__file__).resolve().parents[1]))
        original = artifact_root/data["provenance"]["source_run_directory"]
        video = artifact_root/data["provenance"]["source_video_relative_path"]
        if not video.is_file() or not all((original/name).is_file() for name in data["provenance"]["input_artifact_sha256"]):
            self.skipTest("optional native e420 media/API integration needs original artifacts")
        from tests.test_card_execution_api import CardExecutionApiTests
        from fastapi.testclient import TestClient
        execution.clear_card_execution_cache()
        self.addCleanup(execution.clear_card_execution_cache)
        with tempfile.TemporaryDirectory() as temporary:
            helper = CardExecutionApiTests()
            app, settings, database = helper.app(Path(temporary))
            identity = data["annotation_document"]["source_run_id"]
            directory = settings.runs_dir/identity; directory.mkdir()
            for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl"):
                try: os.link(original/name, directory/name)
                except OSError: shutil.copyfile(original/name, directory/name)
            summary = json.loads((original/"summary.json").read_text(encoding="utf8"))
            summary["input"]["video_path"] = str(video.resolve())
            (directory/"summary.json").write_text(json.dumps(summary), encoding="utf8")
            document = deepcopy(data["annotation_document"])
            document["input_artifact_sha256"]["summary.json"] = _sha(directory/"summary.json")
            baseline = execution.build_card_execution_report(directory)
            path = directory/ANNOTATION_NAME
            path.write_text(json.dumps(document), encoding="utf8")
            merged = execution.build_card_execution_report(directory)
            self.assertEqual(other_cards(merged), other_cards(baseline))
            self.assertEqual(len(merged["cards"]), 298)
            self.assertEqual([i["score"] for i in owned_instances(merged)], [200 / 3, 200 / 3])
            self.assertEqual(merged["coverage"]["computed_card_instance_count"], baseline["coverage"]["computed_card_instance_count"]+2)
            self.assertEqual(merged["coverage"]["directional_executed_card_count"], 2)
            self.assertTrue(merged["input"]["directional_micro_adjustment_execution"]["service_eligible"])
            request = settings.requests_dir/(identity+".json"); request.write_text("{}")
            database.create_job(identity, video.name, video, request, directory)
            database.claim_next("test-worker", settings.lease_seconds, settings.max_attempts)
            database.mark_succeeded(identity, summary)
            with TestClient(app) as client:
                for contract in ("current", "legacy-v1"):
                    response = client.get(f"/v1/jobs/{identity}/demo-result?report_contract={contract}")
                    self.assertEqual(response.status_code, 200, response.text[:1000])
                    actual = response.json()["card_execution"]
                    self.assertEqual(actual, json.loads(json.dumps(merged, allow_nan=False)))
            if os.environ.get("DIRECTIONAL_VALIDATION_OUTPUT"):
                output = Path(os.environ["DIRECTIONAL_VALIDATION_OUTPUT"]); output.mkdir(parents=True, exist_ok=True)
                for name, value in (("baseline-report.json", baseline), ("unified-report.json", merged),
                                    ("actual-http-report.json", actual)):
                    (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
                (output/ANNOTATION_NAME).write_bytes(path.read_bytes())
                (output/"migrated-summary.json").write_bytes((directory/"summary.json").read_bytes())
                (output/"migration-proof.json").write_text(json.dumps({
                    "source_run_id": identity, "source_video_sha256": document["video_sha256"],
                    "original_artifact_sha256": data["provenance"]["input_artifact_sha256"],
                    "execution_artifact_sha256": document["input_artifact_sha256"],
                    "migration": "Only summary.input.video_path changed to the existing same-SHA local video; original frames/events/primary bytes and job identities remain unchanged",
                    "http_contracts": ["current", "legacy-v1"], "http_exact_report_roundtrip": True}, indent=2)+"\n", encoding="utf8")
            # A same-length binding mutation must not retain the previous completion score.
            stat = path.stat(); raw = path.read_bytes()
            modified = raw.replace(b'"derived_selection_epoch": 1', b'"derived_selection_epoch": 9')
            self.assertNotEqual(raw, modified); self.assertEqual(len(raw), len(modified))
            path.write_bytes(modified); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = execution.build_card_execution_report(directory)
            self.assertTrue(all(i["score"] is None for i in owned_instances(changed)))
            self.assertEqual(other_cards(changed), other_cards(baseline))
            # Missing raw FS data is still fatal before any optional adapter.
            (directory/"events.jsonl").unlink()
            with self.assertRaises(OSError): execution.build_card_execution_report(directory)


if __name__ == "__main__":
    unittest.main()
