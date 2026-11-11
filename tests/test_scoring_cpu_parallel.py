from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from rallymate_scoring.artifact_evidence import ArtifactEvidenceError, EvidencePoolWriter, POOL_FILENAME
from rallymate_scoring.cpu_parallel import ScoringCpuPool
from rallymate_scoring.source_assessment import contextual_window_tasks


def _delayed(value, delay=0, *, cancellation_callback=None):
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if cancellation_callback:
            cancellation_callback()
        time.sleep(0.01)
    if value == "error":
        raise ValueError("worker validation failed")
    return value, os.getpid()


def _crash(*, cancellation_callback=None):
    os._exit(7)


def _large_result(size, *, cancellation_callback=None):
    return "完整数据" * size, os.getpid()


class ScoringCpuParallelTests(unittest.TestCase):
    def assert_children_exited(self, pool):
        self.assertTrue(pool.worker_pids)
        self.assertFalse(any(psutil.pid_exists(pid) for pid in pool.worker_pids))

    def test_results_stay_ordered_and_children_join(self):
        with ScoringCpuPool(2) as pool:
            results = list(pool.ordered_map(_delayed, [((1, 0.08), {}), ((2, 0.01), {}), ((3,), {})]))
        self.assertEqual([row[0] for row in results], [1, 2, 3])
        self.assertEqual(len({row[1] for row in results}), 2)
        self.assert_children_exited(pool)

    def test_worker_failure_preserves_exception_and_joins_others(self):
        pool = ScoringCpuPool(2)
        with self.assertRaisesRegex(ValueError, "worker validation failed"):
            with pool:
                list(pool.ordered_map(_delayed, [(("error",), {}), ((2, 1), {})]))
        self.assert_children_exited(pool)

    def test_parent_cancellation_is_not_optional_measurement_failure(self):
        class UserCancelled(Exception):
            pass
        original = UserCancelled("stop this job")
        started = time.monotonic()

        def callback():
            if time.monotonic() - started > 0.25:
                raise original

        pool = ScoringCpuPool(2, cancellation_callback=callback)
        with self.assertRaises(UserCancelled) as raised:
            with pool:
                list(pool.ordered_map(_delayed, [((1, 5), {}), ((2, 5), {})]))
        self.assertIs(raised.exception, original)
        self.assertLess(time.monotonic() - started, 4)
        self.assert_children_exited(pool)

    def test_crashed_child_cannot_hang_or_publish_partial_result(self):
        pool = ScoringCpuPool(2)
        with self.assertRaisesRegex(RuntimeError, "exited without a result"):
            with pool:
                list(pool.ordered_map(_crash, [((), {})]))
        self.assert_children_exited(pool)

    def test_abandoned_iteration_joins_outstanding_children(self):
        pool = ScoringCpuPool(2)
        with pool:
            iterator = pool.ordered_map(_delayed, [((1,), {}), ((2, 3), {})])
            self.assertEqual(next(iterator)[0], 1)
            iterator.close()
        self.assert_children_exited(pool)

    def test_parallel_artifacts_preserve_bytes_and_evidence_pool(self):
        shared = {"source_frames": list(range(3000)), "label": "完整证据"}
        rows = [{"n": n, "source_window": shared, "value": n / 7} for n in range(6)]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            names = ("features.jsonl", "scores.jsonl")
            with EvidencePoolWriter(base / "serial", video_id="video", source_id="job") as writer:
                for name in names:
                    writer.write_jsonl(base / "serial" / name, rows)
            with ScoringCpuPool(2) as pool, EvidencePoolWriter(
                    base / "parallel", video_id="video", source_id="job") as writer:
                writer.write_jsonl_parallel([(base / "parallel" / name, rows) for name in names], cpu_pool=pool)
            for name in (*names, POOL_FILENAME):
                self.assertEqual((base / "serial" / name).read_bytes(),
                                 (base / "parallel" / name).read_bytes())
            self.assert_children_exited(pool)

    def test_invalid_child_output_keeps_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            destination = base / "scores.jsonl"
            destination.write_bytes(b"previous complete result")
            pool = ScoringCpuPool(2)
            with self.assertRaises(ArtifactEvidenceError):
                with pool, EvidencePoolWriter(base, video_id="video", source_id="job") as writer:
                    writer.write_jsonl_parallel([(destination, [{"source_window": {
                        "__rallymate_evidence_ref__": {"untrusted": True}}}])], cpu_pool=pool)
            self.assertEqual(destination.read_bytes(), b"previous complete result")
            self.assertEqual([path.name for path in base.iterdir()], ["scores.jsonl"])
            self.assert_children_exited(pool)

    def test_window_selection_preserves_duplicates_and_original_order(self):
        raw = [{"frame": {"index": n}, "ordinal": i} for i, n in enumerate([3, 1, 2, 1, 5])]
        timeline = [{"source_frame_index": n, "ordinal": i} for i, n in enumerate([5, 1, 3, 1, 2])]
        with patch("rallymate_scoring.source_assessment.canonical_contextual_window",
                   return_value={"source_frames": [1, 2, 3]}):
            tasks = list(contextual_window_tasks(raw, timeline, [{"event_id": "a"}], {}))
        args, _ = tasks[0]
        self.assertEqual(args[0], [row for row in raw if row["frame"]["index"] in {1, 2, 3}])
        self.assertEqual(args[1], [row for row in timeline if row["source_frame_index"] in {1, 2, 3}])

    def test_oversized_task_uses_complete_serial_fallback_and_cleans_spool(self):
        with ScoringCpuPool(2, max_task_bytes=128) as pool:
            value = "保留完整字段" * 500
            result = list(pool.ordered_map(_delayed, [((value,), {})]))
            spool = Path(pool.spool.name)
            self.assertEqual(list(spool.iterdir()), [])
        self.assertEqual(result, [(value, os.getpid())])
        self.assertEqual(pool.serial_fallback_tasks, 1)
        self.assertFalse(spool.exists())

    def test_oversized_result_uses_complete_serial_fallback(self):
        with ScoringCpuPool(2, max_result_bytes=128) as pool:
            result = list(pool.ordered_map(_large_result, [((500,), {})]))
        self.assertEqual(result, [("完整数据" * 500, os.getpid())])
        self.assertEqual(pool.serial_fallback_tasks, 1)
        self.assert_children_exited(pool)

    def test_child_memory_budget_cancels_and_joins(self):
        from rallymate_vision.postprocess_resources import PostprocessResourceLimit
        pool = ScoringCpuPool(2, max_child_memory_bytes=1)
        with self.assertRaises(PostprocessResourceLimit):
            with pool:
                list(pool.ordered_map(_delayed, [((1, 1), {})]))
        self.assert_children_exited(pool)

    def test_window_selection_propagates_memory_error(self):
        failure = MemoryError("resource budget")
        with patch("rallymate_scoring.source_assessment.canonical_contextual_window", side_effect=failure):
            with self.assertRaises(MemoryError) as raised:
                list(contextual_window_tasks([], [], [{}], {}))
        self.assertIs(raised.exception, failure)

    def test_memory_sampling_ignores_a_reused_process_id(self):
        observer = Mock()
        observer.is_running.return_value = False
        with ScoringCpuPool(2) as pool:
            pool.process_observers.append(observer)
            pool._check()
        observer.memory_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
