"""Choose task storage at application entry points; workers keep their queue API."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import URL, make_url

from rallymate_service.database import JobDatabase


def database_url(settings) -> str:
    configured = getattr(settings, "database_url", None)
    if configured:
        try:
            url = make_url(configured)
        except Exception:
            raise ValueError("DATABASE_URL must be a valid PostgreSQL or file SQLite URL") from None
        if url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}:
            return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
        if url.drivername not in {"sqlite", "sqlite+pysqlite"} or not url.database or url.database == ":memory:":
            raise ValueError("DATABASE_URL must select PostgreSQL or a persistent SQLite file")
        if url.query:
            raise ValueError("SQLite DATABASE_URL does not accept connection query options")
        path = Path(url.database).expanduser().resolve()
    else:
        path = Path(settings.database_path).resolve()
    return URL.create("sqlite", database=str(path)).render_as_string(hide_password=False)


class SQLiteAccountDatabase(JobDatabase):
    def __init__(self, path: str | Path):
        super().__init__(path)
        self.url = URL.create("sqlite", database=str(self.path)).render_as_string(hide_password=False)

    def initialize(self) -> None:
        from rallymate_service.migrations import migrate_database
        migrate_database(self.url)


def build_database(settings) -> JobDatabase:
    url = database_url(settings)
    if make_url(url).get_backend_name() == "postgresql":
        from rallymate_service.postgres_database import PostgresJobDatabase
        return PostgresJobDatabase(url)
    return SQLiteAccountDatabase(make_url(url).database)
