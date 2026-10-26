from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from rallymate_service.database import JobDatabase
from rallymate_service.local_secrets import LocalSecretError, load_minimax_key
from rallymate_service.motion_replay_store import MotionReplaySettings, MotionReplayStore


LOGGER = logging.getLogger(__name__)


class CompletedMotionSourceScanner:
    """Persist a completion cursor, including across periods with no API process.

    Enqueue is idempotent in the replay store. The cursor advances only after
    enqueue succeeds (or the source is explicitly ineligible), so a crash
    cannot silently discard newly completed source jobs.
    """

    def __init__(self, path: Path, jobs: JobDatabase, enqueue: Callable[[str], object]):
        self.path = Path(path)
        self.jobs = jobs
        self.enqueue = enqueue

    def initialize(self, *, activated_at: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY, completed_at TEXT NOT NULL, job_id TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS skipped_sources (job_id TEXT PRIMARY KEY, reason TEXT NOT NULL)")
            connection.execute(
                "INSERT OR IGNORE INTO cursor VALUES (1, ?, '')",
                (activated_at or datetime.now(timezone.utc).isoformat(),),
            )

    def scan_once(self) -> int:
        from fastapi import HTTPException
        from rallymate_service.motion_replay_api import MotionReplayInputError

        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            marker = connection.execute("SELECT completed_at, job_id FROM cursor WHERE id=1").fetchone()
        if marker is None:
            raise RuntimeError("Motion source scanner is not initialized")
        with self.jobs.connect() as connection:
            rows = connection.execute(
                "SELECT id, completed_at FROM jobs WHERE status='succeeded' AND "
                "(completed_at > ? OR (completed_at = ? AND id > ?)) "
                "ORDER BY completed_at, id LIMIT 32",
                (marker[0], marker[0], marker[1]),
            ).fetchall()
        handled = 0
        for row in rows:
            skipped_reason = None
            try:
                self.enqueue(row["id"])
            except (HTTPException, MotionReplayInputError) as exc:
                if exc.status_code not in {400, 404, 409, 422}:
                    raise
                # Do not store paths or request contents in scheduler diagnostics.
                skipped_reason = f"source_ineligible_{exc.status_code}"
            with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
                if skipped_reason:
                    connection.execute("INSERT OR REPLACE INTO skipped_sources VALUES (?, ?)", (row["id"], skipped_reason))
                connection.execute(
                    "UPDATE cursor SET completed_at=?, job_id=? WHERE id=1 AND "
                    "(completed_at < ? OR (completed_at = ? AND job_id < ?))",
                    (row["completed_at"], row["id"], row["completed_at"], row["completed_at"], row["id"]),
                )
            handled += 1
        return handled


class MotionReplayRuntime:
    def __init__(self, service_settings, jobs: JobDatabase, *, summary_loader=None):
        self.settings = MotionReplaySettings(
            data_root=service_settings.data_root,
            enabled=service_settings.motion_replay_enabled,
            public_base_url=service_settings.public_base_url,
        )
        self.jobs = jobs
        self.store = MotionReplayStore(self.settings.database_path)
        self.summary_loader = summary_loader
        self.auto_generate = service_settings.motion_replay_auto_generate
        self.stop_event = Event()
        self.threads: list[Thread] = []

    def credential_available(self) -> bool:
        try:
            return bool(load_minimax_key(self.settings.data_root))
        except (LocalSecretError, OSError, UnicodeError):
            return False

    def provider_factory(self):
        from rallymate_service.motion_replay_provider import MiniMaxVideoProvider

        try:
            key = load_minimax_key(self.settings.data_root)
        except (LocalSecretError, OSError, UnicodeError):
            key = None
        return MiniMaxVideoProvider(
            api_key=key,
            allow_proxy_dns=os.getenv("RALLYMATE_MINIMAX_ALLOW_FAKEIP", "0") == "1",
        )

    def enqueue_source(self, job_id: str):
        from rallymate_service.motion_replay_api import enqueue_job_replay

        return enqueue_job_replay(
            jobs=self.jobs, store=self.store, settings=self.settings,
            job_id=job_id, summary_loader=self.summary_loader,
        )

    def start(self) -> None:
        self.store.initialize()
        if not self.settings.enabled or self.threads:
            return
        from rallymate_service.motion_replay_worker import run_motion_replay_worker

        worker = Thread(
            target=run_motion_replay_worker,
            kwargs={"store": self.store, "settings": self.settings, "provider_factory": self.provider_factory, "stop_event": self.stop_event},
            name="rallymate-motion-replays", daemon=True,
        )
        self.threads.append(worker)
        if self.auto_generate:
            scanner = CompletedMotionSourceScanner(
                self.settings.data_root / "motion-replay-scheduler.sqlite3", self.jobs, self.enqueue_source,
            )
            scanner.initialize()

            def scan() -> None:
                while not self.stop_event.is_set():
                    try:
                        if self.credential_available():
                            scanner.scan_once()
                    except Exception as exc:
                        LOGGER.warning("Motion source scan will retry (%s)", type(exc).__name__)
                    self.stop_event.wait(5)

            self.threads.append(Thread(target=scan, name="rallymate-motion-sources", daemon=True))
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=3)
