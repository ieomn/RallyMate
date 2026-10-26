from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import recover_failed_finalization as recovery
from rallymate_service.database import JobDatabase


class CpuRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "service"
        self.data.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.database = JobDatabase(self.data / "jobs.sqlite3")
        self.database.initialize()
        self.source_id = "original-failed-inference"
        self.source = self.data / "runs" / self.source_id
        self.source.mkdir(parents=True)
        self.requests = self.data / "requests"
        self.requests.mkdir()
        self.video = self.data / "video.mp4"
        self.video.write_bytes(b"original video")
        self.detect = self.data / "detect.pt"
        self.detect.write_bytes(b"existing detection checkpoint")
        self.pose = self.data / "pose.ckpt"
        self.pose.write_bytes(b"existing real SAM checkpoint hash stand-in, never loaded")
        self.write(self.source / "frames.jsonl", {"job_id": self.source_id,
            "frame": {"index": 0, "video_sha256": recovery.sha256(self.video)}})
        self.write(self.source / "events.jsonl", {"video_id": self.source_id})
        self.write(self.source / "features.jsonl", {"video_id": self.source_id, "confidence": None})
        self.write(self.source / "scoring-loop-summary.json", {"provenance": {"pipeline_job_id": self.source_id}})
        self.summary = {"schema_version": "1.0.0", "job_id": self.source_id,
            "input": {"video_path": str(self.video), "upstream_metadata": {"original": self.source_id}},
            "processing": {"processed_frames": 1, "elapsed_seconds": 435.2, "effective_processed_fps": 6.7,
                "stage_seconds": {"inference_seconds": 420}},
            "models": {"pose_backend": {"model_sha256": recovery.sha256(self.pose)}},
            "artifacts": {"frames_jsonl": "frames.jsonl"}}
        self.write(self.source / "summary.json", self.summary)
        self.write(self.source / "scoring-readiness.json", {"old": True})
        (self.source / "analysis-report.html").write_text("old report", encoding="utf-8")
        self.request_path = self.requests / f"{self.source_id}.json"
        self.write(self.request_path, {"job_id": self.source_id,
            "source": {"video_path": str(self.video)}, "output": {"directory": str(self.source)},
            "models": {"detect": str(self.detect), "pose": str(self.pose), "pose_backend": "sam3d",
                "pose_native_keypoint_format": "mhr70"}, "upstream_metadata": {"original": self.source_id}})
        self.database.create_job(self.source_id, "original.mp4", self.video, self.request_path, self.source)
        owner = self.database.claim_next("inference-worker", 60, 3)
        self.database.mark_failed(self.source_id, "OutputValidationError: features[0].confidence must be finite and in 0..1",
            worker_id="inference-worker", attempts=owner["attempts"])
        self.original_job = self.database.get_job(self.source_id)
        self.original_hashes = recovery._source_files(self.source)
        self.settings = SimpleNamespace(data_root=self.data, database_path=self.database.path,
            requests_dir=self.requests, runs_dir=self.data / "runs", detect_model=self.detect,
            pose_model=self.pose, pose_config=None, lease_seconds=60, max_attempts=3,
            validate_license=lambda: None, resolved_scoring_registry_lifecycle_manifest=self.root / "registry.json")
        entries = []
        for name in recovery.REQUIRED_RUNTIME_FILES:
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"verified deployed fixture {name}", encoding="utf-8")
            entries.append({"path": name, "sha256": recovery.sha256(path)})
        self.manifests = {}
        for label in ("source", "recovery", "frontend"):
            path = self.root / f"{label}-manifest.json"
            self.write(path, {"files": entries})
            self.manifests[label] = path
        self.options = dict(failed_job_id=self.source_id, worker_paused=True, operator_note="operator confirmed resident worker paused",
            expected_video_sha256=recovery.sha256(self.video), expected_frames_sha256=self.original_hashes["frames.jsonl"],
            expected_detect_model_sha256=recovery.sha256(self.detect), expected_pose_model_sha256=recovery.sha256(self.pose),
            source_manifest=self.manifests["source"], source_manifest_sha256=recovery.sha256(self.manifests["source"]),
            recovery_source_manifest=self.manifests["recovery"], recovery_source_manifest_sha256=recovery.sha256(self.manifests["recovery"]),
            frontend_build_manifest=self.manifests["frontend"], frontend_build_manifest_sha256=recovery.sha256(self.manifests["frontend"]),
            workspace_root=self.workspace)
        self.gpu_guard = patch("rallymate_service.worker.PersistentVisionRunner", side_effect=AssertionError("GPU runner forbidden"))
        self.gpu_guard.start()
        self.addCleanup(self.gpu_guard.stop)
        self.pipeline_guard = patch("rallymate_service.worker.run_pipeline", side_effect=AssertionError("full pipeline forbidden"))
        self.pipeline_guard.start()
        self.addCleanup(self.pipeline_guard.stop)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def fake_finalize(self, request, original_summary, **kwargs):
        self.assertEqual(original_summary, self.summary)
        kwargs["cancellation_callback"]()
        result = copy.deepcopy(original_summary)
        result.update(job_id=request.job_id, reused_inference=True, source_failed_job_id=self.source_id)
        result["recovery"] = {**kwargs["recovery_metadata"], "status": "completed", "cpu_finalization_elapsed_seconds": .001}
        self.write(request.output_dir / "summary.json", result)
        self.write(request.output_dir / "scoring-readiness.json", {"recovered": True})
        (request.output_dir / "analysis-report.html").write_text("new report", encoding="utf-8")
        return result

    def run_recovery(self, **overrides):
        return recovery.recover_failed_finalization(self.settings, **{**self.options, **overrides})

    def assert_source_unchanged(self):
        self.assertEqual(self.database.get_job(self.source_id), self.original_job)
        self.assertEqual(recovery._source_files(self.source), self.original_hashes)
        self.assertFalse((self.data / "finalization-recovery.lock").exists())

    def recovery_jobs(self):
        return [j for j in self.database.list_jobs(100) if j["id"] != self.source_id]

    def test_success_preserves_inference_identity_timing_and_copies_reports(self):
        with patch.object(recovery, "_finalize_artifacts", side_effect=self.fake_finalize) as finalize:
            receipt = self.run_recovery()
        self.assertEqual(finalize.call_count, 1)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["database_terminal_status"], "succeeded")
        self.assertTrue(receipt["reused_inference"])
        new = self.database.get_job(receipt["recovery_job_id"])
        self.assertEqual(new["attempts"], 1)
        self.assertEqual(new["summary"]["input"], self.summary["input"])
        self.assertEqual(new["summary"]["processing"], self.summary["processing"])
        request = json.loads(Path(new["request_path"]).read_text())
        self.assertEqual(request["source"]["video_path"], str(self.video))
        self.assertEqual(request["upstream_metadata"]["original"], self.source_id)
        out = Path(receipt["output_dir"])
        for name in ("frames.jsonl", "events.jsonl", "features.jsonl", "scoring-loop-summary.json"):
            self.assertEqual(recovery.sha256(out / name), self.original_hashes[name])
        for name in ("summary.json", "scoring-readiness.json", "analysis-report.html"):
            self.assertFalse((out / name).samefile(self.source / name))
        audit = out / "_recovery"
        self.assertEqual(json.loads((audit / "source-failed-job.json").read_text()), self.original_job)
        self.assertEqual(json.loads((audit / "receipt.json").read_text()), receipt)
        metadata = json.loads((audit / "provenance.json").read_text())
        self.assertEqual(metadata["source_failed_job_snapshot_sha256"], recovery.sha256(audit / "source-failed-job.json"))
        self.assertEqual(receipt["frames_bytes"], (self.source / "frames.jsonl").stat().st_size)
        self.assertEqual(receipt["model_sha256"]["pose"], recovery.sha256(self.pose))
        self.assert_source_unchanged()

    def test_unrelated_queued_job_is_not_claimed(self):
        self.database.create_job("older-queued", "other.mp4", self.video, self.request_path, self.root / "unrelated")
        queued = self.database.get_job("older-queued")
        with patch.object(recovery, "_finalize_artifacts", side_effect=self.fake_finalize):
            self.assertEqual(self.run_recovery()["status"], "succeeded")
        self.assertEqual(self.database.get_job("older-queued"), queued)

    def test_worker_requires_explicit_cpu_runner_capability_for_cli_job(self):
        with (patch.object(recovery.CpuFinalizationRunner, "cpu_finalization_only", False),
              patch.object(recovery, "_finalize_artifacts") as finalize):
            receipt = self.run_recovery()
        self.assertEqual(receipt["status"], "failed")
        finalize.assert_not_called()
        self.assert_source_unchanged()

    def test_persisted_cli_recovery_marker_prevents_ordinary_worker_execution(self):
        from rallymate_service.worker import process_one
        def no_claim(settings, database, worker_id, runner, *, expected_job_id=None):
            return False
        # Simulate process death after durable create: finally cannot run after
        # SIGKILL. The real production cleanup remains unchanged and is tested
        # separately; leave only this temporary database row queued for restart.
        with (patch("rallymate_service.worker.process_one", no_claim),
              patch.object(recovery, "_ensure_terminal", side_effect=lambda db, identifier, worker: db.get_job(identifier))):
            receipt = self.run_recovery()
        identifier = receipt["recovery_job_id"]
        self.assertEqual(self.database.get_job(identifier)["status"], "queued")
        with patch.object(recovery, "_finalize_artifacts") as cpu_tail:
            ordinary_calls = []
            def ordinary_runner(request):
                ordinary_calls.append(request.job_id)
                raise AssertionError("ordinary inference must never receive recovery jobs")
            self.assertTrue(process_one(self.settings, self.database, "restarted-resident", runner=ordinary_runner))
        self.assertEqual(self.database.get_job(identifier)["status"], "failed")
        self.assertEqual(ordinary_calls, [])
        cpu_tail.assert_not_called()
        self.assert_source_unchanged()

    def test_cli_uses_real_shared_tail_and_real_cpu_report_exporters(self):
        from rallymate_vision import pipeline
        authority = pipeline._resolve_scoring_feasibility_registry_authority(recovery.load_request(self.request_path))
        self.settings.resolved_scoring_registry_lifecycle_manifest = authority.manifest_path
        loop = {"provenance": {"pipeline_job_id": self.source_id,
            "feasibility_registry_sha256": authority.file_sha256,
            "registry_lifecycle": {"authority_version": authority.authority_version,
                "role": authority.role, "kind": authority.kind, "lifecycle": authority.lifecycle,
                "embedded_version": authority.embedded_version, "manifest_sha256": authority.manifest_sha256,
                "artifact_sha256": authority.file_sha256}}}
        self.summary["minimum_scoring_loop"] = loop
        self.summary["input"]["video"] = {"width": 1280, "height": 720, "fps": 30.0, "frame_count": 1, "duration_ms": 34}
        self.summary["models"]["pose_format"] = "mhr70"
        self.write(self.source / "summary.json", self.summary)
        self.write(self.source / "scoring-loop-summary.json", loop)
        self.write(self.source / "frames.jsonl", {"schema_version": "1.1.0", "job_id": self.source_id,
            "frame": {"index": 0, "processed_index": 0, "timestamp_ms": 0, "width": 1280, "height": 720,
                "video_sha256": recovery.sha256(self.video)}, "poses": [], "detections": [], "court": {"status": "disabled"}})
        self.original_hashes = recovery._source_files(self.source)
        self.options["expected_frames_sha256"] = self.original_hashes["frames.jsonl"]
        # Synthetic records cannot pass production semantic validation. Mock only
        # that validator; execute the real shared tail, readiness and HTML export.
        with (patch.object(pipeline, "validate_run_artifacts", return_value={"status": "passed", "validated_frames": 1}) as validator,
              patch.object(pipeline, "Yolo26Perception", side_effect=AssertionError("no model construction")),
              patch.object(pipeline, "run_pipeline", side_effect=AssertionError("no inference")),
              patch.object(pipeline, "finalize_pipeline_artifacts", wraps=pipeline.finalize_pipeline_artifacts) as finalize):
            receipt = self.run_recovery()
        self.assertEqual(receipt["status"], "succeeded")
        finalize.assert_called_once()
        self.assertEqual(validator.call_count, 2)
        out = Path(receipt["output_dir"])
        summary = self.database.get_job(receipt["recovery_job_id"])["summary"]
        self.assertEqual(summary["recovery"]["status"], "completed")
        self.assertEqual(summary["recovery"]["mode"], "cpu_finalization")
        self.assertEqual(summary["processing"], self.summary["processing"])
        self.assertEqual(json.loads((out / "scoring-readiness.json").read_text(encoding="utf-8"))["job_id"], receipt["recovery_job_id"])
        self.assertIn(receipt["recovery_job_id"], (out / "analysis-report.html").read_text(encoding="utf-8"))
        self.assert_source_unchanged()

    def test_runtime_changed_during_finalization_cannot_mark_success(self):
        def changed_runtime(request, summary, **kwargs):
            result = self.fake_finalize(request, summary, **kwargs)
            (self.workspace / recovery.REQUIRED_RUNTIME_FILES[1]).write_text("changed during CPU tail")
            return result
        with patch.object(recovery, "_finalize_artifacts", side_effect=changed_runtime):
            receipt = self.run_recovery()
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("SHA256 mismatch", receipt["error"])
        self.assert_source_unchanged()

    def test_validator_failure_becomes_new_failed_job(self):
        with patch.object(recovery, "_finalize_artifacts", side_effect=ValueError("CPU validator rejected evidence")):
            receipt = self.run_recovery()
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("CPU validator rejected evidence", receipt["error"])
        self.assert_source_unchanged()

    def test_worker_never_claims_cancels_only_recovery_queue_entry(self):
        def no_claim(settings, database, worker_id, runner, *, expected_job_id=None):
            return False
        with patch("rallymate_service.worker.process_one", no_claim):
            receipt = self.run_recovery()
        self.assertEqual(receipt["status"], "cancelled")
        self.assertEqual(self.database.get_job(receipt["recovery_job_id"])["attempts"], 0)
        self.assert_source_unchanged()

    def test_worker_setup_exception_cancels_recovery_before_claim(self):
        def setup_failure(settings, database, worker_id, runner, *, expected_job_id=None):
            raise RuntimeError("setup failed")
        with patch("rallymate_service.worker.process_one", setup_failure), self.assertRaisesRegex(RuntimeError, "setup failed"):
            self.run_recovery()
        self.assertEqual(self.recovery_jobs()[0]["status"], "cancelled")
        self.assert_source_unchanged()

    def test_keyboard_interrupt_after_claim_finishes_owned_attempt(self):
        with patch.object(recovery, "_finalize_artifacts", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_recovery()
        self.assertEqual(self.recovery_jobs()[0]["status"], "failed")
        self.assert_source_unchanged()

    def test_running_cancellation_propagates_through_shared_tail_callback(self):
        def cancelled(request, original_summary, **kwargs):
            self.database.request_cancel(request.job_id)
            kwargs["cancellation_callback"]()
            self.fail("cancellation must stop finalization")
        with patch.object(recovery, "_finalize_artifacts", side_effect=cancelled):
            self.assertEqual(self.run_recovery()["status"], "cancelled")
        self.assert_source_unchanged()

    def test_explicit_worker_pause_is_required_before_queue_mutation(self):
        with self.assertRaisesRegex(ValueError, "worker-paused"):
            self.run_recovery(worker_paused=False)
        self.assertEqual(self.recovery_jobs(), [])

    def test_running_job_blocks_recovery_without_lease_cleanup(self):
        self.database.create_job("running", "other.mp4", self.video, self.request_path, self.root / "unrelated")
        self.database.claim_next("resident", 60, 3)
        before = self.database.get_job("running")
        with self.assertRaisesRegex(RuntimeError, "a job is running"):
            self.run_recovery()
        self.assertEqual(self.database.get_job("running"), before)
        self.assertEqual(len(self.recovery_jobs()), 1)

    def test_manifest_mismatch_prevents_queue_creation(self):
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.run_recovery(recovery_source_manifest_sha256="0" * 64)
        self.assertEqual(self.recovery_jobs(), [])

    def test_current_runtime_content_must_match_recovery_manifest(self):
        (self.workspace / recovery.REQUIRED_RUNTIME_FILES[1]).write_text("changed after freeze")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.run_recovery()
        self.assertEqual(self.recovery_jobs(), [])

    def test_wrong_video_or_frames_or_model_hash_cannot_create_recovery_job(self):
        for name in ("expected_video_sha256", "expected_frames_sha256", "expected_detect_model_sha256", "expected_pose_model_sha256"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_recovery(**{name: "0" * 64})
            self.assertEqual(self.recovery_jobs(), [])

    def test_frame_identity_is_not_rewritten_to_make_it_match(self):
        self.write(self.source / "frames.jsonl", {"job_id": "different-source", "frame": {"video_sha256": recovery.sha256(self.video)}})
        with self.assertRaisesRegex(ValueError, "frame source job/video"):
            self.run_recovery(expected_frames_sha256=recovery.sha256(self.source / "frames.jsonl"))
        self.assertEqual(self.recovery_jobs(), [])

    def test_existing_operator_lock_is_not_removed(self):
        lock = self.data / "finalization-recovery.lock"
        lock.write_text("prior operator evidence")
        with self.assertRaises(FileExistsError):
            self.run_recovery()
        self.assertEqual(lock.read_text(), "prior operator evidence")
        self.assertEqual(self.recovery_jobs(), [])


if __name__ == "__main__":
    unittest.main()
