"""Probe invariants only; mock transport is not evidence of cloud acceptance."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cloud_acceptance_probe.py"
spec = importlib.util.spec_from_file_location("cloud_acceptance_probe", MODULE_PATH)
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
REVISION = "a" * 64
RECEIPT = {"service_url": "https://qa.example.test", "versions": {"source_current_manifest_sha256": REVISION}, "qa_workload_ready": True}
META = {"deployment": {"source_manifest_sha256": REVISION, "source_files_verified": True, "manifest_unchanged_since_startup": True}}
JOB_ID = "12345678-test"


def result_payloads():
    review = {"schema_version": "technical-review-v1.0.0", "job_id": JOB_ID, "revision": 0, "entries": [], "rules": []}
    return {"": {"id": JOB_ID, "status": "succeeded", "summary": {"job_id": JOB_ID}},
            "/demo-result": {"schema_version": "1.2.0", "job_id": JOB_ID, "status": "ready",
                "result_kind": "real_video_training_feedback_preview", "training_evaluation": {}, "analysis_report": {}},
            "/trajectory": {"schema_version": "1.0.0", "job_id": JOB_ID, "status": "not_observed",
                "source": {"frames_path": "frames.jsonl"}, "ball": {}, "racket": {}},
            "/pose-preview": {"schema_version": "1.0.0", "job_id": JOB_ID, "result_kind": "observed_pose_playback",
                "coordinate_space": "normalized_frame_0_1", "frames": [], "source": {"frames_path": "frames.jsonl"}},
            "/technical-review": review, "/technical-review/export": {**review, "history": []}}


class CloudAcceptanceProbeTests(unittest.TestCase):
    def test_deferred_report_waits_for_complete_200_and_negotiates_transport(self):
        probe = Mock(deadline=None)
        ready = result_payloads()["/demo-result"]
        probe.request.side_effect = [(202, {"job_id": JOB_ID, "status": "preparing", "report_delivery": "deferred-v1"}), (200, ready)]
        with patch.object(qa.time, "sleep"):
            self.assertEqual(qa.read_prepared_report(probe, JOB_ID), (200, ready))
        self.assertEqual(probe.request.call_count, 2)
        self.assertIn("report_delivery=deferred-v1", probe.request.call_args.args[1])

    def test_deferred_report_rejects_wrong_receipts_and_dangling_proofs(self):
        for response in [(202, {"job_id": "other", "status": "preparing", "report_delivery": "deferred-v1"}),
                         (202, {"job_id": JOB_ID, "status": "ready"}),
                         (200, {**result_payloads()["/demo-result"], "nested": {"model_evidence_ref": 0}}),
                         (200, {"job_id": JOB_ID, "status": "preparing", "report_delivery": "deferred-v1"})]:
            probe = Mock(deadline=None)
            probe.request.return_value = response
            with patch.object(qa.time, "sleep") as sleep:
                status, payload = qa.read_prepared_report(probe, JOB_ID)
            self.assertNotEqual((status, payload), (200, response[1]))
            self.assertIsNone(payload)
            sleep.assert_not_called()

    def test_workload_requires_explicit_deployment_handoff_and_revision(self):
        receipt = {"service_url": "https://qa.example.test", "versions": {"manifest_sha256": "abc"}}
        self.assertEqual(qa.target_from_receipt(receipt, False)[0], receipt["service_url"])
        with self.assertRaisesRegex(ValueError, "handed the GPU"):
            qa.target_from_receipt(receipt, True)
        for change in ({"versions": {}}, {"service_url": "https://user:secret@qa.example.test"},
                       {"service_url": "http://qa.example.test"}, {"service_url": "https://qa.example.test/private"}):
            with self.assertRaises(ValueError):
                qa.target_from_receipt({**receipt, "qa_workload_ready": True, **change}, True)
        self.assertEqual(qa.target_from_receipt(RECEIPT, True)[1], REVISION)
        for versions in ({"arbitrary": "value"}, {"source_current_manifest_sha256": "a" * 63},
                         {"source_current_manifest_sha256": "g" * 64}):
            with self.assertRaisesRegex(ValueError, "64-hex"):
                qa.target_from_receipt({**RECEIPT, "versions": versions}, True)

    def test_parallel_escalation_rejects_changed_input_revision_frames_and_partial_pass(self):
        report = {"base_url": "https://qa.example.test", "revision_sha256": "r", "video_sha256": "v",
                  "concurrency": 1, "max_frames": 150, "mode": "workload", "successful": True, "jobs": [{"passed": True}]}
        qa.require_serial(report, "https://qa.example.test", "r", "v", 150)
        for change in ({"revision_sha256": "other"}, {"video_sha256": "other"}, {"max_frames": 600},
                       {"concurrency": 2}, {"jobs": []}, {"jobs": [{"passed": False}]}, {"mode": "smoke"}):
            with self.assertRaises(ValueError):
                qa.require_serial({**report, **change}, "https://qa.example.test", "r", "v", 150)

    def test_percentiles_and_failure_rate_do_not_count_timeouts_as_success(self):
        metrics = qa.aggregate([{"passed": True, "end_to_end_seconds": 10, "queue_wait_seconds": 2},
                                {"passed": True, "end_to_end_seconds": 30, "queue_wait_seconds": 5},
                                {"passed": False, "elapsed_seconds": 600}], 640)
        self.assertEqual(metrics["error_rate"], 1 / 3)
        self.assertEqual(metrics["end_to_end_seconds"], {"sample_count": 2, "p50": 10, "p95": 30})
        self.assertIsNone(qa.aggregate([], 1)["error_rate"])
        self.assertIsNone(qa.seconds_between(None, "2026-10-18T12:00:00Z"))
        self.assertIsNone(qa.seconds_between("2026-10-18T12:00:00Z", "2026-10-18T11:00:00Z"))
        self.assertEqual(qa.seconds_between("2026-10-18T12:00:00Z", "2026-10-18T12:00:07Z"), 7)

    def test_http_errors_are_recorded_without_response_secrets_or_foreign_redirects(self):
        probe = qa.Probe("https://qa.example.test")
        probe.client.close()
        probe.client = httpx.Client(base_url=probe.base, transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"Location": "https://other.example.test"}, json={"secret": "hidden"})))
        try:
            self.assertEqual(probe.request("GET", "/health/live")[0], 302)
            self.assertNotIn("hidden", str(probe.requests))
            with self.assertRaises(ValueError):
                probe.request("GET", "https://other.example.test/v1/jobs")
            with self.assertRaises(ValueError):
                probe.request("POST", "/api/advice")
        finally:
            probe.close()

    def test_upload_validates_persisted_chunk_digest_on_reconnect(self):
        class FakeProbe:
            def __init__(self, resumed_digest):
                self.resumed_digest = resumed_digest
                self.calls = []

            def request(self, method, path, **kwargs):
                self.calls.append((method, path))
                if path == "/v1/uploads":
                    return 201, {"chunk_size": 4}
                if method == "PUT":
                    return 200, {"chunk_sha256": {"0": kwargs["headers"]["X-Chunk-SHA256"]}}
                if method == "GET":
                    return 200, {"chunk_sha256": {"0": self.resumed_digest}}
                return 202, {"id": "12345678-test", "status": "queued"}

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "real-input.mp4"
            video.write_bytes(b"1234")  # transport fixture, never claimed to be a real inference video
            probe = FakeProbe("incorrect")
            with self.assertRaisesRegex(ValueError, "upload_resume_failed"):
                qa.upload(probe, video, 150)
            self.assertFalse(any(path.endswith("/complete") for _, path in probe.calls))
            self.assertEqual(qa.upload(FakeProbe(qa.digest(b"1234")), video, 150)["id"], "12345678-test")

    def test_failed_job_is_not_reported_as_passing_or_cancelled(self):
        class FakeProbe:
            requests = []

            def request(self, method, path):
                return (200, META) if path == "/v1/meta" else (200, {"id": JOB_ID, "status": "failed"})

            def close(self):
                pass

        with patch.object(qa, "Probe", return_value=FakeProbe()), patch.object(qa, "upload", return_value={"id": "12345678-test"}):
            result = qa.run_job("http://localhost", Path("unused"), 150, 60, expected_revision=REVISION)
        self.assertFalse(result["passed"])
        self.assertEqual(result["terminal_status"], "failed")
        self.assertEqual(result["error_code"], "job_did_not_succeed")
        self.assertNotIn("server_job_may_still_be_active", result)

    def test_lost_upload_completion_ack_marks_unknown_job_active_without_resubmission(self):
        for lost_at in ("complete", "chunk"):
            with self.subTest(lost_at=lost_at), tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / "transport-fixture.mp4"
                video.write_bytes(b"1234")
                calls = []

                def respond(request):
                    calls.append((request.method, request.url.path))
                    if request.url.path == "/v1/meta":
                        return httpx.Response(200, json=META)
                    if request.url.path == "/v1/uploads":
                        return httpx.Response(201, json={"chunk_size": 4})
                    if request.method == "PUT" and lost_at == "chunk":
                        raise httpx.ReadTimeout("chunk acknowledgement lost", request=request)
                    if request.url.path.endswith("/complete"):
                        # The server may already have created a job when its response is lost.
                        raise httpx.ReadTimeout("completion acknowledgement lost", request=request)
                    return httpx.Response(200, json={"chunk_sha256": {"0": qa.digest(b"1234")}})

                probe = qa.Probe("https://qa.example.test")
                probe.client.close()
                probe.client = httpx.Client(base_url=probe.base, transport=httpx.MockTransport(respond))
                with patch.object(qa, "Probe", return_value=probe):
                    result = qa.run_job(probe.base, video, 150, 60, expected_revision=REVISION)

                self.assertFalse(result["passed"])
                self.assertIsNone(result["job_id"])
                self.assertEqual(result.get("server_job_may_still_be_active", False), lost_at == "complete")
                self.assertEqual(result["error_code"], "upload_complete_failed" if lost_at == "complete" else "chunk_not_acknowledged")
                self.assertEqual(calls.count(("POST", "/v1/uploads")), 1)
                completions = [item for item in result["requests"] if item["path"].endswith("/complete")]
                self.assertEqual(len(completions), int(lost_at == "complete"))
                if completions:
                    self.assertIsNone(completions[0]["status"])
                    self.assertEqual(completions[0]["error_type"], "ReadTimeout")
                self.assertFalse(any(path.startswith("/v1/jobs/") for _, path in calls))
                self.assertTrue(probe.client.is_closed)

    def test_wrong_job_or_malformed_state_never_passes_or_ends_the_expected_job(self):
        for payload in ({"id": "different-job", "status": "succeeded"}, {"id": JOB_ID, "status": {}}):
            probe = Mock(requests=[], deadline=None)
            probe.request.side_effect = [(200, META), (200, payload)]
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload", return_value={"id": JOB_ID}):
                result = qa.run_job(RECEIPT["service_url"], Path("unused"), 150, 30, expected_revision=REVISION)
            self.assertFalse(result["passed"])
            self.assertNotIn("terminal_status", result)
            self.assertTrue(result["server_job_may_still_be_active"])
            self.assertEqual(probe.request.call_count, 2)

    def test_result_contracts_bind_every_endpoint_and_reject_empty_or_wrong_payloads(self):
        payloads = result_payloads()
        for suffix, payload in payloads.items():
            with self.subTest(suffix=suffix):
                self.assertTrue(qa.result_contract_valid(suffix, payload, JOB_ID))
                self.assertFalse(qa.result_contract_valid(suffix, {}, JOB_ID))
                self.assertFalse(qa.result_contract_valid(suffix, payload, "another-job"))
                malformed = {**payload, ("summary" if not suffix else "schema_version"): None}
                self.assertFalse(qa.result_contract_valid(suffix, malformed, JOB_ID))
        for artifacts, expected in ((list(payloads.values()), True), ([{}] * 6, False)):
            probe = Mock(requests=[], deadline=None)
            probe.request.side_effect = [(200, META), (200, payloads[""])] + [(200, item) for item in artifacts] + [(200, META)]
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload", return_value={"id": JOB_ID}):
                result = qa.run_job(RECEIPT["service_url"], Path("unused"), 150, 30, expected_revision=REVISION)
            self.assertEqual(result["passed"], expected)
            self.assertFalse(result["business_quality_verified"])
            self.assertEqual(len(result["deployment_checks"]), 2)

    def test_deployment_must_match_before_upload_and_after_results(self):
        for deployment in ({}, {"deployment": {}}, {"deployment": {**META["deployment"], "source_manifest_sha256": "b" * 64}},
                           {"deployment": {**META["deployment"], "source_files_verified": False}},
                           {"deployment": {**META["deployment"], "manifest_unchanged_since_startup": False}}):
            probe = Mock(requests=[], deadline=None)
            probe.request.return_value = (200, deployment)
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload") as upload:
                result = qa.run_job(RECEIPT["service_url"], Path("unused"), 150, 30, expected_revision=REVISION)
            self.assertFalse(result["passed"])
            upload.assert_not_called()
        probe = Mock(requests=[], deadline=None)
        payloads = result_payloads()
        probe.request.side_effect = [(200, META), (200, payloads[""])] + [(200, item) for item in payloads.values()] + [(200, {})]
        with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload", return_value={"id": JOB_ID}):
            result = qa.run_job(RECEIPT["service_url"], Path("unused"), 150, 30, expected_revision=REVISION)
        self.assertFalse(result["passed"])
        self.assertFalse(result["deployment_checks"][-1]["passed"])

    def test_workload_meta_gate_stops_all_submissions_before_starting_a_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video, receipt, output = [root / name for name in ("fixture.mp4", "receipt.json", "report.json")]
            video.write_bytes(b"mock transport fixture")
            receipt.write_text(json.dumps(RECEIPT), encoding="utf-8")
            probe = Mock(requests=[], deadline=None)
            probe.request.return_value = (200, {"deployment": {**META["deployment"], "source_manifest_sha256": "b" * 64}})
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "smoke", return_value=[{"passed": True}]), \
                    patch.object(qa, "run_job") as run, patch("sys.stdout", new=io.StringIO()):
                code = qa.main(["--deployment-status", str(receipt), "--output", str(output), "--video", str(video)])
            run.assert_not_called()
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertFalse(report["successful"])
            self.assertEqual(report["jobs"], [])
            self.assertEqual(report["checks"][-1]["phase"], "before_workload")

    def test_deployment_verified_flags_require_true_booleans(self):
        for key in ("source_files_verified", "manifest_unchanged_since_startup"):
            for value in (None, False, 1, "true"):
                probe = Mock()
                probe.request.return_value = (200, {"deployment": {**META["deployment"], key: value}})
                self.assertFalse(qa.deployment_check(probe, REVISION, "before_upload")["passed"])
        probe = Mock()
        self.assertFalse(qa.deployment_check(probe, None, "before_upload")["passed"])
        probe.request.assert_not_called()

    def test_worker_exception_preserves_known_job_and_request_trace_without_secret_details(self):
        for error in (TypeError("private-response-secret"), OSError("private-path-secret")):
            probe = Mock(requests=[{"method": "POST", "path": "/v1/uploads/upload-id/complete", "status": 202}])
            probe.request.side_effect = [(200, META), error]
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload", return_value={"id": JOB_ID}):
                result = qa.run_job(RECEIPT["service_url"], Path("unused"), 150, 30, expected_revision=REVISION)
            self.assertFalse(result["passed"])
            self.assertEqual(result["job_id"], JOB_ID)
            self.assertTrue(result["server_job_may_still_be_active"])
            self.assertEqual(result["error_type"], type(error).__name__)
            self.assertNotIn("secret", json.dumps(result))

    def test_one_future_exception_preserves_entire_batch_and_prevents_more_submissions(self):
        for error in (TypeError("private-response-secret"), OSError("private-path-secret")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                video, receipt, serial, output = [root / name for name in ("fixture.mp4", "receipt.json", "serial.json", "report.json")]
                video.write_bytes(b"mock transport fixture")
                receipt.write_text(json.dumps(RECEIPT), encoding="utf-8")
                serial.write_text(json.dumps({"base_url": RECEIPT["service_url"], "revision_sha256": REVISION,
                    "video_sha256": qa.digest(video.read_bytes()), "concurrency": 1, "max_frames": 150,
                    "mode": "workload", "successful": True, "jobs": [{"passed": True}]}), encoding="utf-8")
                probe = Mock(requests=[], deadline=None)
                probe.request.return_value = (200, META)
                with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "smoke", return_value=[{"passed": True}]), \
                        patch.object(qa, "run_job", side_effect=[{"passed": True, "job_id": JOB_ID}, error]) as run, \
                        patch("sys.stdout", new=io.StringIO()):
                    code = qa.main(["--deployment-status", str(receipt), "--output", str(output), "--video", str(video),
                                    "--jobs", "4", "--concurrency", "2", "--serial-report", str(serial)])
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(code, 1)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(len(report["jobs"]), 2)
                self.assertEqual(report["jobs"][0]["job_id"], JOB_ID)
                self.assertTrue(report["jobs"][1]["server_job_may_still_be_active"])
                self.assertTrue(report["stopped_after_failure"])
                self.assertFalse(report["successful"])
                self.assertNotIn("secret", json.dumps(report))

    def test_deadline_does_not_make_an_http_request(self):
        probe = qa.Probe("https://qa.example.test", deadline=0)
        with patch.object(probe.client, "request") as request:
            with self.assertRaisesRegex(ValueError, "deadline"):
                probe.request("GET", "/health/live")
            request.assert_not_called()
        probe.close()


if __name__ == "__main__":
    unittest.main()
