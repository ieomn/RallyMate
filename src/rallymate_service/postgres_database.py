"""PostgreSQL storage for the existing job queue and its raw SQL readers."""
from __future__ import annotations

import re
from contextlib import contextmanager
from threading import RLock, local
from typing import Iterator
from weakref import WeakSet

from psycopg import ClientCursor
from psycopg.pq import TransactionStatus
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from rallymate_service.database import JobDatabase, JobExecutionReader
from rallymate_service.migrations import validate_schema


# One transaction lock preserves the SQLite queue's admission/claim/cancel order.
_QUEUE_LOCK = 0x52414C4C594D4154
_SQL_TOKEN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\r\n]*|/\*.*?\*/"
    r"|(?P<dollar>\$(?:[A-Za-z_]\w*)?\$).*?(?P=dollar)|(?P<parameter>\?)",
    re.DOTALL,
)


def _parameter_sql(statement: str) -> str:
    # ClientCursor renders typed SQL literals, including NULL in `? IS NULL`.
    # Percent signs are escaped even in literals because psycopg scans them too.
    return _SQL_TOKEN.sub(
        lambda token: "%s" if token.lastgroup == "parameter" else token.group(),
        statement.replace("%", "%%"),
    )


class _Row:
    """The sqlite3.Row operations used by the service: names, indexes, dict()."""

    def __init__(self, names: tuple[str, ...], values):
        self._names = names
        self._values = tuple(values)

    def keys(self):
        return self._names

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = self._names.index(key)
            except ValueError:
                raise IndexError(f"No item with key {key!r}") from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def _row_factory(cursor):
    names = tuple(column.name for column in cursor.description or ())
    return lambda values: _Row(names, values)


class _Cursor:
    def __init__(self, connection):
        self._cursor = ClientCursor(connection, row_factory=_row_factory)

    def execute(self, statement: str, parameters=()):
        if statement.strip().rstrip(";").upper() == "BEGIN IMMEDIATE":
            self._cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_QUEUE_LOCK,))
        else:
            self._cursor.execute(_parameter_sql(statement), parameters)
        return self

    def executemany(self, statement: str, parameters):
        self._cursor.executemany(_parameter_sql(statement), parameters)
        return self

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchmany(self, size=1):
        return self._cursor.fetchmany(size)

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _Connection:
    def __init__(self, raw_connection, *, read_only=False):
        self._raw = raw_connection
        self._driver = raw_connection.driver_connection
        self._closed = False
        self._cursors: WeakSet[_Cursor] = WeakSet()
        if read_only:
            # Attempt-scoped readers may outlive ordinary request pool checkouts.
            self._raw.detach()
            try:
                self._driver.autocommit = True
                self._driver.read_only = True
                # Autocommit reads have no BEGIN where psycopg could apply this.
                with self._driver.cursor() as cursor:
                    cursor.execute("SET default_transaction_read_only = on")
            except BaseException:
                self._raw.close()
                raise

    def _check_open(self):
        if self._closed:
            raise RuntimeError("database connection is closed")

    def cursor(self):
        self._check_open()
        cursor = _Cursor(self._driver)
        self._cursors.add(cursor)
        return cursor

    def execute(self, statement: str, parameters=()):
        return self.cursor().execute(statement, parameters)

    def executemany(self, statement: str, parameters):
        return self.cursor().executemany(statement, parameters)

    def commit(self):
        self._check_open()
        self._driver.commit()

    def rollback(self):
        self._check_open()
        self._driver.rollback()

    @property
    def in_transaction(self):
        return self._driver.info.transaction_status != TransactionStatus.IDLE

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            for cursor in self._cursors:
                cursor.close()
            self._cursors.clear()
        finally:
            self._raw.close()


class PostgresJobExecutionReader(JobExecutionReader):
    """Thread-local, read-only polling without retaining a transaction snapshot."""

    def __init__(self, engine: Engine, job_id: str):
        self._engine = engine
        self._job_id = job_id
        self._local = local()
        self._lock = RLock()
        self._connections: list[_Connection] = []
        self._closed = False
        self.read_count = 0
        self.connection_count = 0

    def read(self) -> dict | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job execution reader is closed")
            connection = getattr(self._local, "connection", None)
            if connection is None:
                connection = _Connection(self._engine.raw_connection(), read_only=True)
                self._local.connection = connection
                self._connections.append(connection)
                self.connection_count += 1
            with connection.execute(
                "SELECT status, cancel_requested, worker_id, attempts FROM jobs WHERE id = ?",
                (self._job_id,),
            ) as cursor:
                row = cursor.fetchone()
            self.read_count += 1
            return dict(row) if row is not None else None


class PostgresJobDatabase(JobDatabase):
    """Use migrated PostgreSQL tables while retaining the queue's public API."""

    def __init__(self, database_url: str, *, engine: Engine | None = None):
        url = make_url(database_url)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        if url.drivername != "postgresql+psycopg":
            raise ValueError("PostgresJobDatabase requires a PostgreSQL psycopg URL")
        self.engine = engine if engine is not None else create_engine(
            url, pool_pre_ping=True, isolation_level="READ COMMITTED", hide_parameters=True,
        )
        self._owns_engine = engine is None

    @contextmanager
    def connect(self) -> Iterator[_Connection]:
        connection = _Connection(self.engine.raw_connection())
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        validate_schema(self.engine)

    def execution_reader(self, job_id: str) -> PostgresJobExecutionReader:
        return PostgresJobExecutionReader(self.engine, job_id)

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()
