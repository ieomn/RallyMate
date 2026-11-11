from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from scripts import service_monitor_uploads as monitor

NOW = 1_800_000_000


def sqlite_session(root, identifier="sqlite", *, received_count=1, received_bytes=10,
                   updated=NOW, job_id=None, metadata=None):
    directory = root / "uploads" / ".chunks" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "state.sqlite3"
    item = {"upload_id": identifier, "filename": "比赛.mp4", "size": 25,
            "chunk_size": 10, "chunk_count": 3, "options": {}}
    if metadata:
        item.update(metadata)
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
            CREATE TABLE session (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1), metadata TEXT NOT NULL,
                updated_at REAL NOT NULL, job_id TEXT,
                received_count INTEGER NOT NULL DEFAULT 0 CHECK (received_count >= 0),
                received_bytes INTEGER NOT NULL DEFAULT 0 CHECK (received_bytes >= 0));
            CREATE TABLE chunks (chunk_index INTEGER PRIMARY KEY, sha256 TEXT, size INTEGER);
            PRAGMA user_version=1;
        """)
        connection.execute("INSERT INTO session VALUES(1, ?, ?, ?, ?, ?)",
                           (json.dumps(item), updated, job_id, received_count, received_bytes))
        connection.commit()
    return database


def legacy_session(root, identifier="legacy", *, updated=NOW, total=100, chunks=None, job_id=None):
    directory = root / "uploads" / ".chunks" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    item = {"upload_id": identifier, "filename": "旧视频.mp4", "size": total,
            "updated_at": updated, "job_id": job_id,
            "chunks": {"0": {"size": 30}} if chunks is None else chunks}
    path.write_text(json.dumps(item), encoding="utf-8")
    return path


class MonitorUploadsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def read(self, now=NOW):
        return monitor.read_uploads(self.root, now)

    def test_sqlite_and_legacy_pending_uploads_are_sorted_without_list_truncation(self):
        for index in range(12):
            sqlite_session(self.root, str(index), updated=NOW-index)
        legacy_session(self.root, updated=NOW+1)
        rows, bad = self.read()
        self.assertEqual(bad, 0)
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[0]["id"], "legacy")
        self.assertEqual(rows[1], {"id": "0", "name": "比赛.mp4", "total": 25, "received": 10,
                                   "updated": NOW, "job_id": None, "chunk_count": 3, "received_count": 1})
        self.assertEqual(rows[0]["received_count"], 1)
        self.assertIsNone(rows[0]["chunk_count"])

    def test_real_upload_store_progress_then_durable_completion(self):
        from rallymate_service.uploads import UploadStore
        chunk_size = 128 * 1024
        store = UploadStore(self.root / "uploads/.chunks", 4 * chunk_size, chunk_bytes=chunk_size)
        identity = str(uuid.uuid4())
        with patch("rallymate_service.uploads.time.time", return_value=NOW):
            store.create(identity, "上传.mp4", chunk_size + 3, {})
            self.assertEqual(self.read()[0][0]["received"], 0)
            tail = b"end"
            store.put(identity, 1, tail, hashlib.sha256(tail).hexdigest(), compact_ack=True)
            row = self.read()[0][0]
            self.assertEqual((row["received"], row["received_count"], row["chunk_count"]), (3, 1, 2))
            block = b"x" * chunk_size
            store.put(identity, 0, block, hashlib.sha256(block).hexdigest(), compact_ack=True)
            self.assertEqual(self.read()[0][0]["received"], chunk_size + 3)
            store.complete(identity, lambda path, data: {"id": identity}, lambda _: None)
            self.assertEqual(self.read(), ([], 0))

    def test_sqlite_is_authoritative_over_legacy_even_when_locked_or_corrupt(self):
        database = sqlite_session(self.root, "same")
        legacy_session(self.root, "same", total=500)
        self.assertEqual(self.read()[0][0]["total"], 25)
        database.write_bytes(b"corrupt database")
        self.assertEqual(self.read(), ([], 1))

    def test_only_constant_size_header_read_no_chunk_table_or_disk_writes(self):
        database = sqlite_session(self.root)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DROP TABLE chunks")
            connection.commit()
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        real_connect, queries, arguments = sqlite3.connect, [], []

        def connect(*args, **kwargs):
            arguments.append((args, kwargs))
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(queries.append)
            return connection

        with patch.object(monitor.sqlite3, "connect", side_effect=connect):
            self.assertEqual(self.read()[0][0]["received"], 10)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn("?mode=ro", arguments[0][0][0])
        self.assertTrue(arguments[0][1]["uri"])
        self.assertLessEqual(arguments[0][1]["timeout"], .25)
        self.assertFalse(any("chunks" in query.lower() for query in queries))
        self.assertEqual(sum("FROM session" in query for query in queries), 1)
        renamed = database.with_name("renamed.sqlite3")
        database.rename(renamed)  # No retained SQLite handle, including on Windows.

    def test_locked_database_is_bounded_and_next_poll_recovers(self):
        database = sqlite_session(self.root)
        with closing(sqlite3.connect(database)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            self.assertEqual(self.read(), ([], 1))
            self.assertLess(time.monotonic() - start, 1)
            writer.rollback()
        self.assertEqual(self.read()[1], 0)
        self.assertEqual(len(self.read()[0]), 1)

    def test_submitted_expired_and_zero_received_sessions(self):
        sqlite_session(self.root, "complete", received_count=3, received_bytes=25, job_id="complete")
        sqlite_session(self.root, "stale", updated=NOW-monitor.RECENT_SECONDS-1)
        sqlite_session(self.root, "new", received_count=0, received_bytes=0)
        legacy_session(self.root, "legacy-complete", job_id="legacy-complete")
        legacy_session(self.root, "legacy-stale", updated=NOW-monitor.RECENT_SECONDS-1)
        rows, bad = self.read()
        self.assertEqual([row["id"] for row in rows], ["new"])
        self.assertEqual(rows[0]["received"], 0)
        self.assertEqual(bad, 0)

    def test_invalid_sqlite_metadata_and_counter_bounds_are_isolated(self):
        cases = [{"size": True}, {"size": 0}, {"size": -1}, {"size": 2**63},
                 {"chunk_size": True}, {"chunk_count": 0}, {"chunk_count": 4},
                 {"upload_id": "another"}, {"filename": None}]
        for index, metadata in enumerate(cases):
            sqlite_session(self.root, f"bad-{index}", metadata=metadata)
        sqlite_session(self.root, "bad-time", updated=float("inf"))
        sqlite_session(self.root, "bad-bytes", received_count=1, received_bytes=8)
        sqlite_session(self.root, "bad-count", received_count=4, received_bytes=25)
        sqlite_session(self.root, "bad-zero", received_count=0, received_bytes=10)
        sqlite_session(self.root, "bad-complete", received_count=3, received_bytes=20)
        sqlite_session(self.root, "good")
        rows, bad = self.read()
        self.assertEqual([row["id"] for row in rows], ["good"])
        self.assertEqual(bad, len(cases)+5)

    def test_sqlite_version_and_malformed_or_oversized_json_fail_closed(self):
        for name, raw in (("duplicate", '{"size":1,"size":2}'),
                          ("nonfinite", '{"value":NaN}'), ("array", '[]'),
                          ("oversize", ' ' * (monitor.MAX_METADATA_BYTES+1))):
            database = sqlite_session(self.root, name)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE session SET metadata=?", (raw,))
                connection.commit()
        database = sqlite_session(self.root, "version")
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA user_version=2")
        database = sqlite_session(self.root, "missing")
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM session")
            connection.commit()
        self.assertEqual(self.read(), ([], 6))

    def test_invalid_legacy_fields_and_bounded_manifest(self):
        for name, mutation in (("bool-total", {"size": True}), ("nan", {"updated_at": float("nan")}),
                               ("negative", {"chunks": {"0": {"size": -1}}}),
                               ("bool-size", {"chunks": {"0": {"size": True}}}),
                               ("overflow", {"chunks": {"0": {"size": 101}}}),
                               ("bad-index", {"chunks": {"../0": {"size": 1}}}),
                               ("bad-id", {"upload_id": "another"}),
                               ("bad-count", {"chunk_count": 1, "chunks": {"2": {"size": 1}}})):
            path = legacy_session(self.root, name)
            item = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps({**item, **mutation}), encoding="utf-8")
        path = legacy_session(self.root, "oversized")
        path.write_bytes(b" " * 2049)
        with patch.object(monitor, "MAX_MANIFEST_BYTES", 2048):
            self.assertEqual(self.read(), ([], 9))

    def test_missing_paths_do_not_create_state_and_bad_database_does_not_create_fallback(self):
        self.assertEqual(self.read(), ([], 0))
        self.assertEqual(list(self.root.iterdir()), [])
        directory = self.root / "uploads/.chunks/initializing"
        directory.mkdir(parents=True)
        (directory / "state.sqlite3.tmp").write_bytes(b"in progress")
        self.assertEqual(self.read(), ([], 0))
        self.assertFalse((directory / "state.sqlite3").exists())

    def test_filename_control_sequences_and_credentials_are_not_exposed(self):
        sqlite_session(self.root, metadata={"filename": "比赛\n\x1b[2J\u202e token=private-value sk-abcdefghijklmno.mp4"})
        row = self.read()[0][0]
        self.assertNotIn("\x1b", row["name"])
        self.assertNotIn("\n", row["name"])
        self.assertNotIn("\u202e", row["name"])
        self.assertNotIn("private-value", row["name"])
        self.assertNotIn("sk-abcdefghijklmno", row["name"])

    def test_scan_limit_is_reported_instead_of_silently_hiding_truncation(self):
        for index in range(3):
            sqlite_session(self.root, str(index))
        with patch.object(monitor, "MAX_SESSIONS", 2):
            rows, bad = self.read()
        self.assertEqual(len(rows), 2)
        self.assertEqual(bad, 1)

    def test_scan_time_budget_reports_incomplete_read_without_blocking_next_refresh(self):
        sqlite_session(self.root)
        with patch.object(monitor, "MAX_SCAN_SECONDS", 0):
            self.assertEqual(self.read(), ([], 1))
        self.assertEqual(len(self.read()[0]), 1)

    def test_windows_reparse_file_is_rejected_without_querying_database(self):
        database = sqlite_session(self.root)
        original = Path.lstat

        def metadata(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == database:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value

        with patch.object(Path, "lstat", metadata), patch.object(monitor.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True):
            with patch.object(monitor.sqlite3, "connect", side_effect=AssertionError("must not query a reparse target")):
                self.assertEqual(self.read(), ([], 1))

    def test_directory_file_and_sidecar_symlinks_are_rejected_without_external_reads(self):
        with tempfile.TemporaryDirectory() as outside:
            target_root = Path(outside)
            external = sqlite_session(target_root)
            sessions = self.root / "uploads/.chunks"
            sessions.mkdir(parents=True)
            try:
                (sessions / "linked-directory").symlink_to(external.parent, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")
            file_dir = sessions / "linked-file"
            file_dir.mkdir()
            (file_dir / "state.sqlite3").symlink_to(external)
            sidecar_db = sqlite_session(self.root, "linked-sidecar")
            sidecar_db.with_name("state.sqlite3-wal").symlink_to(external)
            before = external.read_bytes()
            with patch.object(monitor.sqlite3, "connect", side_effect=AssertionError("must not open linked state")):
                self.assertEqual(self.read(), ([], 3))
            self.assertEqual(external.read_bytes(), before)

    def test_linked_upload_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.root / "uploads").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")
            self.assertEqual(self.read(), ([], 1))


if __name__ == "__main__":
    unittest.main()
