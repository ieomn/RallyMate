"""Supervised local worker: all inference/mesh children stay in the owned tree."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from threading import Event, Lock, Thread

from rallymate_service.executor_runtime import HEARTBEAT_SECONDS, utc_now, write_state
from rallymate_service.gpu_fleet import require_managed_worker_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--drain-file", required=True, type=Path)
    parser.add_argument("--poll-seconds", type=float, default=.5)
    args = parser.parse_args()
    worker_id = require_managed_worker_id(args.worker_id)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop, pulse_stop, lock = Event(), Event(), Lock()
    state = {"worker_id": worker_id, "status": "starting", "work_kind": "loading"}

    def pulse():
        while not pulse_stop.is_set():
            try:
                with lock:
                    write_state(args.state_file, {**state, "heartbeat_at": utc_now()})
                if args.stop_file.exists():
                    stop.set()
            except OSError:
                logging.exception("executor heartbeat publication failed")
            pulse_stop.wait(HEARTBEAT_SECONDS)

    def on_state(value, error):
        status, work = {
            "loading_models": ("starting", "loading"), "ready": ("ready", "idle"),
            "analysis": ("busy", "analysis"), "mesh": ("busy", "mesh"),
            "failed": ("unavailable", None), "stopped": ("stopped", None),
        }.get(value, ("unavailable", None))
        with lock:
            state.update(status=status, work_kind=work)
            try:
                write_state(args.state_file, {**state, "heartbeat_at": utc_now()})
            except OSError:
                logging.exception("executor state publication failed")
        if error:
            logging.error("local executor state=%s: %s", value, error)

    heartbeat = Thread(target=pulse, name="executor-heartbeat", daemon=True)
    heartbeat.start()
    try:
        # Model import itself can take time; heartbeat starts before it.
        from rallymate_service.config import load_settings
        from rallymate_service.database import JobDatabase
        from rallymate_service.worker import run_worker
        settings = load_settings()
        run_worker(settings, JobDatabase(settings.database_path), poll_seconds=max(.1, args.poll_seconds),
                   stop_event=stop, worker_id=worker_id, on_state=on_state,
                   should_drain=args.drain_file.exists)
        on_state("stopped", None)
    except BaseException:
        on_state("failed", None)
        raise
    finally:
        pulse_stop.set()
        heartbeat.join(timeout=5)


if __name__ == "__main__":
    main()
