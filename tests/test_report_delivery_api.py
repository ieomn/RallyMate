from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_service import api, user_demo
from tests import test_trajectory_api, test_user_demo
from tests.test_report_evidence_transport import native_fixture
from rallymate_service.report_evidence_transport import unpack_report_evidence


def setup_report(root):
    fixture = test_trajectory_api.TrajectoryApiTests()
    app, settings, database = fixture._app(root)
    job = fixture._succeeded_job(settings, database, running=True)
    summary = {"job_id": job, "status": "completed", "action_recognition": {
        "detector_version": api.DETECTOR_VERSION,
        "motion_analysis": {"analysis_version": api.ANALYSIS_VERSION}}}
    database.mark_succeeded(job, summary)
    return app, settings, database, job


def poll(client, url):
    for _ in range(1000):
        response = client.get(url)
        if response.status_code != 202:
            return response
        time.sleep(.005)
    raise AssertionError("report preparation did not finish")


class ReportDeliveryApiTests(unittest.TestCase):
    def test_slow_cold_request_parallel_polls_cached_bytes_and_original_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job = setup_report(Path(directory))
            entered, release = Event(), Event()
            supplied = native_fixture(); supplied["job_id"] = job
            calls = []
            def build(*args, **kwargs):
                calls.append(kwargs)
                entered.set()
                if not release.wait(5): raise RuntimeError("test builder timed out")
                return copy.deepcopy(supplied)
            with patch.object(api, "build_user_demo_result", side_effect=build), TestClient(app) as client:
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1"
                deferred = url + "&report_delivery=deferred-v1"
                try:
                    start = time.monotonic(); first = client.get(deferred)
                    self.assertLess(time.monotonic() - start, 1)
                    self.assertEqual(first.status_code, 202)
                    self.assertEqual(first.json(), {"job_id": job, "status": "preparing",
                        "report_delivery": "deferred-v1", "retry_after_ms": 1000})
                    self.assertTrue(entered.wait(1))
                    with ThreadPoolExecutor(max_workers=6) as callers:
                        parallel = list(callers.map(lambda _: client.get(deferred), range(12)))
                    self.assertTrue(all(row.status_code == 202 for row in parallel))
                    self.assertEqual(len(calls), 1)
                finally: release.set()
                ready = poll(client, deferred)
                self.assertEqual(ready.status_code, 200, ready.text[:200])
                self.assertEqual(client.get(deferred).content, ready.content)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["include_derived_assessments"], False)
                native = client.get(url.replace("&report_transport=model-proof-pool-v1", ""))
                self.assertEqual(native.status_code, 200)
                self.assertEqual(unpack_report_evidence(ready.json()), native.json())
                self.assertEqual(client.get(url).json(), ready.json())
                self.assertEqual(client.get(deferred.replace("model-proof-pool-v1", "native")).status_code, 422)
                self.assertEqual(client.get(deferred.replace("deferred-v1", "unknown")).status_code, 422)

    def test_artifact_change_invalidates_and_cancelled_job_cannot_hit_ready_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job = setup_report(Path(directory))
            supplied = native_fixture(); supplied["job_id"] = job
            with patch.object(api, "build_user_demo_result", side_effect=lambda *a, **kw: copy.deepcopy(supplied)) as build, TestClient(app) as client:
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                self.assertEqual(poll(client, url).status_code, 200)
                self.assertEqual(build.call_count, 1)
                (settings.runs_dir / job / "preview.jpg").write_bytes(b"new artifact")
                self.assertEqual(client.get(url).status_code, 202)
                self.assertEqual(poll(client, url).status_code, 200)
                self.assertEqual(build.call_count, 2)
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job,))
                    connection.commit()
                self.assertEqual(client.get(url).status_code, 409)
                self.assertEqual(build.call_count, 2)

    def test_source_mutation_during_build_discards_result(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job = setup_report(Path(directory))
            supplied = native_fixture(); supplied["job_id"] = job
            def build(*args, **kwargs):
                (settings.runs_dir / job / "preview.jpg").write_bytes(b"changed during build")
                return copy.deepcopy(supplied)
            with patch.object(api, "build_user_demo_result", side_effect=build), TestClient(app) as client:
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                self.assertEqual(client.get(url).status_code, 202)
                deadline = time.monotonic() + 3
                while app.state.report_delivery_cache.stats()["pending"] and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(app.state.report_delivery_cache.stats()["bytes"], 0)

    def test_reviewed_action_context_invalidates_cached_report(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job = setup_report(Path(directory))
            supplied = native_fixture(); supplied["job_id"] = job
            context = settings.runs_dir / job / "action-context.json"
            with patch.object(api, "build_user_demo_result", side_effect=lambda *a, **kw: copy.deepcopy(supplied)) as build, TestClient(app) as client:
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                self.assertEqual(poll(client, url).status_code, 200)
                context.write_text('{"review":1}', encoding="utf-8")
                self.assertEqual(poll(client, url).status_code, 200)
                self.assertEqual(build.call_count, 2)
                saved = context.stat()
                context.write_text('{"review":2}', encoding="utf-8")
                os.utime(context, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                self.assertEqual(poll(client, url).status_code, 200)
                self.assertEqual(build.call_count, 3)

    def test_standalone_default_derived_results_unchanged_optional_skip_has_no_duplicates(self):
        summary = test_user_demo.UserDemoResultTests()._summary()
        expected = user_demo.build_user_demo_result(summary, [])
        with patch.object(user_demo, "build_process_evaluation") as process, patch.object(user_demo, "build_indicator_assessment") as indicators, patch.object(user_demo, "build_analysis_report") as analysis:
            base = user_demo.build_user_demo_result(summary, [], include_derived_assessments=False)
            process.assert_not_called(); indicators.assert_not_called(); analysis.assert_not_called()
        for name in ("process_evaluation", "indicator_assessment", "analysis_report"):
            self.assertIn(name, expected)
            self.assertNotIn(name, base)
            expected.pop(name)
        self.assertEqual(base, expected)
