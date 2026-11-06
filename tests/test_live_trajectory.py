from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service.live_trajectory import LiveTrajectoryCache
from rallymate_vision.trajectory import TrajectoryExtractionError, build_trajectory_preview, parse_trajectory_line
from tests.test_manual_court import camera
from tests.test_trajectory import _frame


class LiveTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "frames.jsonl"
        self.job = {"id": "live-job", "started_at": "2026-11-02T00:00:00Z", "attempts": 1}
        self.cache = LiveTrajectoryCache()

    @staticmethod
    def row(index):
        row = _frame(index, ball_x=.2 + .01 * index, racket_x=.3, ball_track=1 if index < 10 else 2)
        row["frame"]["timestamp_source"] = "decoder_pts"
        row["camera_motion"] = camera("reference" if index == 0 else "stationary")
        row["court"] = {"status": "calibrated", "method": "manual", "calibration_usable": True,
                        "homography_image_to_court_normalized": [[.001, 0, 0], [0, .002, 0], [0, 0, 1]],
                        "polygon_px": [[0, 0], [1000, 0], [1000, 500], [0, 500]]}
        row["unused_pose_payload"] = "dense pose fields " * 1000
        return row

    def write(self, rows, *, append=False):
        raw = b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows)
        with self.path.open("ab" if append else "wb") as handle:
            handle.write(raw)
        return raw

    def preview(self, *, cache=None, job=None, limit=5):
        return (cache or self.cache).preview(job or self.job, self.path, sample_limit=limit, prediction_horizon_ms=200)

    def assert_full_equivalent(self, preview):
        full = build_trajectory_preview(self.path, sample_limit=5, prediction_horizon_ms=200, allow_partial=True)
        full["source"]["frames_path"] = "frames.jsonl"
        full["job_id"] = self.job["id"]
        actual = deepcopy(preview)
        actual["source"].pop("read_strategy")
        self.assertEqual(actual, full)

    def test_first_scan_then_many_appends_match_full_history_without_reparsing_old_rows(self):
        self.write([self.row(index) for index in range(8)])
        with patch("rallymate_service.live_trajectory.parse_trajectory_line", wraps=parse_trajectory_line) as parse:
            first = self.preview()
            self.assertTrue(first["source"]["read_strategy"]["initial_full_scan"])
            self.assert_full_equivalent(first)
            for index in range(8, 18):
                raw = self.write([self.row(index)], append=True)
                preview = self.preview()
                self.assert_full_equivalent(preview)
                diagnostic = preview["source"]["read_strategy"]
                self.assertFalse(diagnostic["initial_full_scan"])
                self.assertEqual(diagnostic["last_update_input_bytes"], len(raw))
                self.assertEqual(diagnostic["last_update_complete_lines"], 1)
            self.assertEqual(parse.call_count, 18)
            self.assertEqual(self.preview(), preview)
            self.preview(limit=2)  # Resampling uses retained observations.
            self.assertEqual(parse.call_count, 18)
        self.assertEqual(preview["source"]["court_calibration_windows"],
                         [{"start_timestamp_ms": 0, "end_timestamp_ms": 1700}])

    def test_split_utf8_is_not_parsed_or_committed_until_newline_arrives(self):
        self.write([self.row(0)])
        row = self.row(1)
        row["note"] = "网球"
        raw = (json.dumps(row, ensure_ascii=False) + "\n").encode()
        split = raw.index("网".encode()) + 1
        with self.path.open("ab") as stream:
            stream.write(raw[:split])
        with patch("rallymate_service.live_trajectory.parse_trajectory_line", wraps=parse_trajectory_line) as parse:
            partial = self.preview()
            self.assertEqual(partial["source"]["frame_count"], 1)
            self.assertTrue(partial["source"]["read_strategy"]["partial_line_pending"])
            self.assertEqual(self.preview(), partial)
            self.assertEqual(parse.call_count, 1)
            with self.path.open("ab") as stream:
                stream.write(raw[split:])
            complete = self.preview()
            self.assertEqual(parse.call_count, 2)
            self.assertEqual(complete["source"]["frame_count"], 2)
            self.assert_full_equivalent(complete)

    def test_truncation_replacement_same_size_rewrite_and_retry_generation_reset(self):
        self.write([self.row(i) for i in range(3)])
        self.preview()
        self.write([self.row(7)])
        self.assert_full_equivalent(self.preview())
        replacement = self.root / "replacement.jsonl"
        replacement.write_bytes((json.dumps(self.row(8)) + "\n").encode())
        replacement.replace(self.path)
        self.assert_full_equivalent(self.preview())
        before = self.path.stat()
        raw = self.path.read_bytes().replace(b'"index": 8', b'"index": 9')
        self.path.write_bytes(raw)
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        self.assert_full_equivalent(self.preview())
        with patch("rallymate_service.live_trajectory.parse_trajectory_line", wraps=parse_trajectory_line) as parse:
            self.preview(job={**self.job, "attempts": 2})
            self.assertEqual(parse.call_count, 1)
            self.preview(job={**self.job, "attempts": 2, "started_at": "later"})
            self.assertEqual(parse.call_count, 2)

    def test_budget_fallback_retains_full_history_and_is_disclosed(self):
        self.write([self.row(i) for i in range(4)])
        for cache, reason in ((LiveTrajectoryCache(max_observations=2), "observation_budget"),
                              (LiveTrajectoryCache(max_tracks=1), "track_budget")):
            with self.subTest(reason=reason):
                preview = self.preview(cache=cache)
                self.assert_full_equivalent(preview)
                diagnostic = preview["source"]["read_strategy"]
                self.assertEqual(diagnostic["mode"], "full_scan_cache_budget_fallback")
                self.assertEqual(diagnostic["fallback_reason"], reason)
                self.assertEqual(diagnostic["fallback_source_bytes_at_start"], self.path.stat().st_size)
                self.assertEqual(cache.counts()["observations"], 0)
                self.assertEqual(preview["source"]["frame_count"], 4)
        self.write([self.row(4)], append=True)
        self.assert_full_equivalent(self.preview(cache=cache))

    def test_lru_jobs_and_global_observation_limits_evict_without_altering_sources(self):
        original = self.write([self.row(0)])  # Two retained observations.
        cache = LiveTrajectoryCache(max_jobs=2, max_total_observations=3)
        self.preview(cache=cache)
        self.preview(cache=cache, job={**self.job, "id": "another"})
        self.assertEqual(cache.counts(), {"jobs": 1, "observations": 2})
        jobs = LiveTrajectoryCache(max_jobs=1)
        self.preview(cache=jobs)
        self.preview(cache=jobs, job={**self.job, "id": "another"})
        self.assertEqual(jobs.counts()["jobs"], 1)
        jobs.discard("another")
        self.assertEqual(jobs.counts()["observations"], 0)
        cache.clear()
        self.assertEqual(cache.counts(), {"jobs": 0, "observations": 0})
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_unchanged_polls_parse_the_source_once(self):
        self.write([self.row(i) for i in range(5)])
        with patch("rallymate_service.live_trajectory.parse_trajectory_line", wraps=parse_trajectory_line) as parse:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.preview(), range(24)))
            self.assertEqual(parse.call_count, 5)
            self.assertTrue(all(item == results[0] for item in results))

    def test_bad_complete_rows_and_oversized_partial_lines_fail_without_skipping(self):
        for raw in (b'{"frame":{},"frame":{}}\n', b'{"frame":{},"x":NaN}\n', b'{"broken":\n'):
            self.path.write_bytes(raw)
            with self.assertRaises(TrajectoryExtractionError):
                self.preview()
            self.assertEqual(self.cache.counts()["jobs"], 0)
        self.path.write_bytes(b"x" * 65)
        with patch("rallymate_service.live_trajectory.MAX_FRAMES_LINE_BYTES", 64):
            with self.assertRaisesRegex(TrajectoryExtractionError, "single-line"):
                self.preview()

    def test_api_uses_incremental_cache_redacts_paths_and_cancel_only_clears_cache(self):
        from fastapi.testclient import TestClient
        from tests.test_trajectory_api import TrajectoryApiTests
        helper = TrajectoryApiTests()
        with patch("rallymate_service.api.LiveTrajectoryCache", return_value=self.cache):
            app, settings, database = helper._app(self.root)
        job_id = helper._succeeded_job(settings, database, running=True)
        source = settings.runs_dir / job_id / "frames.jsonl"
        before = source.read_bytes()
        with TestClient(app) as client, \
                patch("rallymate_service.live_trajectory.parse_trajectory_line", wraps=parse_trajectory_line) as parse:
            url = f"/v1/jobs/{job_id}/trajectory"
            first = client.get(url)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["source"]["read_strategy"]["mode"], "incremental_complete_history")
            self.assertNotIn(str(self.root), first.text)
            self.assertEqual(client.get(url).json(), first.json())
            self.assertEqual(parse.call_count, 4)
            self.assertEqual(client.get(f"/v1/jobs/{job_id}").status_code, 200)
            self.assertEqual(parse.call_count, 4)
            self.assertEqual(self.cache.counts()["observations"], 8)
            self.assertEqual(client.post(f"/v1/jobs/{job_id}/cancel").status_code, 200)
            self.assertEqual(self.cache.counts(), {"jobs": 0, "observations": 0})
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
