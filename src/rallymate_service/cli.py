from __future__ import annotations

import argparse
import logging
from threading import Event, Thread

import uvicorn

from rallymate_service.config import load_settings
from rallymate_service.api import create_app
from rallymate_service.database import JobDatabase
from rallymate_service.worker import run_worker


def api_main() -> None:
    parser = argparse.ArgumentParser(description="RallyMate API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    load_settings()
    uvicorn.run(
        "rallymate_service.api:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
    )


def worker_main() -> None:
    parser = argparse.ArgumentParser(description="RallyMate GPU worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    run_worker(
        settings,
        JobDatabase(settings.database_path),
        poll_seconds=max(args.poll_seconds, 0.1),
        once=args.once,
    )


def dev_main() -> None:
    """Run the upload API and one persistent GPU worker in a single process."""

    parser = argparse.ArgumentParser(description="RallyMate local inference server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    database = JobDatabase(settings.database_path)
    stop_event = Event()

    def worker_target() -> None:
        run_worker(
            settings,
            database,
            poll_seconds=max(args.poll_seconds, 0.1),
            stop_event=stop_event,
        )

    worker = Thread(target=worker_target, name="rallymate-gpu-worker", daemon=True)
    worker.start()
    try:
        uvicorn.run(
            create_app(settings, database),
            host=args.host,
            port=args.port,
            workers=1,
        )
    finally:
        stop_event.set()
        worker.join(timeout=15)
