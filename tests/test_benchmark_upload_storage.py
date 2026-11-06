from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_upload_storage.py"
SPEC = importlib.util.spec_from_file_location("benchmark_upload_storage", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class UploadStorageBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = self.root / "jobs.sqlite3"
        with sqlite3.connect(self.jobs) as conn:
            conn.execute("CREATE TABLE jobs(status TEXT)")

    def test_payload_bytes_are_independent_of_chunk_partition(self):
        size = 800003
        expected = benchmark.payload_at(0, size)
        for chunk_size in (131072, 1048576):
            actual = b"".join(benchmark.payload_at(offset, min(chunk_size, size - offset))
                              for offset in range(0, size, chunk_size))
            self.assertEqual(actual, expected)

    def test_active_jobs_fail_before_any_case_creates_temporary_data(self):
        with sqlite3.connect(self.jobs) as conn:
            conn.execute("INSERT INTO jobs VALUES('running')")
        output = self.root / "must-not-exist.json"
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(RuntimeError, "idle"):
            benchmark.main(["--idle-jobs-db", str(self.jobs), "--size-mib", "0.125",
                            "--temp-root", str(self.root), "--output", str(output)])
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(output.exists())

    def test_small_real_store_comparison_verifies_resume_and_output_hash(self):
        output = self.root / "comparison.json"
        benchmark.main(["--idle-jobs-db", str(self.jobs), "--size-mib", "0.25",
                        "--chunk-kib", "128", "1024", "--temp-root", str(self.root),
                        "--output", str(output)])
        report = json.loads(output.read_text("utf-8"))
        self.assertEqual(len(report["cases"]), 4)
        for case in report["cases"]:
            self.assertTrue(case["verification"]["assembled_sha256_exact"])
            self.assertTrue(case["verification"]["resumed_exactly"])
            self.assertEqual(case["verification"]["enqueue_calls"], 1)
            self.assertEqual(case["source_sha256"], report["source_sha256"])
            self.assertFalse(Path(case["temporary_filesystem_path"]).exists())
            counters = case["metadata_io"]["put"]
            if case["backend"] == "sqlite":
                self.assertEqual(counters.get("sqlite_full_chunk_reads", 0), 0)
                self.assertEqual(counters["sqlite_chunk_inserts"], case["chunk_count"])
            else:
                self.assertEqual(counters["manifest_full_writes"], case["chunk_count"])
                self.assertGreater(counters["manifest_json_bytes_read"], 0)
        self.assertEqual(report["transport"], "none; no HTTP, Cloudflare, upload API or GPU inference")

    def test_optional_idle_python_runs_store_cases_through_persistent_guard(self):
        output = self.root / "bridged-comparison.json"
        benchmark.main(["--idle-jobs-db", str(self.jobs), "--idle-jobs-python", sys.executable,
                        "--size-mib", "0.25", "--chunk-kib", "128", "--temp-root", str(self.root),
                        "--output", str(output)])
        report = json.loads(output.read_text("utf-8"))
        self.assertEqual(report["idle_guard"]["mode"], "persistent_stdio_bridge")
        self.assertTrue(report["idle_guard"]["fresh_connection_per_check"])
        self.assertEqual(len(report["cases"]), 2)


if __name__ == "__main__":
    unittest.main()
