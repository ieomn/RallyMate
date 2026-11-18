from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import anyio
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import URL, make_url

from rallymate_service.database import JobDatabase, QueueCapacityError, utc_now
from rallymate_service.migrations import migrate_database
from rallymate_service.models import jobs, upload_owners, users
from rallymate_service.ownership import (
    UserScopedJobDatabase, UserScopedUploadStore, assert_job_owner,
    assert_upload_owner, current_user_id,
)
from rallymate_service.uploads import MIN_CHUNK_BYTES, UploadStore


@contextmanager
def as_user(user_id):
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


class OwnershipTests(unittest.TestCase):
    postgres = False

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        if self.postgres:
            url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            base = create_engine(url)
            self.addCleanup(base.dispose)
            schema = "ownership_test_" + uuid4().hex
            with base.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            def drop_schema():
                with base.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            self.addCleanup(drop_schema)
            query = dict(url.query)
            query["options"] = str(query.get("options", "")) + f" -csearch_path={schema}"
            url = url.set(query=query)
            migrate_database(url)
            from rallymate_service.postgres_database import PostgresJobDatabase
            self.delegate = PostgresJobDatabase(url.render_as_string(hide_password=False))
            self.addCleanup(self.delegate.dispose)
        else:
            path = self.root / "jobs.sqlite3"
            url = URL.create("sqlite", database=str(path))
            migrate_database(url)
            self.delegate = JobDatabase(path)
        self.engine = create_engine(url)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(users.insert(), [{"id": actor, "email": actor + "@example.test",
                "username": actor, "password_hash": "fixture", "created_at": utc_now(),
                "status": "APPROVED"} for actor in ("user-a", "user-b")])
        self.db = UserScopedJobDatabase(self.delegate, self.engine)
        self.raw_uploads = UploadStore(self.root / "uploads", 1024 * 1024, chunk_bytes=MIN_CHUNK_BYTES)
        self.uploads = UserScopedUploadStore(self.raw_uploads, self.engine)

    def create_job(self, identifier, user_id=None, **kwargs):
        with as_user(user_id):
            return self.db.create_job(identifier, "fixture.mp4", self.root / (identifier + ".mp4"),
                self.root / (identifier + ".json"), self.root / identifier, **kwargs)

    def set_job(self, identifier, **values):
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == identifier).values(**values))

    def assert_not_found(self, callable, *args):
        with self.assertRaises(HTTPException) as raised:
            callable(*args)
        self.assertEqual(raised.exception.status_code, 404)

    def test_two_users_and_legacy_rows_are_isolated(self):
        self.create_job("legacy")
        self.create_job("a", "user-a")
        self.create_job("b", "user-b")
        with as_user("user-a"):
            self.assertEqual(self.db.get_job("a")["user_id"], "user-a")
            self.assertIsNone(self.db.get_job("b"))
            self.assertIsNone(self.db.get_job("legacy"))
            self.assertEqual([row["id"] for row in self.db.list_jobs()], ["a"])
        with as_user("user-b"):
            self.assertEqual([row["id"] for row in self.db.list_jobs()], ["b"])
        self.assertEqual({row["id"] for row in self.db.list_jobs()}, {"legacy", "a", "b"})
        self.assertIsNone(self.db.get_job("legacy")["user_id"])

    def test_owner_written_with_job_and_rejected_queue_has_no_orphan(self):
        self.create_job("first", "user-a", max_active_jobs=1)
        with self.assertRaises(QueueCapacityError):
            self.create_job("full", "user-b", max_active_jobs=1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(jobs.c.user_id).where(jobs.c.id == "first")).scalar_one(), "user-a")
            self.assertIsNone(connection.execute(select(jobs.c.id).where(jobs.c.id == "full")).first())

    def test_explicit_foreign_owner_cannot_override_context(self):
        with as_user("user-a"):
            with self.assertRaises(HTTPException) as raised:
                self.db.create_job("forged", "x", self.root / "x", self.root / "y", self.root / "z", user_id="user-b")
            self.assertEqual(raised.exception.status_code, 404)
        self.assertIsNone(self.delegate.get_job("forged"))

    def test_owner_check_does_not_load_foreign_report(self):
        self.create_job("private", "user-b")
        with as_user("user-a"), patch.object(self.delegate, "get_job", side_effect=AssertionError("foreign report fetched")):
            self.assertIsNone(self.db.get_job("private"))
            self.assert_not_found(assert_job_owner, self.engine, "user-a", "private")
        self.assert_not_found(assert_job_owner, self.engine, None, "private")

    def test_context_is_copied_into_worker_threads_and_reset(self):
        async def create_for(actor):
            with as_user(actor):
                await anyio.to_thread.run_sync(lambda: self.db.create_job(actor, "x.mp4",
                    self.root / actor, self.root / "request", self.root / "output"))
                found = await anyio.to_thread.run_sync(self.db.list_jobs)
                self.assertEqual([row["user_id"] for row in found], [actor])
        async def both():
            await asyncio.gather(create_for("user-a"), create_for("user-b"))
        asyncio.run(both())
        self.assertIsNone(current_user_id.get())
        self.assertEqual(len(self.db.list_jobs()), 2)

    def test_history_paginates_without_reports_or_paths(self):
        for index in range(3):
            self.create_job(f"a{index}", "user-a")
            self.set_job(f"a{index}", created_at=f"2026-11-17T00:00:0{index}+00:00", summary_json="not parsed JSON")
        self.create_job("other", "user-b")
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            with as_user("user-a"):
                history = self.db.list_user_jobs("user-a", limit=1, offset=1)
                self.assert_not_found(self.db.list_user_jobs, "user-b")
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual([row["id"] for row in history], ["a1"])
        self.assertNotIn("summary_json", " ".join(statements))
        self.assertNotIn("video_path", history[0])
        self.assertNotIn("progress_json", history[0])
        with self.assertRaises(ValueError):
            self.db.list_user_jobs("user-a", limit=101)

    def test_soft_delete_hides_resources_but_keeps_statistics_and_files(self):
        self.create_job("a", "user-a")
        artifact = self.root / "retained.txt"
        artifact.write_text("retained")
        self.set_job("a", status="succeeded", attempts=3, output_dir=str(self.root))
        with as_user("user-b"):
            self.assert_not_found(self.db.delete_owned_job, "user-b", "a")
        with as_user("user-a"):
            result = self.db.delete_owned_job("user-a", "a")
            self.assertTrue(result["deleted"])
            self.assertIsNone(self.db.get_job("a"))
            self.assertEqual(self.db.list_jobs(), [])
            self.assertEqual(self.db.list_user_jobs("user-a"), [])
            self.assert_not_found(assert_job_owner, self.engine, "user-a", "a")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(func.count(), func.sum(jobs.c.attempts))).one(), (1, 3))
        self.assertEqual(artifact.read_text(), "retained")
        self.assertIsNotNone(self.db.get_job("a")["deleted_at"])

    def test_active_job_must_finish_cancellation_before_soft_delete(self):
        self.create_job("a", "user-a")
        with as_user("user-a"):
            with self.assertRaises(HTTPException) as raised:
                self.db.delete_owned_job("user-a", "a")
            self.assertEqual(raised.exception.status_code, 409)
        running = self.delegate.claim_next("worker", 60, 3)
        self.delegate.request_cancel("a")
        with self.assertRaises(HTTPException) as raised:
            self.db.delete_owned_job("user-a", "a")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNone(self.delegate.get_job("a")["deleted_at"])
        self.assertTrue(self.delegate.mark_cancelled("a", worker_id="worker", attempts=running["attempts"]))
        self.assertTrue(self.db.delete_owned_job("user-a", "a")["deleted"])

    def test_upload_owner_guards_all_transport_boundaries(self):
        identifier = str(uuid4())
        content = b"abc"
        digest = hashlib.sha256(content).hexdigest()
        with as_user("user-a"):
            self.uploads.create(identifier, "x.mp4", 3, {})
            directory = self.uploads.directory(identifier)
            self.uploads.put(identifier, 0, content, digest)
        with as_user("user-b"):
            for method, args in ((self.uploads.create, (identifier, "x.mp4", 3, {})),
                (self.uploads.directory, (identifier,)), (self.uploads.read, (directory,)),
                (self.uploads.read_metadata, (directory,)), (self.uploads.status, (identifier,)),
                (self.uploads.put, (identifier, 0, content, digest)),
                (self.uploads.complete, (identifier, None, None)),
                (self.uploads.save, (directory, {})), (self.uploads.remove_parts, (directory,))):
                with self.subTest(method=method.__name__):
                    self.assert_not_found(method, *args)
        self.assertEqual((directory / "0.part").read_bytes(), content)
        with as_user("user-a"):
            self.assertEqual(self.uploads.status(identifier)["received_bytes"], 3)

    def test_failed_upload_creation_retains_owner_for_safe_retry(self):
        identifier = str(uuid4())
        with as_user("user-a"):
            with self.assertRaises(HTTPException) as raised:
                self.uploads.create(identifier, "x.mp4", 0, {})
            self.assertEqual(raised.exception.status_code, 413)
        with as_user("user-b"):
            self.assert_not_found(self.uploads.create, identifier, "x.mp4", 3, {})
        with as_user("user-a"):
            self.assertEqual(self.uploads.create(identifier, "x.mp4", 3, {})["upload_id"], identifier)

    def test_legacy_unowned_directory_and_job_id_cannot_be_claimed(self):
        legacy, empty, existing_job = str(uuid4()), str(uuid4()), str(uuid4())
        self.raw_uploads.create(legacy, "x.mp4", 3, {})
        self.raw_uploads.directory(empty).mkdir()
        self.create_job(existing_job)
        with as_user("user-a"):
            for identifier in (legacy, empty, existing_job):
                self.assert_not_found(self.uploads.create, identifier, "x.mp4", 3, {})
                self.assert_not_found(self.uploads.status, identifier)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(func.count()).select_from(upload_owners)).scalar_one(), 0)

    def test_upload_uuid_claim_is_atomic_between_users(self):
        identifier = str(uuid4())
        ready = threading.Barrier(2)
        def create(actor):
            with as_user(actor):
                ready.wait(timeout=10)
                try:
                    self.uploads.create(identifier, "x.mp4", 3, {})
                    return actor, 200
                except HTTPException as error:
                    return actor, error.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, ("user-a", "user-b")))
        self.assertEqual(sorted(code for actor, code in outcomes), [200, 404])
        winner = next(actor for actor, code in outcomes if code == 200)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_owners.c.user_id)).scalar_one(), winner)

    def test_completed_upload_and_job_share_owner_and_soft_delete_visibility(self):
        identifier = str(uuid4())
        with as_user("user-a"):
            self.uploads.create(identifier, "x.mp4", 3, {})
            self.uploads.put(identifier, 0, b"abc", hashlib.sha256(b"abc").hexdigest())
            def enqueue(merged, metadata):
                self.assertEqual(merged.read_bytes(), b"abc")
                return self.db.create_job(identifier, metadata["filename"], self.root / "input",
                    self.root / "request", self.root / "output")
            job = self.uploads.complete(identifier, enqueue, self.db.get_job)
            self.assertEqual(job["user_id"], "user-a")
            self.assertEqual(self.uploads.complete(identifier, enqueue, self.db.get_job)["id"], identifier)
            self.set_job(identifier, status="cancelled")
            self.db.delete_owned_job("user-a", identifier)
            self.assert_not_found(self.uploads.status, identifier)
            self.assert_not_found(self.uploads.create, identifier, "x.mp4", 3, {})
            self.assert_not_found(assert_upload_owner, self.engine, "user-a", identifier)

    def test_foreign_filesystem_directory_is_rejected(self):
        identifier = str(uuid4())
        with as_user("user-a"):
            self.uploads.create(identifier, "x.mp4", 3, {})
            foreign = self.root / "foreign" / identifier
            foreign.mkdir(parents=True)
            self.assert_not_found(self.uploads.read, foreign)


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
class PostgreSQLOwnershipTests(OwnershipTests):
    postgres = True


if __name__ == "__main__":
    unittest.main()
