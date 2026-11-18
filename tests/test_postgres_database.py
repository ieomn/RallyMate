from __future__ import annotations

import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from psycopg import InterfaceError
from psycopg.errors import ReadOnlySqlTransaction
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from rallymate_service.database import QueueCapacityError
from rallymate_service.migrations import migrate_database
from rallymate_service.postgres_database import PostgresJobDatabase, _parameter_sql


class PostgresParameterSqlTests(unittest.TestCase):
    def test_only_parameters_outside_literals_and_comments_are_replaced(self):
        statement = "SELECT ?, 'it''s ? 50%', \"name?\", $$? 60%$$ /* ? */ -- ?\n, ?"
        expected = "SELECT %s, 'it''s ? 50%%', \"name?\", $$? 60%%$$ /* ? */ -- ?\n, %s"
        self.assertEqual(_parameter_sql(statement), expected)


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "PostgreSQL test URL is not configured")
class PostgresJobDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
        cls.base_url = url.set(drivername="postgresql+psycopg")
        cls.admin = create_engine(cls.base_url)
        cls.addClassCleanup(cls.admin.dispose)
        cls.schema = "test_jobs_" + uuid4().hex
        with cls.admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.addClassCleanup(cls.drop_schema, cls.schema)
        cls.url = cls.schema_url(cls.schema)
        migrate_database(cls.url)

    @classmethod
    def schema_url(cls, schema):
        options = cls.base_url.query.get("options", "")
        return cls.base_url.update_query_dict({
            "options": f"{options} -csearch_path={schema}".strip(),
        }).render_as_string(hide_password=False)

    @classmethod
    def drop_schema(cls, schema):
        with cls.admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

    def setUp(self):
        self.database = PostgresJobDatabase(self.url)
        self.addCleanup(self.database.dispose)
        self.database.initialize()
        with self.database.connect() as connection:
            connection.execute("DELETE FROM jobs")
            connection.commit()

    def create(self, identifier="job", *, database=None, **kwargs):
        return (database or self.database).create_job(
            identifier, "测试 ? 100%.mp4", Path("video.mp4"),
            Path("request.json"), Path("output") / identifier, **kwargs,
        )

    def expire(self, identifier="job"):
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", identifier),
            )
            connection.commit()

    def test_initialize_requires_migrations_and_does_not_create_tables(self):
        schema = "test_empty_" + uuid4().hex
        with self.admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self.drop_schema, schema)
        database = PostgresJobDatabase(self.schema_url(schema))
        self.addCleanup(database.dispose)
        with self.assertRaisesRegex(RuntimeError, "migration"):
            database.initialize()
        self.assertEqual(inspect(database.engine).get_table_names(), [])

    def test_sql_bridge_preserves_literals_nulls_booleans_and_row_access(self):
        value = "中文 ' ? 80% %(parameter)s"
        with self.database.connect() as connection:
            with connection.cursor() as cursor:
                row = cursor.execute(
                    "SELECT '?' AS literal, ? AS bound, 'it''s ?%' AS pattern, "
                    "9 % 4 AS remainder, $tag$? 100%$tag$ AS dollar, "
                    "(? IS NULL) AS missing, ? AS enabled -- ?\n/* ? 5% */",
                    (value, None, True),
                ).fetchone()
                self.assertEqual(row[0], "?")
                self.assertEqual(row["bound"], value)
                self.assertEqual(row["pattern"], "it's ?%")
                self.assertEqual(row["remainder"], 1)
                self.assertEqual(row["dollar"], "? 100%")
                self.assertTrue(row["missing"])
                self.assertTrue(row["enabled"])
                self.assertEqual(dict(row)["bound"], value)
                self.assertEqual(tuple(row)[1], value)
                self.assertEqual(len(row), 7)

    def test_cursor_many_and_uncommitted_context_rollback(self):
        self.create()
        with self.database.connect() as connection:
            connection.execute("CREATE TEMP TABLE bridge_values (value TEXT) ON COMMIT DROP")
            cursor = connection.cursor()
            cursor.executemany("INSERT INTO bridge_values VALUES (?)", [("one?",), ("two%",)])
            self.assertEqual(cursor.rowcount, 2)
            cursor.execute("SELECT value FROM bridge_values ORDER BY value")
            self.assertEqual(cursor.fetchmany(1)[0][0], "one?")
            self.assertEqual([row[0] for row in cursor], ["two%"])
            cursor.close()
            connection.commit()
            connection.execute("UPDATE jobs SET error = ? WHERE id = ?", ("rolled back", "job"))
            retained_cursor = connection.cursor()
        self.assertIsNone(self.database.get_job("job")["error"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            connection.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            connection.commit()
        with self.assertRaises(InterfaceError):
            retained_cursor.execute("SELECT 1")

    def test_concurrent_admission_is_shared_between_database_instances(self):
        other = PostgresJobDatabase(self.url)
        self.addCleanup(other.dispose)
        barrier = threading.Barrier(12)

        def submit(index):
            barrier.wait(timeout=10)
            try:
                return self.create(
                    f"job-{index}", database=other if index % 2 else self.database,
                    max_active_jobs=4,
                )["id"]
            except QueueCapacityError:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            admitted = list(pool.map(submit, range(12)))
        self.assertEqual(sum(value is not None for value in admitted), 4)
        self.assertEqual(self.database.count_by_status(), {"queued": 4})
        cancelled = next(value for value in admitted if value is not None)
        self.database.request_cancel(cancelled)
        self.assertEqual(self.create("replacement", max_active_jobs=4)["status"], "queued")

    def test_concurrent_claims_are_unique_and_directed_claim_respects_id(self):
        for index in range(8):
            self.create(f"job-{index}")
        directed = self.database.claim_next("directed", 60, 3, expected_job_id="job-7", recover_expired=False)
        self.assertEqual(directed["id"], "job-7")
        barrier = threading.Barrier(12)

        def claim(index):
            barrier.wait(timeout=10)
            return self.database.claim_next(f"worker-{index}", 60, 3)

        with ThreadPoolExecutor(max_workers=12) as pool:
            claimed = [job for job in pool.map(claim, range(12)) if job is not None]
        self.assertEqual(len(claimed), 7)
        self.assertEqual(len({job["id"] for job in claimed}), 7)
        self.assertTrue(all(job["attempts"] == 1 for job in claimed))
        self.assertEqual(self.database.count_by_status(), {"running": 8})

    def test_raw_json_is_text_and_public_jobs_decode_exactly(self):
        self.create()
        self.database.claim_next("worker", 60, 3)
        progress = {"phase": "inference", "percent": 37, "message": "完成 ? 37%"}
        self.database.update_progress("job", progress, 60, worker_id="worker", attempts=1)
        with self.database.connect() as connection:
            raw = connection.execute("SELECT progress_json FROM jobs WHERE id=?", ("job",)).fetchone()[0]
        self.assertIsInstance(raw, str)
        self.assertEqual(json.loads(raw), progress)
        summary = {"中文": {"items": [1, None, True]}, "processing": {"processed_frames": 23}}
        self.assertEqual(self.database.mark_succeeded("job", summary, worker_id="worker", attempts=1), "succeeded")
        job = self.database.get_job("job")
        self.assertEqual(job["summary"], summary)
        self.assertFalse(job["cancel_requested"])
        self.assertIsNone(job["user_id"])
        self.assertIsNone(job["deleted_at"])
        with self.database.connect() as connection:
            row = connection.execute("SELECT summary_json, created_at FROM jobs WHERE id=?", ("job",)).fetchone()
        self.assertIsInstance(row["summary_json"], str)
        self.assertIsInstance(row["created_at"], str)
        self.assertEqual(json.loads(row["summary_json"]), summary)
        self.assertEqual(self.database.list_jobs(), [job])

    def test_cancel_acknowledgement_and_expired_attempt_fencing(self):
        self.create()
        old = self.database.claim_next("old-worker", 60, 3)
        self.expire()
        new = self.database.claim_next("new-worker", 60, 3)
        self.assertEqual(new["attempts"], old["attempts"] + 1)
        self.database.update_progress("job", {"percent": 99}, 60, worker_id="old-worker", attempts=1)
        self.assertIsNone(self.database.mark_succeeded("job", {"stale": True}, worker_id="old-worker", attempts=1))
        self.assertIsNone(self.database.mark_failed("job", "stale", worker_id="old-worker", attempts=1))
        self.assertEqual(self.database.get_job("job"), new)
        self.database.request_cancel("job")
        self.expire()
        self.assertIsNone(self.database.claim_next("third-worker", 60, 3))
        self.assertFalse(self.database.mark_cancelled("job", worker_id="old-worker", attempts=1))
        self.assertEqual(self.database.mark_succeeded("job", {}, worker_id="new-worker", attempts=2), "cancelled")
        final = self.database.get_job("job")
        self.assertTrue(final["cancel_requested"])
        self.assertIsNotNone(final["cancelled_at"])

    def test_fleet_expiry_requires_confirmed_exit_before_recovery(self):
        self.create()
        self.database.claim_next("fleet-owner", 60, 3)
        self.expire()
        self.assertIsNone(self.database.claim_next("legacy-worker", 60, 3))
        attempts = self.database.running_attempts_for_worker("fleet-owner")
        self.assertEqual(attempts, [{"job_id": "job", "attempts": 1}])
        with self.assertRaisesRegex(ValueError, "confirmed"):
            self.database.recover_confirmed_worker_attempts("fleet-owner", attempts, max_attempts=3)
        recovered = self.database.recover_confirmed_worker_attempts(
            "fleet-owner", attempts, max_attempts=3, confirmed_tree_exit=True,
        )
        self.assertEqual(recovered[0]["status"], "queued")
        self.assertEqual(self.database.claim_next("replacement", 60, 3)["attempts"], 2)

    def test_cancel_finish_race_has_one_consistent_terminal_result(self):
        for index in range(6):
            identifier = f"race-{index}"
            self.create(identifier)
            self.database.claim_next("worker", 60, 3, expected_job_id=identifier)
            barrier = threading.Barrier(2)

            def cancel():
                barrier.wait(timeout=10)
                self.database.request_cancel(identifier)

            def finish():
                barrier.wait(timeout=10)
                self.database.mark_succeeded(identifier, {"complete": True}, worker_id="worker", attempts=1)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(cancel), pool.submit(finish)]
                for future in futures:
                    future.result(timeout=15)
            job = self.database.get_job(identifier)
            self.assertIn(job["status"], {"succeeded", "cancelled"})
            self.assertEqual(job["cancel_requested"], job["status"] == "cancelled")
            self.assertEqual(job["cancelled_at"] is not None, job["status"] == "cancelled")

    def test_execution_reader_refreshes_absence_owner_cancel_and_closes(self):
        with self.database.execution_reader("job") as reader:
            self.assertIsNone(reader.read())
            self.create()
            self.database.claim_next("worker", 60, 3)
            self.assertEqual(reader.read(), self.database.execution_state("job"))
            self.expire()
            self.database.claim_next("replacement", 60, 3)
            self.assertEqual(reader.read()["worker_id"], "replacement")
            self.database.request_cancel("job")
            self.assertTrue(reader.read()["cancel_requested"])
            self.assertEqual(reader.connection_count, 1)
            self.assertEqual(reader.read_count, 4)
            connection = reader._connections[0]
            self.assertFalse(connection.in_transaction)
            with self.assertRaises(ReadOnlySqlTransaction):
                connection.execute("UPDATE jobs SET worker_id='wrong'")
            self.assertFalse(connection.in_transaction)
            self.assertEqual(reader.read()["worker_id"], "replacement")
        self.assertTrue(connection._driver.closed)
        reader.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            reader.read()
        self.assertTrue(self.database.mark_cancelled("job", worker_id="replacement", attempts=2))

    def test_execution_reader_uses_one_connection_per_thread_and_closes_all(self):
        self.create()
        self.database.claim_next("worker", 60, 3)
        reader = self.database.execution_reader("job")
        self.addCleanup(reader.close)
        barrier = threading.Barrier(2)

        def background():
            before = reader.read()
            barrier.wait(timeout=10)
            barrier.wait(timeout=10)
            return before, reader.read()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(background)
            self.assertFalse(reader.read()["cancel_requested"])
            barrier.wait(timeout=10)
            self.database.request_cancel("job")
            self.assertTrue(reader.read()["cancel_requested"])
            barrier.wait(timeout=10)
            before, after = future.result(timeout=15)
        self.assertFalse(before["cancel_requested"])
        self.assertTrue(after["cancel_requested"])
        self.assertEqual(reader.read_count, 4)
        self.assertEqual(reader.connection_count, 2)
        connections = list(reader._connections)
        self.assertEqual(len({id(connection._driver) for connection in connections}), 2)
        self.assertTrue(all(not connection.in_transaction for connection in connections))
        reader.close()
        self.assertTrue(all(connection._driver.closed for connection in connections))


if __name__ == "__main__":
    unittest.main()
