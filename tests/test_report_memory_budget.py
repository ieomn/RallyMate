from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from rallymate_scoring.artifact_evidence import EvidencePoolWriter, CODEC_VERSION, POOL_FILENAME
from rallymate_scoring.run_bundle_binding import (
    scoring_run_bundle_identity, scoring_run_bundle_root_sha256, ScoringRunBundleBindingError,
)
from rallymate_service.indicator_records import IndicatorRecords
from rallymate_service.user_demo import open_indicator_feature_records, load_indicator_feature_records, UserDemoResultError
from rallymate_vision.postprocess_resources import PostprocessBudget, PostprocessResourceLimit
from tests.test_scoring_run_bundle_binding import _summary


class ReportMemoryBudgetTests(unittest.TestCase):
    def test_lazy_mappings_preserve_every_unknown_field_and_repeated_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            rows = [{"event_id": str(i), "events": [i], "unknown": {"原文": [False, None, i]}}
                    for i in range(25)]
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            with open_indicator_feature_records(path) as records:
                handles = list(records)
                self.assertEqual([dict(r) for r in handles], rows)
                self.assertEqual([dict(r) for r in reversed(handles)], list(reversed(rows)))
                self.assertLessEqual(len(records.cache), 8)

    def test_pooled_records_retain_complete_proof_after_cache_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "indicator-features.jsonl"
            proof = {"source_frames": list(range(100)), "time_binding": "exact"}
            rows = [{"event_id": str(i), "video_id": "video", "source_window": proof} for i in range(30)]
            with EvidencePoolWriter(root, video_id="video", source_id="job") as writer:
                writer.write_jsonl(path, rows)
            with open_indicator_feature_records(path) as records:
                handles = list(records)
                for index in (0, 29, 8, 0):
                    self.assertEqual(dict(handles[index]), rows[index])

    def test_invalid_late_record_is_not_hidden_by_lazy_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text('{}\n{"value":NaN}\n', encoding="utf-8")
            with self.assertRaises(UserDemoResultError):
                with open_indicator_feature_records(path):
                    pass

    def test_input_growth_does_not_retain_all_decoded_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            with path.open("w", encoding="utf-8") as stream:
                for index in range(500):
                    stream.write(json.dumps({"id": index, "evidence": "x" * 65536}) + "\n")
            tracemalloc.start()
            try:
                with IndicatorRecords(path) as records:
                    handles = list(records)
                    self.assertEqual(sum(row["id"] for row in handles), sum(range(500)))
                    self.assertEqual(handles[0]["evidence"], "x" * 65536)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 8 * 1024**2)

    def test_memory_time_and_cancellation_stop_cooperatively(self):
        with patch("rallymate_vision.postprocess_resources.process_memory_bytes", return_value=100):
            budget = PostprocessBudget(max_memory_bytes=50, max_seconds=100)
            with self.assertRaisesRegex(PostprocessResourceLimit, "内存"):
                budget.check(force=True)
        with patch("rallymate_vision.postprocess_resources.process_memory_bytes", return_value=0):
            budget = PostprocessBudget(max_memory_bytes=50, max_seconds=1)
            budget.started -= 2
            with self.assertRaisesRegex(PostprocessResourceLimit, "时间"):
                budget.check(force=True)
        def cancelled():
            raise RuntimeError("cancelled")
        budget = PostprocessBudget(cancellation_callback=cancelled)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            budget.check(force=True)

    def test_v2_bundle_identity_binds_evidence_file_and_source(self):
        summary = _summary()
        summary["evidence_storage"] = {
            "codec": CODEC_VERSION, "path": POOL_FILENAME, "sha256": "1" * 64,
            "binding_sha256": "2" * 64, "source_id": "job", "video_id": summary["video_id"],
            "video_sha256": summary["provenance"]["video_sha256"].lower(),
        }
        identity = scoring_run_bundle_identity(summary, scoring_summary_sha256="a" * 64)
        self.assertEqual(identity["binding_version"], "scoring-run-bundle-v2")
        digest = scoring_run_bundle_root_sha256(identity)
        changed = copy.deepcopy(identity)
        changed["evidence_storage"]["sha256"] = "3" * 64
        self.assertNotEqual(digest, scoring_run_bundle_root_sha256(changed))
        changed["evidence_storage"]["video_id"] = "other-video"
        with self.assertRaises(ScoringRunBundleBindingError):
            scoring_run_bundle_root_sha256(changed)


if __name__ == "__main__":
    unittest.main()
