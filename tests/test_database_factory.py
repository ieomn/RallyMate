import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from rallymate_service.database_factory import build_database, database_url, SQLiteAccountDatabase
from rallymate_service.models import users


def settings(tmp_path, url=None):
    return SimpleNamespace(database_path=tmp_path / "历史 # 100%.sqlite3", database_url=url)


def test_sqlite_startup_preserves_legacy_job_and_adds_nullable_owner(tmp_path):
    from rallymate_service.database import JobDatabase
    options = settings(tmp_path)
    old = JobDatabase(options.database_path)
    old.initialize()
    old.create_job("old", "original.mp4", Path("video"), Path("request"), Path("output"))
    database = build_database(options)
    assert isinstance(database, SQLiteAccountDatabase)
    database.initialize()
    database.initialize()
    assert database.get_job("old")["user_id"] is None
    assert database.get_job("old")["original_filename"] == "original.mp4"


def test_new_owner_is_inserted_with_job_and_foreign_key_failure_is_atomic(tmp_path):
    options = settings(tmp_path)
    database = build_database(options)
    database.initialize()
    engine = create_engine(database_url(options))
    try:
        with engine.begin() as connection:
            connection.execute(users.insert().values(id="owner", email="owner@example.com", username="Owner",
                password_hash="argon2-test-fixture", created_at="2026-11-17T00:00:00+00:00"))
        job = database.create_job("owned", "video.mp4", Path("v"), Path("r"), Path("o"), user_id="owner")
        assert job["user_id"] == "owner"
        with pytest.raises(sqlite3.IntegrityError):
            database.create_job("invalid", "v.mp4", Path("v"), Path("r"), Path("o"), user_id="missing")
        assert database.get_job("invalid") is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("url", ["mysql://user:private@host/db", "sqlite:///:memory:", "invalid-private-url"])
def test_factory_rejects_unsupported_storage_without_echoing_credentials(tmp_path, url):
    with pytest.raises(ValueError) as error:
        database_url(settings(tmp_path, url))
    assert "private" not in str(error.value)


def test_postgres_selection_does_not_connect_or_fallback_to_sqlite(tmp_path):
    from rallymate_service.postgres_database import PostgresJobDatabase
    database = build_database(settings(tmp_path, "postgresql://test:secret@127.0.0.1:1/database"))
    try:
        assert isinstance(database, PostgresJobDatabase)
        assert not settings(tmp_path).database_path.exists()
        assert database.engine.url.drivername == "postgresql+psycopg"
    finally:
        database.dispose()
