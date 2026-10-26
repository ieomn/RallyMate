"""Optional FS merge and cache contracts; mocks are not video validation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rallymate_service.card_execution as execution
from rallymate_service.fs_card_windows import supported_rules
from tests.test_card_execution import real_fixture
from tests.test_fs_card_windows import fixture, compute


class FSWindowUnifiedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sequence, events, timeline = real_fixture()
        cls.baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        cls.arguments = fixture()
        cls.result = compute(cls.arguments)
        cls.owned = set(supported_rules())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.report = deepcopy(self.baseline)
        self.result = deepcopy(self.result)
        # Production result is mocked only to test the isolated merge contract.
        self.result["source_run_id"] = self.report["source_run_id"]
        self.result["input"].update(service_eligible=True, artifact_files_verified=True, decoder_verified_from_original_video=True)

    def annotate(self):
        path = self.directory / "fs-card-window-annotations.json"
        path.write_text(json.dumps(self.arguments[-1]), encoding="utf-8")
        return path

    def others(self, report):
        return [row for row in report["cards"] if row["indicator_id"] not in self.owned]

    def test_absence_preserves_all_existing_results(self):
        with patch("rallymate_service.fs_card_windows.build_fs_card_window_report") as production:
            execution._apply_optional_fs_card_annotations(self.report, self.directory)
        production.assert_not_called()
        self.assertEqual(self.report, self.baseline)

    def test_only_owned_thirty_five_cards_are_replaced_and_summary_is_recomputed(self):
        self.annotate()
        with patch("rallymate_service.fs_card_windows.build_fs_card_window_report", return_value=self.result):
            execution._apply_optional_fs_card_annotations(self.report, self.directory)
        self.assertEqual(len(self.report["cards"]), 298)
        self.assertEqual(self.others(self.report), self.others(self.baseline))
        self.assertEqual(self.report["fs_card_windows"], self.result["fs_card_windows"])
        self.assertEqual(self.report["input"]["fs_card_window_execution"]["status"], "executed")
        all_rows = [instance for row in self.report["cards"] for instance in row["instances"]]
        self.assertEqual(self.report["coverage"]["computed_card_instance_count"], len(all_rows))
        self.assertEqual(self.report["summary"], execution.summarize_card_scores(all_rows, [row["indicator_id"] for row in self.report["cards"]]))
        self.assertEqual(self.report["overall_score"], self.report["summary"]["channels"]["formal"]["score"])

    def test_invalid_annotation_isolated_from_temporal_gs_and_cycle_results(self):
        self.annotate()
        self.report["gs_stage_windows"] = [{"preserve": True}]
        self.report["cycle_windows"] = [{"preserve": True}]
        with patch("rallymate_service.fs_card_windows.build_fs_card_window_report", side_effect=ValueError("fs_window_source_boundary_missing")):
            execution._apply_optional_fs_card_annotations(self.report, self.directory)
        self.assertEqual(self.others(self.report), self.others(self.baseline))
        self.assertEqual(self.report["gs_stage_windows"], [{"preserve": True}])
        self.assertEqual(self.report["cycle_windows"], [{"preserve": True}])
        self.assertTrue(all(row["run_status"] == "unavailable" and not row["instances"]
            for row in self.report["cards"] if row["indicator_id"] in self.owned))

    def test_unverified_foreign_run_or_wrong_card_inventory_never_merges(self):
        self.annotate()
        for key in ("source_run_id", "rule_version", "service_eligible", "artifact_files_verified", "decoder_verified_from_original_video", "cards"):
            result, report = deepcopy(self.result), deepcopy(self.baseline)
            if key in result["input"]:
                result["input"][key] = False
            elif key == "cards":
                result["cards"].pop()
            else:
                result[key] = "another-value"
            with patch("rallymate_service.fs_card_windows.build_fs_card_window_report", return_value=result):
                execution._apply_optional_fs_card_annotations(report, self.directory)
            self.assertEqual(report["input"]["fs_card_window_execution"]["status"], "unavailable")
            self.assertEqual(self.others(report), self.others(self.baseline))

    def test_annotation_and_video_content_changes_invalidate_cache_despite_same_stat(self):
        for name in ("frames.jsonl", "events.jsonl", "primary-player.jsonl"):
            (self.directory / name).write_text("{}\n", encoding="utf-8")
        video = self.directory / "source.mp4"
        video.write_bytes(b"abcd")
        (self.directory / "summary.json").write_text(json.dumps({"input": {"video_path": str(video)}}), encoding="utf-8")
        annotation = self.annotate()
        for path in (annotation, video):
            before = execution._input_signature(self.directory)
            stat = path.stat()
            content = path.read_bytes()
            modified = content.replace(b"contract-test-only-v1", b"contract-test-only-v2") if path == annotation else b"abce"
            self.assertEqual(len(content), len(modified))
            path.write_bytes(modified)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(execution._input_signature(self.directory), before)


if __name__ == "__main__":
    unittest.main()
