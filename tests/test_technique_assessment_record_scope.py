"""Technique phase aggregation preserves the original per-technique oracle."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import Mock, patch

from rallymate_scoring import technique_assessment as assessment
from rallymate_scoring.technique_registry import technique_catalog
from rallymate_service import indicator_records as storage


class TechniqueAssessmentRecordScopeTests(unittest.TestCase):
    def oracle(self, summary, rows):
        # _phase_statuses is the unchanged original public-helper behavior.
        with patch.object(assessment, "_indexed_phase_statuses",
                side_effect=lambda technique, _index, observed:
                    assessment._phase_statuses(technique, rows, observed)):
            return assessment.build_technique_assessment(summary, rows)

    def test_complete_result_matches_original_phase_oracle_for_aliases_and_candidates(self):
        rows = [
            {"event_code": "GS01", "phase": "strike", "feature_status": "measured"},
            {"action_type": "底线正手", "stage": "准备"},
            {"indicator_id": "FS09-M01", "phase_key": "support"},
            {"technique_id": "serve", "stage_code": "trophy_pose"},
            {"technique_id": "serve", "event_code": "GS01", "phase": "strike"},
            {"technique_id": "unknown" * 10000, "event_code": "GS01", "phase": "strike"},
            {"phase": "strike"},
            {"technique_id": "serve", "phase": "landing", "status": "candidate"},
            {"event_code": "GS02", "phase": "strike", "candidate_type": "motion", "contact_confirmed": False},
            {"event_code": "GS03", "phase": "not-a-real-phase" * 10000},
            {"predictions": [{"label": "发球", "stage": "toss"},
                             {"event_code": "GS04", "observed": False}]},
            {"actions": [{"name_zh": "第一步启动", "results": [{"event_code": "FS09"}]}]},
        ]
        summary = {"job_id": "oracle", "minimum_scoring_loop": {"event_counts": {"FS01": 2}},
                   "detections": [{"event_code": "GS05"}]}
        expected = self.oracle(summary, rows)
        actual = assessment.build_technique_assessment(summary, rows)
        self.assertEqual(actual, expected)
        techniques = technique_catalog()["techniques"]
        index = assessment._explicit_phase_index(rows, techniques)
        self.assertEqual(set(index), {str(row["id"]) for row in techniques})
        self.assertLessEqual(max((len(value) for value in index.values()), default=0), 10)
        self.assertTrue(all(len(value) < 100 for values in index.values() for value in values))
        for technique in techniques:
            for observed in (True, False):
                self.assertEqual(assessment._indexed_phase_statuses(technique, index, observed),
                                 assessment._phase_statuses(technique, rows, observed))

    def test_uncached_lazy_rows_resolve_once_per_record_pass_not_per_key_or_technique(self):
        rows = [{"video_id": "video", "event_id": str(index), "event_code": "GS01",
                 "phase": "strike" if index % 2 else "preparation", "feature_status": "measured",
                 "unknown_proof": {"complete": "x" * 50000, "source_binding": index}}
                for index in range(20)]
        expected = self.oracle({}, rows)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "indicator-features.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            # Every row exceeds this cache allowance, exercising the same path
            # as a production proof larger than 16 MiB without huge fixtures.
            with patch.object(storage, "MAX_CACHE_BYTES", 0), storage.IndicatorRecords(path) as records:
                with patch.object(records.reader, "resolve_record", wraps=records.reader.resolve_record) as resolve:
                    actual = assessment.build_technique_assessment({}, records)
                    # Coverage stops on its first measured record; event IDs,
                    # nested containers and all techniques each take one pass.
                    self.assertEqual(resolve.call_count, 3 * len(rows) + 1)
                self.assertEqual(actual, expected)
                self.assertEqual(records.cache_bytes, 0)
                self.assertFalse(records.cache)

    def test_single_phase_pass_does_not_retain_complete_proofs_or_unknown_strings(self):
        techniques = technique_catalog()["techniques"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "indicator-features.jsonl"
            with path.open("w", encoding="utf-8") as stream:
                for index in range(100):
                    stream.write(json.dumps({"event_code": "GS01", "phase": "strike" if index % 2 else "z" * 50000,
                                             "unknown_proof": "x" * 50000}) + "\n")
            tracemalloc.start()
            try:
                with patch.object(storage, "MAX_CACHE_BYTES", 0), storage.IndicatorRecords(path) as records:
                    resolve_count = 0
                    original_resolve = records.reader.resolve_record
                    def resolve_without_retaining_arguments(record):
                        nonlocal resolve_count
                        resolve_count += 1
                        return original_resolve(record)
                    with patch.object(records.reader, "resolve_record", new=resolve_without_retaining_arguments):
                        result = assessment._explicit_phase_index(records, techniques)
                        self.assertEqual(resolve_count, 100)
                    self.assertEqual(result["baseline_forehand"], {"strike"})
                    self.assertTrue(all(not values for key, values in result.items() if key != "baseline_forehand"))
                    self.assertFalse(records.cache)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 4 * 1024 * 1024)

    def test_scope_adapter_accepts_only_owned_handles_and_preserves_guards(self):
        class Ordinary(dict):
            owner = Mock()
            index = 0
        ordinary = Ordinary(event_code="GS01")
        self.assertIs(storage._record_for_scope(ordinary), ordinary)
        ordinary.owner._row.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "indicator-features.jsonl"
            path.write_text('{"event_code":"GS01","proof":{"unknown":true}}', encoding="utf-8")
            cancelled = False
            def check():
                if cancelled:
                    raise RuntimeError("cancelled")
            with storage.IndicatorRecords(path, cancellation_callback=check) as records:
                handle = records[0]
                self.assertEqual(storage._record_for_scope(handle)["proof"], {"unknown": True})
                cancelled = True
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    storage._record_for_scope(handle)
                cancelled = False
                with path.open("a", encoding="utf-8") as stream:
                    stream.write("\n")
                with self.assertRaisesRegex(storage.IndicatorRecordError, "changed"):
                    storage._record_for_scope(handle)
            with self.assertRaisesRegex(storage.IndicatorRecordError, "closed"):
                storage._record_for_scope(handle)


if __name__ == "__main__":
    unittest.main()
