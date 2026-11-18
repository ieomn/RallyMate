import os
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from rallymate_service.database import JobDatabase
from rallymate_service.migrations import SCHEMA_HEAD, migrate_database, validate_schema
from rallymate_service.models import metadata, users


def schema_signature(engine):
    inspector = inspect(engine)
    signature = {}
    for name in sorted(metadata.tables):
        pk = inspector.get_pk_constraint(name)["constrained_columns"]
        signature[name] = {
            "columns": {
                column["name"]: (
                    str(column["type"]),
                    # SQLite's historical TEXT PRIMARY KEY reflects as nullable.
                    column["nullable"] if column["name"] not in pk else False,
                    column["default"],
                )
                for column in inspector.get_columns(name)
            },
            "primary_key": pk,
            "foreign_keys": sorted(
                (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]), str(fk.get("options", {})))
                for fk in inspector.get_foreign_keys(name)
            ),
            "indexes": sorted(
                (item["name"], tuple(item["column_names"]), bool(item["unique"]))
                for item in inspector.get_indexes(name)
            ),
            "unique": sorted(tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)),
            "checks": sorted(re.sub(r"\s+", "", item["sqltext"]) for item in inspector.get_check_constraints(name)),
        }
    return signature


def user_values(**overrides):
    return {
        "id": str(uuid4()), "email": f"{uuid4().hex}@example.test", "username": "Display name",
        "password_hash": "argon2-hash-placeholder", "created_at": "2026-11-17T00:00:00+00:00",
        **overrides,
    }


class AccountMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "jobs.sqlite3"
        self.url = f"sqlite:///{self.path.as_posix()}"
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enforce_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    def test_empty_database_is_idempotent_and_matches_models(self):
        migrate_database(self.url)
        first = schema_signature(self.engine)
        migrate_database(self.url)
        validate_schema(self.engine)
        self.assertEqual(first, schema_signature(self.engine))
        expected_engine = create_engine("sqlite://")
        try:
            metadata.create_all(expected_engine)
            self.assertEqual(first, schema_signature(expected_engine))
        finally:
            expected_engine.dispose()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), SCHEMA_HEAD)

    def _seed_current_jobs_with_dependants(self):
        database = JobDatabase(self.path)
        database.initialize()
        database.create_job("legacy-job", "击球.mp4", Path("D:/video.mp4"), Path("D:/request.json"), Path("D:/outputs"))
        with database.connect() as connection:
            connection.execute("ALTER TABLE jobs ADD COLUMN local_annotation TEXT")
            connection.execute("UPDATE jobs SET summary_json=?, progress_json=?, local_annotation=?", (
                '{ "score": 88, "note": "中文" }', '{"phase":"queued", "percent":0}', "preserve me",
            ))
            connection.execute("CREATE INDEX custom_filename_index ON jobs(original_filename)")
            connection.execute("CREATE TABLE job_links (job_id TEXT REFERENCES jobs(id), note TEXT)")
            connection.execute("INSERT INTO job_links VALUES ('legacy-job', 'keep reference')")
            connection.execute("CREATE TABLE local_updates (job_id TEXT)")
            connection.execute("CREATE TRIGGER custom_job_update AFTER UPDATE ON jobs BEGIN INSERT INTO local_updates VALUES (new.id); END")
            connection.commit()
            before = dict(connection.execute("SELECT * FROM jobs").fetchone())
            rootpage = connection.execute("SELECT rootpage FROM sqlite_master WHERE name='jobs'").fetchone()[0]
        return before, rootpage

    def test_current_jobs_adds_null_owner_without_rebuilding_or_losing_dependants(self):
        before, rootpage = self._seed_current_jobs_with_dependants()
        migrate_database(self.url)
        migrate_database(self.url)
        with self.engine.begin() as connection:
            row = dict(connection.execute(text("SELECT * FROM jobs")).mappings().one())
            self.assertIsNone(row.pop("user_id"))
            self.assertIsNone(row.pop("deleted_at"))
            self.assertEqual(before, row)
            self.assertEqual(rootpage, connection.execute(text("SELECT rootpage FROM sqlite_master WHERE name='jobs'")).scalar_one())
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM local_updates")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT note FROM job_links")).scalar_one(), "keep reference")
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            connection.execute(text("UPDATE jobs SET local_annotation='changed' WHERE id='legacy-job'"))
            self.assertEqual(connection.execute(text("SELECT job_id FROM local_updates")).scalar_one(), "legacy-job")
        index_names = {item["name"] for item in inspect(self.engine).get_indexes("jobs")}
        self.assertTrue({"idx_jobs_user_created", "idx_jobs_status_created", "custom_filename_index"}.issubset(index_names))

    def test_old_cancellation_schema_retains_json_extra_columns_triggers_and_foreign_keys(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
                    original_filename TEXT NOT NULL, video_path TEXT NOT NULL,
                    request_path TEXT NOT NULL, output_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT, lease_expires_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, worker_id TEXT,
                    summary_json TEXT, error TEXT, external_marker TEXT
                );
                CREATE TABLE dependent (job_id TEXT REFERENCES jobs(id));
                CREATE INDEX legacy_marker_index ON jobs(external_marker);
                CREATE TABLE legacy_updates (job_id TEXT);
                CREATE TRIGGER legacy_trigger AFTER UPDATE ON jobs BEGIN INSERT INTO legacy_updates VALUES (new.id); END;
                INSERT INTO jobs (id,status,original_filename,video_path,request_path,output_dir,created_at,updated_at,summary_json,external_marker)
                VALUES ('old-id','succeeded','old.mp4','D:/old.mp4','D:/request.json','D:/out','2020-01-01','2020-01-01','{ "score": 91 }','marker');
                INSERT INTO dependent VALUES ('old-id');
            """)
            connection.commit()
        migrate_database(self.url)
        with self.engine.begin() as connection:
            row = connection.execute(text("SELECT * FROM jobs")).mappings().one()
            self.assertEqual((row["id"], row["status"], row["summary_json"], row["external_marker"]), ("old-id", "succeeded", '{ "score": 91 }', "marker"))
            self.assertEqual(row["cancel_requested"], 0)
            self.assertIsNone(row["user_id"])
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            connection.execute(text("UPDATE jobs SET status='cancelled' WHERE id='old-id'"))
            self.assertEqual(connection.execute(text("SELECT job_id FROM legacy_updates")).scalar_one(), "old-id")
        self.assertIn("legacy_marker_index", {item["name"] for item in inspect(self.engine).get_indexes("jobs")})

    def test_constraints_defaults_and_non_cascading_ownership_are_enforced(self):
        self._seed_current_jobs_with_dependants()
        migrate_database(self.url)
        account = user_values()
        with self.engine.begin() as connection:
            connection.execute(users.insert().values(**account))
            connection.execute(text("UPDATE jobs SET user_id=:id WHERE id='legacy-job'"), {"id": account["id"]})
            row = connection.execute(users.select()).mappings().one()
            self.assertEqual((row["role"], row["status"], row["must_change_password"]), ("USER", "PENDING", 0))
        for changes in ({"role": "OWNER"}, {"status": "ACTIVE"}, {"must_change_password": 2}, {"email": " UPPER@example.test "}, {"email": account["email"]}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), self.engine.begin() as connection:
                connection.execute(users.insert().values(**user_values(**changes)))
        for statement in (
            "UPDATE jobs SET user_id='missing-user'",
            "DELETE FROM users",
            "INSERT INTO sessions VALUES ('session', 'missing-user', 'now', 'later', NULL, 'hash')",
            "INSERT INTO upload_owners VALUES ('upload', 'missing-user', 'now')",
            "INSERT INTO admin_audit VALUES ('event', 'missing-user', NULL, 'approve', 'now', '{}')",
            "INSERT INTO auth_rate_limits VALUES ('key', 'now', -1)",
        ):
            with self.subTest(statement=statement), self.assertRaises(IntegrityError), self.engine.begin() as connection:
                connection.execute(text(statement))

    def test_validation_is_read_only_and_rejects_unmigrated_or_damaged_schema(self):
        statements = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        with self.assertRaisesRegex(RuntimeError, "deployment migration"):
            validate_schema(self.engine)

        self.assertEqual(inspect(self.engine).get_table_names(), [])
        migrate_database(self.url)
        validate_schema(self.engine)
        self.assertFalse(any(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)", sql, flags=re.I) for sql in statements))
        with self.engine.begin() as connection:
            connection.execute(text("DROP INDEX idx_jobs_user_created"))
        with self.assertRaisesRegex(RuntimeError, "idx_jobs_user_created"):
            validate_schema(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='wrong-version'"))
        with self.assertRaisesRegex(RuntimeError, "deployment migration"):
            validate_schema(self.engine)

    def test_failed_additive_migration_rolls_back_new_tables_and_revision(self):
        database = JobDatabase(self.path)
        database.initialize()
        with database.connect() as connection:
            connection.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
            connection.commit()
        with self.assertRaises(OperationalError):
            migrate_database(self.url)
        self.assertEqual(inspect(self.engine).get_table_names(), ["jobs"])


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
class PostgresAccountMigrationTests(unittest.TestCase):
    def setUp(self):
        url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        self.admin = create_engine(url)
        self.addCleanup(self.admin.dispose)
        self.schemas = [f"migration_{uuid4().hex}", f"models_{uuid4().hex}"]
        with self.admin.begin() as connection:
            for schema in self.schemas:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self._drop_schemas)
        self.url = url.update_query_dict({"options": f"-csearch_path={self.schemas[0]}"})
        self.engine = create_engine(self.url)
        self.expected_engine = create_engine(url.update_query_dict({"options": f"-csearch_path={self.schemas[1]}"}))
        self.addCleanup(self.expected_engine.dispose)
        self.addCleanup(self.engine.dispose)

    def _drop_schemas(self):
        with self.admin.begin() as connection:
            for schema in self.schemas:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

    def test_postgres_migration_matches_models_and_is_idempotent(self):
        migrate_database(self.url)
        signature = schema_signature(self.engine)
        migrate_database(self.url)
        self.assertEqual(signature, schema_signature(self.engine))
        metadata.create_all(self.expected_engine)
        self.assertEqual(signature, schema_signature(self.expected_engine))
        validate_schema(self.engine)
        with self.engine.begin() as connection:
            connection.execute(users.insert().values(**user_values()))
            connection.execute(text("INSERT INTO jobs (id,status,original_filename,video_path,request_path,output_dir,created_at,updated_at) VALUES ('job','queued','x.mp4','x.mp4','r.json','out','now','now')"))
            self.assertIsNone(connection.execute(text("SELECT user_id FROM jobs")).scalar_one())
            self.assertEqual(connection.execute(text("SELECT cancel_requested FROM jobs")).scalar_one(), 0)
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(text("UPDATE jobs SET user_id='missing-user'"))

    def test_postgres_validation_does_not_create_schema(self):
        with self.assertRaisesRegex(RuntimeError, "deployment migration"):
            validate_schema(self.engine)
        self.assertEqual(inspect(self.engine).get_table_names(), [])


if __name__ == "__main__":
    unittest.main()
