"""Explicit deployment migrations and read-only application schema validation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL, make_url

from .models import metadata


SCHEMA_HEAD = "20261117_01_accounts"


def normalize_database_url(database_url: str | URL) -> URL:
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("DATABASE_URL must use SQLite or PostgreSQL")
    return url


def prepare_legacy_sqlite(url: URL) -> None:
    if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}:
        from .database import JobDatabase

        # Keep the established cancellation upgrade before adding account ownership.
        JobDatabase(Path(url.database)).initialize()


def migrate_database(database_url: str | URL) -> None:
    """Upgrade explicitly; callers must stop writes and back up production first."""
    url = normalize_database_url(database_url)
    prepare_legacy_sqlite(url)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parent / "alembic"))
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                # sqlite3 otherwise leaves DDL outside its implicit transaction.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        validate_schema(engine)
    finally:
        engine.dispose()


def validate_schema(engine: Engine) -> None:
    """Reject an unmigrated or incompatible schema without creating or changing it."""
    message = "Database schema is not ready; run the deployment migration first"
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "alembic_version" not in tables:
            raise RuntimeError(message)
        versions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if versions != {SCHEMA_HEAD} or not set(metadata.tables).issubset(tables):
            raise RuntimeError(message)
        for table in metadata.sorted_tables:
            columns = {column["name"]: column for column in inspector.get_columns(table.name)}
            for expected in table.columns:
                actual = columns.get(expected.name)
                if actual is None or actual["type"]._type_affinity is not expected.type._type_affinity:
                    raise RuntimeError(f"{message}: incompatible {table.name}.{expected.name}")
                # Legacy SQLite TEXT PRIMARY KEY has implicit nullable reflection.
                if not expected.primary_key and actual["nullable"] != expected.nullable:
                    raise RuntimeError(f"{message}: incompatible nullability for {table.name}.{expected.name}")
            primary_key = inspector.get_pk_constraint(table.name)["constrained_columns"]
            if primary_key != [column.name for column in table.primary_key.columns]:
                raise RuntimeError(f"{message}: missing primary key on {table.name}")
            actual_fks = inspector.get_foreign_keys(table.name)
            for expected in table.foreign_key_constraints:
                target = next(iter(expected.elements)).column.table.name
                matching = [
                    fk for fk in actual_fks
                    if fk["constrained_columns"] == list(expected.column_keys)
                    and fk["referred_table"] == target
                    and fk["referred_columns"] == [item.column.name for item in expected.elements]
                ]
                if not matching or matching[0].get("options", {}).get("ondelete", "NO ACTION") not in {"NO ACTION", "RESTRICT"}:
                    raise RuntimeError(f"{message}: missing safe foreign key on {table.name}")
            actual_indexes = {index["name"]: index for index in inspector.get_indexes(table.name)}
            for expected in table.indexes:
                actual = actual_indexes.get(expected.name)
                if actual is None or actual["column_names"] != [column.name for column in expected.columns]:
                    raise RuntimeError(f"{message}: missing index {expected.name}")
        unique_email = any(
            constraint["column_names"] == ["email"]
            for constraint in inspector.get_unique_constraints("users")
        )
        if not unique_email:
            raise RuntimeError(f"{message}: missing unique user email")
        for table_name, names in {
            "users": {"ck_users_email_normalized", "ck_users_role", "ck_users_status", "ck_users_must_change_password"},
            "auth_rate_limits": {"ck_auth_rate_limits_attempts"},
        }.items():
            actual = {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}
            if not names.issubset(actual):
                raise RuntimeError(f"{message}: missing checks on {table_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply RallyMate database migrations")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("Set DATABASE_URL or pass --database-url")
    migrate_database(args.database_url)
    print(f"Database schema is at {SCHEMA_HEAD}")


if __name__ == "__main__":
    main()
