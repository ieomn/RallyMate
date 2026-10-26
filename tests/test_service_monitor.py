from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("service_monitor", Path(__file__).resolve().parents[1] / "scripts/watch_full_service.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class ServiceMonitorTests(unittest.TestCase):
    def test_rate_uses_deltas_rolling_window_and_resets_after_chunk_repair(self):
        rates = monitor.ReceiveRates()
        self.assertIsNone(rates.sample("upload", 1000, 0))
        self.assertEqual(rates.sample("upload", 1200, 2), 100)
        self.assertEqual(rates.sample("upload", 2000, 10), 100)
        self.assertEqual(rates.sample("upload", 2000, 12), 80)
        self.assertEqual(rates.sample("upload", 2000, 22), 0)
        self.assertIsNone(rates.sample("upload", 500, 24))
        self.assertEqual(rates.sample("upload", 700, 26), 100)

    def test_manifest_committed_bytes_survive_chunk_cleanup_and_bad_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(12):
                directory = root / "uploads/.chunks" / str(index)
                directory.mkdir(parents=True)
                (directory / "manifest.json").write_text(json.dumps({
                    "upload_id": str(index), "filename": "比赛\n\x1b[2J.mp4", "size": 100,
                    "updated_at": 1000 + index, "chunks": {"0": {"size": 100}}, "job_id": str(index),
                }), encoding="utf-8")
            bad = root / "uploads/.chunks/bad"
            bad.mkdir()
            (bad / "manifest.json").write_text("{incomplete", encoding="utf-8")
            rows, failures = monitor.read_uploads(root, 1015)
            self.assertEqual(failures, 1)
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows[0]["id"], "11")
            self.assertEqual(rows[0]["received"], 100)
            self.assertNotIn("\n", rows[0]["name"])
            self.assertNotIn("\x1b", rows[0]["name"])
            self.assertEqual(monitor.read_uploads(root, 100000)[0], [])

    def test_readonly_missing_locked_database_and_active_job_priority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNotNone(monitor.read_jobs(root, 100000)[1])
            self.assertFalse((root / "jobs.sqlite3").exists())
            database = sqlite3.connect(root / "jobs.sqlite3")
            database.execute("CREATE TABLE jobs (id TEXT, original_filename TEXT, status TEXT, progress_json TEXT, updated_at TEXT)")
            database.execute("INSERT INTO jobs VALUES ('old-running', 'active.mp4', 'running', '{bad', '1960-01-01')")
            database.execute("INSERT INTO jobs VALUES ('old-done', 'old.mp4', 'succeeded', '{}', '1960-01-01')")
            database.commit()
            rows, error = monitor.read_jobs(root, 100000)
            self.assertIsNone(error)
            self.assertEqual([row["id"] for row in rows], ["old-running"])
            self.assertEqual(monitor.job_progress(rows[0]), "进度暂不可读")
            _, error = monitor.read_rows(root / "jobs.sqlite3", "DELETE FROM jobs", ())
            self.assertIsNotNone(error)
            database.execute("BEGIN EXCLUSIVE")
            self.assertIsNotNone(monitor.read_jobs(root, 100000)[1])
            database.rollback()
            self.assertEqual(database.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)
            database.close()

    def test_replay_status_and_empty_dashboard_without_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = list(root.iterdir())
            text = monitor.dashboard(root, monitor.ReceiveRates(), now=100000, monotonic=0)
            self.assertIn("服务端确认分块", text)
            self.assertEqual(list(root.iterdir()), before)
            with sqlite3.connect(root / "motion-replays.sqlite3") as database:
                database.execute("CREATE TABLE motion_replays (id TEXT, job_id TEXT, generation_status TEXT, updated_at REAL)")
                database.execute("INSERT INTO motion_replays VALUES ('r', 'j', 'provider_processing', 0)")
            database.close()
            replays, error = monitor.read_replays(root, 100000)
            self.assertIsNone(error)
            self.assertEqual(replays[0]["generation_status"], "provider_processing")
            self.assertIn("MiniMax 生成中", monitor.dashboard(root, monitor.ReceiveRates(), now=100000, monotonic=0))


if __name__ == "__main__":
    unittest.main()
