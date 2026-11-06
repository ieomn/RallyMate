from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import tracemalloc
import unittest

from rallymate_scoring.artifact_evidence import (
    ArtifactEvidenceError, ArtifactEvidenceReader, EvidencePoolWriter,
    POOL_FILENAME, REFERENCE_KEY, contains_artifact_references,
    iter_artifact_records, verify_evidence_manifest,
)


class ArtifactEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "features.jsonl"

    def ledger(self, count=300):
        return {"version": "test-v1", "rows_by_source_frame": {
            str(i): {"source_frame": i, "identity": "original-player", "geometry": [1.25, None, False]}
            for i in range(count)
        }}

    def rows(self, count=2):
        ledger = self.ledger()
        return [{"video_id": "video", "event_id": f"event-{i}", "feature_name": "test",
                 "value": i, "provenance": {"coordinate_metadata": {
                     "source_pose_person_association": ledger}}, "raw_value": [None, True, -0.0]}
                for i in range(count)]

    def write(self, rows, *, directory=None, source_id="source", **kwargs):
        directory = directory or self.root
        with EvidencePoolWriter(directory, video_id="video", source_id=source_id,
                                video_sha256="a" * 64, **kwargs) as writer:
            writer.write_jsonl(directory / "features.jsonl", rows)
        return writer.manifest

    def test_round_trip_preserves_values_and_does_not_mutate_input(self):
        rows = self.rows()
        original = deepcopy(rows)
        manifest = self.write(rows)
        self.assertEqual(rows, original)
        decoded = list(iter_artifact_records(self.path, expected_video_id="video", expected_source_id="source"))
        self.assertEqual(decoded, original)
        left = decoded[0]["provenance"]["coordinate_metadata"]["source_pose_person_association"]
        right = decoded[1]["provenance"]["coordinate_metadata"]["source_pose_person_association"]
        self.assertIs(left, right)
        verify_evidence_manifest(self.root, manifest, expected_video_id="video", expected_source_id="source")

    def test_repeated_full_video_ledger_is_stored_once(self):
        rows = self.rows(150)
        raw_bytes = sum(len(json.dumps(row, ensure_ascii=False).encode()) + 1 for row in rows)
        self.write(rows)
        size = self.path.stat().st_size + (self.root / POOL_FILENAME).stat().st_size
        self.assertLess(size, raw_bytes / 20)
        with sqlite3.connect(self.root / POOL_FILENAME) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_equal_independent_proofs_deduplicate_by_content(self):
        rows = deepcopy(self.rows())
        rows[1]["provenance"] = deepcopy(rows[0]["provenance"])
        self.write(rows)
        with sqlite3.connect(self.root / POOL_FILENAME) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_streaming_peak_does_not_scale_with_duplicate_ledger_count(self):
        rows = self.rows(1000)
        self.write(rows)
        tracemalloc.start()
        try:
            count = 0
            for row in iter_artifact_records(self.path):
                self.assertEqual(len(row["provenance"]["coordinate_metadata"]["source_pose_person_association"]["rows_by_source_frame"]), 300)
                count += 1
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(count, 1000)
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_legacy_inline_records_need_no_pool(self):
        rows = self.rows()
        self.path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        self.assertEqual(list(iter_artifact_records(self.path)), rows)
        self.assertFalse((self.root / POOL_FILENAME).exists())

    def test_foreign_task_or_video_is_rejected(self):
        self.write(self.rows())
        with self.assertRaisesRegex(ArtifactEvidenceError, "another source task"):
            list(iter_artifact_records(self.path, expected_source_id="foreign"))
        with self.assertRaisesRegex(ArtifactEvidenceError, "another video"):
            list(iter_artifact_records(self.path, expected_video_id="foreign"))
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        rows[0]["video_id"] = "foreign"
        self.path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        with self.assertRaisesRegex(ArtifactEvidenceError, "video differ"):
            list(iter_artifact_records(self.path))

    def test_pool_swap_is_rejected_even_for_same_video(self):
        self.write(self.rows())
        other = self.root / "other"
        other.mkdir()
        self.write(self.rows(), directory=other, source_id="different-task")
        shutil.copyfile(other / POOL_FILENAME, self.root / POOL_FILENAME)
        with self.assertRaisesRegex(ArtifactEvidenceError, "another run"):
            list(iter_artifact_records(self.path))

    def test_content_tampering_and_manifest_hash_tampering_are_rejected(self):
        manifest = self.write(self.rows())
        with sqlite3.connect(self.root / POOL_FILENAME) as connection:
            connection.execute("UPDATE evidence SET payload=?", (b'{"forged":true}',))
        with self.assertRaisesRegex(ArtifactEvidenceError, "content hash mismatch"):
            list(iter_artifact_records(self.path))
        with self.assertRaisesRegex(ArtifactEvidenceError, "file hash mismatch"):
            verify_evidence_manifest(self.root, manifest)

    def test_missing_pool_and_path_escape_are_rejected(self):
        self.write(self.rows())
        records = [json.loads(line) for line in self.path.read_text().splitlines()]
        ref = records[0]["provenance"]["coordinate_metadata"]["source_pose_person_association"][REFERENCE_KEY]
        ref["pool"] = "../scoring-evidence.sqlite3"
        self.path.write_text(json.dumps(records[0]), encoding="utf-8")
        with self.assertRaisesRegex(ArtifactEvidenceError, "invalid evidence reference"):
            list(iter_artifact_records(self.path))
        ref["pool"] = POOL_FILENAME
        self.path.write_text(json.dumps(records[0]), encoding="utf-8")
        (self.root / POOL_FILENAME).unlink()
        with self.assertRaisesRegex(ArtifactEvidenceError, "missing"):
            list(iter_artifact_records(self.path))

    def test_reader_can_index_encoded_rows_and_resolve_on_demand(self):
        rows = self.rows()
        self.write(rows)
        with ArtifactEvidenceReader(self.root) as reader:
            packed = list(reader.iter_records(self.path, resolve_evidence=False))
            self.assertIn(REFERENCE_KEY, packed[0]["provenance"]["coordinate_metadata"]["source_pose_person_association"])
            self.assertEqual(reader.resolve_record(packed[1]), rows[1])

    def test_oversized_single_record_fails_before_unbounded_read(self):
        self.path.write_bytes(b'{"text":"' + b'x' * 2000 + b'"}\n')
        with self.assertRaisesRegex(ArtifactEvidenceError, "memory budget"):
            list(iter_artifact_records(self.path, max_record_bytes=1024))

    def test_invalid_byte_budget_cannot_request_unbounded_read(self):
        self.path.write_bytes(b'{}\n')
        for budget in (-1, 0, True, 128 * 1024 * 1024):
            with self.subTest(budget=budget), self.assertRaises(ArtifactEvidenceError):
                list(iter_artifact_records(self.path, max_record_bytes=budget))

    def test_cancellation_removes_only_staged_files(self):
        self.path.write_bytes(b'original\n')
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.write(self.rows(100), cancellation_callback=cancel)
        self.assertEqual(self.path.read_bytes(), b'original\n')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["features.jsonl"])

    def test_reserved_marker_is_not_silently_interpreted_as_user_evidence(self):
        with self.assertRaisesRegex(ArtifactEvidenceError, "reserved"):
            self.write([{"video_id": "video", REFERENCE_KEY: "ordinary-data"}])

    def test_offline_consumers_can_detect_nested_pool_dependencies(self):
        self.assertFalse(contains_artifact_references(self.rows()))
        self.write(self.rows())
        encoded = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertTrue(contains_artifact_references(encoded))

    def test_duplicate_pool_metadata_keys_are_rejected(self):
        self.write(self.rows())
        with sqlite3.connect(self.root / POOL_FILENAME) as connection:
            value = connection.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()[0]
            connection.execute("UPDATE metadata SET value=? WHERE key='binding'", ('{"video_id":"forged",' + value[1:],))
        with self.assertRaisesRegex(ArtifactEvidenceError, "duplicate JSON key"):
            list(iter_artifact_records(self.path))


if __name__ == "__main__":
    unittest.main()
