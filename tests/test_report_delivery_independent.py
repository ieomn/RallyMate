"""Independent concurrency/freshness regressions for deferred report transport.

Owned separately from the implementation's cache/API unit tests. Builders here
return small deterministic bytes and never invoke scoring or GPU work.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Lock
import tempfile
import time
import unittest
from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient

from rallymate_service.report_delivery_cache import ReportDeliveryCache, artifact_signature
from tests import test_trajectory_api as trajectory_fixture
from tests.test_source_assessment import fixture as source_fixture
from rallymate_service import source_assessment
from rallymate_scoring import indicator_assessment, training_dimensions


def rewrite_same_size_preserving_mtime(path, replacement):
    before = path.stat()
    assert len(replacement) == before.st_size
    with path.open("r+b") as stream:
        stream.write(replacement)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


class IndependentReportDeliveryTests(unittest.TestCase):
    def cache(self, **kwargs):
        cache = ReportDeliveryCache(**kwargs)
        close = getattr(cache, "close", None) or getattr(cache, "shutdown", None)
        if callable(close):
            self.addCleanup(close)
        return cache

    def completed(self, cache, key, build, source_is_current=lambda: True):
        deadline = time.monotonic() + 3
        while True:
            result = cache.request(key, build, source_is_current)
            if result.state != "preparing":
                return result
            if time.monotonic() >= deadline:
                self.fail("bounded deferred builder did not complete")
            time.sleep(.005)

    def held_builder(self, body):
        started, release = Event(), Event()
        self.addCleanup(release.set)

        def build():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test release not received")
            return body

        return build, started, release

    def test_same_key_parallel_requests_build_once_and_return_identical_bytes(self):
        cache = self.cache()
        inner, started, release = self.held_builder(b'{"job_id":"A","score":null,"value":0}')
        calls, lock = [], Lock()

        def build():
            with lock:
                calls.append("A")
            return inner()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: cache.request(("A", "current", "pool"), build, lambda: True), range(8)))
        self.assertTrue(started.wait(1))
        self.assertTrue(all(row.state == "preparing" for row in results))
        self.assertEqual(calls, ["A"])
        release.set()
        ready = self.completed(cache, ("A", "current", "pool"), build)
        self.assertEqual(ready.state, "ready")
        self.assertEqual(ready.body, b'{"job_id":"A","score":null,"value":0}')
        self.assertEqual(cache.request(("A", "current", "pool"), build, lambda: True).body, ready.body)
        self.assertEqual(calls, ["A"])

    def test_different_keys_share_one_worker_without_cross_job_body(self):
        cache = self.cache(max_queued=1)
        first, first_started, release = self.held_builder(b'{"job_id":"A"}')
        second_started = Event()

        def second():
            second_started.set()
            return b'{"job_id":"B"}'

        self.assertEqual(cache.request(("A",), first, lambda: True).state, "preparing")
        self.assertTrue(first_started.wait(1))
        self.assertEqual(cache.request(("B",), second, lambda: True).state, "preparing")
        self.assertFalse(second_started.is_set())
        self.assertEqual(cache.request(("A",), first, lambda: True).state, "preparing")
        release.set()
        self.assertEqual(self.completed(cache, ("A",), first).body, b'{"job_id":"A"}')
        self.assertEqual(self.completed(cache, ("B",), second).body, b'{"job_id":"B"}')

    def test_source_invalid_before_build_never_calls_builder(self):
        cache = self.cache()
        calls = []
        result = self.completed(cache, ("old-source",), lambda: calls.append(1) or b"stale", lambda: False)
        self.assertEqual(result.state, "error")
        self.assertIsNone(result.body)
        self.assertEqual(calls, [])

    def test_source_changes_during_build_never_publishes_stale_body(self):
        cache = self.cache()
        build, started, release = self.held_builder(b"stale")
        current = [True]
        self.assertEqual(cache.request(("snapshot-A",), build, lambda: current[0]).state, "preparing")
        self.assertTrue(started.wait(1))
        current[0] = False
        release.set()
        result = self.completed(cache, ("snapshot-A",), build, lambda: current[0])
        self.assertEqual(result.state, "error")
        self.assertIsNone(result.body)
        replacement = self.completed(cache, ("snapshot-B",), lambda: b"fresh")
        self.assertEqual(replacement.state, "ready")
        self.assertEqual(replacement.body, b"fresh")

    def test_ready_result_is_not_delivered_when_current_identity_is_false(self):
        cache = self.cache()
        build = lambda: b"bound-to-A"
        self.assertEqual(self.completed(cache, ("A",), build).state, "ready")
        result = cache.request(("A",), build, lambda: False)
        self.assertNotEqual(result.state, "ready")
        self.assertIsNone(result.body)

    def test_queue_limit_does_not_evict_or_duplicate_inflight_key(self):
        cache = self.cache(max_queued=1)
        first, started, release = self.held_builder(b"A")
        calls = []
        cache.request(("A",), first, lambda: True)
        self.assertTrue(started.wait(1))
        cache.request(("B",), lambda: calls.append("B") or b"B", lambda: True)
        overflow = cache.request(("C",), lambda: calls.append("C") or b"C", lambda: True)
        self.assertEqual(overflow.state, "error")
        self.assertIsNone(overflow.body)
        self.assertEqual(cache.request(("A",), first, lambda: True).state, "preparing")
        release.set()
        self.assertEqual(self.completed(cache, ("A",), first).body, b"A")
        self.assertEqual(self.completed(cache, ("B",), lambda: calls.append("B") or b"B").body, b"B")
        self.assertEqual(calls, ["B"])

    def test_completed_lru_entries_are_bounded_and_eviction_rebuilds(self):
        cache = self.cache(max_entries=2, max_bytes=32)
        calls = []

        def builder(key):
            return lambda: calls.append(key) or key.encode()

        for key in ("A", "B", "C"):
            self.assertEqual(self.completed(cache, (key,), builder(key)).body, key.encode())
        self.assertEqual(self.completed(cache, ("A",), builder("A")).body, b"A")
        self.assertEqual(calls, ["A", "B", "C", "A"])

    def test_oversized_body_is_not_published_as_an_unbounded_cache_entry(self):
        cache = self.cache(max_bytes=16)
        result = self.completed(cache, ("large",), lambda: b"x" * 17)
        self.assertEqual(result.state, "error")
        self.assertIsNone(result.body)

    def test_total_completed_bytes_are_bounded_across_individually_small_reports(self):
        cache = self.cache(max_entries=4, max_bytes=10)
        calls = []

        def builder(key):
            return lambda: calls.append(key) or key.encode() * 6

        for key in ("A", "B"):
            self.assertEqual(self.completed(cache, (key,), builder(key)).body, key.encode() * 6)
        self.assertEqual(cache.stats(), {"pending": 0, "entries": 1, "bytes": 6})
        self.assertEqual(self.completed(cache, ("B",), builder("B")).body, b"BBBBBB")
        self.assertEqual(self.completed(cache, ("A",), builder("A")).body, b"AAAAAA")
        self.assertEqual(calls, ["A", "B", "A"])

    def test_ready_source_check_error_fails_closed_and_evicts_only_its_bytes(self):
        cache = self.cache()
        self.assertEqual(self.completed(cache, ("A",), lambda: b"AAA").body, b"AAA")
        self.assertEqual(self.completed(cache, ("B",), lambda: b"BB").body, b"BB")

        def unavailable():
            raise OSError("private-read-location")

        result = cache.request(("A",), lambda: b"AAA", unavailable)
        self.assertEqual(result.state, "error")
        self.assertEqual(result.error.status, 409)
        self.assertNotIn("private-read-location", result.error.message)
        self.assertEqual(cache.stats(), {"pending": 0, "entries": 1, "bytes": 2})
        self.assertEqual(self.completed(cache, ("B",), lambda: b"wrong").body, b"BB")

    def test_transient_error_is_sanitized_and_retries_after_bounded_cooldown(self):
        cache = self.cache(error_ttl_seconds=.1)
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("private-path-or-token-do-not-return")
            return b"recovered"

        failed = self.completed(cache, ("retry",), flaky)
        self.assertEqual(failed.state, "error")
        self.assertNotIn("private-path-or-token", failed.error.message)
        self.assertEqual(cache.request(("retry",), flaky, lambda: True).state, "error")
        self.assertEqual(len(calls), 1)
        time.sleep(.12)
        recovered = self.completed(cache, ("retry",), flaky)
        self.assertEqual(recovered.body, b"recovered")
        self.assertEqual(len(calls), 2)

    def test_many_failed_keys_are_bounded_like_successful_entries(self):
        cache = self.cache(max_entries=2)

        def broken():
            raise ValueError("expected synthetic failure")

        for index in range(10):
            self.assertEqual(self.completed(cache, ("bad", index), broken).state, "error")
            stats = cache.stats()
            self.assertLessEqual(stats["entries"], 2)
            self.assertEqual(stats["bytes"], 0)
            self.assertEqual(stats["pending"], 0)

    def test_closed_cache_does_not_resubmit_builds_or_return_old_ready_body(self):
        cache = self.cache()
        calls = []
        build = lambda: calls.append(1) or b"result"
        self.assertEqual(self.completed(cache, ("closed",), build).body, b"result")
        cache.close()
        result = cache.request(("closed",), build, lambda: True)
        self.assertEqual(result.state, "error")
        self.assertIsNone(result.body)
        self.assertEqual(calls, [1])


class IndependentReportDeliveryApiTests(unittest.TestCase):
    def setup_case(self, root):
        fixture = trajectory_fixture.TrajectoryApiTests()
        app, settings, database = fixture._app(root)
        job = fixture._succeeded_job(settings, database, running=True)
        summary = {"job_id": job, "status": "completed"}
        database.mark_succeeded(job, summary)
        folder = settings.runs_dir / job
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        marker = folder / "indicator-features.jsonl"
        marker.write_text('{"marker":"A"}', encoding="utf-8")
        return app, settings, database, job, folder, marker

    def lightweight_builders(self, marker, calls, *, overrides=None):
        stack = ExitStack()

        def build(summary, records, **kwargs):
            calls.append(1)
            return {"job_id": summary["job_id"], "source_marker": marker.read_text(encoding="utf-8"),
                    "score": None, "numeric_measurement": 0}

        replacements = {"build_user_demo_result": build, "load_indicator_feature_records": lambda *args, **kwargs: [],
            "load_footwork_review": lambda *args, **kwargs: {}, "load_source_aligned_assessment": lambda *args, **kwargs: {},
            "build_process_evaluation": lambda *args, **kwargs: {}, "build_indicator_assessment": lambda *args, **kwargs: {},
            "build_analysis_report": lambda *args, **kwargs: {}, "pack_report_evidence": lambda value: value,
            "recognize_strokes_from_artifacts": lambda *args, **kwargs: {}}
        replacements.update(overrides or {})
        for name, replacement in replacements.items():
            stack.enter_context(patch("rallymate_service.api." + name, side_effect=replacement))
        return stack

    def ready(self, client, url):
        deadline = time.monotonic() + 3
        while True:
            response = client.get(url)
            if response.status_code != 202:
                self.assertEqual(response.status_code, 200, response.text)
                return response
            self.assertEqual(set(response.json()), {"job_id", "status", "report_delivery", "retry_after_ms"})
            if time.monotonic() >= deadline:
                self.fail("deferred fixture did not complete")
            time.sleep(.005)

    def test_same_size_in_place_rewrite_with_restored_mtime_cannot_return_stale_report(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls), TestClient(app) as client:
                first = self.ready(client, url)
                self.assertEqual(first.json()["source_marker"], '{"marker":"A"}')
                original_stat, original_signature = marker.stat(), artifact_signature(marker)
                with marker.open("r+b") as handle:
                    handle.write(b'{"marker":"B"}')
                os.utime(marker, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                # On Windows st_ctime is creation time; this is an actual local
                # mutation, not a mocked stat result or an impossible timestamp.
                unchanged_stat_signature = artifact_signature(marker) == original_signature
                second = self.ready(client, url)
                self.assertEqual(second.json()["source_marker"], '{"marker":"B"}',
                    f"changed bytes were hidden by unchanged stat signature={unchanged_stat_signature}")
                self.assertEqual(calls, [1, 1])
                self.assertIsNone(second.json()["score"])
                self.assertEqual(second.json()["numeric_measurement"], 0)

    def test_every_read_artifact_and_new_public_artifact_invalidates_completed_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls), TestClient(app) as client:
                self.ready(client, url)
                for index, filename in enumerate(("summary.json", "frames.jsonl", "primary-player.jsonl", "events.jsonl",
                        "indicator-features.jsonl", "source-aligned-measurements.json", "preview.jpg"), start=2):
                    with self.subTest(dependency=filename):
                        path = folder / filename
                        previous = path.read_bytes() if path.exists() else b""
                        path.write_bytes(previous + b"\n")
                        self.ready(client, url)
                        self.assertEqual(len(calls), index)

    def test_metadata_preserving_source_change_during_api_build_drops_old_body(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            started, release = Event(), Event()
            calls = []

            def build(summary, records, **kwargs):
                value = marker.read_text(encoding="utf-8")
                calls.append(value)
                if len(calls) == 1:
                    started.set()
                    if not release.wait(3):
                        raise RuntimeError("test release not received")
                return {"job_id": summary["job_id"], "source_marker": value}

            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build}), TestClient(app) as client:
                try:
                    self.assertEqual(client.get(url).status_code, 202)
                    self.assertTrue(started.wait(1))
                    rewrite_same_size_preserving_mtime(marker, b'{"marker":"B"}')
                finally:
                    release.set()
                deadline = time.monotonic() + 3
                while app.state.report_delivery_cache.stats()["pending"] and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(app.state.report_delivery_cache.stats()["pending"], 0)
                self.assertEqual(app.state.report_delivery_cache.stats()["bytes"], 0)
                self.assertEqual(self.ready(client, url).json()["source_marker"], '{"marker":"B"}')
                self.assertEqual(calls, ['{"marker":"A"}', '{"marker":"B"}'])

    def test_summary_content_invalidates_even_when_job_updated_at_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls), TestClient(app) as client:
                self.ready(client, url)
                original = database.get_job(job)
                summary = {**original["summary"], "independent_revision": "changed"}
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET summary_json = ? WHERE id = ?", (json.dumps(summary), job))
                    connection.commit()
                self.assertEqual(database.get_job(job)["updated_at"], original["updated_at"])
                self.ready(client, url)
                self.assertEqual(calls, [1, 1])

    def test_operator_registry_content_not_just_stat_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, settings, database, job, folder, marker = self.setup_case(root)
            registry = root / "operator-registry.json"
            registry.write_text('{"revision":"A"}', encoding="utf-8")
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls), patch(
                    "rallymate_service.config.ServiceSettings.resolved_technique_registry",
                    new_callable=PropertyMock, return_value=registry), TestClient(app) as client:
                self.ready(client, url)
                original = registry.stat()
                with registry.open("r+b") as handle:
                    handle.write(b'{"revision":"B"}')
                os.utime(registry, ns=(original.st_atime_ns, original.st_mtime_ns))
                self.ready(client, url)
                self.assertEqual(calls, [1, 1])

    def test_non_succeeded_job_and_unsupported_transport_do_not_schedule_work(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            calls = []
            with self.lightweight_builders(marker, calls), TestClient(app) as client:
                self.assertEqual(client.get(f"/v1/jobs/{job}/demo-result?report_delivery=deferred-v1").status_code, 422)
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job,))
                    connection.commit()
                url = f"/v1/jobs/{job}/demo-result?report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                self.assertEqual(client.get(url).status_code, 409)
                self.assertEqual(calls, [])
                self.assertEqual(app.state.report_delivery_cache.stats()["pending"], 0)

    def test_action_inner_cache_reloads_both_body_inputs_after_metadata_preserving_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            paths = [folder / "frames.jsonl", folder / "primary-player.jsonl"]
            for path in paths:
                path.write_bytes(b'{"marker":"A"}')
            calls, recognition_calls = [], []

            def recognize(*inputs):
                recognition_calls.append(1)
                return {"independent_input_bytes": [path.read_text(encoding="utf-8") for path in inputs]}

            def build(summary, records, **kwargs):
                calls.append(1)
                return {"job_id": summary["job_id"], "action_recognition": summary["action_recognition"]}

            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build,
                    "recognize_strokes_from_artifacts": recognize}), TestClient(app) as client:
                self.ready(client, url)
                for index, path in enumerate(paths, start=2):
                    with self.subTest(dependency=path.name):
                        rewrite_same_size_preserving_mtime(path, b'{"marker":"B"}')
                        response = self.ready(client, url)
                        self.assertEqual(response.json()["action_recognition"]["independent_input_bytes"],
                                         [item.read_text(encoding="utf-8") for item in paths])
                        self.assertEqual(len(recognition_calls), index)
                        self.assertEqual(len(calls), index)

    def test_real_source_inner_cache_does_not_survive_new_outer_dependency_key(self):
        for changed in ("source-aligned-measurements.json", "events.jsonl", "primary-player.jsonl", "frames.jsonl"):
            with self.subTest(dependency=changed), tempfile.TemporaryDirectory() as directory:
                app, settings, database, job, folder, marker = self.setup_case(Path(directory))
                artifact, events = source_fixture()
                inputs = {"events.jsonl": ("".join(json.dumps(event) + "\n" for event in events)).encode(),
                          "primary-player.jsonl": b'{"player":1}\n', "frames.jsonl": b'{"frame":1}\n'}
                artifact.update(events_sha256=hashlib.sha256(inputs["events.jsonl"]).hexdigest(),
                    primary_timeline_sha256=hashlib.sha256(inputs["primary-player.jsonl"]).hexdigest(),
                    frames_sha256=hashlib.sha256(inputs["frames.jsonl"]).hexdigest())
                inputs["source-aligned-measurements.json"] = json.dumps(artifact).encode()
                for name, raw in inputs.items():
                    (folder / name).write_bytes(raw)
                summary = {"job_id": job, "status": "completed", "input": {"video": {"duration_ms": 100}},
                    "minimum_scoring_loop": {"video_id": "video", "provenance": {
                        "video_sha256": "a" * 64, "frames_sha256": artifact["frames_sha256"]}}}
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET summary_json = ? WHERE id = ?", (json.dumps(summary), job))
                    connection.commit()
                calls = []
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                with self.lightweight_builders(marker, calls, overrides={
                        "load_source_aligned_assessment": source_assessment.load_source_aligned_assessment}), TestClient(app) as client:
                    first = self.ready(client, url).json()["source_aligned_assessment"]
                    self.assertEqual(first["status"], "available")
                    original = inputs[changed]
                    replacement = (original.replace(b'"value": 0.12', b'"value": 0.21') if changed == "source-aligned-measurements.json"
                        else original.replace(b'event-0', b'event-1') if changed == "events.jsonl"
                        else original.replace(b':1', b':2'))
                    self.assertNotEqual(original, replacement)
                    rewrite_same_size_preserving_mtime(folder / changed, replacement)
                    result = self.ready(client, url).json()["source_aligned_assessment"]
                    self.assertEqual(calls, [1, 1])
                    if changed == "source-aligned-measurements.json":
                        self.assertEqual(result["indicators"][0]["windows"][0]["measurements"][0]["value"], .21)
                    else:
                        self.assertEqual(result["status"], "unavailable")

    def test_process_version_change_invalidates_completed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = self.setup_case(Path(directory))
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with self.lightweight_builders(marker, calls), TestClient(app) as client:
                self.ready(client, url)
                with patch("rallymate_service.api.REPORT_PROCESS_VERSION", "independent-new-process-version"):
                    self.ready(client, url)
                self.assertEqual(calls, [1, 1])

    def test_packaged_catalogue_change_cannot_reuse_stale_internal_no_argument_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "resources"
            (resources / "data").mkdir(parents=True)
            catalogue = resources / "data" / "technical_review_rules.json"
            original = json.loads((Path(__file__).parents[1] / "src/rallymate_scoring/data/technical_review_rules.json").read_text(encoding="utf-8"))
            original["source_reference_version"] = "independent-version-A"
            catalogue.write_text(json.dumps(original), encoding="utf-8")
            indicator_assessment._catalog.cache_clear()
            training_dimensions._source_bases.cache_clear()
            self.addCleanup(indicator_assessment._catalog.cache_clear)
            self.addCleanup(training_dimensions._source_bases.cache_clear)
            with patch("rallymate_service.api.files", return_value=resources), patch(
                    "rallymate_scoring.indicator_assessment.files", return_value=resources), patch(
                    "rallymate_scoring.training_dimensions.files", return_value=resources):
                app, settings, database, job, folder, marker = self.setup_case(root)
                calls = []

                def build(summary, records, **kwargs):
                    calls.append(1)
                    return {"job_id": summary["job_id"], "dimension_source": training_dimensions.source_basis_for_dimension("movement")}

                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                with self.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build,
                        "build_indicator_assessment": indicator_assessment.build_indicator_assessment}), TestClient(app) as client:
                    first = self.ready(client, url).json()
                    self.assertEqual(first["indicator_assessment"]["source_binding"]["reference_version"], "independent-version-A")
                    self.assertEqual(first["dimension_source"]["source_reference_version"], "independent-version-A")
                    rewrite_same_size_preserving_mtime(catalogue, catalogue.read_bytes().replace(b'independent-version-A', b'independent-version-B'))
                    response = client.get(url)
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "report_rules_changed_restart_required")
                    self.assertIn("restart", response.json()["detail"]["message"].lower())
                    self.assertNotIn("dimension_source", response.json())
                    self.assertEqual(calls, [1])
                    self.assertEqual(client.get(url.replace("&report_delivery=deferred-v1", "")).status_code, 503)


if __name__ == "__main__":
    unittest.main()
