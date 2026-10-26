from pathlib import Path
from threading import Event
import hashlib
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.report_delivery_cache import ReportDeliveryCache
from rallymate_service.report_delivery_store import (
    ReportDeliveryStore, persistent_report_key, report_builder_fingerprint,
)
from tests import test_report_delivery_independent as independent_fixture


def completed(cache, key, build, check=lambda: True, **kwargs):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = cache.request(key, build, check, **kwargs)
        if result.state != "preparing":
            return result
        time.sleep(.005)
    raise AssertionError("report did not complete")


class ReportDeliveryPersistenceTests(unittest.TestCase):
    def test_restart_reuses_exact_bytes_without_building_and_checks_source_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            body = b'{"score":0,"unobserved":null,"evidence":[1.23456789]}'
            key = "a" * 64
            cache = ReportDeliveryCache(storage_dir=Path(directory))
            try:
                self.assertEqual(completed(cache, "process-one", lambda: body, persistent_key=key).body, body)
            finally:
                cache.close()
            calls = []
            cache = ReportDeliveryCache(storage_dir=Path(directory))
            try:
                def check():
                    calls.append(1)
                    return True
                with patch.object(cache._store, "load", wraps=cache._store.load) as load:
                    result = completed(cache, "process-two", lambda: self.fail("rebuild"), check,
                                       persistent_key=key)
                self.assertEqual(result.body, body)
                self.assertEqual(load.call_count, 1)
                self.assertGreaterEqual(len(calls), 2)
            finally:
                cache.close()

    def test_persistent_hit_changed_during_load_never_publishes_old_body(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ReportDeliveryCache(storage_dir=Path(directory))
            cache._store.save("a" * 64, b"old")
            checks = []
            def check():
                checks.append(1)
                return len(checks) == 1
            try:
                result = completed(cache, "key", lambda: self.fail("rebuild"), check,
                                   persistent_key="a" * 64)
                self.assertEqual(result.error.code, "report_source_changed")
                self.assertEqual(cache.stats()["bytes"], 0)
            finally:
                cache.close()

    def test_process_nonce_excluded_but_source_contract_and_builder_not_excluded(self):
        key = persistent_report_key("builder-a", ("nonce-one", "source-a", "current"))
        self.assertEqual(key, persistent_report_key("builder-a", ("nonce-two", "source-a", "current")))
        for version, source in (("builder-b", ("nonce-one", "source-a", "current")),
                ("builder-a", ("nonce-one", "source-b", "current")),
                ("builder-a", ("nonce-one", "source-a", "other"))):
            self.assertNotEqual(key, persistent_report_key(version, source))

    def test_builder_version_changes_with_same_size_code_edit_and_new_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "rallymate_service"
            folder.mkdir()
            source = folder / "builder.py"
            source.write_text("value=1")
            with patch("rallymate_service.report_delivery_store.metadata.distributions", return_value=[]):
                original = report_builder_fingerprint(root)
                source.write_text("value=2")
                edited = report_builder_fingerprint(root)
                (folder / "helper.py").write_text("value=2")
                added = report_builder_fingerprint(root)
            self.assertEqual(len({original, edited, added}), 3)

    def test_corrupt_truncated_misbound_and_oversized_entries_are_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReportDeliveryStore(Path(directory), max_body_bytes=64)
            key = "b" * 64
            path = Path(directory) / (key + ".report")
            for damage in (lambda raw: raw[:-1], lambda raw: raw.replace(b"payload", b"damaged"),
                           lambda raw: raw.replace(key.encode(), b"c" * 64),
                           lambda raw: b"[]\npayload", lambda raw: b"x" * 2048):
                store.save(key, b"payload")
                path.write_bytes(damage(path.read_bytes()))
                self.assertIsNone(store.load(key))
            cache = ReportDeliveryCache(storage_dir=Path(directory))
            try:
                self.assertEqual(completed(cache, "key", lambda: b"rebuilt", persistent_key=key).body, b"rebuilt")
                self.assertEqual(store.load(key), b"rebuilt")
            finally:
                cache.close()

    def test_disk_budget_and_key_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReportDeliveryStore(Path(directory), max_body_bytes=100, max_entries=2, max_bytes=600)
            for index in range(8):
                store.save(hashlib.sha256(str(index).encode()).hexdigest(), b"x" * 90)
            paths = list(Path(directory).glob("*.report"))
            self.assertLessEqual(len(paths), 2)
            self.assertLessEqual(sum(path.stat().st_size for path in paths), 600)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            with self.assertRaises(ValueError):
                store.load("../escape")

    def test_storage_failure_does_not_hide_valid_report(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "not-a-directory"
            file.write_bytes(b"existing")
            cache = ReportDeliveryCache(storage_dir=file)
            try:
                self.assertEqual(completed(cache, "key", lambda: b"report", persistent_key="d" * 64).body, b"report")
                self.assertEqual(file.read_bytes(), b"existing")
            finally:
                cache.close()

    def test_pending_scope_does_not_start_second_build_or_publish_mutated_source(self):
        started, release = Event(), Event()
        current = [True]
        def build():
            started.set()
            self.assertTrue(release.wait(3))
            return b"old"
        cache = ReportDeliveryCache()
        try:
            self.assertEqual(cache.request("old", build, lambda: current[0], scope="scope").state, "preparing")
            self.assertTrue(started.wait(1))
            self.assertTrue(cache.is_preparing("scope"))
            self.assertFalse(cache.is_preparing("other"))
            self.assertEqual(cache.request("new", lambda: self.fail("duplicate"), lambda: True, scope="scope").state, "preparing")
            current[0] = False
            release.set()
            self.assertEqual(completed(cache, "old", build, lambda: False).error.code, "report_source_changed")
            self.assertFalse(cache.is_preparing("scope"))
            self.assertEqual(completed(cache, "new", lambda: b"new", scope="scope").body, b"new")
        finally:
            release.set()
            cache.close()

    def test_timeout_retains_capacity_discards_late_body_and_recovers_after_exit(self):
        started, release = Event(), Event()
        calls = []
        def slow():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(3))
            return b"too late"
        with tempfile.TemporaryDirectory() as directory:
            cache = ReportDeliveryCache(max_queued=0, preparation_timeout_seconds=.04,
                                        error_ttl_seconds=0, storage_dir=Path(directory))
            try:
                cache.request("key", slow, lambda: True, scope="scope", persistent_key="a" * 64)
                self.assertTrue(started.wait(1))
                time.sleep(.06)
                self.assertEqual(cache.pending_result("scope").error.status, 504)
                for _ in range(3):
                    self.assertEqual(cache.request("key", slow, lambda: True).error.code, "report_preparation_timeout")
                self.assertEqual(cache.request("different", lambda: b"new", lambda: True).error.status, 503)
                self.assertEqual(cache.stats()["pending"], 1)
                self.assertEqual(calls, [1])
                release.set()
                deadline = time.monotonic() + 2
                while cache.stats()["pending"] and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(cache.stats()["bytes"], 0)
                self.assertEqual(list(Path(directory).glob("*.report")), [])
                self.assertEqual(completed(cache, "key", lambda: b"recovered").body, b"recovered")
            finally:
                release.set()
                cache.close()

    def test_expired_queued_report_never_runs_its_builder(self):
        started, release = Event(), Event()
        cache = ReportDeliveryCache(max_queued=1, preparation_timeout_seconds=.04)
        def slow():
            started.set()
            self.assertTrue(release.wait(3))
            return b"late"
        try:
            cache.request("running", slow, lambda: True)
            self.assertTrue(started.wait(1))
            cache.request("queued", lambda: self.fail("expired builder ran"), lambda: True, scope="queued")
            time.sleep(.06)
            self.assertEqual(cache.pending_result("queued").error.status, 504)
            release.set()
            deadline = time.monotonic() + 2
            while cache.stats()["pending"] and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(cache.request("queued", lambda: self.fail("expired retry"), lambda: True).error.status, 504)
            self.assertEqual(cache.stats()["bytes"], 0)
        finally:
            release.set()
            cache.close()


class ReportDeliveryPendingApiTests(unittest.TestCase):
    def test_relative_policy_edit_invalidates_live_and_persistent_reports(self):
        fixture = independent_fixture.IndependentReportDeliveryApiTests()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "policy-resources"
            (resources / "data").mkdir(parents=True)
            policy = resources / "data" / "relative_reference_policy.json"
            policy.write_bytes(b'{"prior_sample_weight":3}')
            with patch("rallymate_service.api.files", return_value=resources):
                app, settings, database, job, folder, marker = fixture.setup_case(root)
                calls = []
                def build(summary, records, **kwargs):
                    calls.append(1)
                    return {"job_id": summary["job_id"], "policy": json.loads(policy.read_bytes())}
                url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
                with fixture.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build}):
                    with TestClient(app) as client:
                        first = fixture.ready(client, url)
                        self.assertEqual(first.json()["policy"]["prior_sample_weight"], 3)
                        independent_fixture.rewrite_same_size_preserving_mtime(policy, b'{"prior_sample_weight":4}')
                        changed = client.get(url)
                        self.assertEqual(changed.status_code, 503)
                        self.assertEqual(changed.json()["detail"]["code"], "report_rules_changed_restart_required")
                        self.assertEqual(calls, [1])
                    with TestClient(create_app(settings, database)) as restarted:
                        second = fixture.ready(restarted, url)
                        self.assertEqual(second.json()["policy"]["prior_sample_weight"], 4)
                        self.assertEqual(calls, [1, 1])

    def test_new_app_reuses_disk_bytes_and_content_change_rebuilds(self):
        fixture = independent_fixture.IndependentReportDeliveryApiTests()
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = fixture.setup_case(Path(directory))
            calls = []
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with fixture.lightweight_builders(marker, calls):
                with TestClient(app) as client:
                    first = fixture.ready(client, url).content
                restarted = create_app(settings, database)
                with TestClient(restarted) as client:
                    self.assertEqual(fixture.ready(client, url).content, first)
                    self.assertEqual(calls, [1])
                    independent_fixture.rewrite_same_size_preserving_mtime(marker, b'{"marker":"B"}')
                    changed = fixture.ready(client, url)
                    self.assertNotEqual(changed.content, first)
                    self.assertEqual(changed.json()["source_marker"], '{"marker":"B"}')
                    self.assertEqual(calls, [1, 1])

    def test_preparing_polls_skip_hashes_but_job_cancellation_is_checked(self):
        fixture = independent_fixture.IndependentReportDeliveryApiTests()
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = fixture.setup_case(Path(directory))
            started, release = Event(), Event()
            calls = []
            def build(*args, **kwargs):
                calls.append(1)
                started.set()
                if not release.wait(4):
                    raise RuntimeError("release not received")
                return {"job_id": job, "score": None}
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with fixture.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build}), TestClient(app) as client:
                try:
                    self.assertEqual(client.get(url).status_code, 202)
                    self.assertTrue(started.wait(1))
                    with patch("rallymate_service.api.artifact_signature", side_effect=AssertionError("poll rehashed")), \
                            patch.object(database, "get_job", side_effect=AssertionError("poll parsed full summary")):
                        for _ in range(5):
                            self.assertEqual(client.get(url).status_code, 202)
                        # Completed jobs are not cancellable through the queue
                        # command. Still fail closed if persisted state changes.
                        with database.connect() as connection:
                            connection.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job,))
                            connection.commit()
                        self.assertEqual(client.get(url).status_code, 409)
                    self.assertEqual(calls, [1])
                finally:
                    release.set()
            self.assertEqual(list((settings.data_root / "report-delivery-cache").glob("*.report")), [])

    def test_native_pose_rewrite_invalidates_cached_action_score(self):
        fixture = independent_fixture.IndependentReportDeliveryApiTests()
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database, job, folder, marker = fixture.setup_case(Path(directory))
            native = folder / "sam3d_frames.jsonl"
            native.write_bytes(b'{"pose":"A"}\n')
            calls = []
            def build(summary, records, **kwargs):
                calls.append(1)
                return {"job_id": job, "native_pose_marker": native.read_text()}
            url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
            with fixture.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build}):
                with TestClient(app) as client:
                    first = fixture.ready(client, url).content
                with TestClient(create_app(settings, database)) as client:
                    self.assertEqual(fixture.ready(client, url).content, first)
                    self.assertEqual(calls, [1])
                    independent_fixture.rewrite_same_size_preserving_mtime(native, b'{"pose":"B"}\n')
                    changed = fixture.ready(client, url)
                    self.assertEqual(changed.json()["native_pose_marker"], '{"pose":"B"}\n')
                    self.assertEqual(calls, [1, 1])
