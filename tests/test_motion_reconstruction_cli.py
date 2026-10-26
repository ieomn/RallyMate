"""Paid-submission fencing and local recovery for explicit reconstruction runs."""
from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generate_motion_reconstruction as cli
from rallymate_service.motion_replay_provider import ProviderError


class FakeProvider:
    def __init__(self):
        self.submissions = []
        self.queries = []
        self.downloads = []
        self.submit_error = None
        self.query_error = None
        self.download_error = None
        self.result = {"status": "running", "download_url": None}

    def submit(self, path, **kwargs):
        self.submissions.append((path, kwargs))
        if self.submit_error:
            raise self.submit_error
        return "remote-task-123"

    def query(self, task_id):
        self.queries.append(task_id)
        if self.query_error:
            raise self.query_error
        return copy.deepcopy(self.result)

    def download(self, url, path):
        self.downloads.append((url, path))
        path.write_bytes(b"verified-video" if not self.download_error else b"partial")
        if self.download_error:
            raise self.download_error


class ReconstructionCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "experiment"
        self.output.mkdir()
        self.video = self.root / "original.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomavc1")
        self.args = argparse.Namespace(command="submit", output=self.output, reference_video=self.video,
                                      appearance_image=None, subject="", evidence_hint="", source_manifest=None,
                                      data_root=self.root, allow_proxy_dns=False)
        self.provider = FakeProvider()
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.key = contexts.enter_context(patch.object(cli, "load_minimax_key", return_value="test-only-key"))
        self.factory = contexts.enter_context(patch.object(cli, "MiniMaxVideoProvider", return_value=self.provider))
        self.validator = contexts.enter_context(patch.object(cli, "_validate_output", return_value={"frame_count": 144}))

    @property
    def state_path(self):
        return self.output / "generation.json"

    def state(self, status="provider_processing", **changes):
        value = {"version": "motion-reconstruction-experiment-v1", "status": status,
                 "provider_task_id": "remote-task-123", "remote_failures": 0,
                 "request": {"prompt": "frozen-experiment-prompt"}, "used_for_scoring": False}
        value.update(changes)
        self.state_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def poll(self):
        self.args.command = "poll"
        return cli.run(self.args)

    def succeeded(self):
        self.provider.result = {"status": "succeeded", "download_url": "https://cdn.example.com/result.mp4"}

    def test_successful_submit_reserves_once_and_freezes_source_request(self):
        state = cli.run(self.args)
        self.assertEqual(state["provider_task_id"], "remote-task-123")
        self.assertEqual(state["status"], "provider_processing")
        self.assertEqual(state["request"]["reference_video_sha256"], cli.sha256_path(self.video))
        frozen = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            cli.run(self.args)
        self.assertEqual(self.state_path.read_bytes(), frozen)
        self.assertEqual(len(self.provider.submissions), 1)

    def test_ambiguous_post_is_durable_and_never_resubmitted_by_submit_or_poll(self):
        self.provider.submit_error = ProviderError("submission_outcome_unknown", "uncertain", submission_unknown=True)
        state = cli.run(self.args)
        self.assertEqual(state["status"], "submit_unknown")
        frozen = self.state_path.read_bytes()
        with self.assertRaises(ValueError):
            cli.run(self.args)
        self.key.side_effect = AssertionError("terminal local state must not load credentials")
        self.assertEqual(self.poll()["status"], "submit_unknown")
        self.assertEqual(self.state_path.read_bytes(), frozen)
        self.assertEqual(len(self.provider.submissions), 1)
        self.assertEqual(self.provider.queries, [])

    def test_crash_during_post_leaves_reservation_and_poll_marks_unknown(self):
        self.provider.submit_error = RuntimeError("simulated process interruption")
        with self.assertRaises(RuntimeError):
            cli.run(self.args)
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "submitting")
        self.assertNotIn("provider_task_id", saved)
        with self.assertRaises(ValueError):
            cli.run(self.args)
        self.key.side_effect = AssertionError("recovery does not issue a new request")
        state = self.poll()
        self.assertEqual(state["status"], "submit_unknown")
        self.assertEqual(state["error_code"], "submission_outcome_unknown")
        self.assertEqual(len(self.provider.submissions), 1)

    def test_crash_after_post_before_task_id_persistence_never_repeats_post(self):
        with patch.object(cli, "write_state", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                cli.run(self.args)
        with self.assertRaises(ValueError):
            cli.run(self.args)
        state = self.poll()
        self.assertEqual(state["status"], "submit_unknown")
        self.assertEqual(len(self.provider.submissions), 1)
        self.assertEqual(self.provider.queries, [])

    def test_query_failure_resumes_only_the_same_task_and_preserves_prompt(self):
        original = self.state()
        self.provider.query_error = ProviderError("provider_temporarily_unavailable", "temporary", retryable=True)
        state = self.poll()
        self.assertEqual(state["remote_failures"], 1)
        self.assertEqual(state["provider_task_id"], "remote-task-123")
        self.provider.query_error = None
        state = self.poll()
        self.assertEqual(state["remote_failures"], 0)
        self.assertEqual(state["request"], original["request"])
        self.assertEqual(self.provider.queries, ["remote-task-123", "remote-task-123"])
        self.assertEqual(self.provider.submissions, [])

    def test_download_failure_removes_partial_then_requeries_same_id_without_post(self):
        self.state()
        self.succeeded()
        self.provider.download_error = ProviderError("output_download_failed", "temporary", retryable=True)
        state = self.poll()
        self.assertEqual(state["status"], "downloading")
        self.assertEqual(state["remote_failures"], 1)
        self.assertFalse((self.output / "generated.mp4").exists())
        self.assertFalse((self.output / "generated.pending.mp4").exists())
        self.provider.download_error = None
        state = self.poll()
        self.assertEqual(state["status"], "completed")
        self.assertEqual((self.output / "generated.mp4").read_bytes(), b"verified-video")
        self.assertEqual(self.provider.queries, ["remote-task-123", "remote-task-123"])
        self.assertEqual(self.provider.submissions, [])

    def test_invalid_download_never_publishes_final_media(self):
        self.state()
        self.succeeded()
        self.validator.side_effect = ProviderError("output_decode_incomplete", "invalid video")
        state = self.poll()
        self.assertEqual(state["status"], "downloading")
        self.assertEqual(state["remote_failures"], 1)
        self.assertFalse((self.output / "generated.mp4").exists())
        self.assertFalse((self.output / "generated.pending.mp4").exists())
        self.assertEqual(self.provider.submissions, [])

    def test_completed_state_and_media_are_unchanged_without_credentials_or_network(self):
        self.state("completed", generated_sha256="recorded-hash")
        video = self.output / "generated.mp4"
        video.write_bytes(b"existing-completed-output")
        original_state, original_video = self.state_path.read_bytes(), video.read_bytes()
        self.key.side_effect = AssertionError("completed results are local")
        self.factory.side_effect = AssertionError("must not instantiate provider")
        for _ in range(2):
            self.assertEqual(self.poll()["status"], "completed")
        self.assertEqual(self.state_path.read_bytes(), original_state)
        self.assertEqual(video.read_bytes(), original_video)
        self.validator.assert_not_called()

    def test_crash_after_verified_media_publish_recovers_locally_without_overwrite(self):
        self.state("downloading", remote_failures=5)
        video = self.output / "generated.mp4"
        video.write_bytes(b"verified-video-from-earlier-download")
        original = video.read_bytes()
        self.key.side_effect = AssertionError("verified local output requires no credentials")
        state = self.poll()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["generated_sha256"], cli.sha256_path(video))
        self.assertEqual(video.read_bytes(), original)
        self.assertEqual(self.provider.downloads, [])
        self.assertEqual(self.provider.queries, [])

    def test_existing_unowned_output_prevents_new_paid_submit_or_poll_overwrite(self):
        video = self.output / "generated.mp4"
        video.write_bytes(b"existing-unowned-video")
        with self.assertRaisesRegex(ValueError, "will not be overwritten"):
            cli.run(self.args)
        self.assertFalse(self.state_path.exists())
        self.state("provider_processing")
        with self.assertRaisesRegex(ValueError, "will not be overwritten"):
            self.poll()
        self.assertEqual(video.read_bytes(), b"existing-unowned-video")
        self.assertEqual(self.provider.submissions, [])
        self.assertEqual(self.provider.queries, [])

    def test_failure_budget_never_triggers_new_paid_generation(self):
        self.state(remote_failures=5)
        self.key.side_effect = AssertionError("budget is checked before credentials")
        with self.assertRaisesRegex(ValueError, "Repeated remote failures"):
            self.poll()
        self.assertEqual(self.provider.submissions, [])
        self.assertEqual(self.provider.queries, [])

    def test_terminal_remote_failure_is_persisted_and_subsequent_poll_is_local(self):
        self.state()
        self.provider.result = {"status": "failed", "download_url": None}
        state = self.poll()
        self.assertEqual(state["status"], "failed")
        self.key.side_effect = AssertionError("terminal failure requires no further request")
        self.assertEqual(self.poll(), state)
        self.assertEqual(self.provider.queries, ["remote-task-123"])
        self.assertEqual(self.provider.submissions, [])

    def test_experiment_lock_blocks_concurrent_writers_and_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "test interruption"):
            with cli.exclusive_experiment(self.output):
                with self.assertRaisesRegex(ValueError, "already being updated"):
                    cli.run(self.args)
                raise RuntimeError("test interruption")
        self.key.assert_not_called()
        self.factory.assert_not_called()
        with cli.exclusive_experiment(self.output):
            pass
        self.assertFalse(self.state_path.exists())


if __name__ == "__main__":
    unittest.main()
