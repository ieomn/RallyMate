"""Mock control-flow tests, not evidence of cloud cancellation or GPU recovery."""
from collections import deque
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "cloud_cancellation_probe.py"
spec = importlib.util.spec_from_file_location("scripts.cloud_cancellation_probe", MODULE)
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
ID = "cancel-job-123456"
REVISION = "a" * 64
RECEIPT = {"service_url": "https://qa.example.test", "versions": {"source_current_manifest_sha256": REVISION}, "qa_workload_ready": True}
META = {"deployment": {"source_manifest_sha256": REVISION, "source_files_verified": True, "manifest_unchanged_since_startup": True}}


def job(state, **extra):
    return {"id": ID, "status": state, "created_at": "2026-10-18T11:59:00Z", "original_filename": "input.mp4",
            "started_at": "2026-10-18T11:59:01Z" if state == "running" else None, "attempts": 1 if state == "running" else 0,
            **({"cancel_requested": True, "cancel_requested_at": "2026-10-18T12:00:00Z",
                "cancelled_at": "2026-10-18T12:00:02Z"} if state == "cancelled" else {}), **extra}


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeProbe:
    def __init__(self, replies, meta_replies=()):
        self.replies = deque(replies)
        self.meta_replies = deque(meta_replies)
        self.requests = []
        self.closed = False

    def request(self, method, path, **kwargs):
        if path == "/v1/meta":
            status, payload = self.meta_replies.popleft() if self.meta_replies else (200, META)
        else:
            status, payload = self.replies.popleft() if self.replies else (200, job("running", cancel_requested=True))
        self.requests.append({"method": method, "path": path, "status": status})
        return status, payload

    def close(self):
        self.closed = True


class CloudCancellationProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video = Path(self.temp.name) / "input.mp4"
        self.video.write_bytes(b"mock transport fixture, never an inference accuracy baseline")

    def run_probe(self, replies, meta_replies=(), **options):
        clock, probe = Clock(), FakeProbe(replies, meta_replies)
        with patch.object(qa, "Probe", return_value=probe) as factory, \
                patch.object(qa, "upload", return_value=job("queued")) as upload, \
                patch.object(qa, "validate_video", return_value={"first_frame_decoded": True, "width": 1280, "height": 720}), \
                patch.object(qa.time, "monotonic", clock.monotonic), patch.object(qa.time, "sleep", clock.sleep):
            result = qa.run_cancellation(RECEIPT, self.video, poll_seconds=1, **options)
        upload.assert_called_once()
        self.assertTrue(probe.closed)
        self.assertEqual(factory.call_args.args, (RECEIPT["service_url"],))
        self.assertEqual(result["revision_sha256"], qa.target_from_receipt(RECEIPT, True)[1])
        self.assertFalse(result["gpu_release_verified"])
        return result, probe

    def test_running_waits_for_explicit_phase_and_confirmed_stop_then_checks_idempotency_and_history(self):
        cancelled = job("cancelled", cancel_requested=True, cancel_requested_at="2026-10-18T12:00:00Z", cancelled_at="2026-10-18T12:00:02Z")
        result, probe = self.run_probe([(200, job("queued")), (200, job("running")),
            (200, job("running", cancel_requested=True)), (200, job("running", cancel_requested=True)),
            (200, cancelled), (200, cancelled), (200, cancelled)])
        self.assertTrue(result["passed"])
        self.assertTrue(result["target_phase_observed"])
        self.assertEqual(result["stop_latency_seconds"], 2)
        self.assertFalse(result["server_job_may_still_be_active"])
        job_requests = [item for item in probe.requests if item["path"] != "/v1/meta"]
        self.assertEqual([item["method"] for item in job_requests], ["GET", "GET", "POST", "GET", "GET", "POST", "GET"])
        self.assertTrue(all(item["path"].startswith(f"/v1/jobs/{ID}") for item in job_requests))
        self.assertEqual([item["phase"] for item in result["deployment_checks"]], ["before_upload", "after_cancellation"])

    def test_queued_confirmed_cancellation_is_immediate_but_does_not_claim_gpu_release(self):
        cancelled = job("cancelled", cancel_requested=True)
        result, _ = self.run_probe([(200, job("queued")), (200, cancelled), (200, cancelled), (200, cancelled)], when="queued")
        self.assertTrue(result["passed"])
        self.assertEqual(result["stop_latency_seconds"], 0)
        self.assertIn("independent timestamped", " ".join(result["limitations"]))

    def test_completed_before_target_is_not_exercised_and_never_creates_another_job(self):
        complete = job("succeeded", summary={"preserved": True})
        result, probe = self.run_probe([(200, complete), (200, complete)])
        self.assertFalse(result["passed"])
        self.assertEqual(result["outcome"], "not_exercised")
        self.assertFalse(result["cancellation_confirmed"])
        self.assertIsNone(result["stop_latency_seconds"])
        self.assertFalse(any(item["method"] == "POST" for item in probe.requests))

    def test_completion_race_is_not_exercised_for_both_requested_phases_and_keeps_report(self):
        for phase in ("queued", "running"):
            with self.subTest(phase=phase):
                complete = job("succeeded", summary={"preserved": True}, artifact_urls={"report": "same-origin-report"})
                result, _ = self.run_probe([(200, job(phase)), (200, complete), (200, complete), (200, complete)], when=phase)
                self.assertFalse(result["passed"])
                self.assertEqual(result["outcome"], "not_exercised")
                self.assertEqual(result["reason"], "completed_in_cancel_race")
                self.assertTrue(all(check["passed"] for check in result["checks"]))

    def test_missed_queued_phase_cleans_up_its_single_job_without_claiming_queued_coverage(self):
        cancelled = job("cancelled", cancel_requested=True)
        for initial in ("queued", "running"):
            with self.subTest(initial=initial):
                result, _ = self.run_probe([(200, job(initial)), (200, job("running", cancel_requested=True)),
                    (200, cancelled), (200, cancelled), (200, cancelled)], when="queued")
                self.assertFalse(result["passed"])
                self.assertEqual(result["outcome"], "not_exercised")
                self.assertTrue(result["cancellation_confirmed"])
                self.assertFalse(result["cancel_phase_exercised"])

    def test_requested_flag_and_poll_timeout_never_imply_stop_or_resubmit(self):
        result, probe = self.run_probe([(200, job("running")), (200, job("running", cancel_requested=True))], deadline_seconds=30)
        self.assertFalse(result["passed"])
        self.assertFalse(result["cancellation_confirmed"])
        self.assertIsNone(result["stop_latency_seconds"])
        self.assertTrue(result["server_job_may_still_be_active"])
        self.assertEqual(result["error_code"], "job_deadline_exceeded")
        self.assertEqual(result["elapsed_seconds"], 30)
        self.assertEqual(sum(item["method"] == "POST" for item in probe.requests), 1)

    def test_queued_claim_race_and_missing_claim_evidence_never_count_as_queued_coverage(self):
        for change in ({"started_at": "2026-10-18T11:59:01Z", "attempts": 1}, {"attempts": 1},
                       {"started_at": "2026-10-18T11:59:01Z"}, {"attempts": None}):
            with self.subTest(change=change):
                cancelled = job("cancelled", **change)
                result, probe = self.run_probe([(200, job("queued")), (200, cancelled), (200, cancelled), (200, cancelled)], when="queued")
                self.assertFalse(result["passed"])
                self.assertEqual(result["outcome"], "not_exercised")
                self.assertFalse(result["cancel_phase_exercised"])
                self.assertTrue(result["cancellation_confirmed"])
                self.assertEqual(sum(item["method"] == "POST" for item in probe.requests), 2)

    def test_first_cancel_must_retain_pre_cancel_identity_and_existing_outputs(self):
        before = job("running", summary={"existing": "history"}, artifact_urls={"summary.json": "/existing-report"})
        good = job("cancelled", summary=before["summary"], artifact_urls=before["artifact_urls"])
        for change in ({"created_at": None}, {"original_filename": None}, {"summary": None},
                       {"summary": {}}, {"artifact_urls": {}}, {"artifact_urls": None}):
            with self.subTest(change=change):
                broken = {**good, **change}
                result, _ = self.run_probe([(200, before), (200, broken), (200, broken), (200, broken)])
                self.assertFalse(result["passed"])
                self.assertTrue(any(check["name"] == "pre_cancel_identity_and_outputs_preserved" and not check["passed"]
                                    for check in result["checks"]))
        result, _ = self.run_probe([(200, before), (200, good), (200, good), (200, good)])
        self.assertTrue(result["passed"])

    def test_cancelled_requires_flag_and_ordered_timezone_aware_timestamps(self):
        for change in ({"cancel_requested": False}, {"cancel_requested": None}, {"cancel_requested_at": None},
                       {"cancelled_at": None}, {"cancelled_at": "2026-10-18T12:00:01"},
                       {"cancelled_at": "2026-10-18T11:59:59Z"}, {"cancelled_at": "invalid"}):
            with self.subTest(change=change):
                broken = job("cancelled", **change)
                result, _ = self.run_probe([(200, job("running")), (200, broken), (200, broken), (200, broken)])
                self.assertFalse(result["passed"])
                self.assertFalse(result["cancellation_confirmed"])
                self.assertTrue(result["server_job_may_still_be_active"])

    def test_deployment_identity_is_required_before_upload_and_rechecked_at_end(self):
        for payload in ({}, {"deployment": {**META["deployment"], "source_manifest_sha256": "b" * 64}},
                        {"deployment": {**META["deployment"], "source_files_verified": False}},
                        {"deployment": {**META["deployment"], "manifest_unchanged_since_startup": False}}):
            probe = FakeProbe([], [(200, payload)])
            with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload") as upload, \
                    patch.object(qa, "validate_video", return_value={"first_frame_decoded": True}):
                result = qa.run_cancellation(RECEIPT, self.video)
            upload.assert_not_called()
            self.assertFalse(result["passed"])
            self.assertFalse(result["server_job_may_still_be_active"])
            self.assertEqual(result["error_code"], "deployment_identity_failed")
        cancelled = job("cancelled")
        result, _ = self.run_probe([(200, job("queued")), (200, cancelled), (200, cancelled), (200, cancelled)],
            when="queued", meta_replies=[(200, META), (200, {})])
        self.assertFalse(result["passed"])
        self.assertTrue(result["cancellation_confirmed"])
        self.assertFalse(result["deployment_checks"][-1]["passed"])

    def test_lost_cancel_ack_still_observes_terminal_but_does_not_pass_http_acceptance(self):
        cancelled = job("cancelled", cancel_requested=True)
        result, probe = self.run_probe([(200, job("running")), (None, None), (503, None),
            (200, cancelled), (200, cancelled), (200, cancelled)])
        self.assertFalse(result["passed"])
        self.assertTrue(result["cancellation_confirmed"])
        self.assertFalse(result["server_job_may_still_be_active"])
        self.assertEqual(sum(item["method"] == "POST" for item in probe.requests), 2)

    def test_mismatched_job_and_malformed_states_fail_without_recording_payload_secrets(self):
        for response in ({"id": "another-job", "status": "cancelled", "secret": "do-not-store"}, job({"secret": "do-not-store"})):
            with self.subTest(response=response):
                result, _ = self.run_probe([(200, response)])
                self.assertFalse(result["passed"])
                self.assertTrue(result["server_job_may_still_be_active"])
                self.assertNotIn("do-not-store", json.dumps(result))

    def test_duplicate_cancel_or_history_mutation_fails_and_keeps_known_job_identity(self):
        cancelled = job("cancelled", cancel_requested=True, cancelled_at="2026-10-18T12:00:00Z", summary={"kept": True})
        for repeat, history in (({**cancelled, "cancelled_at": "changed"}, cancelled), (cancelled, {**cancelled, "summary": None}),
                                (job("running"), job("running"))):
            with self.subTest(repeat=repeat, history=history):
                result, _ = self.run_probe([(200, job("running")), (200, cancelled), (200, repeat), (200, history)])
                self.assertFalse(result["passed"])
                self.assertEqual(result["job_id"], ID)
                if repeat["status"] == "running":
                    self.assertTrue(result["server_job_may_still_be_active"])

    def test_guard_rejects_missing_gpu_handoff_changed_target_shape_and_invalid_bounds_before_http(self):
        for receipt in ({**RECEIPT, "qa_workload_ready": False}, {**RECEIPT, "versions": {}},
                        {**RECEIPT, "service_url": "https://user:secret@qa.example.test"}):
            with patch.object(qa, "Probe") as factory, self.assertRaises(ValueError):
                qa.run_cancellation(receipt, self.video)
            factory.assert_not_called()
        for options in ({"max_frames": 601}, {"max_frames": 0}, {"deadline_seconds": 29}, {"deadline_seconds": 1201},
                        {"poll_seconds": 0}, {"poll_seconds": 11}, {"when": "any"}):
            with patch.object(qa, "Probe") as factory, self.assertRaises(ValueError):
                qa.run_cancellation(RECEIPT, self.video, **options)
            factory.assert_not_called()

    def test_video_validation_checks_decode_and_always_releases_local_capture(self):
        capture = Mock()
        capture.isOpened.return_value = True
        capture.read.return_value = (True, SimpleNamespace(ndim=3, shape=(720, 1280, 3)))
        with patch.dict("sys.modules", {"cv2": SimpleNamespace(VideoCapture=Mock(return_value=capture))}):
            self.assertEqual(qa.validate_video(self.video), {"first_frame_decoded": True, "width": 1280, "height": 720})
            capture.release.assert_called_once()
            capture.reset_mock()
            capture.read.return_value = (False, None)
            with self.assertRaisesRegex(ValueError, "decodable frame"):
                qa.validate_video(self.video)
            capture.release.assert_called_once()
        self.video.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "64 MiB"):
            qa.validate_video(self.video)
        with self.video.open("wb") as handle:
            handle.truncate(64 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "64 MiB"):
            qa.validate_video(self.video)

    def test_lost_upload_completion_response_records_unknown_job_may_be_active_without_retry(self):
        probe = FakeProbe([])

        def lost_completion(*_args):
            probe.requests.append({"method": "POST", "path": "/v1/uploads/upload-123456/complete", "status": None})
            raise ValueError("upload_complete_failed")

        with patch.object(qa, "Probe", return_value=probe), patch.object(qa, "upload", side_effect=lost_completion) as upload, \
                patch.object(qa, "validate_video", return_value={"first_frame_decoded": True}):
            result = qa.run_cancellation(RECEIPT, self.video)
        upload.assert_called_once()
        self.assertIsNone(result["job_id"])
        self.assertTrue(result["server_job_may_still_be_active"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_code"], "upload_complete_failed")
        self.assertEqual(len(probe.requests), 2)

    def test_existing_evidence_path_is_never_overwritten_or_used_to_start_work(self):
        output = Path(self.temp.name) / "existing.json"
        output.write_text("previous evidence", encoding="utf-8")
        with patch.object(qa, "run_cancellation") as run, patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            qa.main(["--deployment-status", "not-read.json", "--video", str(self.video), "--when", "running", "--output", str(output)])
        run.assert_not_called()
        self.assertEqual(output.read_text(), "previous evidence")


if __name__ == "__main__":
    unittest.main()
