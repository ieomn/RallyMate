from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException

from rallymate_service import upload_state
from rallymate_service.uploads import DEFAULT_CHUNK_BYTES, MIN_CHUNK_BYTES, UploadStore


class IncrementalUploadStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = UploadStore(self.root, 16 * 1024 * 1024)
        self.upload_id = str(uuid.uuid4())
        self.directory = self.store.directory(self.upload_id)

    def create(self, data=b"abc", **kwargs):
        self.store.create(self.upload_id, "video.mp4", len(data), kwargs)
        return data

    def put(self, index, content, *, compact=True):
        return self.store.put(self.upload_id, index, content, hashlib.sha256(content).hexdigest(), compact_ack=compact)

    def test_compact_put_and_metadata_validation_never_load_chunk_history(self):
        self.store = UploadStore(self.root, 16 * 1024 * 1024, chunk_bytes=MIN_CHUNK_BYTES)
        content = b"a" * MIN_CHUNK_BYTES
        self.create(content * 32)
        with patch.object(upload_state, "chunks", side_effect=AssertionError("full history on PUT")):
            for index in range(32):
                metadata = self.store.read_metadata(self.directory)
                self.assertNotIn("chunks", metadata)
                self.assertEqual(metadata["received_count"], index)
                receipt = self.put(index, content)
                self.assertLess(len(json.dumps(receipt)), 400)
            self.put(31, content)
        self.assertTrue((self.directory / upload_state.STATE_FILENAME).is_file())
        self.assertFalse((self.directory / "manifest.json").exists())
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], list(range(32)))
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], 32 * len(content))

    def test_failed_sql_transaction_does_not_acknowledge_part_and_retry_repairs_it(self):
        self.create()
        with upload_state.connection(self.directory) as conn:
            conn.execute("""CREATE TRIGGER reject_counter BEFORE UPDATE OF received_count ON session
                BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.put(0, b"abc")
        self.assertEqual((self.directory / "0.part").read_bytes(), b"abc")
        # The INSERT preceding the rejected counter UPDATE rolls back too.
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], [])
        self.assertEqual(self.store.read_metadata(self.directory)["received_count"], 0)
        with upload_state.connection(self.directory) as conn:
            conn.execute("DROP TRIGGER reject_counter")
        resumed = UploadStore(self.root, 16 * 1024 * 1024)
        resumed.put(self.upload_id, 0, b"abc", hashlib.sha256(b"abc").hexdigest(), compact_ack=True)
        self.assertEqual(resumed.status(self.upload_id)["received_bytes"], 3)
        self.assertEqual(resumed.read_metadata(self.directory)["received_count"], 1)

    def test_part_fsync_failure_cannot_commit_chunk(self):
        self.create()
        with patch("rallymate_service.uploads.os.fsync", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.put(0, b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], [])
        self.assertFalse((self.directory / "0.part").exists())
        self.assertTrue((self.directory / "0.part.tmp").exists())
        self.put(0, b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], 3)

    def test_directory_sync_failure_does_not_acknowledge_or_register_part(self):
        self.create()
        with patch.object(upload_state, "sync_directory", side_effect=OSError("directory sync failed")):
            with self.assertRaises(OSError):
                self.put(0, b"abc")
        self.assertEqual((self.directory / "0.part").read_bytes(), b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], [])
        self.put(0, b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], 3)

    def test_unpublished_sqlite_database_and_journal_are_replaced_on_retry(self):
        self.directory.mkdir()
        unpublished = self.directory / (upload_state.STATE_FILENAME + ".tmp")
        unpublished.write_bytes(b"interrupted initialization")
        journal = Path(str(unpublished) + "-journal")
        journal.write_bytes(b"leftover unpublished rollback journal")
        self.create()
        self.assertFalse(unpublished.exists())
        self.assertFalse(journal.exists())
        self.put(0, b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], 3)

    def test_creation_retries_directory_sync_before_returning_existing_session(self):
        with patch.object(upload_state, "sync_directory", side_effect=OSError("parent sync failed")):
            with self.assertRaises(OSError):
                self.create()
        self.assertFalse((self.directory / upload_state.STATE_FILENAME).exists())
        with patch.object(upload_state, "sync_directory", wraps=upload_state.sync_directory) as sync:
            self.create()
            self.assertEqual(sync.call_args_list[0].args, (self.root,))
            self.assertIn((self.directory,), [call.args for call in sync.call_args_list])

    def test_published_state_with_failed_directory_sync_is_resynced_on_create_retry(self):
        real_sync = upload_state.sync_directory
        def fail_after_publish(directory):
            if directory == self.directory and (directory / upload_state.STATE_FILENAME).exists():
                raise OSError("published name sync failed")
            real_sync(directory)
        with patch.object(upload_state, "sync_directory", side_effect=fail_after_publish):
            with self.assertRaises(OSError):
                self.create()
        self.assertTrue((self.directory / upload_state.STATE_FILENAME).exists())
        with patch.object(upload_state, "sync_directory", wraps=real_sync) as sync:
            self.create()
        self.assertEqual([call.args for call in sync.call_args_list], [(self.root,), (self.directory,)])
        self.put(0, b"abc")
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], 3)

    def test_posix_directory_descriptor_is_closed_when_fsync_fails(self):
        with patch.object(upload_state.os, "name", "posix"), \
             patch.object(upload_state.os, "open", return_value=123) as opened, \
             patch.object(upload_state.os, "fsync", side_effect=OSError("sync failed")), \
             patch.object(upload_state.os, "close") as closed:
            with self.assertRaises(OSError):
                upload_state.sync_directory(self.root)
        opened.assert_called_once()
        closed.assert_called_once_with(123)

    def test_duplicate_conflict_does_not_change_saved_bytes_or_count(self):
        self.create()
        self.put(0, b"abc")
        with self.assertRaises(HTTPException) as conflict:
            self.put(0, b"xyz")
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual((self.directory / "0.part").read_bytes(), b"abc")
        metadata = self.store.read_metadata(self.directory)
        self.assertEqual((metadata["received_count"], metadata["received_bytes"]), (1, 3))

    def test_queue_commit_then_metadata_failure_recovers_without_double_submit(self):
        self.create()
        self.put(0, b"abc")
        jobs, calls = {}, []
        def enqueue(path, metadata):
            self.assertEqual(path.read_bytes(), b"abc")
            self.assertEqual(metadata["chunks"]["0"]["size"], 3)
            calls.append(metadata["upload_id"])
            jobs[self.upload_id] = {"id": self.upload_id}
            return jobs[self.upload_id]
        with patch.object(upload_state, "mark_complete", side_effect=OSError("metadata unavailable")):
            with self.assertRaises(OSError):
                self.store.complete(self.upload_id, enqueue, jobs.get)
        self.assertIsNone(self.store.read_metadata(self.directory)["job_id"])
        self.assertTrue((self.directory / "0.part").exists())
        self.assertFalse((self.directory / "assembled.video").exists())
        self.assertEqual(self.store.complete(self.upload_id, enqueue, jobs.get), jobs[self.upload_id])
        self.assertEqual(calls, [self.upload_id])
        self.assertEqual(self.store.status(self.upload_id)["job_id"], self.upload_id)
        self.assertFalse((self.directory / "0.part").exists())
        # A retransmitted successful chunk still gets its original receipt.
        self.assertEqual(self.put(0, b"abc")["chunk_bytes"], 3)

    def test_corrupt_part_invalidates_only_its_row_and_preserves_other_chunks(self):
        first, tail = b"a" * DEFAULT_CHUNK_BYTES, b"tail"
        self.create(first + tail)
        self.put(0, first)
        self.put(1, tail)
        (self.directory / "1.part").write_bytes(b"oops")
        with self.assertRaises(HTTPException) as corrupt:
            self.store.complete(self.upload_id, lambda *_: self.fail("must not enqueue"), lambda _: None)
        self.assertEqual(corrupt.exception.status_code, 409)
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], [0])
        self.assertEqual(self.store.read_metadata(self.directory)["received_bytes"], len(first))
        self.put(1, tail)
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], len(first) + len(tail))

    def test_cross_process_distinct_and_duplicate_chunks_have_consistent_counters(self):
        self.create(b"a" * DEFAULT_CHUNK_BYTES * 2)
        program = """
import hashlib, sys
from pathlib import Path
from rallymate_service.uploads import UploadStore, DEFAULT_CHUNK_BYTES
root, upload_id, index = sys.argv[1:]
store = UploadStore(Path(root), 16 * 1024 * 1024)
content = bytes([97 + int(index)]) * DEFAULT_CHUNK_BYTES
for _ in range(2):
    store.put(upload_id, int(index), content, hashlib.sha256(content).hexdigest(), compact_ack=True)
"""
        project = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(project / "src")}
        def child(index):
            return subprocess.run([sys.executable, "-c", program, str(self.root), self.upload_id, str(index)],
                                  env=env, capture_output=True, text=True, timeout=30)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(child, [0, 1]))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.store.status(self.upload_id)["received_chunks"], [0, 1])
        self.assertEqual(self.store.read_metadata(self.directory)["received_count"], 2)
        self.assertEqual(self.store.status(self.upload_id)["received_bytes"], DEFAULT_CHUNK_BYTES * 2)

    def test_new_and_legacy_sessions_both_count_toward_disk_reservation(self):
        self.create(b"abc")
        old_id = str(uuid.uuid4())
        old_dir = self.store.directory(old_id)
        old_dir.mkdir()
        self.store.save(old_dir, {"upload_id": old_id, "filename": "old.mp4", "size": 5,
                                "chunk_count": 1, "chunks": {}, "options": {}, "job_id": None})
        enough_if_old_ignored = 512 * 1024 * 1024 + 3 * (3 + 1)
        disk = type("Disk", (), {"free": enough_if_old_ignored})()
        with patch("rallymate_service.uploads.shutil.disk_usage", return_value=disk):
            with self.assertRaises(HTTPException) as full:
                self.store.create(str(uuid.uuid4()), "new.mp4", 1, {})
        self.assertEqual(full.exception.status_code, 507)


if __name__ == "__main__":
    unittest.main()
