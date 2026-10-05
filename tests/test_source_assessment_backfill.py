from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_backfill", ROOT / "scripts/backfill_source_assessments.py")
assert SPEC is not None and SPEC.loader is not None
backfill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backfill)


class BackfillTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "input.mp4"
        source.write_bytes(b"fixture source")
        output = root / "run"
        output.mkdir()
        for name in ["frames.jsonl", "primary-player.jsonl", "events.jsonl", "scores.jsonl", "features.jsonl"]:
            (output / name).write_bytes(b"")
        source_sha, frames_sha = backfill.file_sha(source), backfill.file_sha(output / "frames.jsonl")
        loop = {"video_id": "fixture", "provenance": {"video_sha256": source_sha, "frames_sha256": frames_sha},
                "artifact_sha256": {"features_jsonl": backfill.file_sha(output / "features.jsonl")}}
        summary = {"minimum_scoring_loop": loop, "input": {"video": {"duration_ms": 1000}}}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return {"id": "fixture", "status": "succeeded", "video_path": str(source), "output_dir": str(output), "summary": summary}

    def test_preservation_guard_rejects_existing_baseline_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.fixture(Path(directory))
            output = Path(job["output_dir"])
            (output / "features.jsonl").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "original scoring artifact changed"):
                backfill.validate_baseline(None, job, backfill.core_hashes(output))

    def test_preservation_guard_runs_even_when_measurement_writer_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.fixture(Path(directory))
            def corrupting_writer(*args, **kwargs):
                (Path(job["output_dir"]) / "scores.jsonl").write_bytes(b"changed")
                raise RuntimeError("writer failure")
            with patch.object(backfill, "pose_sequence_from_records", return_value=object()), \
                 patch.object(backfill, "write_source_aligned_assessment", side_effect=corrupting_writer), \
                 self.assertRaisesRegex(ValueError, "original core artifacts changed during backfill"):
                backfill.process_job(None, job)

    def test_statistics_distinguish_measurement_missing_from_zero_and_unique_phase_repairs(self):
        row = {"feature_name": "test", "value": 0., "unit": "ratio", "status": "measured", "reason": None,
               "evidence": {"source_phase": {"status": "observed_candidate", "original_preload_ms": 800,
                    "source_preload_ms": 200, "repair_reason": "original_preload_after_takeoff_replaced_by_observed_turn"}}}
        artifact = {"records": [{"event_id": "candidate", "grade": None, "indicators": [{
            "indicator_id": "FS01-M02", "source_rule_gate": {"pose_coverage": {"passes": True}},
            "source_measurements": [row, {**row, "feature_name": "second"},
                {**row, "feature_name": "missing", "value": None, "status": "unavailable", "reason": "insufficient_window_samples"}]}]}],
            "technical_grade": None, "technical_score_0_to_100": None}
        result = backfill.summarize_artifact(artifact)
        self.assertEqual(result["phase_repaired_event_count"], 1)
        indicator = result["indicators"]["FS01-M02"]
        self.assertEqual(indicator["measurement_status_counts"], {"measured": 2, "unavailable": 1})
        self.assertEqual(indicator["measurement_samples"][0]["value"], 0.)
        self.assertIsNone(indicator["measurement_samples"][2]["value"])
        artifact["technical_grade"] = "A"
        with self.assertRaisesRegex(ValueError, "must never create"):
            backfill.summarize_artifact(artifact)


if __name__ == "__main__":
    unittest.main()
