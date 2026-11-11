from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from rallymate_service.database import JobDatabase
from rallymate_service.executor_runtime import LocalExecutorSupervisor, read_state, write_state, utc_now
from rallymate_service.gpu_fleet import new_managed_worker_id
from rallymate_vision.owned_subprocess import OwnedProcess, ProcessTreeCleanupError


class FakeProcess:
    def __init__(self, *, exited=False, fail_cleanup=False, before_close=None):
        self.exited, self.fail_cleanup = exited, fail_cleanup
        self.closed = False
        self.before_close = before_close
        self.close_calls = 0

    def poll(self):
        return 1 if self.exited else None

    def close(self):
        self.close_calls += 1
        if self.before_close:
            self.before_close()
        self.closed = True
        if self.fail_cleanup:
            raise ProcessTreeCleanupError("descendant still alive")
        self.exited = True


class LocalExecutorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = JobDatabase(self.root / "jobs.sqlite3")
        self.db.initialize()
        self.settings = SimpleNamespace(data_root=self.root, max_attempts=2)
        self.supervisor = LocalExecutorSupervisor(self.settings, self.db, restart_delay=.01)
        self.supervisor._thread = SimpleNamespace(is_alive=lambda: True)
        self.supervisor._worker_id = new_managed_worker_id()
        self.supervisor._state_path = self.root / "state.json"
        self.supervisor._drain_path = self.root / "drain"
        self.supervisor._child = FakeProcess()
        self.supervisor._log = io.StringIO()
        clear = patch("rallymate_service.executor_runtime.clear_terminated_owner_gpu_state")
        self.clear = clear.start()
        self.addCleanup(clear.stop)

    def state(self, **overrides):
        value = {"worker_id": self.supervisor._worker_id, "status": "ready",
                 "heartbeat_at": utc_now(), "work_kind": "idle", **overrides}
        write_state(self.supervisor._state_path, value)

    def job(self, name, owner):
        self.db.create_job(name, "video.mp4", self.root / "video.mp4", self.root / "request.json", self.root / name)
        return self.db.claim_next(owner, 60, 2, expected_job_id=name, recover_expired=False)

    def test_heartbeat_reports_idle_analysis_mesh_without_business_progress(self):
        for status, kind in (("ready", "idle"), ("busy", "analysis"), ("busy", "mesh")):
            self.state(status=status, work_kind=kind)
            snapshot = self.supervisor.snapshot()
            self.assertEqual(snapshot["status"], status)
            self.assertEqual(snapshot["work_kind"], kind)
            self.assertTrue(snapshot["available"])
        self.assertNotIn("worker_id", snapshot)
        self.assertNotIn("pid", snapshot)

    def test_exited_process_overrides_fresh_ready_heartbeat_immediately(self):
        self.state()
        self.supervisor._child.exited = True
        result = self.supervisor.snapshot()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "restarting")

    def test_stale_heartbeat_never_kills_or_requeues_live_task(self):
        original = self.job("busy", self.supervisor._worker_id)
        self.state(status="busy", work_kind="analysis",
            heartbeat_at=(datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat())
        self.assertEqual(self.supervisor.snapshot()["status"], "unavailable")
        self.assertEqual(self.supervisor._child.close_calls, 0)
        self.assertEqual(self.db.get_job("busy"), original)

    def test_missing_heartbeat_after_startup_grace_is_unavailable_without_kill(self):
        self.supervisor._launched_at = time.monotonic() - 40
        self.assertEqual(self.supervisor.snapshot()["status"], "unavailable")
        self.assertEqual(self.supervisor._child.close_calls, 0)

    def test_maintenance_refuses_analysis_queue_and_busy_mesh(self):
        self.state()
        self.job("busy", self.supervisor._worker_id)
        with self.assertRaisesRegex(ValueError, "active"):
            self.supervisor.request_idle_shutdown()
        self.assertFalse(self.supervisor._drain.is_set())
        self.db.mark_succeeded("busy", {}, worker_id=self.supervisor._worker_id, attempts=1)
        self.state(status="busy", work_kind="mesh")
        with self.assertRaisesRegex(ValueError, "idle"):
            self.supervisor.request_idle_shutdown()
        self.assertFalse(self.supervisor._drain_path.exists())
        self.assertEqual(self.supervisor._child.close_calls, 0)

    def test_maintenance_does_not_signal_cancellation_or_terminate_live_child(self):
        self.state()
        result = self.supervisor.request_idle_shutdown()
        self.assertEqual(result["status"], "restarting")
        self.assertTrue(self.supervisor._drain_path.exists())
        self.assertFalse(self.supervisor._stop.is_set())
        self.assertFalse(self.supervisor._child.closed)
        self.assertEqual(self.supervisor._child.close_calls, 0)

    def test_maintenance_waits_for_boundary_exit_and_never_respawns(self):
        child = FakeProcess()
        ready = Event()
        launches = []
        def factory(args, **kwargs):
            launches.append(1)
            worker_id = args[args.index("--worker-id") + 1]
            state_file = Path(args[args.index("--state-file") + 1])
            write_state(state_file, {"worker_id": worker_id, "status": "ready",
                        "heartbeat_at": utc_now(), "work_kind": "idle"})
            ready.set()
            return child
        supervisor = LocalExecutorSupervisor(self.settings, self.db, process_factory=factory, restart_delay=.001)
        supervisor.start()
        self.assertTrue(ready.wait(3))
        deadline = time.monotonic() + 3
        while supervisor.snapshot()["status"] != "ready" and time.monotonic() < deadline:
            time.sleep(.01)
        supervisor.request_idle_shutdown()
        # A task racing the idle snapshot may finish at its own boundary. Even
        # advancing the clock far beyond shutdown grace must not force-kill it.
        with patch("rallymate_service.executor_runtime.time.monotonic", return_value=10**12):
            time.sleep(.7)
        self.assertEqual(child.close_calls, 0)
        self.assertEqual(supervisor.snapshot()["status"], "restarting")
        child.exited = True
        supervisor._thread.join(timeout=3)
        self.assertEqual(supervisor.snapshot()["status"], "stopped")
        self.assertEqual(child.close_calls, 1)
        self.assertEqual(len(launches), 1)
        supervisor.close()

    def test_drained_supervisor_is_idempotent_and_failed_unowned_supervisor_can_stop(self):
        self.supervisor._child = None
        self.supervisor._status = "unavailable"
        self.assertEqual(self.supervisor.request_idle_shutdown()["status"], "stopped")
        self.assertEqual(self.supervisor.request_idle_shutdown()["status"], "stopped")

    def test_supervisor_thread_death_never_returns_ready(self):
        self.state()
        self.supervisor._thread = SimpleNamespace(is_alive=lambda: False)
        self.assertFalse(self.supervisor.snapshot()["available"])

    def test_cleanup_recovers_only_owner_after_tree_exit_including_last_claim(self):
        owner = self.supervisor._worker_id
        other = self.job("other", new_managed_worker_id())
        self.job("original", owner)
        self.supervisor._child.before_close = lambda: self.job("last-claim", owner)
        child = self.supervisor._child
        self.supervisor._cleanup()
        self.assertTrue(child.closed)
        self.assertEqual(self.db.get_job("original")["status"], "queued")
        self.assertEqual(self.db.get_job("last-claim")["status"], "queued")
        self.assertEqual(self.db.get_job("other"), other)
        self.clear.assert_called_once_with(owner)

    def test_failed_tree_cleanup_does_not_recover_or_clear_gpu(self):
        original = self.job("busy", self.supervisor._worker_id)
        self.supervisor._child.fail_cleanup = True
        with self.assertRaises(ProcessTreeCleanupError):
            self.supervisor._cleanup()
        self.assertEqual(self.db.get_job("busy"), original)
        self.clear.assert_not_called()

    def test_cancelled_attempt_is_cancelled_instead_of_retried(self):
        self.job("cancel", self.supervisor._worker_id)
        self.db.request_cancel("cancel")
        self.supervisor._cleanup()
        self.assertEqual(self.db.get_job("cancel")["status"], "cancelled")

    def test_crash_loop_is_bounded_and_logs_are_preserved_with_config(self):
        launches = []
        def factory(args, **kwargs):
            self.assertEqual(kwargs["env"]["RALLYMATE_SAM3D_BATCH_FRAMES"], "8")
            self.assertNotIn("RALLYMATE_MANAGED_GPU_UUID", kwargs["env"])
            self.assertEqual(kwargs["env"]["RALLYMATE_SUPERVISED_LOCAL_WORKER"], "1")
            launches.append((args, kwargs["stdout"].name))
            return FakeProcess(exited=True)
        supervisor = LocalExecutorSupervisor(self.settings, self.db, process_factory=factory,
            max_restarts=1, restart_delay=.001)
        with patch.dict("os.environ", {"RALLYMATE_SAM3D_BATCH_FRAMES": "8"}, clear=True):
            supervisor.start()
            supervisor._thread.join(timeout=5)
        self.assertFalse(supervisor._thread.is_alive())
        self.assertEqual(len(launches), 2)
        self.assertNotEqual(launches[0][1], launches[1][1])
        self.assertTrue(all(Path(path).exists() for _, path in launches))
        self.assertEqual(supervisor.snapshot()["status"], "unavailable")
        supervisor.close()

    def test_cleanup_failure_stops_restarts_and_shutdown_reports_unconfirmed_tree(self):
        calls = []
        def factory(*args, **kwargs):
            calls.append(1)
            return FakeProcess(exited=True, fail_cleanup=True)
        supervisor = LocalExecutorSupervisor(self.settings, self.db, process_factory=factory, restart_delay=.001)
        with self.assertLogs("rallymate.executor", level="ERROR"):
            supervisor.start()
            supervisor._thread.join(timeout=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(supervisor.snapshot()["status"], "unavailable")
        with self.assertRaises(ProcessTreeCleanupError):
            supervisor.close()
        supervisor._log.close()

    def test_real_owned_non_gpu_child_exit_triggers_recovery_before_relaunch(self):
        # Exercise real OS process ownership, without loading inference/models.
        launches, recovered_before_restart = [], []
        def factory(args, **kwargs):
            owner = args[args.index("--worker-id") + 1]
            if launches:
                recovered_before_restart.append(self.db.get_job("crash")["status"])
            else:
                self.job("crash", owner)
            launches.append(owner)
            return OwnedProcess([sys.executable, "-c", "raise SystemExit(7)"], **kwargs)
        supervisor = LocalExecutorSupervisor(self.settings, self.db, process_factory=factory,
            max_restarts=1, restart_delay=.001)
        supervisor.start()
        supervisor._thread.join(timeout=10)
        self.assertFalse(supervisor._thread.is_alive())
        self.assertEqual(recovered_before_restart, ["queued"])
        self.assertEqual(len(set(launches)), 2)
        self.assertEqual(supervisor.snapshot()["status"], "unavailable")
        supervisor.close()

    def test_two_real_cpu_owned_start_drain_cycles_release_durable_gpu_marker(self):
        # A fake physical UUID exercises the real OS mutex and ownership marker,
        # not CUDA. Repeating the cycle catches preview restart quarantine bugs.
        identifier = "GPU-" + str(uuid.uuid4())
        state_root = self.root / "gpu-state"
        child_code = """
import sys,time
from pathlib import Path
from rallymate_service.gpu_fleet import GPUWorkerSlot
from rallymate_service.executor_runtime import write_state,utc_now
owner, state, drain, gpu_root, identifier = sys.argv[1:]
with GPUWorkerSlot(Path(gpu_root), identifier, worker_id=owner, state_directory=Path(gpu_root)):
    while not Path(drain).exists():
        write_state(Path(state), {'worker_id':owner, 'status':'ready', 'work_kind':'idle', 'heartbeat_at':utc_now()})
        time.sleep(.02)
"""
        def factory(args, **kwargs):
            return OwnedProcess([sys.executable, "-u", "-c", child_code,
                args[args.index("--worker-id") + 1], args[args.index("--state-file") + 1],
                args[args.index("--drain-file") + 1], str(state_root), identifier], **kwargs)
        for _ in range(2):
            supervisor = LocalExecutorSupervisor(self.settings, self.db, process_factory=factory)
            supervisor.start()
            try:
                deadline = time.monotonic() + 10
                while supervisor.snapshot()["status"] != "ready" and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(supervisor.snapshot()["status"], "ready")
                self.assertTrue(list(state_root.glob("*.json")))
                supervisor.request_idle_shutdown()
                supervisor._thread.join(timeout=10)
                self.assertEqual(supervisor.snapshot()["status"], "stopped")
                self.assertEqual(list(state_root.glob("*.json")), [])
            finally:
                if supervisor._drain_path is not None:
                    supervisor._drain_path.touch()
                supervisor.close()

    def test_sidecar_rejects_wrong_owner_malformed_time_and_oversized_data(self):
        for changes in ({"worker_id": "another"}, {"heartbeat_at": "bad"}, {"work_kind": "invented"}):
            self.state(**changes)
            self.assertIsNone(read_state(self.supervisor._state_path, self.supervisor._worker_id))
        self.supervisor._state_path.write_text("x" * 5000)
        self.assertIsNone(read_state(self.supervisor._state_path, self.supervisor._worker_id))

    def test_local_worker_emits_heartbeat_while_business_progress_is_unchanged(self):
        from rallymate_service import local_worker
        state_file, stop_file = self.root / "heartbeat.json", self.root / "stop"
        owner = new_managed_worker_id()
        stamps = []
        settings = SimpleNamespace(database_path=self.root / "jobs.sqlite3")
        def runner(settings, database, **kwargs):
            self.assertEqual(kwargs["worker_id"], owner)
            kwargs["on_state"]("analysis", None)
            first = read_state(state_file, owner)[0]["heartbeat_at"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                value = read_state(state_file, owner)
                if value and value[0]["heartbeat_at"] != first:
                    stamps.extend((first, value[0]["heartbeat_at"]))
                    self.assertEqual(value[0]["work_kind"], "analysis")
                    break
                time.sleep(.01)
            stop_file.touch()
            self.assertTrue(kwargs["stop_event"].wait(3))
        with patch.object(sys, "argv", ["local_worker", "--worker-id", owner,
                "--state-file", str(state_file), "--stop-file", str(stop_file),
                "--drain-file", str(self.root / "drain")]), \
             patch("rallymate_service.config.load_settings", return_value=settings), \
             patch("rallymate_service.worker.run_worker", side_effect=runner), \
             patch.object(local_worker, "HEARTBEAT_SECONDS", .03):
            local_worker.main()
        self.assertEqual(len(stamps), 2)
        self.assertNotEqual(*stamps)


if __name__ == "__main__":
    unittest.main()
