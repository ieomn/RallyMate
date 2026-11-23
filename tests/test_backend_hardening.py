"""Wave-1 backend hardening: per-account fairness, upload lifecycle, report
builder bounds, lazy ASGI app, digest memoization, worker stop signals."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL

from rallymate_service import report_delivery_cache, uploads as uploads_module
from rallymate_service.database import JobDatabase, QueueCapacityError, UserQueueCapacityError, utc_now
from rallymate_service.migrations import migrate_database
from rallymate_service.models import auth_rate_limits, jobs, users
from rallymate_service.ownership import UserScopedJobDatabase, current_user_id
from rallymate_service.report_delivery_cache import artifact_signature
from rallymate_service.uploads import MIN_CHUNK_BYTES, SETTLED_MARKER, TOMBSTONE_RETENTION_SECONDS, UploadStore

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def as_user(user_id):
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class UploadLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = UploadStore(Path(temporary.name), 1024 * 1024, chunk_bytes=MIN_CHUNK_BYTES)

    def _session(self, size=3):
        upload_id = str(uuid.uuid4())
        self.store.create(upload_id, "a.mp4", size, {})
        return upload_id

    def test_cancel_releases_parts_and_tombstones_the_session(self):
        upload_id = self._session()
        self.store.put(upload_id, 0, b"abc", _digest(b"abc"))
        directory = self.store.directory(upload_id)
        self.assertTrue((directory / "0.part").exists())
        self.assertEqual(self.store.cancel(upload_id), {"upload_id": upload_id, "cancelled": True})
        self.assertFalse((directory / "0.part").exists())
        self.assertTrue((directory / SETTLED_MARKER).exists())
        for call in (lambda: self.store.status(upload_id),
                     lambda: self.store.put(upload_id, 0, b"abc", _digest(b"abc")),
                     lambda: self.store.complete(upload_id, lambda *_: self.fail("must not enqueue"), lambda _: None),
                     lambda: self.store.create(upload_id, "a.mp4", 3, {})):
            with self.assertRaises(HTTPException) as raised:
                call()
            self.assertEqual(raised.exception.status_code, 410)
        # Idempotent.
        self.assertEqual(self.store.cancel(upload_id)["cancelled"], True)

    def test_cancel_never_touches_a_submitted_upload_and_unknown_is_404(self):
        upload_id = self._session()
        self.store.put(upload_id, 0, b"abc", _digest(b"abc"))
        self.assertEqual(self.store.complete(upload_id, lambda *_: {"id": upload_id}, lambda _: None), {"id": upload_id})
        with self.assertRaises(HTTPException) as raised:
            self.store.cancel(upload_id)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.store.status(upload_id)["job_id"], upload_id)
        with self.assertRaises(HTTPException) as missing:
            self.store.cancel(str(uuid.uuid4()))
        self.assertEqual(missing.exception.status_code, 404)

    def test_settled_sessions_are_not_reopened_by_admission(self):
        completed = []
        for _ in range(5):
            upload_id = self._session()
            self.store.put(upload_id, 0, b"abc", _digest(b"abc"))
            self.store.complete(upload_id, lambda *_: {"id": "x"}, lambda _: None)
            completed.append(self.store.directory(upload_id))
        live = self._session()
        opened = []
        original = self.store._unchecked_metadata

        def spy(directory):
            opened.append(Path(directory))
            return original(directory)

        self.store._last_cleanup = None
        with patch.object(self.store, "_unchecked_metadata", side_effect=spy):
            self._session()
        self.assertFalse(set(opened) & set(completed))
        self.assertIn(self.store.directory(live), opened)

    def test_creation_cleanup_is_throttled_but_explicit_cleanup_is_not(self):
        self._session()
        with patch.object(self.store, "cleanup", wraps=self.store.cleanup) as cleanup:
            self._session()
            self._session()
        cleanup.assert_not_called()
        self.assertEqual(self.store.cleanup(), 0)

    def test_old_tombstones_are_pruned(self):
        upload_id = self._session()
        self.store.cancel(upload_id)
        directory = self.store.directory(upload_id)
        old = time.time() - TOMBSTONE_RETENTION_SECONDS - 10
        os.utime(directory / SETTLED_MARKER, (old, old))
        fresh = self._session()
        self.store.cancel(fresh)
        self.store.cleanup()
        self.assertFalse(directory.exists())
        self.assertTrue(self.store.directory(fresh).exists())
        with self.assertRaises(HTTPException) as raised:
            self.store.status(upload_id)
        self.assertEqual(raised.exception.status_code, 404)

    def test_admission_callback_sees_live_ids_and_can_reject(self):
        first = self._session()
        seen = []

        def admit(active_ids):
            seen.append(list(active_ids))
            raise HTTPException(429, "limit")

        rejected = str(uuid.uuid4())
        with self.assertRaises(HTTPException):
            self.store.create(rejected, "b.mp4", 3, {}, admit=admit)
        self.assertEqual(seen, [[first]])
        self.assertFalse((self.store.directory(rejected) / "state.sqlite3").exists())


class AccountFairnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
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
        self.db = UserScopedJobDatabase(self.delegate, self.engine, max_active_jobs_per_user=2)

    def create(self, identifier, user_id, **kwargs):
        with as_user(user_id):
            return self.db.create_job(identifier, "f.mp4", self.root / identifier, self.root / identifier,
                                      self.root / identifier, max_active_jobs=32, **kwargs)

    def test_one_account_cannot_take_the_whole_queue(self):
        self.create("a1", "user-a")
        self.create("a2", "user-a")
        with self.assertRaises(UserQueueCapacityError) as raised:
            self.create("a3", "user-a")
        self.assertIsInstance(raised.exception, QueueCapacityError)
        self.assertEqual(raised.exception.limit, 2)
        self.create("b1", "user-b")
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == "a1").values(status="succeeded"))
        self.create("a3", "user-a")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(jobs.c.id).where(jobs.c.user_id == "user-a")
                                                ).scalars().all().__len__(), 3)

    def test_list_jobs_uses_one_query_and_hides_deleted(self):
        for name in ("a1", "a2"):
            self.create(name, "user-a")
        self.create("b1", "user-b")
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == "a1").values(deleted_at=utc_now()))
        with as_user("user-a"), patch.object(self.delegate, "get_job", side_effect=AssertionError("N+1")):
            self.assertEqual([job["id"] for job in self.db.list_jobs()], ["a2"])

    def test_soft_deleted_job_is_hidden_without_request_actor(self):
        self.create("a1", "user-a")
        self.assertIsNotNone(self.db.get_job("a1"))
        self.assertIsNotNone(self.db.execution_state("a1"))
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == "a1").values(status="succeeded", deleted_at=utc_now()))
        # Report-preparation threads run without the request ContextVar.
        # Raw get_job keeps deleted rows visible to internal callers (with
        # deleted_at); the polling projection used by delivery hides them.
        self.assertIsNotNone(self.db.get_job("a1")["deleted_at"])
        self.assertIsNone(self.db.execution_state("a1"))
        with as_user("user-a"):
            self.assertIsNone(self.db.execution_state("a1"))
        with as_user("user-b"):
            self.create("b1", "user-b")
            self.assertIsNone(self.db.execution_state("a1"))
            self.assertIsNotNone(self.db.execution_state("b1"))

    def test_expired_auth_rate_limit_rows_are_pruned(self):
        from rallymate_service.accounts import AccountStore
        store = AccountStore(self.engine, "x" * 16 + "abcdefghijklmnopqrstuvwxyz")
        with self.engine.begin() as connection:
            connection.execute(auth_rate_limits.insert(), [
                {"key": "old", "window_start": "2000-01-01T00:00:00+00:00", "attempts": 3}])
        store._rate_limit("login:email:someone@example.com", 10, 900)
        with self.engine.connect() as connection:
            keys = connection.execute(select(auth_rate_limits.c.key)).scalars().all()
        self.assertNotIn("old", keys)
        self.assertEqual(len(keys), 1)


class ApiHardeningTests(unittest.TestCase):
    def _app(self, root: Path, **overrides):
        from rallymate_service.api import create_app
        from rallymate_service.config import ServiceSettings
        from tests.service_auth import test_auth_config
        detect = root / "detect.pt"; detect.touch()
        pose = root / "pose.pt"; pose.touch()
        settings = ServiceSettings(data_root=root / "data", database_path=root / "jobs.sqlite3",
                                   detect_model=detect, pose_model=pose, **overrides)
        settings.ensure_directories()
        database = JobDatabase(settings.database_path)
        return create_app(settings, database, auth_config=test_auth_config()), settings, database

    def test_per_user_upload_limit_and_owner_cancel(self):
        from tests.service_auth import authenticated_client
        with tempfile.TemporaryDirectory() as directory:
            app, _, _ = self._app(Path(directory), max_active_uploads_per_user=1)
            with authenticated_client(app) as client:
                def create(upload_id):
                    return client.post("/v1/uploads", json={"upload_id": upload_id, "filename": "a.mp4", "size": 3})
                first, second = str(uuid.uuid4()), str(uuid.uuid4())
                self.assertEqual(create(first).status_code, 201)
                # Resuming one's own session is not a new slot.
                self.assertEqual(create(first).status_code, 201)
                blocked = create(second)
                self.assertEqual(blocked.status_code, 429, blocked.text)
                self.assertEqual(blocked.json()["detail"]["code"], "upload_user_limit")
                cancelled = client.delete(f"/v1/uploads/{first}")
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json(), {"upload_id": first, "cancelled": True})
                self.assertEqual(client.delete(f"/v1/uploads/{first}").status_code, 200)
                self.assertEqual(client.get(f"/v1/uploads/{first}").status_code, 410)
                self.assertEqual(create(second).status_code, 201)
                self.assertEqual(client.delete(f"/v1/uploads/{uuid.uuid4()}").status_code, 404)

    def test_meta_reports_mandatory_account_auth_and_segmented_delivery_is_scoped(self):
        from tests.service_auth import authenticated_client
        with tempfile.TemporaryDirectory() as directory:
            app, _, _ = self._app(Path(directory))
            self.assertIs(app.state.segmented_delivery.db, app.state.database)
            self.assertIsInstance(app.state.database, UserScopedJobDatabase)
            with authenticated_client(app) as client:
                authentication = client.get("/v1/meta").json()["authentication"]
            self.assertEqual(authentication["scheme"], "account_session")
            self.assertIs(authentication["required"], True)
            self.assertIs(authentication["maintenance_bearer"], False)

    def test_heavy_report_builders_are_bounded(self):
        import dataclasses
        from rallymate_service import api
        from tests.service_auth import authenticated_client, test_auth_config
        from tests.test_report_delivery_api import setup_report
        with tempfile.TemporaryDirectory() as directory:
            _, settings, database, job = setup_report(Path(directory))
            # Same fixture data, served by an app with a single builder slot.
            app = api.create_app(dataclasses.replace(settings, heavy_report_concurrency=1), database,
                                 auth_config=test_auth_config())
            entered, release = threading.Event(), threading.Event()

            def slow(*_args, **_kwargs):
                entered.set()
                release.wait(5)
                return {"ok": True}

            with patch.object(api, "build_tennis_assessment", side_effect=slow), \
                    patch.object(api, "HEAVY_REPORT_WAIT_SECONDS", 0.05), \
                    authenticated_client(app, job_ids=(job,)) as client:
                results = {}
                worker = threading.Thread(target=lambda: results.setdefault(
                    "first", client.get(f"/v1/jobs/{job}/tennis-assessment")))
                worker.start()
                try:
                    self.assertTrue(entered.wait(5))
                    busy = client.get(f"/v1/jobs/{job}/swing-kinematics")
                    self.assertEqual(busy.status_code, 429, busy.text)
                    self.assertEqual(busy.json()["detail"]["code"], "report_builder_busy")
                    self.assertEqual(busy.headers.get("Retry-After"), "5")
                finally:
                    release.set()
                    worker.join(10)
                self.assertEqual(results["first"].status_code, 200, results["first"].text)


class LazyAppTests(unittest.TestCase):
    def test_import_does_not_build_app_or_require_secrets(self):
        environment = {key: value for key, value in os.environ.items() if key != "JWT_SECRET"}
        environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
        completed = subprocess.run([sys.executable, "-c",
            "import rallymate_service.api as a; print('_default_app' in vars(a) and a._default_app is None);"
            "print(callable(a.create_app))"], capture_output=True, text=True, env=environment, timeout=300)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertEqual(completed.stdout.split(), ["True", "True"])

    def test_unknown_attribute_still_raises(self):
        from rallymate_service import api
        with self.assertRaises(AttributeError):
            api.no_such_attribute  # noqa: B018


class DigestMemoTests(unittest.TestCase):
    def setUp(self):
        report_delivery_cache._digest_memo.clear()
        self.addCleanup(report_delivery_cache._digest_memo.clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "frames.jsonl"
        self.path.write_bytes(b'{"frame":1}\n')

    def test_posix_memo_reuses_digest_for_identical_stat_and_invalidates_on_change(self):
        with patch.object(report_delivery_cache, "_DIGEST_MEMO_ENABLED", True), \
                patch.object(report_delivery_cache, "_DIGEST_MEMO_MIN_AGE_NS", -1):
            first = artifact_signature(self.path, content_hash=True)
            with patch.object(Path, "open", side_effect=AssertionError("re-read")):
                self.assertEqual(artifact_signature(self.path, content_hash=True), first)
            self.path.write_bytes(b'{"frame":22}\n')
            changed = artifact_signature(self.path, content_hash=True)
            self.assertNotEqual(changed[-1], first[-1])
            self.assertEqual(changed[-1], _digest(b'{"frame":22}\n'))

    def test_recently_changed_files_are_not_memoized(self):
        with patch.object(report_delivery_cache, "_DIGEST_MEMO_ENABLED", True):
            artifact_signature(self.path, content_hash=True)
        self.assertEqual(len(report_delivery_cache._digest_memo), 0)

    def test_memo_is_bounded(self):
        with patch.object(report_delivery_cache, "_DIGEST_MEMO_ENABLED", True), \
                patch.object(report_delivery_cache, "_DIGEST_MEMO_MIN_AGE_NS", -1), \
                patch.object(report_delivery_cache, "_DIGEST_MEMO_MAX_ENTRIES", 3):
            for index in range(6):
                self.path.write_bytes(b"x" * (index + 1))
                artifact_signature(self.path, content_hash=True)
            self.assertEqual(len(report_delivery_cache._digest_memo), 3)

    def test_disabled_memo_detects_same_size_restored_mtime_rewrite(self):
        with patch.object(report_delivery_cache, "_DIGEST_MEMO_ENABLED", False):
            first = artifact_signature(self.path, content_hash=True)
            stat = self.path.stat()
            with self.path.open("r+b") as stream:
                stream.write(b'{"frame":2}\n')
            os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(artifact_signature(self.path, content_hash=True)[-1], first[-1])
        self.assertEqual(len(report_delivery_cache._digest_memo), 0)


class WorkerSignalTests(unittest.TestCase):
    def test_first_signal_requests_stop_second_interrupts(self):
        from rallymate_service.cli import install_stop_signals
        stop = threading.Event()
        previous = install_stop_signals(stop)
        try:
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)
            self.assertTrue(stop.is_set())
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        finally:
            for number, original in previous.items():
                signal.signal(number, original)

    def test_worker_main_passes_stop_event(self):
        from rallymate_service import cli
        captured = {}
        with patch.object(sys, "argv", ["rallymate-worker", "--once"]), \
                patch.object(cli, "load_settings"), patch.object(cli, "build_database"), \
                patch.object(cli, "install_stop_signals") as install, \
                patch("rallymate_service.observability.observe_worker") as observe, \
                patch.object(cli, "run_worker", side_effect=lambda *a, **kw: captured.update(kw)):
            observe.return_value.__enter__.return_value = None
            cli.worker_main()
        self.assertIsInstance(captured["stop_event"], threading.Event)
        install.assert_called_once_with(captured["stop_event"])


class FfmpegResolutionTests(unittest.TestCase):
    def test_system_ffmpeg_needs_no_imageio_wheel(self):
        from rallymate_service import gs_card_execution
        with patch.object(gs_card_execution.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                patch.dict(sys.modules, {"imageio_ffmpeg": None}):
            self.assertEqual(gs_card_execution._ffmpeg_executable(), "/usr/bin/ffmpeg")

    def test_missing_decoder_is_a_value_error_code(self):
        from rallymate_service import gs_card_execution
        with patch.object(gs_card_execution.shutil, "which", return_value=None), \
                patch.dict(sys.modules, {"imageio_ffmpeg": None}):
            with self.assertRaises(ValueError) as raised:
                gs_card_execution._ffmpeg_executable()
        self.assertEqual(str(raised.exception), "source_video_decoder_unavailable")


if __name__ == "__main__":
    unittest.main()
