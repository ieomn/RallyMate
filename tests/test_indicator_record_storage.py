from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.artifact_evidence import (
    EvidencePoolWriter, contains_artifact_references, resident_evidence_bytes,
)
from rallymate_service.indicator_records import IndicatorRecords


class IndicatorRecordStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "indicator-features.jsonl"

    def rows(self, count):
        return [{"video_id": "video", "event_id": str(i), "source_window": {
            "rows": [{"n": n, "value": f"evidence-{i}-{n}"} for n in range(300)]
        }} for i in range(count)]

    def write(self, rows):
        with EvidencePoolWriter(self.root, video_id="video", source_id="source") as writer:
            writer.write_jsonl(self.path, rows)

    def test_tiny_reference_to_large_proof_is_not_retained_over_row_budget(self):
        rows = self.rows(1)
        self.write(rows)
        self.assertLess(self.path.stat().st_size, 1024)
        self.assertGreater(resident_evidence_bytes(rows[0], 32768), 32768)
        with patch("rallymate_service.indicator_records.MAX_CACHE_BYTES", 32768):
            with IndicatorRecords(self.path) as records:
                self.assertEqual(dict(records[0]), rows[0])
                self.assertEqual(len(records.cache), 0)
                self.assertEqual(records.cache_bytes, 0)

    def test_decoded_row_lru_charges_resident_objects_and_stays_bounded(self):
        rows = self.rows(7)
        self.write(rows)
        per_row = resident_evidence_bytes(rows[0], 1024 * 1024)
        budget = per_row * 2 + 4096
        with patch("rallymate_service.indicator_records.MAX_CACHE_BYTES", budget):
            with IndicatorRecords(self.path) as records:
                for i, expected in enumerate(rows):
                    self.assertEqual(dict(records[i]), expected)
                    self.assertLessEqual(records.cache_bytes, budget)
                    self.assertLessEqual(len(records.cache), 2)
            self.assertEqual(records.cache_bytes, 0)

    def test_regular_json_retains_compact_rows_and_resolves_only_on_access(self):
        rows = self.rows(12)
        self.write(rows)
        encoded = [json.loads(line) for line in self.path.read_text("utf-8").splitlines()]
        self.path.write_text(json.dumps({"records": encoded}, indent=2), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            self.assertTrue(contains_artifact_references(records.document))
            self.assertEqual(records.document, encoded)
            self.assertFalse(records.cache)
            for i in (0, 5, 11):
                self.assertEqual(dict(records[i]), rows[i])

    def test_pretty_legacy_regular_json_keeps_unknown_fields(self):
        rows = [{"event_id": "first", "unknown": {"nested": [True, None, 1.2]}},
                {"event_id": "second", "measurement": 5}]
        self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            self.assertEqual([dict(row) for row in records], rows)


if __name__ == "__main__":
    unittest.main()
