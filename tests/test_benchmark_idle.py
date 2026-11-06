"""Read-only fresh production-idle checks, using only temporary SQLite files."""
from __future__ import annotations

from contextlib import closing
import io
import json
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import benchmark_idle


class IdleGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "jobs.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
            connection.execute("INSERT INTO jobs (status) VALUES ('completed')")
            connection.commit()

    def insert_status(self, status):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO jobs (status) VALUES (?)", (status,))
            connection.commit()

    def test_native_checks_fresh_committed_status_for_both_queued_and_running(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                benchmark_idle.require_idle(self.database)
                self.insert_status(status)
                with self.assertRaises(RuntimeError):
                    benchmark_idle.require_idle(self.database)
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("DELETE FROM jobs WHERE status = ?", (status,))
                    connection.commit()
        benchmark_idle.require_idle(self.database)

    def test_missing_database_is_not_created_by_native_check(self):
        missing = self.database.parent / "missing.sqlite3"
        with self.assertRaises((RuntimeError, sqlite3.Error, FileNotFoundError)):
            benchmark_idle.require_idle(missing)
        self.assertFalse(missing.exists())

    def test_native_connection_is_read_only_and_closed_on_idle_busy_and_query_error(self):
        connect = sqlite3.connect
        observed = []

        class TrackingConnection:
            def __init__(self, wrapped):
                self.wrapped, self.closed = wrapped, False
            def __getattr__(self, name):
                return getattr(self.wrapped, name)
            def __enter__(self):
                self.wrapped.__enter__()
                return self
            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)
            def close(self):
                self.closed = True
                self.wrapped.close()

        def tracked_connect(*args, **kwargs):
            self.assertIn("mode=ro", str(args[0]))
            self.assertTrue(kwargs.get("uri"))
            connection = TrackingConnection(connect(*args, **kwargs))
            observed.append(connection)
            return connection

        with patch.object(benchmark_idle.sqlite3, "connect", side_effect=tracked_connect):
            benchmark_idle.require_idle(self.database)
        self.insert_status("running")
        with patch.object(benchmark_idle.sqlite3, "connect", side_effect=tracked_connect):
            with self.assertRaises(RuntimeError):
                benchmark_idle.require_idle(self.database)
        with closing(connect(self.database)) as connection:
            connection.execute("DROP TABLE jobs")
            connection.commit()
        with patch.object(benchmark_idle.sqlite3, "connect", side_effect=tracked_connect):
            with self.assertRaises((RuntimeError, sqlite3.Error)):
                benchmark_idle.require_idle(self.database)
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(connection.closed for connection in observed))

    def test_bridge_reuses_one_process_and_sees_a_new_job_after_successful_checks(self):
        popen = subprocess.Popen
        processes = []

        def tracked_popen(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(subprocess, "Popen", side_effect=tracked_popen):
            with benchmark_idle.IdleJobsGuard(self.database, python=Path(sys.executable), timeout=10) as guard:
                guard.check()
                guard.check()
                self.assertEqual(len(processes), 1)
                self.assertIsNone(processes[0].poll())
                self.insert_status("queued")
                with self.assertRaises(RuntimeError):
                    guard.check()
            guard.close()
        self.assertIsNotNone(processes[0].poll())

    def test_bridge_missing_database_fails_without_creating_it(self):
        missing = self.database.parent / "missing.sqlite3"
        with self.assertRaises((RuntimeError, sqlite3.Error, FileNotFoundError)):
            with benchmark_idle.IdleJobsGuard(missing, python=Path(sys.executable), timeout=10) as guard:
                guard.check()
        self.assertFalse(missing.exists())

    def mock_bridge(self, response):
        with patch.object(benchmark_idle.IdleJobsGuard, "_start_bridge"):
            guard = benchmark_idle.IdleJobsGuard(self.database, python=Path(sys.executable), timeout=.01)
        guard.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
        guard.messages = queue.Queue()
        if response is not _NO_RESPONSE:
            guard.messages.put(response)
        # No subprocess was started. Observe the failure cleanup contract
        # without depending on the actual OS process ownership implementation.
        guard.close = Mock()
        return guard

    def test_bridge_rejects_eof_malformed_mismatched_and_invalid_count_responses(self):
        responses = (None, {"protocol_error": "malformed response"},
                     {"id": 2, "active_jobs": 0}, {"id": True, "active_jobs": 0},
                     {"id": 1, "active_jobs": "0"}, {"id": 1, "active_jobs": -1},
                     {"id": 1, "active_jobs": True}, {"id": 1, "error": "read failed"})
        for response in responses:
            with self.subTest(response=response):
                guard = self.mock_bridge(response)
                with self.assertRaises(RuntimeError):
                    guard.check()
                guard.close.assert_called_once_with()

    def test_bridge_timeout_closes_owner_and_never_treats_silence_as_idle(self):
        guard = self.mock_bridge(_NO_RESPONSE)
        with self.assertRaises(RuntimeError):
            guard.check()
        guard.close.assert_called_once_with()

    def test_bridge_sends_incrementing_check_ids_and_requires_corresponding_answers(self):
        guard = self.mock_bridge({"id": 1, "active_jobs": 0})
        guard.check()
        guard.messages.put({"id": 2, "active_jobs": 0})
        guard.check()
        sent = [json.loads(line) for line in guard.process.stdin.getvalue().splitlines()]
        self.assertEqual(sent, [{"id": 1, "command": "check"}, {"id": 2, "command": "check"}])
        guard.close.assert_not_called()

    def test_wsl_bridge_paths_use_native_translation_as_argv_without_shell(self):
        source, python = self.database, Path("python.exe")
        absolute = str(source.resolve())
        translated = "D:\\runtime with spaces\\jobs.sqlite3"
        # Replace this module's OS view only; mutating os.name globally would
        # also make pathlib attempt PosixPath construction on a Windows host.
        with patch.object(benchmark_idle, "os", SimpleNamespace(name="posix")), patch.object(
                benchmark_idle.subprocess, "run", return_value=SimpleNamespace(stdout=translated + "\n")) as run:
            self.assertEqual(benchmark_idle._bridge_path(source, python), translated)
        self.assertEqual(run.call_args.args[0], ["wslpath", "-w", absolute])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["text"])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)
        self.assertFalse(run.call_args.kwargs.get("shell", False))


_NO_RESPONSE = object()


if __name__ == "__main__":
    unittest.main()
