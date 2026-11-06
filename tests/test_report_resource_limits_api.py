from pathlib import Path
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_service import api
from rallymate_vision.postprocess_resources import (
    encode_bounded_json, PostprocessOutputLimit, PostprocessResourceLimit,
)
from tests.test_report_delivery_api import setup_report, poll
from tests.test_report_evidence_transport import native_fixture


class ReportResourceLimitTests(unittest.TestCase):
    def test_bounded_encoding_preserves_bytes_and_checks_size_and_cancellation(self):
        payload = {"中文": [1, None, False, {"value": "ok"}]}
        checks = []
        encoded = encode_bounded_json(payload, max_bytes=1024,
                                      cancellation_callback=lambda: checks.append(True))
        self.assertEqual(encoded, json.dumps(payload, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.assertGreaterEqual(len(checks), 2)
        with self.assertRaises(PostprocessOutputLimit):
            encode_bounded_json(payload, max_bytes=4, cancellation_callback=lambda: None)
        with self.assertRaises(ValueError):
            encode_bounded_json({"nan": float("nan")}, max_bytes=1024,
                                cancellation_callback=lambda: None)
        def cancelled():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            encode_bounded_json(payload, max_bytes=1024, cancellation_callback=cancelled)

    def test_late_report_builder_exceeding_memory_returns_failure(self):
        for delivery in ("inline", "deferred-v1"):
            with self.subTest(delivery=delivery), tempfile.TemporaryDirectory() as directory:
                app, _, _, job = setup_report(Path(directory))
                supplied = native_fixture()
                supplied["job_id"] = job
                memory = [0]
                def exhausted(*args, **kwargs):
                    memory[0] = 2 ** 60
                    return {}
                with patch.object(api, "build_user_demo_result", side_effect=lambda *a, **kw: copy.deepcopy(supplied)), \
                     patch.object(api, "build_analysis_report", side_effect=exhausted), \
                     patch("rallymate_vision.postprocess_resources.process_memory_bytes", side_effect=lambda: memory[0]), \
                     TestClient(app) as client:
                    url = (f"/v1/jobs/{job}/demo-result?report_contract=current"
                           f"&report_transport=model-proof-pool-v1&report_delivery={delivery}")
                    result = poll(client, url)
                    self.assertEqual(result.status_code, 503, result.text[:300])
                    self.assertEqual(result.json()['detail']['code'], 'report_resource_limit')
                    self.assertIn('内存', result.json()['detail']['message'])
                    self.assertEqual(app.state.report_delivery_cache.stats()["bytes"], 0)

    def test_evidence_transport_capacity_returns_413_with_reason_for_both_modes(self):
        for delivery in ("inline", "deferred-v1"):
            with self.subTest(delivery=delivery), tempfile.TemporaryDirectory() as directory:
                app, _, _, job = setup_report(Path(directory))
                supplied = native_fixture()
                supplied["job_id"] = job
                with patch.object(api, "build_user_demo_result", side_effect=lambda *a, **kw: copy.deepcopy(supplied)), \
                     patch('rallymate_service.report_evidence_transport.MAX_MODEL_PROOFS', 0), \
                     TestClient(app) as client:
                    url = (f"/v1/jobs/{job}/demo-result?report_contract=current"
                           f"&report_transport=model-proof-pool-v1&report_delivery={delivery}")
                    result = poll(client, url)
                    self.assertEqual(result.status_code, 413, result.text[:300])
                    self.assertEqual(result.json()['detail']['code'], 'report_delivery_limit')
                    self.assertEqual(app.state.report_delivery_cache.stats()["bytes"], 0)

    def test_encoded_wire_limit_is_a_size_failure_instead_of_transient_503(self):
        with tempfile.TemporaryDirectory() as directory:
            app, _, _, job = setup_report(Path(directory))
            supplied = native_fixture()
            supplied['job_id'] = job
            app.state.report_delivery_cache.max_bytes = 16
            with patch.object(api, 'build_user_demo_result', side_effect=lambda *a, **kw: copy.deepcopy(supplied)), \
                 TestClient(app) as client:
                result = poll(client, f'/v1/jobs/{job}/demo-result?report_contract=current'
                                     '&report_transport=model-proof-pool-v1&report_delivery=deferred-v1')
                self.assertEqual(result.status_code, 413, result.text[:300])
                self.assertEqual(result.json()['detail']['code'], 'report_delivery_limit')
                self.assertEqual(app.state.report_delivery_cache.stats()['bytes'], 0)

    def test_pooling_checks_budget_during_the_walk(self):
        with tempfile.TemporaryDirectory() as directory:
            app, _, _, job = setup_report(Path(directory))
            supplied = native_fixture()
            supplied["job_id"] = job
            memory = [0]
            original_pack = api.pack_report_evidence

            def pack(payload, *, cancellation_callback):
                memory[0] = 2 ** 60
                return original_pack(payload, cancellation_callback=cancellation_callback)

            with patch.object(api, 'build_user_demo_result', side_effect=lambda *a, **kw: copy.deepcopy(supplied)), \
                 patch.object(api, 'pack_report_evidence', side_effect=pack), \
                 patch('rallymate_vision.postprocess_resources.process_memory_bytes', side_effect=lambda: memory[0]), \
                 TestClient(app) as client:
                # Force an immediate sample at the first cooperative pack check.
                with patch('rallymate_vision.postprocess_resources.PostprocessBudget.check', autospec=True) as checked:
                    def check(budget, *, force=False):
                        if memory[0]:
                            raise PostprocessResourceLimit('报告后处理内存超过预算')
                    checked.side_effect = check
                    result = poll(client, f'/v1/jobs/{job}/demo-result?report_contract=current'
                                         '&report_transport=model-proof-pool-v1&report_delivery=deferred-v1')
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()['detail']['code'], 'report_resource_limit')
                self.assertEqual(app.state.report_delivery_cache.stats()['bytes'], 0)


if __name__ == "__main__":
    unittest.main()
