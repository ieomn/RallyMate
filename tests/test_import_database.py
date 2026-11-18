from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from rallymate_service import import_database as importer
from rallymate_service.migrations import SCHEMA_HEAD, migrate_database
from rallymate_service.models import jobs, metadata


RAW_SUMMARY = '{ "中文": "网球", "escaped": "\\u0061", "nested": {"score": 1.0} }\n'
MAPPINGS = (importer.PathMapping(r"D:\RallyMate", "/srv/rallymate"),)


class ImportDatabaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.sqlite3"
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, original_filename TEXT NOT NULL,
                video_path TEXT NOT NULL, request_path TEXT NOT NULL, output_dir TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                completed_at TEXT, lease_expires_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT, progress_json TEXT, summary_json TEXT, error TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0, cancel_requested_at TEXT,
                cancelled_at TEXT)""")
            for identifier, status in (("old-a", "succeeded"), ("old-b", "failed"), ("old-c", "cancelled")):
                connection.execute("""INSERT INTO jobs (id,status,original_filename,video_path,
                    request_path,output_dir,created_at,updated_at,summary_json,progress_json,attempts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (identifier, status, "练习.mp4",
                    rf"D:\RallyMate\videos\{identifier}.mp4", rf"D:\RallyMate\requests\{identifier}.json",
                    rf"D:\RallyMate\outputs\{identifier}", "2026-11-17T00:00:00+00:00",
                    "2026-11-17T01:00:00+00:00", RAW_SUMMARY, '{ "step": 2 }', 2))
            connection.commit()
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": SCHEMA_HEAD})

    def update_source(self, sql, parameters=()):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute(sql, parameters)
            connection.commit()

    def snapshot(self):
        return importer._read_snapshot(self.source, MAPPINGS)

    def import_fixture(self):
        rows, manifest = self.snapshot()
        with self.engine.begin() as connection:
            result = importer._write_snapshot(connection, rows, manifest["mapped"])
        return result

    def test_inspect_preserves_original_bytes_and_raw_json(self):
        before = self.source.read_bytes()
        rows, manifest = self.snapshot()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(manifest["eligible"])
        self.assertEqual(manifest["source"]["job_count"], 3)
        self.assertEqual(manifest["source"]["status_counts"], {"succeeded": 1, "failed": 1, "cancelled": 1})
        self.assertEqual(manifest["mapped_path_count"], 9)
        self.assertEqual(rows[0]["summary_json"], RAW_SUMMARY)
        self.assertEqual(rows[0]["video_path"], "/srv/rallymate/videos/old-a.mp4")
        self.assertTrue(all(row["user_id"] is None for row in rows))
        self.assertEqual(manifest["source"]["ids_sha256"], manifest["mapped"]["ids_sha256"])
        self.assertEqual(manifest["source"]["summaries_sha256"], manifest["mapped"]["summaries_sha256"])
        self.assertNotEqual(manifest["source"]["paths_sha256"], manifest["mapped"]["paths_sha256"])

    def test_prefix_mapping_case_boundaries_longest_match_and_traversal(self):
        maps = (*MAPPINGS, importer.PathMapping(r"D:\RallyMate\videos", "/mnt/videos"))
        self.assertEqual(importer._map_path(r"d:/rallymate/videos/练习.mp4", maps), "/mnt/videos/练习.mp4")
        self.assertEqual(importer._map_path("/srv/existing.mp4", ()), "/srv/existing.mp4")
        for path in (r"D:\RallyMateElse\x.mp4", r"D:\RallyMate\..\x.mp4", "relative.mp4", r"D:x.mp4"):
            with self.subTest(path=path), self.assertRaises(importer.DatabaseImportError):
                importer._map_path(path, maps)
        for before, after in (("relative", "/srv/data"), ("D:/data", "relative"), ("D:/data", "/srv/../escape")):
            with self.subTest(before=before, after=after), self.assertRaises(importer.DatabaseImportError):
                importer.PathMapping(before, after)
        with self.assertRaises(importer.DatabaseImportError):
            importer._read_snapshot(self.source, (*MAPPINGS, importer.PathMapping("d:/rallymate", "/other")))

    def test_dry_run_never_creates_engine_and_does_not_print_url(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        secret = "postgresql://operator:secret-password@example.invalid/db"
        with patch.dict(os.environ, {"DATABASE_URL": secret}), patch.object(importer, "create_engine") as create:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = importer.main(["--source", str(self.source), "--dry-run", "--path-map", r"D:\RallyMate=/srv/rallymate"])
        self.assertEqual(code, 0)
        create.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())["mapped"]["job_count"], 3)
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())

    def test_active_jobs_rejected_before_target_connection(self):
        for status in ("queued", "running"):
            self.update_source("UPDATE jobs SET status=? WHERE id='old-a'", (status,))
            self.assertFalse(importer.inspect_database(self.source, MAPPINGS)["eligible"])
            with patch.object(importer, "create_engine") as create, self.assertRaisesRegex(importer.DatabaseImportError, "queued or running"):
                importer.import_database(self.source, "postgresql://unused", MAPPINGS)
            create.assert_not_called()

    def test_source_account_and_session_rows_rejected(self):
        for table in importer.AUTH_TABLES:
            with self.subTest(table=table):
                self.update_source(f"CREATE TABLE {table}(id TEXT PRIMARY KEY)")
                self.update_source(f"INSERT INTO {table} VALUES ('account-row')")
                before = self.source.read_bytes()
                with patch.object(importer, "create_engine") as create, self.assertRaisesRegex(importer.DatabaseImportError, "account or ownership"):
                    importer.import_database(self.source, "postgresql://unused", MAPPINGS)
                create.assert_not_called()
                self.assertEqual(self.source.read_bytes(), before)
                self.update_source(f"DROP TABLE {table}")

    def test_non_null_legacy_owner_is_not_discarded(self):
        self.update_source("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        self.update_source("UPDATE jobs SET user_id='owner' WHERE id='old-a'")
        with self.assertRaisesRegex(importer.DatabaseImportError, "account-era"):
            importer.import_database(self.source, "postgresql://unused", MAPPINGS)

    def test_unknown_columns_are_not_silently_discarded(self):
        self.update_source("ALTER TABLE jobs ADD COLUMN extra_evidence TEXT")
        with self.assertRaisesRegex(importer.DatabaseImportError, "unsupported columns"):
            self.snapshot()

    def test_non_postgres_target_rejected(self):
        with self.assertRaisesRegex(importer.DatabaseImportError, "must be PostgreSQL"):
            importer.import_database(self.source, "sqlite:///unused.sqlite3", MAPPINGS)
        self.assertFalse((self.root / "unused.sqlite3").exists())

    def test_import_exact_records_idempotence_and_immutable_source(self):
        before = self.source.read_bytes()
        first, second = self.import_fixture(), self.import_fixture()
        self.assertEqual(first["inserted"], 3)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["already_present"], 3)
        self.assertEqual(first["verified"], second["verified"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(jobs.c.summary_json).where(jobs.c.id == "old-a")).scalar_one(), RAW_SUMMARY)
        self.assertEqual(self.source.read_bytes(), before)

    def test_conflicting_or_partial_target_leaves_all_rows_unchanged(self):
        self.import_fixture()
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == "old-a").values(summary_json='{"target":true}'))
            connection.execute(jobs.delete().where(jobs.c.id == "old-b"))
        with self.engine.connect() as connection:
            before = list(connection.execute(select(jobs)).mappings())
        with self.assertRaisesRegex(importer.DatabaseImportError, "differ"):
            self.import_fixture()
        with self.engine.connect() as connection:
            self.assertEqual(list(connection.execute(select(jobs)).mappings()), before)

    def test_failed_verification_rolls_back_inserted_rows(self):
        with self.engine.begin() as connection:
            connection.execute(text("""CREATE TRIGGER corrupt_import AFTER INSERT ON jobs BEGIN
                UPDATE jobs SET summary_json='corrupted' WHERE id=NEW.id; END"""))
        with self.assertRaisesRegex(importer.DatabaseImportError, "verification failed"):
            self.import_fixture()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(jobs.c.id)).all(), [])

    def test_target_account_rows_and_wrong_schema_rejected(self):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE sessions"))
            connection.execute(text("CREATE TABLE sessions(id TEXT PRIMARY KEY)"))
            connection.execute(text("INSERT INTO sessions VALUES ('existing-session')"))
        with self.assertRaisesRegex(importer.DatabaseImportError, "Target contains account"):
            self.import_fixture()
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM sessions"))
            connection.execute(text("UPDATE alembic_version SET version_num='old'"))
        with self.assertRaisesRegex(importer.DatabaseImportError, "schema revision"):
            self.import_fixture()

    def test_wal_snapshot_includes_uncheckpointed_changes_and_is_consistent(self):
        real_connect = sqlite3.connect
        with closing(real_connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE jobs SET summary_json='wal-value' WHERE id='old-a'")
            writer.commit()
            class ConcurrentWriter:
                def __init__(self, connection):
                    self.connection = connection
                @property
                def row_factory(self):
                    return self.connection.row_factory
                @row_factory.setter
                def row_factory(self, value):
                    self.connection.row_factory = value
                def execute(self, sql, *args):
                    result = self.connection.execute(sql, *args)
                    if sql == "PRAGMA table_info(jobs)":
                        writer.execute("UPDATE jobs SET summary_json='later-value' WHERE id='old-a'")
                        writer.commit()
                    return result
                def rollback(self):
                    self.connection.rollback()
                def close(self):
                    self.connection.close()
            with patch.object(importer.sqlite3, "connect", side_effect=lambda *args, **kwargs: ConcurrentWriter(real_connect(*args, **kwargs))):
                rows, _ = self.snapshot()
            self.assertEqual(rows[0]["summary_json"], "wal-value")
            self.assertEqual(writer.execute("SELECT summary_json FROM jobs WHERE id='old-a'").fetchone()[0], "later-value")

    def test_cli_suppresses_driver_credentials(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused"}), patch.object(importer, "import_database", side_effect=RuntimeError("postgresql://user:secret@host/db")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = importer.main(["--source", str(self.source)])
        self.assertEqual(code, 2)
        self.assertNotIn("secret", stdout.getvalue() + stderr.getvalue())

    @unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
    def test_real_postgresql_import_idempotence_conflict_and_rollback(self):
        url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        schema = "import_test_" + uuid4().hex
        base = create_engine(url)
        self.addCleanup(base.dispose)
        with base.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop_schema():
            with base.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        self.addCleanup(drop_schema)
        query = dict(url.query)
        query["options"] = str(query.get("options", "")) + f" -csearch_path={schema}"
        scoped_url = url.set(query=query).render_as_string(hide_password=False)
        migrate_database(scoped_url)
        engine = create_engine(scoped_url)
        self.addCleanup(engine.dispose)
        before = self.source.read_bytes()
        first = importer.import_database(self.source, scoped_url, MAPPINGS)
        second = importer.import_database(self.source, scoped_url, MAPPINGS)
        self.assertEqual(first["inserted"], 3)
        self.assertEqual(second["already_present"], 3)
        self.assertEqual(first["verified"], first["mapped"])
        with engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == "old-a").values(summary_json="target-data"))
        with self.assertRaisesRegex(importer.DatabaseImportError, "differ"):
            importer.import_database(self.source, scoped_url, MAPPINGS)
        with engine.begin() as connection:
            self.assertEqual(connection.execute(select(jobs.c.summary_json).where(jobs.c.id == "old-a")).scalar_one(), "target-data")
            connection.execute(jobs.delete())
            connection.execute(text("""CREATE FUNCTION corrupt_import() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN NEW.summary_json := 'corrupted'; RETURN NEW; END $$"""))
            connection.execute(text("CREATE TRIGGER corrupt_import BEFORE INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION corrupt_import()"))
        with self.assertRaisesRegex(importer.DatabaseImportError, "verification failed"):
            importer.import_database(self.source, scoped_url, MAPPINGS)
        with engine.connect() as connection:
            self.assertEqual(connection.execute(select(jobs.c.id)).all(), [])
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
