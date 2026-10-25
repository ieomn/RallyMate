from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from rallymate_service.database import JobDatabase
from rallymate_service.local_secrets import load_minimax_key, save_minimax_key
from rallymate_service.motion_replay_runtime import CompletedMotionSourceScanner
from rallymate_service.motion_replay_api import MotionReplayInputError


def completed_job(database, job_id, completed_at):
    database.create_job(job_id, "example.mp4", Path("source.mp4"), Path("request.json"), Path("run"))
    database.claim_next("test-worker", 60, 3)
    database.mark_succeeded(job_id, {})
    with database.connect() as connection:
        connection.execute("UPDATE jobs SET completed_at=? WHERE id=?", (completed_at, job_id))
        connection.commit()


class MotionReplayRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def track_scheduler_connections(self, scanner):
        original_connect = sqlite3.connect
        retained = []

        def connect(path, *args, **kwargs):
            connection = original_connect(path, *args, **kwargs)
            if Path(path) == scanner.path:
                # Keep a reference so garbage collection cannot conceal a leak.
                retained.append(connection)
                self.addCleanup(connection.close)
            return connection

        return retained, patch("rallymate_service.motion_replay_runtime.sqlite3.connect", side_effect=connect)

    def assert_connections_closed(self, retained):
        self.assertTrue(retained)
        for connection in retained:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_scheduler_closes_connections_after_successful_scans(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()
        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, lambda _: None)
        retained, tracking = self.track_scheduler_connections(scanner)
        with tracking:
            scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
            completed_job(jobs, "one", "2026-01-02T00:00:00+00:00")
            self.assertEqual(scanner.scan_once(), 1)
            self.assertEqual(scanner.scan_once(), 0)
        self.assert_connections_closed(retained)

    def test_scheduler_closes_read_connection_when_cursor_is_missing(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()
        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, lambda _: None)
        scanner.initialize()
        with closing(sqlite3.connect(scanner.path)) as connection, connection:
            connection.execute("DELETE FROM cursor")
        retained, tracking = self.track_scheduler_connections(scanner)
        with tracking, self.assertRaisesRegex(RuntimeError, "not initialized"):
            scanner.scan_once()
        self.assert_connections_closed(retained)

    def test_scheduler_rolls_back_skip_and_closes_connections_when_cursor_update_fails(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()

        def ineligible(_):
            raise MotionReplayInputError("Insufficient observed motion", 409)

        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, ineligible)
        scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
        completed_job(jobs, "one", "2026-01-02T00:00:00+00:00")
        with closing(sqlite3.connect(scanner.path)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_cursor BEFORE UPDATE ON cursor "
                               "BEGIN SELECT RAISE(ABORT, 'cursor fixture failure'); END")
        retained, tracking = self.track_scheduler_connections(scanner)
        with tracking, self.assertRaisesRegex(sqlite3.IntegrityError, "cursor fixture failure"):
            scanner.scan_once()
        self.assert_connections_closed(retained)
        with closing(sqlite3.connect(scanner.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM skipped_sources").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT completed_at, job_id FROM cursor").fetchone(),
                             ("2026-01-01T00:00:00+00:00", ""))

    def test_scheduler_ignores_history_and_recovers_completions_after_restart(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()
        completed_job(jobs, "old", "2026-01-01T00:00:00+00:00")
        called = []
        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, called.append)
        scanner.initialize(activated_at="2026-01-02T00:00:00+00:00")
        completed_job(jobs, "first", "2026-01-02T00:00:01+00:00")
        self.assertEqual(scanner.scan_once(), 1)
        self.assertEqual(scanner.scan_once(), 0)
        completed_job(jobs, "offline", "2026-01-02T00:00:02+00:00")
        restarted = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, called.append)
        restarted.initialize(activated_at="2026-01-03T00:00:00+00:00")
        self.assertEqual(restarted.scan_once(), 1)
        self.assertEqual(called, ["first", "offline"])

    def test_scheduler_does_not_skip_transient_enqueue_failure(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()
        attempts = []

        def enqueue(job_id):
            attempts.append(job_id)
            if len(attempts) == 1:
                raise OSError("temporarily unavailable")

        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, enqueue)
        scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
        completed_job(jobs, "one", "2026-01-02T00:00:00+00:00")
        with self.assertRaises(OSError):
            scanner.scan_once()
        self.assertEqual(scanner.scan_once(), 1)
        self.assertEqual(attempts, ["one", "one"])

    def test_scheduler_skips_explicit_ineligible_source_without_blocking_next(self):
        jobs = JobDatabase(self.root / "jobs.sqlite3")
        jobs.initialize()
        received = []

        def enqueue(job_id):
            if job_id == "a":
                raise MotionReplayInputError("Insufficient observed motion", 409)
            received.append(job_id)

        scanner = CompletedMotionSourceScanner(self.root / "scheduler.sqlite3", jobs, enqueue)
        scanner.initialize(activated_at="2026-01-01T00:00:00+00:00")
        completed_job(jobs, "a", "2026-01-02T00:00:00+00:00")
        completed_job(jobs, "b", "2026-01-02T00:00:00+00:00")
        self.assertEqual(scanner.scan_once(), 2)
        self.assertEqual(received, ["b"])

    def test_environment_credential_takes_precedence(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": " placeholder-test-credential "}):
            self.assertEqual(load_minimax_key(self.root), "placeholder-test-credential")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI credential storage")
    def test_local_credential_is_encrypted_and_round_trips(self):
        with patch.dict(os.environ):
            os.environ.pop("MINIMAX_API_KEY", None)
            path = save_minimax_key(self.root, "placeholder-test-credential")
            self.assertNotIn(b"placeholder-test-credential", path.read_bytes())
            self.assertEqual(load_minimax_key(self.root), "placeholder-test-credential")


if __name__ == "__main__":
    unittest.main()
