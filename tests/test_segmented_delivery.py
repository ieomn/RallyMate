import copy
import json
import os
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from rallymate_service import segmented_report as segments
from rallymate_service.report_delivery_cache import DeliveryError
from rallymate_service.report_evidence_transport import ReportEvidenceLimitError
from tests.test_segmented_report import fixture, prepared_get


class SegmentedDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app, self.settings, self.db, self.job, *_ = fixture(Path(temp.name))
        self.delivery = self.app.state.segmented_delivery
        self.addCleanup(self.delivery.close)
        self.base = f"/v1/jobs/{self.job['id']}"

    def finish(self):
        deadline = time.monotonic() + 5
        while self.delivery.cache.stats()["pending"] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.delivery.cache.stats()["pending"], 0)

    def test_same_pending_scope_joins_without_reloading_summary_or_cancelling_on_get_return(self):
        entered, release = Event(), Event()
        builds = []
        original = segments.SegmentedReportStore.actions
        def blocked(store, *args, **kwargs):
            builds.append(1)
            entered.set()
            release.wait(5)
            return original(store, *args, **kwargs)
        with patch.object(segments.SegmentedReportStore, "actions", blocked), \
                patch.object(self.delivery, "_snapshot", wraps=self.delivery._snapshot) as identities, \
                TestClient(self.app) as client:
            deferred = self.base + "/report-actions?report_delivery=deferred-v1"
            first = client.get(deferred)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json()["delivery"]["state"], "preparing")
            self.assertTrue(entered.wait(5))
            before = identities.call_count
            with ThreadPoolExecutor(max_workers=4) as pool:
                statuses = list(pool.map(lambda _: client.get(deferred).status_code, range(6)))
            self.assertEqual(statuses, [202] * 6)
            self.assertEqual(identities.call_count, before)
            self.assertEqual(builds, [1])
            release.set()
            result = prepared_get(client, self.base + "/report-actions")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.get(self.base + "/report-actions").content, result.content)
            self.assertEqual(builds, [1])

    def test_existing_inline_client_waits_for_the_same_deferred_build(self):
        entered, release = Event(), Event()
        builds = []
        original = segments.SegmentedReportStore.actions
        def blocked(store, *args, **kwargs):
            builds.append(1)
            entered.set()
            release.wait(5)
            return original(store, *args, **kwargs)
        with patch.object(segments.SegmentedReportStore, "actions", blocked), TestClient(self.app) as client, \
                ThreadPoolExecutor(max_workers=1) as readers:
            inline = readers.submit(client.get, self.base + "/report-actions")
            try:
                self.assertTrue(entered.wait(5))
                deferred = client.get(self.base + "/report-actions?report_delivery=deferred-v1")
                self.assertEqual(deferred.status_code, 202)
                self.assertFalse(inline.done())
            finally:
                release.set()
            result = inline.result(timeout=5)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(prepared_get(client, self.base + "/report-actions").content, result.content)
            self.assertEqual(builds, [1])

    def test_disconnected_inline_reader_does_not_cancel_shared_work(self):
        entered, release = Event(), Event()
        original = segments.SegmentedReportStore.actions
        def blocked(store, *args, **kwargs):
            entered.set()
            release.wait(5)
            return original(store, *args, **kwargs)
        with patch.object(segments.SegmentedReportStore, "actions", blocked), TestClient(self.app) as client:
            self.assertEqual(client.get(self.base + "/report-actions?report_delivery=deferred-v1").status_code, 202)
            self.assertTrue(entered.wait(5))
            try:
                with patch.object(Request, "is_disconnected", new=AsyncMock(return_value=True)):
                    cancelled = client.get(self.base + "/report-actions")
                self.assertEqual(cancelled.status_code, 409)
                self.assertEqual(cancelled.json()["detail"]["code"], "report_read_cancelled")
                self.assertEqual(self.delivery.cache.stats()["pending"], 1)
            finally:
                release.set()
            self.assertEqual(prepared_get(client, self.base + "/report-actions").status_code, 200)

    def test_ready_cache_does_not_hide_same_size_restored_time_tampering(self):
        with TestClient(self.app) as client:
            first = prepared_get(client, self.base + "/report-actions")
            self.assertEqual(first.status_code, 200)
            path = Path(self.job["output_dir"]) / "indicator-features.jsonl"
            previous = path.stat()
            path.write_bytes(path.read_bytes().replace(b'"value":1', b'"value":9', 1))
            os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            rejected = prepared_get(client, self.base + "/report-actions")
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertEqual(rejected.json()["detail"]["code"], "report_evidence_invalid")

    def test_pages_and_event_identity_are_separate_cache_scopes(self):
        with TestClient(self.app) as client:
            one = prepared_get(client, self.base + "/report-actions/event-1?offset=0&limit=1")
            two = prepared_get(client, self.base + "/report-actions/event-1?offset=1&limit=1")
            missing = prepared_get(client, self.base + "/report-actions/no-event?offset=0&limit=1")
            self.assertEqual((one.status_code, two.status_code, missing.status_code), (200, 200, 404))
            self.assertNotEqual(one.content, two.content)

    def test_proof_output_limit_remains_413_in_shared_preparation(self):
        with patch.object(segments.SegmentedReportStore, "detail", side_effect=ReportEvidenceLimitError("limit")), \
                TestClient(self.app) as client:
            rejected = prepared_get(client, self.base + "/report-actions/event-1")
            self.assertEqual(rejected.status_code, 413, rejected.text)
            self.assertEqual(rejected.json()["detail"]["code"], "report_segment_limit")

    def test_job_cancellation_invalidates_shared_preparation_and_isolated_other_job_can_finish(self):
        entered, release = Event(), Event()
        def blocked(job, check):
            entered.set()
            release.wait(5)
            check(force=True)
            return b'{"should_not_publish":true}'
        self.assertEqual(self.delivery.request(self.job["id"], "actions", 0, 20, None, blocked).state, "preparing")
        self.assertTrue(entered.wait(5))
        with self.db.connect() as connection:
            connection.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (self.job["id"],))
            connection.commit()
        with self.assertRaises(DeliveryError) as denied:
            self.delivery.request(self.job["id"], "actions", 0, 20, None, blocked)
        self.assertEqual(denied.exception.status, 409)
        other = "33333333-3333-4333-8333-333333333333"
        root = self.settings.runs_dir / other
        root.mkdir()
        self.db.create_job(other, "other.mp4", self.settings.uploads_dir / (other + ".mp4"),
            self.settings.requests_dir / (other + ".json"), root)
        self.db.claim_next("other-worker", 60, 2, expected_job_id=other)
        self.db.mark_succeeded(other, {"job_id": other})
        build_other = lambda job, check: b'{"other":true}'
        self.assertEqual(self.delivery.request(other, "motion", 0, 20, "other", build_other).state, "preparing")
        release.set()
        self.finish()
        completed = self.delivery.request(other, "motion", 0, 20, "other", build_other)
        self.assertEqual(completed.state, "ready")
        self.assertTrue(all(result.body is None for result in self.delivery.cache._completed.values()
                            if result.error is not None))

    def test_queue_memory_and_time_limits_remain_bounded(self):
        self.assertEqual(self.delivery.cache.preparation_timeout_seconds, 60)
        self.assertEqual(self.delivery.cache.queue_timeout_seconds, 120)
        self.assertEqual(self.delivery.cache.max_bytes, 32 * 1024 * 1024)
        self.assertEqual(self.delivery.cache.max_entries, 16)
        entered, release = Event(), Event()
        def blocked(job, check):
            entered.set()
            release.wait(5)
            check(force=True)
            return b'{}'
        self.delivery.request(self.job["id"], "motion", 0, 20, "one", blocked)
        self.assertTrue(entered.wait(5))
        try:
            self.delivery.request(self.job["id"], "motion", 0, 20, "two", blocked)
            self.delivery.request(self.job["id"], "motion", 0, 20, "three", blocked)
            full = self.delivery.request(self.job["id"], "motion", 0, 20, "four", blocked)
            self.assertEqual(full.error.code, "report_preparation_queue_full")
            self.assertEqual(self.delivery.cache.stats()["pending"], 3)
        finally:
            release.set()
        self.finish()


if __name__ == "__main__":
    unittest.main()
