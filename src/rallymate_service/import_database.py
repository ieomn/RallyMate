"""Read a legacy SQLite snapshot and import it into an explicitly migrated PostgreSQL DB."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import sys

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import SQLAlchemyError

from .models import jobs
from .migrations import SCHEMA_HEAD, validate_schema


AUTH_TABLES = ("users", "sessions", "upload_owners", "admin_audit", "auth_rate_limits")
PATH_COLUMNS = ("video_path", "request_path", "output_dir")
LEGACY_DEFAULTS = {
    "progress_json": None, "cancel_requested": 0, "cancel_requested_at": None,
    "cancelled_at": None, "user_id": None, "deleted_at": None,
}


class DatabaseImportError(RuntimeError):
    """A safe, credential-free operator message."""


@dataclass(frozen=True)
class PathMapping:
    windows_prefix: str
    linux_prefix: str

    def __post_init__(self) -> None:
        source, destination = PureWindowsPath(self.windows_prefix), PurePosixPath(self.linux_prefix)
        if not source.is_absolute() or ".." in source.parts:
            raise DatabaseImportError("A path mapping must start with an absolute Windows prefix without '..'.")
        if (not destination.is_absolute() or ".." in destination.parts
                or "\\" in self.linux_prefix or self.linux_prefix.startswith("//")):
            raise DatabaseImportError("A path mapping must end with an absolute Linux prefix without '..'.")


def _map_path(value: str, mappings: tuple[PathMapping, ...]) -> str:
    path = PureWindowsPath(value)
    if ".." in path.parts:
        raise DatabaseImportError("Source paths containing '..' require operator correction in a working copy.")
    if path.is_absolute():
        for mapping in sorted(mappings, key=lambda item: len(PureWindowsPath(item.windows_prefix).parts), reverse=True):
            try:
                suffix = path.relative_to(PureWindowsPath(mapping.windows_prefix))
            except ValueError:
                continue
            return str(PurePosixPath(mapping.linux_prefix).joinpath(*suffix.parts))
        raise DatabaseImportError("A Windows file path has no matching --path-map prefix.")
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    raise DatabaseImportError("Every file reference must be an absolute Linux path or an explicitly mapped Windows path.")


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _manifest(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda row: row["id"])
    return {
        "job_count": len(rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "null_owner_count": sum(row["user_id"] is None for row in rows),
        "jobs_sha256": _digest(rows),
        "ids_sha256": _digest([row["id"] for row in rows]),
        "statuses_sha256": _digest([[row["id"], row["status"]] for row in rows]),
        "summaries_sha256": _digest([[row["id"], row["summary_json"]] for row in rows]),
        "paths_sha256": _digest([[row["id"], *[row[name] for name in PATH_COLUMNS]] for row in rows]),
    }


def _read_snapshot(source: str | Path, mappings: tuple[PathMapping, ...]) -> tuple[list[dict], dict]:
    path = Path(source).resolve(strict=True)
    mapping_sources = [PureWindowsPath(item.windows_prefix) for item in mappings]
    if len(set(mapping_sources)) != len(mapping_sources):
        raise DatabaseImportError("Each Windows mapping prefix must be unique.")
    # mode=ro retains WAL visibility; immutable=1 would silently omit uncheckpointed rows.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jobs" not in tables:
            raise DatabaseImportError("The source has no legacy jobs table.")
        blockers = []
        for table in AUTH_TABLES:
            if table in tables and connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                blockers.append("Source contains account or ownership data; use an account-aware migration.")
                break
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        known_columns = set(jobs.c.keys())
        if columns - known_columns:
            raise DatabaseImportError("Source jobs has unsupported columns; importing would lose data.")
        if known_columns - columns - LEGACY_DEFAULTS.keys():
            raise DatabaseImportError("Source jobs is missing required legacy columns.")
        original = []
        for record in connection.execute("SELECT * FROM jobs ORDER BY id"):
            row = {**LEGACY_DEFAULTS, **dict(record)}
            for column in jobs.columns:
                value = row[column.name]
                if value is None:
                    if not column.nullable:
                        raise DatabaseImportError("Source jobs contains a missing required value.")
                elif type(value) is not column.type.python_type:
                    raise DatabaseImportError("Source jobs contains an unsupported field type.")
                elif isinstance(value, str) and "\x00" in value:
                    raise DatabaseImportError("Source text contains a NUL character that PostgreSQL cannot preserve.")
            if row["status"] not in {"queued", "running", "succeeded", "failed", "cancelled"}:
                raise DatabaseImportError("Source jobs contains an unknown status.")
            original.append(row)
        # All source schema, account and job reads belong to this one read transaction.
        connection.rollback()
    if len({row["id"] for row in original}) != len(original):
        raise DatabaseImportError("Source jobs contains duplicate identifiers.")
    original.sort(key=lambda row: row["id"])
    if any(row["status"] in {"queued", "running"} for row in original):
        blockers.append("Source contains queued or running jobs; stop writers and complete safe cancellation or recovery first.")
    if any(row["user_id"] is not None or row["deleted_at"] is not None for row in original):
        blockers.append("Source jobs contains account-era ownership or deletion data; use an account-aware migration.")
    mapped = [{**row, **{name: _map_path(row[name], mappings) for name in PATH_COLUMNS}} for row in original]
    manifest = {
        "schema_revision": SCHEMA_HEAD,
        "eligible": not blockers,
        "blockers": blockers,
        "source": _manifest(original),
        "mapped": _manifest(mapped),
        "mapped_path_count": sum(a[name] != b[name] for a, b in zip(original, mapped) for name in PATH_COLUMNS),
    }
    return mapped, manifest


def inspect_database(source: str | Path, path_mappings: tuple[PathMapping, ...] = ()) -> dict:
    """Return a read-only source manifest; never connect to the target."""
    return _read_snapshot(source, path_mappings)[1]


def _write_snapshot(connection: Connection, rows: list[dict], expected: dict) -> dict:
    """Called inside one transaction; any mismatch must escape to roll it back."""
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if revision != [SCHEMA_HEAD]:
        raise DatabaseImportError("Target schema revision does not match; run the explicit migration command first.")
    for table in AUTH_TABLES:
        if connection.execute(text(f'SELECT 1 FROM "{table}" LIMIT 1')).first():
            raise DatabaseImportError("Target contains account or ownership data; the legacy importer requires an empty target.")
    existing = [dict(row) for row in connection.execute(select(jobs)).mappings()]
    if existing:
        if _manifest(existing) != expected:
            raise DatabaseImportError("Target jobs differ from the mapped source; no records were overwritten.")
        inserted = 0
    else:
        if rows:
            connection.execute(jobs.insert(), rows)
        inserted = len(rows)
    actual_rows = [dict(row) for row in connection.execute(select(jobs)).mappings()]
    actual = _manifest(actual_rows)
    if actual != expected or sorted(actual_rows, key=lambda row: row["id"]) != rows:
        raise DatabaseImportError("Post-import record verification failed; the transaction will be rolled back.")
    return {"inserted": inserted, "already_present": len(rows) - inserted, "verified": actual}


def import_database(source: str | Path, target_url: str, path_mappings: tuple[PathMapping, ...] = ()) -> dict:
    """Atomically import a stopped legacy queue into an empty or identical PostgreSQL DB."""
    rows, manifest = _read_snapshot(source, path_mappings)
    if manifest["blockers"]:
        raise DatabaseImportError(" ".join(manifest["blockers"]))
    engine = None
    try:
        url = make_url(target_url)
        if url.get_backend_name() != "postgresql":
            raise DatabaseImportError("The import target must be PostgreSQL.")
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        engine = create_engine(url, echo=False, hide_parameters=True)
        validate_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))
            # Hold writers out until the final record/digest verification has committed.
            connection.execute(text("LOCK TABLE jobs, users, sessions, upload_owners, admin_audit, auth_rate_limits, alembic_version IN EXCLUSIVE MODE"))
            result = _write_snapshot(connection, rows, manifest["mapped"])
        return {**manifest, **result}
    except SQLAlchemyError:
        raise DatabaseImportError("Target database operation failed; no import was committed. Check connectivity and schema.") from None
    finally:
        if engine is not None:
            engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Legacy SQLite file (opened read-only). Stop writers before importing.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts and digests without connecting to the target.")
    parser.add_argument("--target-url-env", default="DATABASE_URL", help="Name of the environment variable containing the target PostgreSQL URL.")
    parser.add_argument("--path-map", action="append", default=[], metavar="WINDOWS_PREFIX=LINUX_PREFIX", help="Map an entire Windows path prefix; repeat for separate file roots.")
    args = parser.parse_args(argv)
    try:
        mappings = []
        for value in args.path_map:
            before, separator, after = value.partition("=")
            if not separator:
                raise DatabaseImportError("Each --path-map must have WINDOWS_PREFIX=LINUX_PREFIX form.")
            mappings.append(PathMapping(before, after))
        if args.dry_run:
            result = inspect_database(args.source, tuple(mappings))
        else:
            target_url = os.environ.get(args.target_url_env)
            if not target_url:
                raise DatabaseImportError("The target URL environment variable is not set.")
            result = import_database(args.source, target_url, tuple(mappings))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["eligible"] else 2
    except DatabaseImportError as exc:
        print(f"Import refused: {exc}", file=sys.stderr)
    except (OSError, sqlite3.Error, ValueError, RuntimeError, ImportError):
        # Database driver errors and filenames can contain credentials; report only fixed text.
        print("Import failed; check the source file, mapping syntax, target connection and explicit schema migration.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
