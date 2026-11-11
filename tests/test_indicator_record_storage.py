from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.artifact_evidence import (
    ArtifactEvidenceError, ArtifactEvidenceReader, EvidencePoolWriter, POOL_FILENAME,
    contains_artifact_references, resident_evidence_bytes,
)
from rallymate_service import indicator_records as storage
from rallymate_service.indicator_records import IndicatorRecordError, IndicatorRecords
from rallymate_service.training_evaluation import validated_training_records


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

    def test_scalar_passes_skip_repeat_resolution_after_every_complete_record_was_validated(self):
        rows = self.rows(20)
        for row in rows:
            row.update(indicator_id="FS01-M02", event_code="FS01", person_track_id=1,
                       feature_status="measured", grade=None)
        self.write(rows)
        resolve = ArtifactEvidenceReader.resolve_record
        with patch.object(ArtifactEvidenceReader, "resolve_record", autospec=True, side_effect=resolve) as calls:
            with IndicatorRecords(self.path) as records:
                self.assertEqual(calls.call_count, len(rows))
                for _ in range(3):
                    for index, row in enumerate(records):
                        self.assertEqual(row["event_id"], str(index))
                        self.assertEqual(row["indicator_id"], "FS01-M02")
                        self.assertEqual(row["person_track_id"], 1)
                        self.assertIsNone(row["grade"])
                self.assertEqual(calls.call_count, len(rows))
                # Complete Mapping iteration/equality still reads every proof.
                for index, expected in enumerate(rows):
                    self.assertEqual(dict(records[index]), expected)
                self.assertGreater(calls.call_count, len(rows))
                self.assertLessEqual(len(records.cache), storage.MAX_CACHED_ROWS)

    def test_missing_indexed_field_keeps_keyerror_and_full_mapping_semantics(self):
        row = {"event_id": "known", "unknown": {"evidence": [None, True, 1]}}
        self.path.write_text(json.dumps(row), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            handle = records[0]
            with patch.object(records.reader, "resolve_record", wraps=records.reader.resolve_record) as resolve:
                self.assertEqual(handle.get("video_id", "missing"), "missing")
                with self.assertRaises(KeyError) as caught:
                    handle["video_id"]
                self.assertEqual(caught.exception.args, ("video_id",))
                self.assertEqual(resolve.call_count, 0)
                self.assertEqual(set(handle), set(row))
                self.assertEqual(len(handle), len(row))
                self.assertEqual(dict(handle), row)
                self.assertEqual(handle, row)

    def test_index_cap_falls_back_without_retaining_objects_or_long_scalar_values(self):
        rows = [{"event_id": str(index), "video_id": "v" * 200} for index in range(30)]
        self.path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        with patch.object(storage, "MAX_SCALAR_INDEX_BYTES", 4096):
            with IndicatorRecords(self.path) as records:
                indexed = sum(row is not None for row in records._scalar_index)
                self.assertGreater(indexed, 0)
                self.assertLess(indexed, len(rows))
                actual = sys.getsizeof(records._scalar_index)
                for scalars in records._scalar_index:
                    if scalars is not None:
                        actual += sys.getsizeof(scalars)
                        actual += sum(sys.getsizeof(value) for value in scalars
                                      if value is not storage._MISSING and value is not storage._UNINDEXED)
                self.assertEqual(actual, records.scalar_index_bytes)
                self.assertLessEqual(actual, 4096)
                self.assertEqual([dict(row) for row in records], rows)
                self.assertLessEqual(records.scalar_index_bytes, 4096)
            self.assertEqual(records.scalar_index_bytes, 0)
            self.assertIsNone(records._scalar_index)
        unusual = {"event_id": "e" * 10000, "grade": {"proof": "x" * 10000},
                   "person_track_id": 1 << 5000, "video_id": "video"}
        self.path.write_text(json.dumps(unusual), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            indexed = records._scalar_index[0]
            for key in ("event_id", "grade", "person_track_id"):
                self.assertIs(indexed[storage._SCALAR_POSITIONS[key]], storage._UNINDEXED)
            self.assertEqual(dict(records[0]), unusual)
        with patch.object(storage, "MAX_SCALAR_INDEX_BYTES", 0):
            with IndicatorRecords(self.path) as records:
                self.assertIsNone(records._scalar_index)
                self.assertEqual(records.scalar_index_bytes, 0)
                self.assertEqual(dict(records[0]), unusual)

    def test_scalar_hits_still_check_cancellation_close_and_file_changes(self):
        self.path.write_text(json.dumps({"event_id": "event", "proof": [1, 2, 3]}), encoding="utf-8")
        cancelled = False
        def check():
            if cancelled:
                raise RuntimeError("cancelled")
        with IndicatorRecords(self.path, cancellation_callback=check) as records:
            handle = records[0]
            self.assertEqual(handle["event_id"], "event")
            cancelled = True
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                handle["event_id"]
            cancelled = False
            self.assertEqual(handle["proof"], [1, 2, 3])  # Also populate the row LRU.
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            for key in ("event_id", "proof"):
                with self.subTest(key=key), self.assertRaisesRegex(IndicatorRecordError, "changed"):
                    handle[key]
        with self.assertRaisesRegex(IndicatorRecordError, "closed"):
            handle["event_id"]

    def test_unknown_large_proof_conflicts_are_not_reduced_to_indexed_identity(self):
        first = {"video_id": "video", "event_id": "event", "event_code": "FS01",
                 "indicator_id": "FS01-M02", "person_track_id": 1,
                 "unknown_proof": {"long": "x" * 20000, "binding": "A"}}
        second = {**first, "unknown_proof": {**first["unknown_proof"], "binding": "B"}}
        self.path.write_text("\n".join(json.dumps(row) for row in (first, second)), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            self.assertEqual(records[0]["event_id"], records[1]["event_id"])
            self.assertNotEqual(records[0], records[1])
            validated, counts = validated_training_records(records)
            self.assertEqual(validated, [])
            self.assertEqual(counts["conflicting_record_count"], 2)
            self.assertEqual(dict(records[0]), first)
            self.assertEqual(dict(records[1]), second)

    def test_unknown_ignored_proof_is_fully_validated_before_scalar_index_use(self):
        rows = [{"video_id": "video", "event_id": "ignored", "indicator_id": "unrecognized",
                 "unknown_proof": {"source_window": {"frames": list(range(2000))}}}]
        self.write(rows)
        with closing(sqlite3.connect(self.root / POOL_FILENAME)) as connection:
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
            connection.execute("UPDATE evidence SET payload=?", (b'{"changed":true}',))
            connection.commit()
        with self.assertRaisesRegex(ArtifactEvidenceError, "content hash mismatch"):
            with IndicatorRecords(self.path):
                self.fail("tampered proof must fail before metadata can be served")

    def test_regular_json_uses_bounded_scalar_index_and_does_not_reuse_it_across_requests(self):
        row = {"event_id": "first", "unknown": {"full": [1, 2, 3]}}
        self.path.write_text(json.dumps([row], indent=2), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            with patch.object(records.reader, "resolve_record", wraps=records.reader.resolve_record) as resolve:
                self.assertEqual(records[0]["event_id"], "first")
                resolve.assert_not_called()
                self.assertEqual(dict(records[0]), row)
        row["event_id"] = "second"
        self.path.write_text(json.dumps([row], indent=2), encoding="utf-8")
        with IndicatorRecords(self.path) as records:
            self.assertEqual(records[0]["event_id"], "second")
            self.assertEqual(dict(records[0]), row)


if __name__ == "__main__":
    unittest.main()
