from __future__ import annotations

import argparse
import logging

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
    """Run the upload API with one independently supervised local GPU worker."""

    parser = argparse.ArgumentParser(description="RallyMate local inference server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--api-only", action="store_true", help="Serve reports/uploads without loading a GPU worker")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    database = JobDatabase(settings.database_path)
    from rallymate_service.executor_runtime import LocalExecutorSupervisor
    executor = None if args.api_only else LocalExecutorSupervisor(
        settings, database, poll_seconds=max(args.poll_seconds, .1))
    # Bind the listening socket before loading the inference worker.  When a
    # second launcher is started on the same port, uvicorn now fails here and
    # no model thread is created or GPU/CPU resources consumed.
    config = uvicorn.Config(
        create_app(settings, database, executor_status=executor.snapshot if executor else None,
                   executor_shutdown=executor.request_idle_shutdown if executor else None),
        host=args.host,
        port=args.port,
        workers=1,
    )
    socket = config.bind_socket()
    try:
        if executor is not None:
            executor.start()
        server = uvicorn.Server(config)
        server.run(sockets=[socket])
    finally:
        if executor is not None:
            executor.close()
