"""Local stdlib-only tests. They do not contact or change a deployed service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing, redirect_stdout
import io
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("rallymate_autodl_monitor", Path(__file__).with_name("monitor.py"))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def database(self):
        path = self.root / "jobs.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, started_at TEXT, lease_expires_at TEXT, progress_json TEXT, original_filename TEXT, error TEXT, attempts INTEGER DEFAULT 1)")
            connection.commit()
        return path

    def add_job(self, path, job_id, status, updated, *, lease=None, progress=None):
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("INSERT INTO jobs(id, status, created_at, updated_at, started_at, lease_expires_at, progress_json, original_filename, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (job_id, status, updated.isoformat(), updated.isoformat(), updated.isoformat(), lease.isoformat() if lease else None, json.dumps(progress or {}), "PRIVATE_PERSON_VIDEO.mp4", "/root/private/SECRET_TOKEN"))
            connection.commit()

    def test_missing_queue_is_not_created(self):
        missing = self.root / "never-created.sqlite3"
        result = monitor.collect_jobs(missing, {}, self.now, 900)
        self.assertFalse(result["ok"])
        self.assertFalse(missing.exists())
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_stalled_progress_and_new_failure_cursor_are_read_only_and_private(self):
        database = self.database()
        self.add_job(database, "old-failure", "failed", self.now - timedelta(hours=1))
        self.add_job(database, "slow-job", "running", self.now - timedelta(minutes=20), lease=self.now + timedelta(minutes=40), progress={"percent": 30, "message": "/root/private", "phase": "inference"})
        state = {}
        first = monitor.collect_jobs(database, state, self.now, 900)
        self.assertTrue(first["running"][0]["stuck"])
        self.assertEqual(first["new_failures"], [])
        self.add_job(database, "new-failure", "failed", self.now + timedelta(seconds=1))
        second = monitor.collect_jobs(database, state, self.now + timedelta(minutes=1), 900)
        self.assertEqual([job["job_id"] for job in second["new_failures"]], ["new-failure"])
        self.assertEqual(monitor.collect_jobs(database, state, self.now + timedelta(minutes=2), 900)["new_failures"], [])
        self.assertNotIn("PRIVATE", json.dumps(second))
        self.assertNotIn("/root/", json.dumps(second))
        self.assertNotIn("SECRET", json.dumps(second))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM jobs WHERE id='slow-job'").fetchone()[0], "running")

    def test_upload_statistics_never_remove_bytes_or_publish_names(self):
        directory = self.root / "upload-session"
        directory.mkdir()
        manifest = {"updated_at": self.now.timestamp() - 90000, "job_id": None, "filename": "PRIVATE_NAME.mp4", "api_key": "SECRET"}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "0.part").write_bytes(b"12345")
        result = monitor.collect_uploads(self.root, self.now)
        self.assertEqual(result["expired_sessions"], 1)
        self.assertEqual(result["partial_bytes"], 5)
        self.assertTrue((directory / "0.part").exists())
        self.assertTrue((directory / "manifest.json").exists())
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("SECRET", json.dumps(result))

    def test_success_cursor_baselines_history_and_delivers_each_new_completion_once(self):
        database = self.database()
        self.add_job(database, "old-success", "succeeded", self.now - timedelta(hours=1))
        self.add_job(database, "old-failure", "failed", self.now - timedelta(minutes=50))
        state = {}
        first = monitor.collect_jobs(database, state, self.now, 900)
        self.assertEqual(first["new_completions"], [])
        self.assertEqual(first["new_failures"], [])
        self.add_job(database, "new-success", "succeeded", self.now + timedelta(seconds=1))
        second = monitor.collect_jobs(database, state, self.now + timedelta(minutes=1), 900)
        self.assertEqual(second["new_completions"], [{"job_id": "new-success", "completed_at": (self.now + timedelta(seconds=1)).isoformat()}])
        self.assertEqual(second["new_failures"], [])
        self.assertEqual(monitor.collect_jobs(database, state, self.now + timedelta(minutes=2), 900)["new_completions"], [])

    def test_fresh_heartbeats_do_not_hide_unchanged_frames_and_progress_resets_timer(self):
        database = self.database()
        self.add_job(database, "stalled-job", "running", self.now, lease=self.now + timedelta(hours=1), progress={"phase": "inference", "percent": 10, "processed_frames": 100, "total_frames": 1000})
        state = {}
        self.assertFalse(monitor.collect_jobs(database, state, self.now, 900)["running"][0]["stuck"])
        later = self.now + timedelta(minutes=16)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE jobs SET updated_at=?, lease_expires_at=? WHERE id='stalled-job'", (later.isoformat(), (later + timedelta(hours=1)).isoformat()))
            connection.commit()
        # Persisted state survives monitor restarts; a fresh DB heartbeat is not progress.
        state = json.loads(json.dumps(state))
        stalled = monitor.collect_jobs(database, state, later, 900)["running"][0]
        self.assertEqual(stalled["last_update_seconds"], 0)
        self.assertEqual(stalled["last_progress_seconds"], 960)
        self.assertTrue(stalled["stuck"])
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE jobs SET progress_json=? WHERE id='stalled-job'", (json.dumps({"phase": "inference", "percent": 10, "processed_frames": 101, "total_frames": 1000}),))
            connection.commit()
        advanced = monitor.collect_jobs(database, state, later + timedelta(seconds=60), 900)["running"][0]
        self.assertFalse(advanced["stuck"])
        self.assertEqual(advanced["last_progress_seconds"], 0)

    def test_atomic_snapshot_and_staleness_are_read_only(self):
        path = self.root / "health.json"
        snapshot = {"schema_version": "rallymate-health/1", "generated_at": self.now.isoformat(), "interval_seconds": 60, "overall_status": "healthy"}
        monitor.atomic_json(path, snapshot)
        before = path.read_bytes()
        self.assertFalse(monitor.read_snapshot(path, self.now + timedelta(seconds=60))["stale"])
        self.assertEqual(monitor.read_snapshot(path, self.now + timedelta(seconds=181))["overall_status"], "stale")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_tunnel_hostname_changes_never_reuse_prior_start(self):
        path = self.root / "cloudflared.log"
        path.write_text("RALLYMATE_TUNNEL_STARTED first\nhttps://old-route.trycloudflare.com\nRALLYMATE_TUNNEL_STARTED second\n")
        self.assertIsNone(monitor.current_tunnel_url(path))
        with path.open("a") as handle:
            handle.write("https://new-route.trycloudflare.com\nhttps://attacker.invalid/\n")
        self.assertEqual(monitor.current_tunnel_url(path), "https://new-route.trycloudflare.com")

    def test_internal_key_is_not_sent_to_public_probe_or_copied_from_health_body(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b'{"status":"ready","private":"SECRET_BODY"}'
        with patch.object(monitor, "build_opener") as make_opener:
            make_opener.return_value.open.return_value = Response()
            local = monitor.probe_http("http://127.0.0.1:8001/health/ready", api_key="SECRET_KEY", readiness=True)
            request = make_opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer SECRET_KEY")
            public = monitor.probe_http("https://safe-route.trycloudflare.com/", api_key="SECRET_KEY")
            request = make_opener.return_value.open.call_args.args[0]
            self.assertIsNone(request.get_header("Authorization"))
            self.assertNotIn("SECRET", json.dumps([local, public]))

    def test_forced_snapshot_entry_does_not_probe_cleanup_or_start_any_process(self):
        monitor.atomic_json(self.root / "health.json", {"schema_version": "rallymate-health/1", "generated_at": self.now.isoformat(), "overall_status": "healthy", "interval_seconds": 60})
        output = io.StringIO()
        with patch.object(monitor, "OPS_DIR", self.root), patch.object(monitor, "utc_now", return_value=self.now), patch("sys.argv", ["monitor.py", "--snapshot"]), patch.object(monitor, "collect_snapshot", side_effect=AssertionError("must not collect")), patch.object(monitor, "clean_upload_parts_once", side_effect=AssertionError("must not clean")), patch.object(monitor.subprocess, "run", side_effect=AssertionError("must not execute")), redirect_stdout(output):
            self.assertEqual(monitor.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["overall_status"], "healthy")

    def test_failure_events_remain_visible_for_fifteen_minute_external_reader(self):
        processes = {name: {"state": "RUNNING", "pid": 1} for name in monitor.PROGRAMS}
        state = {}
        failed = {"ok": True, "counts": {"failed": 1}, "running": [], "new_failures": [{"job_id": "new-failure", "failed_at": self.now.isoformat()}], "new_completions": [{"job_id": "new-success", "completed_at": self.now.isoformat()}]}
        with patch.object(monitor, "utc_now", return_value=self.now), patch.object(monitor, "process_health", return_value=processes), patch.object(monitor, "current_tunnel_url", return_value="https://safe-route.trycloudflare.com"), patch.object(monitor, "probe_http", return_value={"ok": True}), patch.object(monitor, "collect_jobs", return_value=failed), patch.object(monitor, "gpu_health", return_value={"ok": True}), patch.object(monitor, "disk_health", return_value={"ok": True}), patch.object(monitor, "cleanup_uploads", return_value={"ok": True}), patch.object(monitor, "collect_uploads", return_value={"ok": True, "invalid_manifests": 0}):
            first = monitor.collect_snapshot(self.root, state, 60, 900)
            self.assertEqual(first["new_events"][0]["type"], "job_failed")
            self.assertEqual(first["new_events"][1]["type"], "job_succeeded")
            self.assertEqual(first["new_events"][1]["completed_at"], self.now.isoformat())
            failed["new_failures"] = []
            failed["new_completions"] = []
            second = monitor.collect_snapshot(self.root, state, 60, 900)
            self.assertEqual(second["new_events"], [])
            self.assertEqual(second["recent_events"][0]["job_id"], "new-failure")
            self.assertEqual(second["recent_events"][1]["job_id"], "new-success")


if __name__ == "__main__":
    unittest.main()
