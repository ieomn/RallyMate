from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.engine import make_url

from rallymate_service import account_maintenance as maintenance
from rallymate_service.migrations import migrate_database
from rallymate_service.models import admin_audit, jobs, users


class MaintenanceContract:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name).resolve()
        self.root = self.temporary / "data"
        for folder in ("uploads", "requests", "runs"):
            (self.root / folder).mkdir(parents=True)
        self.engine = self.make_engine()
        self.actor = self.add_user("ADMIN", "APPROVED")
        self.owner = self.add_user("USER", "APPROVED")

    def add_user(self, role, status):
        identifier = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(users.insert().values(id=identifier, email=f"{identifier}@example.com", username="fixture", password_hash="fixture",
                role=role, status=status, created_at="2026-11-17T00:00:00+00:00"))
        return identifier

    def job(self, *, deleted=False, owner=None, status="succeeded"):
        identifier = str(uuid4())
        video = self.root / "uploads" / f"{identifier}.mp4"
        request = self.root / "requests" / f"{identifier}.json"
        output = self.root / "runs" / identifier
        output.mkdir()
        (output / "nested").mkdir()
        video.write_bytes(b"fixture-video")
        request.write_text("{}")
        (output / "report.json").write_text('{"score": 87}')
        (output / "nested" / "mesh.bin").write_bytes(b"mesh")
        with self.engine.begin() as connection:
            connection.execute(jobs.insert().values(id=identifier, status=status, original_filename="fixture.mp4", video_path=str(video),
                request_path=str(request), output_dir=str(output), created_at="2026-11-17T00:00:00+00:00", updated_at="2026-11-17T00:00:00+00:00",
                attempts=2, summary_json='{ "score": 87 }', user_id=owner, deleted_at="2026-11-17T01:00:00+00:00" if deleted else None))
        return identifier, video, request, output

    def row(self, identifier):
        with self.engine.connect() as connection:
            return dict(connection.execute(select(jobs).where(jobs.c.id == identifier)).mappings().one())

    def test_assignment_preserves_record_and_has_transactional_audit(self):
        identifier, *_ = self.job()
        before = self.row(identifier)
        result = maintenance.assign_record(self.engine, identifier, self.owner, self.actor)
        self.assertEqual(self.row(identifier), {**before, "user_id": self.owner})
        with self.engine.connect() as connection:
            audit = connection.execute(select(admin_audit).where(admin_audit.c.id == result["audit_id"])).mappings().one()
        self.assertEqual((audit["action"], audit["actor_id"], audit["target_user_id"]), ("assign_record", self.actor, self.owner))
        self.assertEqual(json.loads(audit["details_json"])["job_id"], identifier)
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.assign_record(self.engine, identifier, self.actor, self.actor)
        self.assertEqual(self.row(identifier)["user_id"], self.owner)

    def test_assignment_failure_rolls_back_owner(self):
        identifier, *_ = self.job()
        with patch.object(maintenance, "_audit", side_effect=RuntimeError("audit failed")), self.assertRaises(RuntimeError):
            maintenance.assign_record(self.engine, identifier, self.owner, self.actor)
        self.assertIsNone(self.row(identifier)["user_id"])

    def test_assignment_rejects_nonterminal_deleted_and_inactive_actor(self):
        for changes in ({"status": "running"}, {"status": "queued"}, {"deleted": True}):
            identifier, *_ = self.job(**changes)
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.assign_record(self.engine, identifier, self.owner, self.actor)
        identifier, *_ = self.job()
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.assign_record(self.engine, identifier, self.owner, self.owner)
        with self.engine.begin() as connection:
            connection.execute(update(users).where(users.c.id == self.actor).values(status="SUSPENDED"))
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.assign_record(self.engine, identifier, self.owner, self.actor)
        self.assertIsNone(self.row(identifier)["user_id"])

    def test_concurrent_assignment_cannot_steal_ownership(self):
        identifier, *_ = self.job()
        second = self.add_user("USER", "APPROVED")

        def assign(owner):
            try:
                return maintenance.assign_record(self.engine, identifier, owner, self.actor)["user_id"]
            except maintenance.MaintenanceError:
                return "refused"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, (self.owner, second)))
        self.assertEqual(results.count("refused"), 1)
        self.assertIn(self.row(identifier)["user_id"], results)

    def test_purge_retains_database_statistics_and_supports_safe_retries(self):
        identifier, video, request, output = self.job(deleted=True, owner=self.owner)
        before = self.row(identifier)
        result = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertTrue(result["complete"])
        self.assertEqual((result["removed_files"], result["removed_directories"], result["shared_targets"]), (4, 2, 0))
        self.assertTrue(all(not path.exists() for path in (video, request, output)))
        self.assertEqual(self.row(identifier), before)
        again = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertEqual((again["removed_files"], again["missing_targets"]), (0, 3))
        with self.engine.connect() as connection:
            actions = connection.execute(select(admin_audit.c.action).order_by(admin_audit.c.created_at)).scalars().all()
        self.assertEqual(actions, ["purge_record_requested", "purge_record", "purge_record_requested", "purge_record"])

    def test_purge_requires_deleted_terminal_job_and_admin(self):
        for changes in ({}, {"deleted": True, "status": "running"}, {"deleted": True, "status": "queued"}):
            identifier, video, request, output = self.job(**changes)
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.purge_record(self.engine, identifier, self.actor, self.root)
            self.assertTrue(all(path.exists() for path in (video, request, output)))
        identifier, video, *_ = self.job(deleted=True)
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.purge_record(self.engine, identifier, self.owner, self.root)
        self.assertTrue(video.exists())

    def test_all_targets_are_preflighted_before_any_file_is_removed(self):
        identifier, video, request, output = self.job(deleted=True)
        outside = self.temporary / "outside.json"
        outside.write_text("preserve")
        for bad_path in (str(outside), str(self.root / "requests"), "relative.json", str(self.root / "requests" / ".." / "outside.json")):
            with self.engine.begin() as connection:
                connection.execute(update(jobs).where(jobs.c.id == identifier).values(request_path=bad_path))
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.purge_record(self.engine, identifier, self.actor, self.root)
            self.assertTrue(all(path.exists() for path in (video, request, output, outside)))

    def test_shared_recovery_video_and_nested_output_reference_are_retained(self):
        identifier, video, request, output = self.job(deleted=True)
        other, *_ = self.job(status="running")
        with self.engine.begin() as connection:
            connection.execute(update(jobs).where(jobs.c.id == other).values(video_path=str(video), output_dir=str(output / "nested")))
        result = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertEqual((result["shared_targets"], result["shared_references"], result["removed_files"]), (2, 2, 1))
        self.assertTrue(video.exists())
        self.assertTrue(output.exists())
        self.assertFalse(request.exists())

    def test_linked_entry_anywhere_in_output_tree_refuses_the_entire_purge(self):
        identifier, video, request, output = self.job(deleted=True)
        external = self.temporary / "external.bin"
        external.write_bytes(b"preserve")
        link = output / "nested" / "link.bin"
        try:
            link.symlink_to(external)
        except OSError:
            if os.name != "nt":
                raise
            linked_directory = self.temporary / "external-linked-directory"
            linked_directory.mkdir()
            link = output / "nested" / "linked-directory"
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(linked_directory)], check=True, capture_output=True)
        self.addCleanup(lambda: link.rmdir() if link.is_dir() else link.unlink())
        with self.assertRaisesRegex(maintenance.MaintenanceError, "links"):
            maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertTrue(all(path.exists() for path in (video, request, external)))

    def test_junction_ancestor_refuses_the_entire_purge(self):
        if os.name != "nt":
            self.skipTest("Windows junction verification")
        identifier, video, request, output = self.job(deleted=True)
        external = self.temporary / "external-folder"
        external.mkdir()
        (external / "video.mp4").write_bytes(b"preserve")
        link = self.root / "uploads" / "junction"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], check=True, capture_output=True)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.engine.begin() as connection:
            connection.execute(update(jobs).where(jobs.c.id == identifier).values(video_path=str(link / "video.mp4")))
        with self.assertRaisesRegex(maintenance.MaintenanceError, "junctions"):
            maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertTrue(all(path.exists() for path in (video, request, output, external / "video.mp4")))

    def test_directory_swap_after_final_stat_preserves_external_and_original_files(self):
        identifier, video, request, output = self.job(deleted=True, owner=self.owner)
        uploads = video.parent
        saved_uploads = self.temporary / "original-uploads"
        external = self.temporary / "outside-data-root"
        external.mkdir()
        external_video = external / video.name
        external_video.write_bytes(b"outside root must remain")
        original_remove = maintenance._remove_entry
        original_stat = maintenance._checked_stat
        swapped = False

        def remove_with_directory_swap(entry, root):
            if entry.path != video:
                return original_remove(entry, root)
            calls = 0

            def stat_then_swap(path):
                nonlocal calls, swapped
                info = original_stat(path)
                if path == video:
                    calls += 1
                    # First lstat is the ancestor check. Swap after the final
                    # identity check, before the actual deletion opens a handle.
                    if calls == 2:
                        for target in (uploads, saved_uploads, external):
                            self.assertIn(self.temporary, target.resolve().parents)
                        uploads.rename(saved_uploads)
                        if os.name == "nt":
                            subprocess.run(["cmd", "/c", "mklink", "/J", str(uploads), str(external)],
                                           check=True, capture_output=True)
                        else:
                            uploads.symlink_to(external, target_is_directory=True)
                        swapped = True
                return info

            with patch.object(maintenance, "_checked_stat", side_effect=stat_then_swap):
                return original_remove(entry, root)

        try:
            with patch.object(maintenance, "_remove_entry", side_effect=remove_with_directory_swap):
                result = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
            self.assertTrue(swapped)
            self.assertFalse(result["complete"])
            self.assertEqual((result["removed_files"], result["removed_directories"]), (0, 0))
            self.assertEqual(external_video.read_bytes(), b"outside root must remain")
            self.assertEqual((saved_uploads / video.name).read_bytes(), b"fixture-video")
            self.assertTrue(request.exists())
            self.assertTrue(output.exists())
            with self.engine.connect() as connection:
                details = connection.execute(select(admin_audit.c.details_json).where(
                    admin_audit.c.id == result["audit_id"])).scalar_one()
            self.assertFalse(json.loads(details)["complete"])
        finally:
            if swapped:
                # Remove only the link itself; TemporaryDirectory owns both trees.
                if os.name == "nt":
                    uploads.rmdir()
                else:
                    uploads.unlink()

    def test_partial_filesystem_failure_is_audited_and_can_resume(self):
        identifier, video, request, output = self.job(deleted=True)
        original = maintenance._remove_entry

        def remove(entry, root):
            if entry.path == request:
                raise PermissionError("fixture is locked")
            return original(entry, root)

        with patch.object(maintenance, "_remove_entry", side_effect=remove):
            result = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertFalse(result["complete"])
        self.assertEqual(result["removed_files"], 1)
        self.assertFalse(video.exists())
        self.assertTrue(request.exists())
        self.assertTrue(output.exists())
        with self.engine.connect() as connection:
            details = connection.execute(select(admin_audit.c.details_json).where(admin_audit.c.id == result["audit_id"])).scalar_one()
        self.assertFalse(json.loads(details)["complete"])
        resumed = maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertTrue(resumed["complete"])
        self.assertEqual(resumed["missing_targets"], 1)

    def test_request_audit_survives_failure_after_files_are_deleted(self):
        identifier, video, request, output = self.job(deleted=True)
        original = maintenance._audit

        def audit(connection, actor, target, action, details):
            if action == "purge_record":
                raise RuntimeError("fixture completion failure")
            return original(connection, actor, target, action, details)

        with patch.object(maintenance, "_audit", side_effect=audit), self.assertRaises(RuntimeError):
            maintenance.purge_record(self.engine, identifier, self.actor, self.root)
        self.assertTrue(all(not path.exists() for path in (video, request, output)))
        self.assertIsNotNone(self.row(identifier)["deleted_at"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(admin_audit.c.action)).scalar_one(), "purge_record_requested")


class SQLiteMaintenanceTests(MaintenanceContract, unittest.TestCase):
    def make_engine(self):
        self.url = f"sqlite:///{(self.temporary / 'jobs.sqlite3').as_posix()}"
        migrate_database(self.url)
        engine = create_engine(self.url, connect_args={"timeout": 30})
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine

    def test_cli_assign_and_missing_database_never_creates_a_database(self):
        identifier, *_ = self.job()
        output, errors = io.StringIO(), io.StringIO()
        args = ["assign-record", "--job-id", identifier, "--user-id", self.owner, "--actor-id", self.actor]
        with patch.dict(os.environ, {"DATABASE_URL": self.url}), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(maintenance.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["user_id"], self.owner)
        missing = self.temporary / "missing.sqlite3"
        with patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{missing.as_posix()}"}), redirect_stderr(errors):
            self.assertEqual(maintenance.main(args), 2)
        self.assertFalse(missing.exists())

    def test_cli_unmigrated_database_is_not_changed_and_errors_hide_url_secrets(self):
        legacy = self.temporary / "unmigrated.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute("CREATE TABLE untouched (id TEXT)")
            connection.commit()
        errors = io.StringIO()
        args = ["assign-record", "--job-id", "job", "--user-id", "user", "--actor-id", "actor"]
        with patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{legacy.as_posix()}"}), redirect_stderr(errors):
            self.assertEqual(maintenance.main(args), 2)
        with closing(sqlite3.connect(legacy)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("untouched",)])
        secret = "postgresql://user:secret-password@localhost/db"
        with patch.dict(os.environ, {"DATABASE_URL": secret}), patch.object(maintenance, "_open_engine", side_effect=ValueError(secret)), redirect_stderr(errors):
            self.assertEqual(maintenance.main(args), 2)
        self.assertNotIn("secret-password", errors.getvalue())
        self.assertNotIn(secret, errors.getvalue())


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
class PostgresMaintenanceTests(MaintenanceContract, unittest.TestCase):
    def make_engine(self):
        url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        admin = create_engine(url)
        self.addCleanup(admin.dispose)
        schema = f"maintenance_{uuid4().hex}"
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

        def cleanup():
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

        self.addCleanup(cleanup)
        scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
        migrate_database(scoped)
        engine = create_engine(scoped)
        self.addCleanup(engine.dispose)
        return engine


if __name__ == "__main__":
    unittest.main()
