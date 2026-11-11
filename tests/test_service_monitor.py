from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("service_monitor", Path(__file__).resolve().parents[1] / "scripts/watch_full_service.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)
NOW = 1_800_000_000


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def job(identifier, status, *, updated=NOW, created=None, progress=None, error=None):
    return {"id": identifier, "original_filename": identifier + ".mp4", "status": status,
            "progress_json": json.dumps(progress or {}), "updated_at": iso(updated),
            "created_at": iso(updated if created is None else created), "error": error}


def create_database(root, rows=()):
    path = root / "jobs.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("""CREATE TABLE jobs (id TEXT, original_filename TEXT, status TEXT,
            progress_json TEXT, updated_at TEXT, created_at TEXT, error TEXT)""")
        connection.executemany("""INSERT INTO jobs VALUES (
            :id, :original_filename, :status, :progress_json, :updated_at, :created_at, :error)""", rows)
        connection.commit()
    return path


def upload_manifest(root, identifier, *, received=50, total=100, submitted=False, updated=NOW, filename="比赛.mp4"):
    directory = root / "uploads/.chunks" / identifier
    directory.mkdir(parents=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps({"upload_id": identifier, "filename": filename, "size": total,
        "updated_at": updated, "chunks": {"0": {"size": received}},
        "job_id": identifier if submitted else None}), encoding="utf-8")
    return path


def create_logs(root):
    for name in monitor.LOG_NAMES:
        (root / name).write_bytes(b"")


def append_log(root, name, value):
    with (root / name).open("ab") as handle:
        handle.write(value.encode("utf-8"))


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
        self.assertIsNone(rates.sample("upload", 700, 26))
        rates.sample("other", 0, 0)
        rates.retain({"upload"})
        self.assertEqual(set(rates.samples), {"upload"})

    def test_pending_uploads_are_not_capped_and_submitted_uploads_disappear(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(12):
                upload_manifest(root, str(index), received=100, updated=NOW - index, filename="比赛\n\x1b[2J.mp4")
            upload_manifest(root, "submitted", received=100, submitted=True)
            upload_manifest(root, "expired", updated=NOW - monitor.RECENT_SECONDS - 1)
            upload_manifest(root, "bad").write_text("{incomplete", encoding="utf-8")
            rows, failures = monitor.read_uploads(root, NOW)
            self.assertEqual(failures, 1)
            self.assertEqual(len(rows), 12)
            self.assertEqual(rows[0]["id"], "0")
            self.assertEqual(rows[0]["received"], 100)
            self.assertNotIn("submitted", [row["id"] for row in rows])
            self.assertNotIn("expired", [row["id"] for row in rows])
            self.assertNotIn("\n", rows[0]["name"])
            self.assertNotIn("\x1b", rows[0]["name"])
            self.assertFalse(list(root.rglob("*.part")))
            self.assertEqual(monitor.read_uploads(root, NOW + monitor.RECENT_SECONDS + 1)[0], [])

    def test_invalid_upload_byte_counts_are_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upload_manifest(root, "overflow", received=101)
            upload_manifest(root, "negative", received=-1)
            upload_manifest(root, "boolean", received=True)
            upload_manifest(root, "zero-total", total=0, received=0)
            upload_manifest(root, "nonfinite", updated=float("nan"))
            self.assertEqual(monitor.read_uploads(root, NOW), ([], 5))

    def test_readonly_missing_locked_database_and_invalid_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for read in (monitor.read_jobs, monitor.read_counts, monitor.read_history):
                self.assertIn("不存在", read(root, NOW)[1])
            self.assertFalse((root / "jobs.sqlite3").exists())
            row = job("old-running", "running", updated=0)
            row["progress_json"] = "{bad"
            path = create_database(root, [row, job("old-done", "succeeded", updated=0)])
            rows, error = monitor.read_jobs(root, NOW)
            self.assertIsNone(error)
            self.assertEqual([row["id"] for row in rows], ["old-running"])
            self.assertEqual(monitor.job_progress(rows[0]), "进度暂不可读")
            self.assertIsNotNone(monitor.read_rows(path, "DELETE FROM jobs", ())[1])
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(database.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)
                database.execute("BEGIN EXCLUSIVE")
                self.assertIn("忙", monitor.read_jobs(root, NOW)[1])
                text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW, monotonic=0)
                self.assertIn("状态未知", text)
                self.assertNotIn("空闲，等待新任务", text)
                database.rollback()
                self.assertEqual(database.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_running_priority_counts_all_active_and_history_prioritizes_recent_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [job(f"queue-{index}", "queued", updated=0, created=index) for index in range(12)]
            rows += [job("running-new", "running", updated=0, created=200),
                     job("running-old", "running", updated=0, created=100),
                     job("recent-success", "succeeded", updated=NOW - 1),
                     job("recent-failure", "failed", updated=NOW - 2, error="historic failure"),
                     job("recent-cancel", "cancelled", updated=NOW - 3),
                     job("expired-failure", "failed", updated=NOW - monitor.RECENT_SECONDS - 1)]
            create_database(root, rows)
            active, error = monitor.read_jobs(root, NOW)
            self.assertIsNone(error)
            self.assertEqual(len(active), monitor.LIMIT)
            self.assertEqual([row["id"] for row in active[:3]], ["running-old", "running-new", "queue-0"])
            self.assertTrue(all(row["status"] in {"running", "queued"} for row in active))
            counts, error = monitor.read_counts(root, NOW)
            self.assertIsNone(error)
            self.assertEqual(counts, {"running": 2, "queued": 12, "succeeded": 1, "failed": 1, "cancelled": 1})
            history, error = monitor.read_history(root, NOW)
            self.assertIsNone(error)
            self.assertEqual([row["id"] for row in history], ["recent-failure", "recent-success", "recent-cancel"])
            self.assertEqual(history[0]["error"], "historic failure")

    def test_terminal_status_overrides_stale_percent_and_phase(self):
        for state, label in (("cancelled", "已取消"), ("failed", "失败"), ("succeeded", "分析完成")):
            with self.subTest(state=state):
                self.assertEqual(monitor.job_progress(job("j", state, progress={"phase": "inference", "percent": 8})), label)

    def test_dashboard_has_no_replay_reads_or_side_effects_and_missing_database_is_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = list(root.iterdir())
            with patch.object(monitor, "read_rows", wraps=monitor.read_rows) as reads:
                text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW, monotonic=0)
            self.assertEqual(list(root.iterdir()), before)
            self.assertIn("无法判断队列", text)
            self.assertIn("分析 / 排队：未知", text)
            self.assertNotIn("空闲，等待新任务", text)
            self.assertNotIn("MiniMax", text)
            self.assertNotIn("白模", text)
            self.assertTrue(reads.call_args_list)
            self.assertTrue(all(call.args[0].name == "jobs.sqlite3" for call in reads.call_args_list))

    def test_schema_mismatch_is_not_an_empty_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with closing(sqlite3.connect(root / "jobs.sqlite3")) as database:
                database.execute("CREATE TABLE other (id TEXT)")
            text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW, monotonic=0)
            self.assertIn("结构不匹配", text)
            self.assertNotIn("空闲，等待新任务", text)

    def test_dashboard_is_height_bounded_counts_hidden_rows_and_separates_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root, [job("running", "running", progress={"phase": "inference", "percent": 12})]
                + [job(f"queue-{index}", "queued") for index in range(10)]
                + [job("cancelled", "cancelled", progress={"phase": "cancelled", "percent": 8}),
                   job("failed", "failed", updated=NOW - 1, error="HISTORIC_FAILURE")])
            for index in range(12):
                upload_manifest(root, str(index))
            for height in (24, 32):
                with self.subTest(height=height):
                    text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW, monotonic=0,
                        height=height, log_errors=["CURRENT_ERROR", "SECOND_ERROR", "THIRD_ERROR"],
                        log_issues=["LOG_UNAVAILABLE"], error_limit=2)
                    self.assertLessEqual(len(text.splitlines()), height)
                    self.assertIn("上传 12  |  分析 1  |  排队 10", text)
                    self.assertIn("失败 1  已取消 1（历史记录）", text)
                    self.assertIn("CURRENT_ERROR", text)
                    self.assertIn("LOG_UNAVAILABLE", text)
                    self.assertNotIn("SECOND_ERROR", text)
                    self.assertIn("另有 2 条提示", text)
                    self.assertNotIn("8.0%", text)
                    self.assertIn("Ctrl+C", text)
                    attention = text.split("[需要关注", 1)[1].split("[当前任务]", 1)[0]
                    self.assertNotIn("HISTORIC_FAILURE", attention)

    def test_dashboard_only_history_failure_does_not_claim_current_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root, [job("past", "failed", error="HISTORIC_FAILURE")])
            text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW, monotonic=0, height=32)
            self.assertIn("未发现新增异常", text)
            self.assertIn("HISTORIC_FAILURE", text.split("不代表当前故障", 1)[1])
            self.assertIn("空闲，等待新任务", text)

    def test_safe_text_removes_controls_and_credentials(self):
        value = "名\n\r\x1b[2J\u202e token=private_token Authorization:Bearer_secret Bearer abcdefghijklmnop sk-abcdefghijklmnop"
        cleaned = monitor.safe_text(value, 500)
        for raw in ("\n", "\r", "\x1b", "\u202e", "private_token", "Bearer_secret", "abcdefghijklmnop"):
            self.assertNotIn(raw, cleaned)
        self.assertIn("已隐藏", cleaned)


class MonitorPipelineTests(unittest.TestCase):
    def host(self):
        return {"network": {"interfaces": [{"name": "以太网", "is_up": True,
            "rx_bytes_per_second": 2048, "tx_bytes_per_second": 1024}]},
            "cpu": {"percent": 25}, "memory": {"used_bytes": 1024, "total_bytes": 2048},
            "gpu": {"devices": [{"index": 0, "utilization_percent": 70,
                "memory_used_bytes": 1024, "memory_total_bytes": 2048}], "sample_age_seconds": 2}}

    def test_tiny_windows_preserve_network_upload_rate_and_analysis(self):
        from scripts.service_monitor_terminal import clip_text
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root, [job("running", "running", progress={"phase": "inference",
                "percent": 25, "processed_frames": 100, "total_frames": 400, "effective_fps": 10})])
            manifest = upload_manifest(root, "upload", received=20)
            rates = monitor.ReceiveRates()
            monitor.collect_snapshot(root, rates, NOW, 0)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["chunks"]["0"]["size"] = 40
            manifest.write_text(json.dumps(data), encoding="utf-8")
            snapshot = monitor.collect_snapshot(root, rates, NOW + 2, 2, host=self.host())
            for height in (10, 12, 16, 24, 40):
                for width in (40, 60, 120):
                    text = monitor.dashboard(root, rates, now=NOW+2, monotonic=2,
                        height=height, snapshot=snapshot)
                    visible = "\n".join(clip_text(line, width-1) for line in text.splitlines())
                    self.assertLess(len(visible.splitlines()), height)
                    self.assertIn("2.0 KiB/s", visible)
                    self.assertIn("10.0 B/s", visible)
                    self.assertIn("分析", visible)
                    self.assertIn("Ctrl+C", visible)

    def test_inference_eta_is_not_report_eta_and_stale_rate_is_not_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = create_database(root, [job("j", "running", created=NOW-100,
                progress={"phase": "inference", "percent": 25, "processed_frames": 100,
                          "total_frames": 400, "effective_fps": 10})])
            samples = monitor.JobSamples()
            snapshot = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW, 10, job_samples=samples)
            self.assertEqual(snapshot["jobs"][0]["eta_seconds"], 30)
            self.assertEqual(snapshot["jobs"][0]["elapsed_seconds"], 100)
            stale = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW+61, 71, job_samples=samples)
            self.assertIsNone(stale["jobs"][0]["eta_seconds"])
            progress = json.dumps({"phase": "writing_results", "percent": 90,
                "processed_frames": 400, "total_frames": 400, "effective_fps": 10,
                "completed_items": 3, "total_items": 20})
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("UPDATE jobs SET progress_json=?, updated_at=?", (progress, iso(NOW+62)))
                conn.commit()
            finalizing = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW+62, 72, job_samples=samples)
            row = finalizing["jobs"][0]
            self.assertIsNone(row["eta_seconds"])
            self.assertIsNone(row["observed_fps"])
            self.assertEqual(row["observed_phase_seconds"], 0)
            text = monitor.dashboard(root, monitor.ReceiveRates(), now=NOW+62, snapshot=finalizing, height=40)
            self.assertIn("本阶段 3/20", text)
            self.assertNotIn("推理剩余", text)

    def test_recording_is_opt_in_bounded_and_excludes_large_raw_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root, [job("j", "running", progress={"phase": "inference", "percent": 1,
                "unrelated_secret": "never-export", "message": "token=secret-token"})])
            snapshot = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW, 0, host=self.host())
            path = root / "samples/log.jsonl"
            self.assertFalse(path.parent.exists())
            recorder = monitor.SnapshotRecorder(path)
            self.assertIsNone(recorder.write(snapshot))
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn("never-export", saved)
            self.assertNotIn("secret-token", saved)
            self.assertEqual(json.loads(saved)["version"], "rallymate-monitor-v2")
            limited = monitor.SnapshotRecorder(path, max_bytes=path.stat().st_size)
            self.assertIn("停止追加", limited.write(snapshot))
            self.assertEqual(path.read_text(encoding="utf-8"), saved)

    def test_counter_errors_and_missing_host_never_become_zero_bandwidth(self):
        self.assertIn("未采样", " ".join(monitor.host_lines(None)))
        self.assertIn("不可用", " ".join(monitor.host_lines({"network": {"interfaces": []}})))
        self.assertIsNone(monitor.finite_number(float("nan")))
        self.assertIsNone(monitor.finite_number(True))
        self.assertIsNone(monitor.finite_number(-1))

    def test_unknown_interface_state_does_not_hide_valid_traffic(self):
        host = self.host()
        host["network"]["interfaces"][0]["is_up"] = None
        self.assertIn("2.0 KiB/s", monitor.host_lines(host)[0])

    def test_recorder_refuses_service_data_and_unrelated_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "service"
            data.mkdir()
            original = b'{"private":"keep unchanged"}\n'
            for path in (data / "manifest.json", data / "frames.jsonl", root / "unrelated.jsonl"):
                path.write_bytes(original)
                with self.assertRaises(ValueError):
                    monitor.SnapshotRecorder(path, data_root=data)
                self.assertEqual(path.read_bytes(), original)

    def test_record_projection_omits_free_text_and_pseudonymizes_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root, [job("sensitive-id", "running", progress={"phase": "inference", "percent": 1,
                "message": "password=private-pw C:/private/location"}),
                job("past", "failed", error="password=private-error")])
            snapshot = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW, 0, host=self.host())
            recorded = json.dumps(monitor.record_projection(snapshot), ensure_ascii=False)
            for value in ("sensitive-id", "private-pw", "private-error", "C:/private/location", ".mp4", "以太网"):
                self.assertNotIn(value, recorded)
            self.assertIn("rx_bytes_per_second", recorded)

    def test_active_upload_is_shown_ahead_of_newer_completed_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_database(root)
            upload_manifest(root, "completed", received=100)
            upload_manifest(root, "active", received=20, updated=NOW-1)
            upload_manifest(root, "stalled", received=20, updated=NOW-200)
            snapshot = monitor.collect_snapshot(root, monitor.ReceiveRates(), NOW, 0)
            self.assertEqual([item["id"] for item in snapshot["uploads"]], ["active", "completed", "stalled"])


class RecentLogErrorsTests(unittest.TestCase):
    def test_initial_seed_uses_recent_explicit_times_and_ignores_old_undated_and_202(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_logs(root)
            append_log(root, "api.err.log", "ERROR undated old failure\n"
                + f"{iso(NOW - 301)} ERROR expired failure\n"
                + f"{iso(NOW - 1)} ERROR recent failure\n")
            append_log(root, "api.log", 'INFO: "GET /v1/jobs/j/demo-result HTTP/1.1" 202 Accepted\n')
            errors, issues = monitor.RecentLogErrors(root).poll(NOW)
            self.assertEqual(issues, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("recent failure", errors[0])
            self.assertNotIn("undated", "\n".join(errors))
            self.assertNotIn("expired", "\n".join(errors))

    def test_incremental_undated_errors_are_deduplicated_not_replayed_and_expire(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_logs(root)
            reader = monitor.RecentLogErrors(root)
            self.assertEqual(reader.poll(NOW), ([], []))
            append_log(root, "api.err.log", "ERROR repeated failure\n")
            errors, _ = reader.poll(NOW + 1)
            self.assertEqual(len(errors), 1)
            self.assertNotIn("×", errors[0])
            self.assertEqual(reader.poll(NOW + 2)[0], errors)
            append_log(root, "api.err.log", "ERROR repeated failure\n")
            errors, _ = reader.poll(NOW + 3)
            self.assertEqual(len(errors), 1)
            self.assertIn("×2", errors[0])
            self.assertEqual(reader.poll(NOW + 4)[0], errors)
            self.assertEqual(reader.poll(NOW + 304)[0], [])

    def test_incomplete_lines_wait_for_newline_and_are_read_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_logs(root)
            reader = monitor.RecentLogErrors(root)
            reader.poll(NOW)
            append_log(root, "web.err.log", "ERROR partial")
            self.assertEqual(reader.poll(NOW + 1)[0], [])
            append_log(root, "web.err.log", " line\n")
            errors, _ = reader.poll(NOW + 2)
            self.assertEqual(len(errors), 1)
            self.assertIn("partial line", errors[0])
            self.assertEqual(reader.poll(NOW + 3)[0], errors)

    def test_file_replacement_and_truncation_drop_previous_errors(self):
        for rotate in ("replace", "truncate"):
            with self.subTest(rotate=rotate), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                create_logs(root)
                append_log(root, "api.err.log", f"{iso(NOW)} ERROR old session " + "x" * 300 + "\n")
                reader = monitor.RecentLogErrors(root)
                self.assertEqual(len(reader.poll(NOW)[0]), 1)
                fresh = f"{iso(NOW + 1)} ERROR new session\n"
                target = root / "api.err.log"
                if rotate == "replace":
                    replacement = root / "replacement.log"
                    replacement.write_text(fresh, encoding="utf-8")
                    replacement.replace(target)
                else:
                    target.write_text(fresh, encoding="utf-8")
                errors, issues = reader.poll(NOW + 1)
                self.assertEqual(issues, [])
                self.assertEqual(len(errors), 1)
                self.assertIn("new session", errors[0])
                self.assertNotIn("old session", errors[0])

    def test_missing_logs_report_incomplete_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            errors, issues = monitor.RecentLogErrors(root).poll(NOW)
            self.assertEqual(errors, [])
            self.assertEqual(len(issues), len(monitor.LOG_NAMES))
            self.assertTrue(all("暂不可读" in issue for issue in issues))
            self.assertEqual(list(root.iterdir()), [])

    def test_http_202_is_not_error_but_new_500_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_logs(root)
            reader = monitor.RecentLogErrors(root)
            reader.poll(NOW)
            append_log(root, "api.log", 'INFO: "GET /v1/jobs/j/demo-result HTTP/1.1" 202 Accepted\n'
                       'INFO: "GET /health/ready HTTP/1.1" 200 OK\n')
            self.assertEqual(reader.poll(NOW + 1)[0], [])
            append_log(root, "api.log", 'INFO: "GET /v1/jobs/j/demo-result HTTP/1.1" 500 Internal Server Error\n')
            errors, _ = reader.poll(NOW + 2)
            self.assertEqual(len(errors), 1)
            self.assertIn("500", errors[0])

    def test_tail_logs_shows_real_update_time_without_historical_cloudflare(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_logs(root)
            append_log(root, "api.err.log", "INFO earlier\nERROR latest\n")
            os.utime(root / "api.err.log", (NOW - 100, NOW - 100))
            (root / "cloudflared-20261013.stderr.log").write_text("ERR STALE_TUNNEL_FAILURE\n", encoding="utf-8")
            text = monitor.tail_logs(root, 1)
            expected_time = datetime.fromtimestamp(NOW - 100).strftime("%m-%d %H:%M:%S")
            self.assertIn("最后写入 " + expected_time, text)
            self.assertIn("ERROR latest", text)
            self.assertNotIn("INFO earlier", text)
            self.assertNotIn("STALE_TUNNEL_FAILURE", text)
            self.assertNotIn("cloudflared-20261013.stderr.log", text)
            self.assertIn("历史隧道文件不在此展示", text)


if __name__ == "__main__":
    unittest.main()
