from __future__ import annotations

import argparse
import logging
import signal
import threading

import uvicorn

from rallymate_service.config import load_settings
from rallymate_service.database_factory import build_database
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


def install_stop_signals(stop_event: threading.Event) -> dict:
    """Route SIGTERM/SIGINT into the worker's cooperative stop event.

    ``docker stop`` sends SIGTERM. The first signal stops claiming new work,
    cancels an optional mesh render and lets the loop release its GPU slot;
    a second signal falls back to an immediate KeyboardInterrupt. Returns the
    previous handlers so callers (and tests) can restore them.
    """
    logger = logging.getLogger("rallymate.worker")

    def request_stop(signum, _frame):
        if stop_event.is_set():
            raise KeyboardInterrupt
        logger.warning("Received signal %s; stopping after the current task", signum)
        stop_event.set()

    previous = {}
    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        previous[number] = signal.getsignal(number)
        signal.signal(number, request_stop)
    return previous


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
    from rallymate_service.observability import observe_worker
    database = build_database(settings)
    stop_event = threading.Event()
    install_stop_signals(stop_event)
    with observe_worker(settings, database):
        run_worker(settings, database, poll_seconds=max(args.poll_seconds, 0.1), once=args.once,
                   stop_event=stop_event)


def dev_main() -> None:
    """Run the upload API with one independently supervised local GPU worker."""

    from rallymate_service.api import create_app

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
    database = build_database(settings)
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
