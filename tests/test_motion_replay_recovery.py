from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from rallymate_service.motion_replay_provider import ProviderError
from rallymate_service.motion_replay_recovery import recover_existing_download
from rallymate_service.motion_replay_store import MotionReplaySettings, MotionReplayStore


class DownloadOnlyProvider:
    allow_proxy_dns = True

    def __init__(self):
        self.queries, self.downloads, self.submissions = [], [], 0
        self.status = "succeeded"
        self.on_query = self.on_download = None

    def submit(self, *args, **kwargs):
        self.submissions += 1
        raise AssertionError("Recovery must never submit")

    def query(self, task_id):
        self.queries.append(task_id)
        if self.on_query:
            self.on_query()
        return {"status": self.status, "download_url": "https://video-product.cdn.minimax.io/private-id/output.mp4?signature=never-log-this"}

    def download(self, url, target):
        self.downloads.append(target)
        if self.on_download:
            self.on_download()
        target.write_bytes(b"\x00\x00\x00\x18ftypisom-test")


class MotionReplayRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = MotionReplaySettings(Path(temp.name), lease_seconds=30)
        self.store = MotionReplayStore(self.settings.database_path)
        self.store.initialize()
        initial = self.store.create("job-1", {"duration_seconds": 6})
        claimed = self.store.claim_next("setup", 30)
        self.row = self.store.transition(initial["id"], claimed["lease_token"], "failed",
            provider_task_id="remote-1", provider_history=["remote-1"], retry_stage="provider_processing",
            error_code="invalid_output_url", error_message="生成结果下载地址不可用。", error_retryable=False)
        self.provider = DownloadOnlyProvider()

    def recover(self, **overrides):
        options = {"replay_id": self.row["id"], "job_id": "job-1", "provider": self.provider,
                   "output_validator": lambda *_: {"duration_ms": 6000, "width": 1366, "height": 768,
                                                    "decoded_frame_count": 144, "fidelity_verified": False}}
        options.update(overrides)
        return recover_existing_download(self.store, self.settings, **options)

    def test_existing_successful_task_downloads_under_lease_without_resubmission(self):
        self.provider.on_download = lambda: self.assertIsNone(self.store.claim_next("other-worker", 30))
        result = self.recover()
        saved = self.store.get(self.row["id"])
        self.assertEqual(result["generation_status"], "completed")
        self.assertEqual(saved["quality_status"], "pending_review")
        self.assertEqual(saved["attempts"], self.row["attempts"])
        self.assertEqual(saved["provider_history"], ["remote-1"])
        self.assertEqual(self.provider.queries, ["remote-1"])
        self.assertEqual(self.provider.submissions, 0)
        self.assertEqual(len(self.provider.downloads), 1)
        self.assertFalse(saved["generated_metadata"]["fidelity_verified"])
        audit_text = Path(result["audit_path"]).read_text(encoding="utf-8")
        audit = json.loads(audit_text)
        self.assertEqual(audit["original"]["error_code"], "invalid_output_url")
        self.assertEqual(audit["status"], "completed")
        self.assertNotIn("never-log-this", audit_text)
        self.assertNotIn("private-id", audit_text)
        self.assertNotIn("https://", audit_text)
        self.assertTrue(Path(result["media_path"]).is_file())

    def test_wrong_job_or_unbound_task_is_rejected_before_query(self):
        with self.assertRaises(ProviderError):
            self.recover(job_id="different-job")
        with self.store.connect() as connection:
            connection.execute("UPDATE motion_replays SET provider_history_json='[]' WHERE id=?", (self.row["id"],))
            connection.commit()
        with self.assertRaises(ProviderError):
            self.recover()
        self.assertEqual(self.provider.queries, [])
        self.assertEqual(self.provider.submissions, 0)

    def test_running_or_failed_provider_task_never_downloads_or_creates_another(self):
        for status in ("running", "queued", "failed", "cancelled"):
            self.provider.status = status
            with self.subTest(status=status), self.assertRaises(ProviderError) as error:
                self.recover()
            self.assertEqual(error.exception.code, "recovery_provider_not_succeeded")
        self.assertEqual(self.provider.downloads, [])
        self.assertEqual(self.provider.submissions, 0)
        self.assertEqual(self.store.get(self.row["id"])["generation_status"], "failed")

    def test_concurrent_state_change_aborts_before_download(self):
        def change():
            with self.store.connect() as connection:
                connection.execute("UPDATE motion_replays SET updated_at=updated_at+1 WHERE id=?", (self.row["id"],))
                connection.commit()
        self.provider.on_query = change
        with self.assertRaises(ProviderError) as error:
            self.recover()
        self.assertEqual(error.exception.code, "recovery_state_changed")
        self.assertEqual(self.provider.downloads, [])

    def test_existing_ssrf_rejection_is_not_bypassed(self):
        def reject():
            raise ProviderError("invalid_output_url", "blocked")
        self.provider.on_download = reject
        with self.assertRaises(ProviderError) as error:
            self.recover()
        saved = self.store.get(self.row["id"])
        self.assertEqual(error.exception.code, "invalid_output_url")
        self.assertEqual(saved["generation_status"], "failed")
        self.assertEqual(saved["retry_stage"], "provider_processing")
        self.assertEqual(saved["provider_history"], ["remote-1"])
        self.assertEqual(self.provider.submissions, 0)

    def test_invalid_media_never_publishes_a_completed_or_approved_result(self):
        def reject(*_):
            raise ProviderError("output_decode_incomplete", "incomplete")
        with self.assertRaises(ProviderError):
            self.recover(output_validator=reject)
        saved = self.store.get(self.row["id"])
        self.assertEqual(saved["generation_status"], "failed")
        self.assertEqual(saved["quality_status"], "pending_review")
        self.assertNotIn("generated_video", saved["artifacts"] or {})
        self.assertEqual(self.provider.submissions, 0)


if __name__ == "__main__":
    unittest.main()
