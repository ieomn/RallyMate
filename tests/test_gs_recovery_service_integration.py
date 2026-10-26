"""Recorded short-window counterexamples through core and original-video service.

These frozen candidates end at the first visible recovery foot motion. They do
not establish a complete M10 boundary or any new positive coverage. The media
integration checks that source-valid but short evidence stays unscored.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from rallymate_service.card_execution import build_card_execution_report, clear_card_execution_cache
from rallymate_service.gs_card_execution import build_gs_card_execution_report, compute_gs_card_execution


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path(os.environ.get("CARD_ARTIFACT_ROOT", ROOT))


def sha(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def rows(report):
    return [instance for card in report["cards"] for instance in card["instances"]]


class GSRecoveryRecordedWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads((ROOT / "tests/fixtures/gs-m10-mit-short-windows-20261024.json").read_text(encoding="utf-8"))
        cls.case = next(case for case in json.loads((ROOT / "tests/fixtures/gs-card-real-mit-preparation-v1.json")
            .read_text(encoding="utf-8"))["cases"] if case["source_job_id"] == cls.frozen["annotations"]["source_run_id"])

    def assert_short_candidates(self, report):
        observed = rows(report)
        self.assertEqual(len(observed), 16)
        by_annotation = {row["annotation_id"]: row for row in self.frozen["observations"]}
        for row in observed:
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["score"])
            self.assertIsNone(row["original_grade"])
            self.assertTrue(all(condition["passed"] is None for condition in row["conditions"]))
            expected = by_annotation[row["event_id"]]
            self.assertEqual(row["source_stage_evidence"]["source_frames"], expected["source_frames"])
            self.assertEqual(row["source_annotation"]["recovery_reference_observation"]["action_id"], row["action_id"])
            if row["indicator_id"] in {"GS02-M10-02", "GS02-M10-03"}:
                self.assertIn("recovery_continuous_window_too_short", row["reason_codes"])
                proof = row["recovery_evidence"]["stage"]
                self.assertEqual(proof["total_frame_count"], len(expected["source_frames"]))
                self.assertEqual([item["frame_index"] for item in proof["source_geometry_series"]], expected["source_frames"])
            else:
                self.assertFalse(row["auxiliary_observations"]["scoring_eligible"])

    def test_recorded_geometry_rejects_frozen_short_candidates_without_padding(self):
        inputs = [self.case["frame_records"], self.case["primary_timeline"],
                  self.case["decoder_timeline"], self.frozen["annotations"]]
        before = deepcopy(inputs)
        report = compute_gs_card_execution(*inputs, source_job_id=self.case["source_job_id"])
        self.assert_short_candidates(report)
        self.assertEqual(inputs, before)
        self.assertFalse(report["input"]["artifact_files_verified"])


class GSRecoveryServiceIntegrationTests(GSRecoveryRecordedWindowTests):
    # Avoid inheriting the core test as a duplicate test case.
    test_recorded_geometry_rejects_frozen_short_candidates_without_padding = None

    def test_original_video_candidate_replay_merges_298_rows_and_preserves_fs(self):
        source = ARTIFACT_ROOT / self.frozen["source_run_dir"]
        names = ("frames.jsonl", "primary-player.jsonl", "events.jsonl", "summary.json")
        if not all((source / name).is_file() for name in names):
            self.skipTest("integration_requires_original_mit_thbh_artifacts")
        video = ARTIFACT_ROOT / self.frozen["source_video"]
        if not video.is_file():
            self.skipTest("integration_requires_original_mit_thbh_video")
        before = {name: sha(source / name) for name in names}
        self.assertEqual(sha(video), self.frozen["video_sha256"])
        for name, value in self.frozen["annotations"]["input_artifact_sha256"].items():
            self.assertEqual(before[name], value)
        clear_card_execution_cache()
        self.addCleanup(clear_card_execution_cache)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / self.frozen["annotations"]["source_run_id"]
            directory.mkdir()
            for name in names:
                try:
                    os.link(source / name, directory / name)
                except OSError:
                    shutil.copyfile(source / name, directory / name)
            baseline = build_card_execution_report(directory)
            annotation = self.frozen["annotations"]
            (directory / "gs-stage-annotations.json").write_text(json.dumps(annotation, ensure_ascii=False) + "\n", encoding="utf-8")
            direct = build_gs_card_execution_report(directory, annotation, video_path=video)
            merged = build_card_execution_report(directory)
            self.assertTrue(direct["input"]["service_eligible"])
            self.assertTrue(direct["input"]["decoder_verified_from_original_video"])
            self.assertEqual(merged["input"]["gs_execution"]["status"], "executed")
            self.assertEqual(len(merged["cards"]), 298)
            self.assertEqual([c for c in merged["cards"] if c["event_code"].startswith("GS")], direct["cards"])
            self.assertEqual([c for c in merged["cards"] if c["event_code"].startswith("FS")],
                             [c for c in baseline["cards"] if c["event_code"].startswith("FS")])
            self.assert_short_candidates(direct)
            self.assertEqual(merged["coverage"]["gs_annotated_stage_count"], 4)
            self.assertEqual(merged["coverage"]["gs_executed_card_count"], 4)
            self.assertEqual(merged["summary"]["channels"]["original"]["scored_cards"], 0)
        self.assertEqual({name: sha(source / name) for name in names}, before)
        self.assertEqual(sha(video), self.frozen["video_sha256"])


if __name__ == "__main__":
    unittest.main()
