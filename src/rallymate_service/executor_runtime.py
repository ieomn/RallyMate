"""Local execution supervision, independent from API and business progress.

Only a confirmed owned-process-tree exit permits retry. A stale heartbeat is
reported as unavailable; it never interrupts a possibly busy GPU operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread
import time

from rallymate_service.gpu_fleet import (
    UUID_PATTERN, clear_gpu_quarantine, new_managed_worker_id, node_gpu_state_root,
)
from rallymate_vision.owned_subprocess import OwnedProcess, ProcessTreeCleanupError

LOGGER = logging.getLogger("rallymate.executor")
HEARTBEAT_SECONDS = 2.0
STALE_SECONDS = 30.0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_state(path: Path, value: dict):
    """Publish a small complete record, never a partially written heartbeat."""
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf8")
        # Windows readers may briefly hold a handle that denies replacement.
        # Retry that bounded sharing race without turning telemetry into a
        # worker failure; callers still retain their last complete heartbeat.
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.01)
    finally:
        temporary.unlink(missing_ok=True)


def read_state(path: Path, worker_id: str):
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(value, dict) or value.get("worker_id") != worker_id:
            return None
        if value.get("status") not in {"starting", "ready", "busy", "unavailable", "stopped"}:
            return None
        if value.get("work_kind") not in {"loading", "idle", "analysis", "mesh", None}:
            return None
        stamp = datetime.fromisoformat(value["heartbeat_at"])
        if stamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if age < -5:
            return None
        return value, age
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_terminated_owner_gpu_state(worker_id: str):
    """Look up only this owner; the GPU mutex and exact owner fence deletion."""
    root = node_gpu_state_root()
    if not root.exists():
        return
    paths = list(root.glob("*.json"))
    if len(paths) > 256:
        raise ProcessTreeCleanupError("GPU ownership state exceeds recovery bound")
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ProcessTreeCleanupError("GPU ownership state cannot be safely inspected")
        value = json.loads(path.read_text(encoding="utf8"))
        if value.get("worker_id") == worker_id:
            identifier = value.get("gpu_uuid", "")
            if not UUID_PATTERN.fullmatch(identifier):
                raise ProcessTreeCleanupError("GPU ownership identity is invalid")
            if not clear_gpu_quarantine(identifier, worker_id, confirmed_tree_exit=True):
                raise ProcessTreeCleanupError("Terminated GPU ownership could not be cleared")


class LocalExecutorSupervisor:
    """Own one local GPU worker with bounded retries and durable diagnostics."""

    def __init__(self, settings, database, *, poll_seconds=.5, max_restarts=3,
                 restart_delay=2.0, process_factory=OwnedProcess):
        self.settings, self.database = settings, database
        self.poll_seconds = max(.1, poll_seconds)
        self.max_restarts, self.restart_delay = max_restarts, restart_delay
        self.process_factory = process_factory
        self._lock, self._stop, self._drain = Lock(), Event(), Event()
        self._thread = None
        self._child = self._log = self._state_path = self._stop_path = self._drain_path = None
        self._worker_id = None
        self._status = "starting"
        self._message = "执行服务正在启动"
        self._attempt = 0
        self._launched_at = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Executor supervision already started")
        self._thread = Thread(target=self._run, name="rallymate-executor-supervisor", daemon=True)
        self._thread.start()

    def _set(self, status, message):
        with self._lock:
            self._status, self._message = status, message

    def snapshot(self):
        with self._lock:
            status, message = self._status, self._message
            child, state_path, worker_id = self._child, self._state_path, self._worker_id
        result = {"status": status, "available": False, "heartbeat_at": None,
                  "work_kind": None, "message": message}
        if status in {"stopped", "unavailable", "restarting"}:
            return result
        if self._thread is None or not self._thread.is_alive():
            return {**result, "status": "unavailable", "message": "执行服务监管已停止"}
        if child is None:
            return result
        if child.poll() is not None:
            return {**result, "status": "restarting", "message": "执行服务已退出，正在确认清理并恢复"}
        record = read_state(state_path, worker_id)
        if record is None:
            if self._launched_at is not None and time.monotonic() - self._launched_at > STALE_SECONDS:
                return {**result, "status": "unavailable", "message": "执行服务心跳暂时不可用，正在保留当前任务"}
            return result
        value, age = record
        result.update(heartbeat_at=value["heartbeat_at"], work_kind=value["work_kind"])
        if age > STALE_SECONDS:
            return {**result, "status": "unavailable", "message": "执行服务心跳暂时不可用，正在保留当前任务"}
        status = value["status"]
        messages = {"starting": "执行服务正在加载模型", "ready": "执行服务就绪",
                    "busy": "执行服务正在处理任务", "unavailable": "执行服务异常，等待安全恢复",
                    "stopped": "执行服务已停止"}
        return {**result, "status": status, "available": status in {"ready", "busy"},
                "message": messages[status]}

    def _launch(self):
        state_root = self.settings.data_root / "executor-state"
        log_root = self.settings.data_root / "executor-logs"
        state_root.mkdir(parents=True, exist_ok=True)
        log_root.mkdir(parents=True, exist_ok=True)
        worker_id = new_managed_worker_id()
        state_path = state_root / (worker_id + ".json")
        stop_path = state_root / (worker_id + ".stop")
        drain_path = state_root / (worker_id + ".drain")
        log = (log_root / (worker_id + ".log")).open("x", encoding="utf8")
        environment = dict(os.environ)
        # Preserve the local performance configuration; managed multi-GPU
        # profile enforcement and mesh disabling do not apply to this child.
        environment["RALLYMATE_SUPERVISED_LOCAL_WORKER"] = "1"
        try:
            child = self.process_factory([sys.executable, "-u", "-m", "rallymate_service.local_worker",
                "--worker-id", worker_id, "--state-file", str(state_path),
                "--stop-file", str(stop_path), "--drain-file", str(drain_path),
                "--poll-seconds", str(self.poll_seconds)],
                env=environment, stdout=log, stderr=subprocess.STDOUT)
        except BaseException:
            log.close()
            raise
        with self._lock:
            self._worker_id, self._state_path, self._stop_path = worker_id, state_path, stop_path
            self._drain_path = drain_path
            self._child, self._log = child, log
            self._launched_at = time.monotonic()
        LOGGER.info("local executor launched owner=%s log=%s", worker_id, log.name)

    def _cleanup(self):
        child, worker_id = self._child, self._worker_id
        if child is None:
            return
        if child.closed:
            raise ProcessTreeCleanupError("Executor ownership was closed outside supervision")
        child.close()
        if child.closed is not True or child.poll() is None:
            raise ProcessTreeCleanupError("Executor tree exit was not confirmed")
        # Capture after termination, including any last claim before shutdown.
        clear_terminated_owner_gpu_state(worker_id)
        attempts = self.database.running_attempts_for_worker(worker_id)
        recovered = self.database.recover_confirmed_worker_attempts(worker_id, attempts,
            max_attempts=self.settings.max_attempts, confirmed_tree_exit=True)
        LOGGER.info("local executor exit confirmed owner=%s recovered=%s", worker_id, recovered)
        with self._lock:
            self._child = None
        self._log.close()
        self._log = None

    def _run(self):
        try:
            while not self._stop.is_set() and not self._drain.is_set():
                self._set("starting", "执行服务正在启动")
                try:
                    self._launch()
                except Exception:
                    LOGGER.exception("local executor launch failed")
                else:
                    while not self._stop.wait(.5) and self._child.poll() is None:
                        pass
                    if self._stop.is_set():
                        self._stop_path.touch()
                        deadline = time.monotonic() + 15
                        while self._child.poll() is None and time.monotonic() < deadline:
                            time.sleep(.1)
                    self._set("restarting", "执行服务已退出，正在确认清理并恢复")
                    self._cleanup()
                if self._stop.is_set() or self._drain.is_set():
                    break
                self._attempt += 1
                if self._attempt > self.max_restarts:
                    self._set("unavailable", "执行服务恢复失败，请检查服务日志")
                    return
                self._set("restarting", "执行服务正在安全重启")
                self._stop.wait(min(30, self.restart_delay * self._attempt))
        except BaseException:
            # Do not relaunch or recover jobs if cleanup/ownership is uncertain.
            LOGGER.exception("local executor supervision stopped; no unsafe recovery attempted")
            self._set("unavailable", "执行服务需要检查，当前任务已保留")
        else:
            self._set("stopped", "执行服务已停止")

    def request_idle_shutdown(self):
        """Drain at a worker boundary; callers must first freeze API admission.

        No timeout or stale heartbeat kills a live process here. A mesh started
        between the idle snapshot and this request is allowed to finish. The
        caller must await ``stopped`` before terminating API/web processes.
        """
        if self._drain.is_set():
            return self.snapshot()
        statuses = self.database.count_by_status()
        if statuses.get("running", 0) or statuses.get("queued", 0):
            raise ValueError("Cannot stop an executor while analysis jobs are active")
        snapshot = self.snapshot()
        with self._lock:
            if self._child is None and self._status in {"unavailable", "stopped"}:
                # Exhausted retries, or already drained, with confirmed cleanup.
                self._drain.set()
                self._status, self._message = "stopped", "执行服务已停止"
            elif snapshot["status"] == "ready" and snapshot["work_kind"] == "idle":
                self._drain.set()  # Prevent respawn even if the child exits now.
                try:
                    self._drain_path.touch()
                except OSError:
                    self._drain.clear()
                    raise
                self._status, self._message = "restarting", "执行服务正在完成当前步骤并停止维护"
            else:
                raise ValueError("Executor must be confirmed idle before maintenance")
        return self.snapshot()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=25)
            if self._thread.is_alive():
                raise ProcessTreeCleanupError("Executor supervision did not finish shutdown")
        if self._child is not None:
            raise ProcessTreeCleanupError("Executor process-tree cleanup remains unconfirmed")
