from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rallymate_service.report_delivery_cache import DeliveryError, ReportDeliveryCache


def completed(cache, key, build=lambda: b"unused", check=lambda: True):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = cache.request(key, build, check)
        if result.state != "preparing":
            return result
        time.sleep(.005)
    raise AssertionError("report preparation did not finish")


class ReportDeliveryCacheTests(unittest.TestCase):
    def test_separate_queue_budget_preserves_actual_preparation_time(self):
        cache = ReportDeliveryCache(max_queued=1, preparation_timeout_seconds=60, queue_timeout_seconds=120)
        first_started, release_first, second_started, release_second = Event(), Event(), Event(), Event()
        now = [0.0]
        def first():
            first_started.set()
            release_first.wait(5)
            return b"overview"
        def second():
            second_started.set()
            release_second.wait(5)
            return b"actions"
        with patch("rallymate_service.report_delivery_cache.time", SimpleNamespace(monotonic=lambda: now[0])):
            try:
                cache.request("overview", first, lambda: True)
                self.assertTrue(first_started.wait(1))
                cache.request("actions", second, lambda: True, scope="actions")
                now[0] = 35
                release_first.set()
                self.assertTrue(second_started.wait(1))
                # 70 seconds after enqueue, but only 35 seconds of own work.
                now[0] = 70
                self.assertEqual(cache.pending_result("actions").state, "preparing")
                release_second.set()
                self.assertEqual(completed(cache, "actions").body, b"actions")
            finally:
                release_first.set()
                release_second.set()
                cache.close()

    def test_separate_queue_wait_is_bounded_and_expired_work_never_builds(self):
        cache = ReportDeliveryCache(max_queued=1, preparation_timeout_seconds=60, queue_timeout_seconds=120)
        entered, release = Event(), Event()
        now, calls = [0.0], []
        def first():
            entered.set()
            release.wait(5)
            return b"late"
        with patch("rallymate_service.report_delivery_cache.time", SimpleNamespace(monotonic=lambda: now[0])):
            try:
                cache.request("running", first, lambda: True)
                self.assertTrue(entered.wait(1))
                cache.request("queued", lambda: calls.append(1) or b"never", lambda: True, scope="queued")
                now[0] = 119
                self.assertEqual(cache.pending_result("queued").state, "preparing")
                now[0] = 120
                expired = cache.pending_result("queued")
                self.assertEqual(expired.error.code, "report_preparation_queue_timeout")
                self.assertEqual(cache.stats()["pending"], 2)
                release.set()
                deadline = time.monotonic() + 2
                while cache.stats()["pending"] and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(cache.stats()["pending"], 0)
                self.assertEqual(completed(cache, "queued").error.code, "report_preparation_queue_timeout")
                self.assertEqual(calls, [])
            finally:
                release.set()
                cache.close()

    def test_separate_preparation_budget_still_expires_at_sixty_seconds(self):
        cache = ReportDeliveryCache(preparation_timeout_seconds=60, queue_timeout_seconds=120)
        entered, release = Event(), Event()
        now = [0.0]
        def held():
            entered.set()
            release.wait(5)
            return b"late"
        with patch("rallymate_service.report_delivery_cache.time", SimpleNamespace(monotonic=lambda: now[0])):
            try:
                cache.request("work", held, lambda: True, scope="work")
                self.assertTrue(entered.wait(1))
                now[0] = 60
                self.assertEqual(cache.pending_result("work").error.code, "report_preparation_timeout")
                release.set()
                result = completed(cache, "work")
                self.assertEqual(result.error.code, "report_preparation_timeout")
                self.assertIsNone(result.body)
            finally:
                release.set()
                cache.close()

    def test_original_failure_is_logged_without_exposing_it_to_client(self):
        cache = ReportDeliveryCache()
        def build():
            try:
                raise ValueError("diagnostic cause only for server logs")
            except ValueError as exc:
                raise DeliveryError(500, "report_preparation_failed", "Report preparation failed.") from exc
        try:
            with self.assertLogs("rallymate.report_delivery", level="INFO") as logs:
                result = completed(cache, "private-source-key", build)
            logged = "\n".join(logs.output)
            self.assertIn("phase=building_report", logged)
            self.assertIn("diagnostic cause only for server logs", logged)
            self.assertNotIn("private-source-key", logged)
            self.assertEqual(result.error.detail(), {
                "code": "report_preparation_failed", "message": "Report preparation failed."})
            self.assertIsNone(result.body)
        finally:
            cache.close()

    def test_parallel_same_key_singleflight_and_one_worker_across_keys(self):
        cache = ReportDeliveryCache(max_queued=1)
        entered, release = Event(), Event()
        calls, active, peak = [], 0, 0
        lock = Lock()
        def build():
            nonlocal active, peak
            with lock:
                calls.append(1); active += 1; peak = max(peak, active)
            entered.set()
            self.assertTrue(release.wait(5))
            with lock: active -= 1
            return b'{"value":0,"confidence":null}'
        try:
            with ThreadPoolExecutor(max_workers=12) as requests:
                results = list(requests.map(lambda _: cache.request("a", build, lambda: True), range(24)))
            self.assertTrue(entered.wait(1))
            self.assertTrue(all(row.state == "preparing" for row in results))
            self.assertEqual(calls, [1])
            self.assertEqual(cache.request("b", build, lambda: True).state, "preparing")
            busy = cache.request("c", build, lambda: True)
            self.assertEqual((busy.error.status, busy.error.code), (503, "report_preparation_queue_full"))
            release.set()
            self.assertEqual(completed(cache, "a").body, b'{"value":0,"confidence":null}')
            self.assertEqual(completed(cache, "b").state, "ready")
            self.assertEqual((len(calls), peak), (2, 1))
        finally:
            release.set(); cache.close()

    def test_source_checks_before_and_after_build_never_publish_stale_bytes(self):
        for fail_at in (0, 1):
            with self.subTest(fail_at=fail_at):
                cache = ReportDeliveryCache()
                checks, calls = [], []
                def check():
                    checks.append(1)
                    return len(checks) - 1 != fail_at
                def build():
                    calls.append(1)
                    return b"stale"
                try:
                    result = completed(cache, "key", build, check)
                    self.assertEqual(result.error.code, "report_source_changed")
                    self.assertIsNone(result.body)
                    self.assertEqual(len(calls), fail_at)
                    self.assertEqual(cache.stats()["bytes"], 0)
                    self.assertEqual(completed(cache, "new-source", lambda: b"current").body, b"current")
                finally: cache.close()

    def test_error_is_explicit_sanitized_bounded_and_retryable_after_cooldown(self):
        cache = ReportDeliveryCache(max_entries=1, error_ttl_seconds=.05)
        calls = []
        def build():
            calls.append(1)
            if len(calls) == 1: raise RuntimeError("private file or secret")
            return b"recovered"
        try:
            result = completed(cache, "key", build)
            self.assertEqual(result.error.status, 500)
            self.assertNotIn("private", str(result.error.detail()))
            self.assertEqual(completed(cache, "key", build).state, "error")
            self.assertEqual(len(calls), 1)
            time.sleep(.06)
            self.assertEqual(completed(cache, "key", build).body, b"recovered")
            self.assertEqual(len(calls), 2)
            def conflict(): raise DeliveryError(409, "specific", "public message")
            self.assertEqual(completed(cache, "bad", conflict).error.code, "specific")
            self.assertEqual(cache.stats(), {"pending": 0, "entries": 1, "bytes": 0})
        finally: cache.close()

    def test_lru_entry_byte_budget_and_oversized_response(self):
        cache = ReportDeliveryCache(max_entries=2, max_bytes=8)
        try:
            self.assertEqual(completed(cache, "a", lambda: b"aaaa").body, b"aaaa")
            completed(cache, "b", lambda: b"bbbb")
            completed(cache, "a")  # Touch A: B must be evicted next.
            completed(cache, "c", lambda: b"cccc")
            self.assertEqual(cache.stats()["bytes"], 8)
            self.assertEqual(cache.request("a", lambda: b"bad", lambda: True).body, b"aaaa")
            self.assertEqual(completed(cache, "b", lambda: b"new").body, b"new")
            self.assertLessEqual(cache.stats()["bytes"], 8)
            large = completed(cache, "large", lambda: b"a" * 9)
            self.assertEqual((large.error.status, large.error.code), (413, "report_delivery_limit"))
            self.assertLessEqual(cache.stats()["entries"], 2)
        finally: cache.close()
        self.assertEqual(cache.request("closed", lambda: b"", lambda: True).error.status, 503)
