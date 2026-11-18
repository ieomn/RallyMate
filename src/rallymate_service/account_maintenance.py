"""Assign legacy ownership and reclaim deleted job files under explicit operator control."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from .database_factory import database_url
from .migrations import validate_schema
from .models import admin_audit, jobs, users


TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
PATH_ROOTS = {"video_path": "uploads", "request_path": "requests", "output_dir": "runs"}


class MaintenanceError(RuntimeError):
    """A fixed operator message that contains no connection credentials."""


@contextmanager
def _transaction(engine, *, freeze_jobs=False):
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))
            lock_id = int.from_bytes(hashlib.sha256(b"rallymate:account-administration").digest()[:8], "big", signed=True)
            connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
            if freeze_jobs:
                # A new recovery job must not acquire a file reference during reclamation.
                connection.execute(text("LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE"))
        yield connection


def _actor_and_job(connection, actor_id: str, job_id: str):
    actor = connection.execute(select(users).where(users.c.id == actor_id)).mappings().first()
    if not actor or actor["deleted_at"] is not None or actor["role"] != "ADMIN" or actor["status"] != "APPROVED" or actor["must_change_password"]:
        raise MaintenanceError("An approved, active administrator without a pending password change is required.")
    job = connection.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
    if not job:
        raise MaintenanceError("The job does not exist.")
    if job["status"] not in TERMINAL:
        raise MaintenanceError("The job must be terminal; finish cancellation and worker shutdown first.")
    return job


def _audit(connection, actor_id, target_id, action, details):
    identifier = str(uuid4())
    connection.execute(admin_audit.insert().values(id=identifier, actor_id=actor_id, target_user_id=target_id,
        action=action, created_at=datetime.now(timezone.utc).isoformat(), details_json=json.dumps(details, ensure_ascii=False)))
    return identifier


def assign_record(engine, job_id: str, user_id: str, actor_id: str) -> dict:
    """Bind only an unowned, undeleted terminal record; ownership cannot be replaced."""
    with _transaction(engine) as connection:
        job = _actor_and_job(connection, actor_id, job_id)
        if job["user_id"] is not None or job["deleted_at"] is not None:
            raise MaintenanceError("Only an undeleted job with a NULL owner can be assigned.")
        target = connection.execute(select(users.c.id).where(users.c.id == user_id, users.c.deleted_at.is_(None))).first()
        if target is None:
            raise MaintenanceError("The destination user does not exist or has been deleted.")
        connection.execute(update(jobs).where(jobs.c.id == job_id).values(user_id=user_id))
        audit_id = _audit(connection, actor_id, user_id, "assign_record", {"job_id": job_id, "user_id": user_id})
        return {"job_id": job_id, "user_id": user_id, "audit_id": audit_id}


def _absolute_path(value) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError
        return path
    except (TypeError, ValueError):
        raise MaintenanceError("Every data path must be absolute and must not contain '..'.") from None


def _checked_stat(path: Path):
    try:
        result = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(result.st_mode) or getattr(result, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise MaintenanceError("Symbolic links, junctions, and other reparse points are not eligible for reclamation.")
    if not stat.S_ISREG(result.st_mode) and not stat.S_ISDIR(result.st_mode):
        raise MaintenanceError("Only regular files and directories are eligible for reclamation.")
    return result


def _check_ancestors(path: Path):
    for ancestor in reversed((path, *path.parents)):
        _checked_stat(ancestor)


def _identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


@dataclass(frozen=True)
class FileEntry:
    path: Path
    identity: tuple
    directory: bool


def _inventory(path: Path) -> list[FileEntry]:
    entries = []
    pending = [path]
    while pending:
        current = pending.pop()
        info = _checked_stat(current)
        if info is None:
            raise MaintenanceError("A file changed during preflight; retry after filesystem writers stop.")
        directory = stat.S_ISDIR(info.st_mode)
        entries.append(FileEntry(current, _identity(info), directory))
        if directory:
            with os.scandir(current) as children:
                pending.extend(Path(child.path) for child in children)
    return sorted(entries, key=lambda entry: len(entry.path.parts), reverse=True)


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _purge_plan(connection, job, data_root: Path) -> dict:
    if job["deleted_at"] is None:
        raise MaintenanceError("Only a logically deleted terminal job can have its files reclaimed.")
    root = _absolute_path(data_root)
    _check_ancestors(root)
    if not root.is_dir() or root.parent == root:
        raise MaintenanceError("data-root must identify an existing application data directory.")
    root = root.resolve(strict=True)
    references = []
    for other in connection.execute(select(jobs.c.id, *[jobs.c[name] for name in PATH_ROOTS]).where(jobs.c.id != job["id"])).mappings():
        for field in PATH_ROOTS:
            references.append((other["id"], _absolute_path(other[field]).resolve(strict=False)))
    targets = []
    for field, subdirectory in PATH_ROOTS.items():
        path = _absolute_path(job[field])
        _check_ancestors(path)
        path = path.resolve(strict=False)
        allowed = root / subdirectory
        if path == allowed or allowed not in path.parents:
            raise MaintenanceError("A target is outside its uploads, requests, or runs directory, or names the directory itself.")
        info = _checked_stat(path)
        if info is not None and field != "output_dir" and not stat.S_ISREG(info.st_mode):
            raise MaintenanceError("Video and request references must name regular files.")
        if info is not None and field == "output_dir" and not stat.S_ISDIR(info.st_mode):
            raise MaintenanceError("The output reference must name a directory.")
        entries = _inventory(path) if info is not None else []
        shared_with = {job_id for job_id, other_path in references if _overlaps(path, other_path)}
        targets.append({"field": field, "path": path, "entries": entries, "shared_with": len(shared_with)})
    return {"root": root, "targets": targets}


def _remove_entry(entry: FileEntry, root: Path):
    _check_ancestors(entry.path)
    resolved = entry.path.resolve(strict=False)
    if resolved != entry.path or root not in resolved.parents:
        raise MaintenanceError("A path changed after preflight; reclamation stopped.")
    current = _checked_stat(entry.path)
    if current is None or _identity(current) != entry.identity:
        raise MaintenanceError("A file changed after preflight; reclamation stopped.")
    if os.name == "nt":
        _remove_windows_handle(entry)
    else:
        _remove_relative_entry(entry)


def _remove_relative_entry(entry: FileEntry):
    """Resolve parents without following links, then delete relative to that directory."""
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent = os.open(entry.path.anchor, flags)
        descriptors.append(parent)
        for component in entry.path.parts[1:-1]:
            parent = os.open(component, flags, dir_fd=parent)
            descriptors.append(parent)
        info = os.stat(entry.path.name, dir_fd=parent, follow_symlinks=False)
        if _identity(info) != entry.identity:
            raise MaintenanceError("A file changed after preflight; reclamation stopped.")
        operation = os.rmdir if entry.directory else os.unlink
        operation(entry.path.name, dir_fd=parent)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _remove_windows_handle(entry: FileEntry):
    """Delete the verified open file object, without resolving its pathname again."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    # DELETE | FILE_READ_ATTRIBUTES; share read/write/delete; OPEN_EXISTING;
    # BACKUP_SEMANTICS supports directories and OPEN_REPARSE_POINT avoids following a leaf link.
    handle = create(str(entry.path), 0x00010000 | 0x80, 0x7, None, 3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = None
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        if _identity(os.fstat(descriptor)) != entry.identity:
            raise MaintenanceError("A file changed after preflight; reclamation stopped.")
        delete_file = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile (BOOLEAN)
        if not dispose(handle, 4, ctypes.byref(delete_file), ctypes.sizeof(delete_file)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor is None:
            close(handle)
        else:
            os.close(descriptor)


def purge_record(engine, job_id: str, actor_id: str, data_root: str | Path) -> dict:
    """Reclaim unshared files, retaining the job, its owner, statistics, and audit trail."""
    # Persist intent before touching files because filesystem deletion cannot roll back.
    with _transaction(engine, freeze_jobs=True) as connection:
        job = _actor_and_job(connection, actor_id, job_id)
        plan = _purge_plan(connection, job, Path(data_root))
        request_id = _audit(connection, actor_id, job["user_id"], "purge_record_requested", {"job_id": job_id, "data_root": str(plan["root"])})
    with _transaction(engine, freeze_jobs=True) as connection:
        job = _actor_and_job(connection, actor_id, job_id)
        plan = _purge_plan(connection, job, Path(data_root))
        result = {"job_id": job_id, "request_audit_id": request_id, "removed_files": 0, "removed_directories": 0,
                  "missing_targets": sum(not target["entries"] and not target["shared_with"] for target in plan["targets"]),
                  "shared_targets": sum(bool(target["shared_with"]) for target in plan["targets"]),
                  "shared_references": sum(target["shared_with"] for target in plan["targets"]), "complete": True}
        for target in plan["targets"]:
            if target["shared_with"]:
                continue
            if not target["entries"]:
                continue
            try:
                for entry in target["entries"]:
                    _remove_entry(entry, plan["root"])
                    result["removed_directories" if entry.directory else "removed_files"] += 1
            except (OSError, MaintenanceError):
                result["complete"] = False
                result["error"] = "Filesystem reclamation stopped after a file changed or could not be removed; inspect and retry."
                break
        result["audit_id"] = _audit(connection, actor_id, job["user_id"], "purge_record", result)
        return result


def _open_engine(configured_url: str):
    normalized = database_url(SimpleNamespace(database_url=configured_url))
    url = make_url(normalized)
    options = {"echo": False, "hide_parameters": True}
    if url.get_backend_name() == "sqlite":
        path = Path(url.database)
        # mode=rw refuses a missing database; this command never creates or migrates one.
        options["creator"] = lambda: sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30)
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assign = commands.add_parser("assign-record", help="Assign an unowned historical job to an existing user")
    purge = commands.add_parser("purge-record", help="Reclaim files of a logically deleted terminal job")
    for command in (assign, purge):
        command.add_argument("--job-id", required=True)
        command.add_argument("--actor-id", required=True)
    assign.add_argument("--user-id", required=True)
    purge.add_argument("--data-root", required=True, type=Path)
    args = parser.parse_args(argv)
    engine = None
    try:
        configured_url = os.environ.get("DATABASE_URL")
        if not configured_url:
            raise MaintenanceError("Set DATABASE_URL before running account maintenance.")
        engine = _open_engine(configured_url)
        validate_schema(engine)
        if args.command == "assign-record":
            result = assign_record(engine, args.job_id, args.user_id, args.actor_id)
        else:
            result = purge_record(engine, args.job_id, args.actor_id, args.data_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("complete", True) else 2
    except MaintenanceError as error:
        print(f"Maintenance refused: {error}", file=sys.stderr)
    except (SQLAlchemyError, OSError, ValueError, RuntimeError):
        print("Maintenance failed; check the connection, explicit schema migration, and filesystem access. A started purge may have removed files; its request audit is retained.", file=sys.stderr)
    finally:
        if engine is not None:
            engine.dispose()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
