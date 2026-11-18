"""Deployment migrations use DATABASE_URL or a programmatically supplied connection."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from rallymate_service.migrations import normalize_database_url, prepare_legacy_sqlite
from rallymate_service.models import metadata


config = context.config


def run_with_connection(connection):
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    run_with_connection(connection)
else:
    database_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running database migrations")
    url = normalize_database_url(database_url)
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        prepare_legacy_sqlite(url)
        engine = create_engine(url, poolclass=pool.NullPool)
        try:
            with engine.connect() as connection:
                if engine.dialect.name == "sqlite":
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                run_with_connection(connection)
                connection.commit()
        finally:
            engine.dispose()
