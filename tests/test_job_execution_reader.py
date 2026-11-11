from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rallymate_service import worker
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase, JobExecutionReader
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError


class JobExecutionReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # URI quoting must preserve both Unicode and URI delimiters on Windows.
        self.database = JobDatabase(self.root / "取消 # 100%.sqlite3")
        self.database.initialize()
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"fixture")
        self.request = self.root / "request.json"
        self.request.write_text("{}", encoding="utf-8")
        self.output = self.root / "output"
        self.output.mkdir()
        self.database.create_job("job", "video.mp4", self.video, self.request, self.output)

    def claim(self):
        return self.database.claim_next("worker", 60, 3)

    def replace_owner(self):
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job'")
            connection.commit()
        return self.database.claim_next("replacement", 60, 3)

    def assert_closed(self, connections):
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_reused_connection_returns_fresh_state_and_does_not_hold_transaction(self):
        self.claim()
        with self.database.execution_reader("job") as reader:
            self.assertEqual(reader.read(), self.database.execution_state("job"))
            connections = list(reader._connections)
            self.database.request_cancel("job")
            self.assertEqual(reader.read(), self.database.execution_state("job"))
            self.assertTrue(reader.read()["cancel_requested"])
            self.assertEqual(reader.connection_count, 1)
            self.assertEqual(reader.read_count, 3)
            self.assertFalse(connections[0].in_transaction)
            with self.database.connect() as connection:
                self.assertEqual(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)
        self.assert_closed(connections)

    def test_owner_and_attempt_are_refreshed_without_lease_time_inference(self):
        old = self.claim()
        with self.database.execution_reader("job") as reader:
            self.assertEqual(reader.read()["attempts"], old["attempts"])
            new = self.replace_owner()
            state = reader.read()
            self.assertEqual(state["worker_id"], "replacement")
            self.assertEqual(state["attempts"], new["attempts"])
            self.assertEqual(state, self.database.execution_state("job"))

    def test_threads_reuse_separate_connections_observe_cancellation_and_all_close(self):
        self.claim()
        reader = self.database.execution_reader("job")
        self.addCleanup(reader.close)
        barrier = threading.Barrier(2)
        states, errors = [], []

        def background():
            try:
                states.append(reader.read())
                barrier.wait(timeout=5)
                barrier.wait(timeout=5)
                states.append(reader.read())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=background)
        thread.start()
        try:
            self.assertFalse(reader.read()["cancel_requested"])
            barrier.wait(timeout=5)
            self.database.request_cancel("job")
            self.assertTrue(reader.read()["cancel_requested"])
            barrier.wait(timeout=5)
        finally:
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([bool(row["cancel_requested"]) for row in states], [False, True])
        self.assertEqual(reader.connection_count, 2)
        self.assertEqual(reader.read_count, 4)
        connections = list(reader._connections)
        self.assertEqual(len({id(connection) for connection in connections}), 2)
        reader.close()
        reader.close()
        self.assert_closed(connections)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            reader.read()

    def test_connection_is_read_only_and_missing_job_does_not_cache_absence(self):
        with self.database.execution_reader("later") as reader:
            self.assertIsNone(reader.read())
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                reader._connections[0].execute("UPDATE jobs SET worker_id = 'changed'")
            self.database.create_job("later", "video.mp4", self.video, self.request, self.output)
            self.assertEqual(reader.read(), self.database.execution_state("later"))
            self.assertEqual(reader.connection_count, 1)

    def test_missing_database_is_not_created(self):
        missing = self.root / "absent.sqlite3"
        with JobExecutionReader(missing, "job") as reader:
            with self.assertRaises(sqlite3.OperationalError):
                reader.read()
        self.assertFalse(missing.exists())

    def test_sqlite_query_error_propagates_without_returning_stale_state(self):
        with self.database.execution_reader("job") as reader:
            reader.read()
            with self.database.connect() as connection:
                connection.execute("DROP TABLE jobs")
                connection.commit()
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
                reader.read()

    def test_context_cleanup_error_preserves_original_control_exception(self):
        reader = self.database.execution_reader("job")
        primary, cleanup = ProcessTreeCleanupError("original cleanup"), sqlite3.OperationalError("close failed")
        with patch.object(reader, "close", side_effect=cleanup):
            with self.assertRaises(ProcessTreeCleanupError) as caught:
                with reader:
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.__cause__, cleanup)

    def run_worker(self, runner, *, loader=None, on_state=None):
        settings = ServiceSettings(data_root=self.root / "service", database_path=self.database.path,
                                   detect_model=self.video, pose_model=self.video)
        request = SimpleNamespace(scoring=SimpleNamespace(calibration_assets=[]))
        with patch.object(worker, "load_request", side_effect=loader or (lambda _: request)):
            return worker.process_one(settings, self.database, "worker", runner, on_state=on_state)

    def track_reader_cleanup(self, joined=None):
        readers, connections = [], []
        original_factory = self.database.execution_reader

        def make_reader(job_id):
            reader = original_factory(job_id)
            original_close = reader.close

            def close():
                if joined is not None:
                    self.assertTrue(joined.is_set(), "reader closed before callback thread joined")
                connections.extend(reader._connections)
                original_close()
            reader.close = close
            readers.append(reader)
            return reader

        return patch.object(self.database, "execution_reader", side_effect=make_reader), readers, connections

    def test_worker_closes_after_success_and_joined_background_callback(self):
        joined, errors = threading.Event(), []
        tracking, readers, connections = self.track_reader_cleanup(joined)

        def runner(request, cancellation_callback=None):
            def background():
                try:
                    cancellation_callback()
                    cancellation_callback()
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=background)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            joined.set()
            return {"all_results": ["score", "3d"]}

        with tracking:
            self.assertTrue(self.run_worker(runner))
        self.assertEqual(errors, [])
        self.assertEqual(self.database.get_job("job")["summary"], {"all_results": ["score", "3d"]})
        self.assertEqual(readers[0].connection_count, 2)
        self.assert_closed(connections)

    def test_worker_cancellation_during_load_still_closes_reader(self):
        tracking, readers, connections = self.track_reader_cleanup()

        def load(_):
            self.database.request_cancel("job")
            return SimpleNamespace(scoring=SimpleNamespace(calibration_assets=[]))
        with tracking:
            self.run_worker(lambda _: self.fail("cancelled job started runner"), loader=load)
        self.assertEqual(self.database.get_job("job")["status"], "cancelled")
        self.assertEqual(readers[0].connection_count, 1)
        self.assert_closed(connections)

    def test_worker_replacement_owner_is_preserved_and_reader_closed(self):
        tracking, readers, connections = self.track_reader_cleanup()

        def runner(request, cancellation_callback=None):
            self.replace_owner()
            cancellation_callback()
            self.fail("stale worker continued")
        with tracking:
            self.run_worker(runner)
        self.assertEqual(self.database.get_job("job")["worker_id"], "replacement")
        self.assertEqual(self.database.get_job("job")["attempts"], 2)
        self.assert_closed(connections)

    def test_worker_failure_and_control_exceptions_close_connections(self):
        for error in (ValueError("inference failure"), ProcessTreeCleanupError("cleanup failure"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                # Restore a queued state between independent attempt cases.
                with self.database.connect() as connection:
                    connection.execute("UPDATE jobs SET status='queued', attempts=0, worker_id=NULL WHERE id='job'")
                    connection.commit()
                tracking, readers, connections = self.track_reader_cleanup()

                def runner(request, cancellation_callback=None):
                    cancellation_callback()
                    raise error
                with tracking:
                    if isinstance(error, (ProcessTreeCleanupError, KeyboardInterrupt)):
                        with self.assertRaises(type(error)) as caught:
                            self.run_worker(runner)
                        self.assertIs(caught.exception, error)
                        self.assertEqual(self.database.get_job("job")["status"], "running")
                    else:
                        self.run_worker(runner)
                        self.assertEqual(self.database.get_job("job")["status"], "failed")
                self.assert_closed(connections)

    def test_worker_reader_cleanup_failure_preserves_process_tree_error(self):
        primary, cleanup = ProcessTreeCleanupError("original cleanup"), sqlite3.OperationalError("close failed")
        reader = self.database.execution_reader("job")
        original_close = reader.close
        connections = []

        def failed_close():
            connections.extend(reader._connections)
            original_close()
            raise cleanup

        def runner(request):
            raise primary

        with patch.object(self.database, "execution_reader", return_value=reader), patch.object(reader, "close", side_effect=failed_close):
            with self.assertRaises(ProcessTreeCleanupError) as caught:
                self.run_worker(runner)
        self.assertIs(caught.exception, primary)
        self.assertIs(caught.exception.__cause__, cleanup)
        self.assert_closed(connections)
        self.assertEqual(self.database.get_job("job")["status"], "running")


if __name__ == "__main__":
    unittest.main()
