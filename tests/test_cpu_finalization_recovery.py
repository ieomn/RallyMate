from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import time
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rallymate_vision import pipeline
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ProcessingConfig
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.validation import OutputValidationError


class CpuFinalizationRecoveryTests(unittest.TestCase):
    """CPU orchestration tests; artifact semantics are covered by the run validator.

    The successful fixture uses the real readiness and HTML exporters, but mocks
    the full validator because its synthetic ledger is not a scored video run.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "failed"
        self.source.mkdir()
        self.output = self.root / "recovered"
        self.request = PipelineRequest(
            "cpu-recovery-job", self.root / "video.mp4", self.output,
            ModelConfig(self.root / "unused-detect.pt", self.root / "unused-pose.pt", device="cpu"),
        )
        self.request.video_path.write_bytes(b"synthetic input; never decoded")
        self.authority = pipeline._resolve_scoring_feasibility_registry_authority(self.request)
        provenance = {
            "pipeline_job_id": "original-failed-job",
            "feasibility_registry_path": str(self.authority.path),
            "feasibility_registry_sha256": self.authority.file_sha256,
            "registry_lifecycle": {
                "authority_version": self.authority.authority_version,
                "role": self.authority.role,
                "kind": self.authority.kind,
                "lifecycle": self.authority.lifecycle,
                "embedded_version": self.authority.embedded_version,
                "manifest_sha256": self.authority.manifest_sha256,
                "artifact_sha256": self.authority.file_sha256,
            },
        }
        loop = {"provenance": provenance, "video_id": "original-failed-job"}
        self.summary = {
            "schema_version": "1.0.0", "job_id": "original-failed-job", "status": "completed",
            "input": {"video_path": str(self.request.video_path), "video": {
                "width": 1280, "height": 720, "fps": 30.0, "frame_count": 1, "duration_ms": 34,
            }},
            "processing": {"processed_frames": 1, "elapsed_seconds": 512.5,
                           "effective_processed_fps": 5.7,
                           "stage_seconds": {"pose_seconds": 302.0}},
            "minimum_scoring_loop": loop,
            "models": {"pose_format": "coco17"},
            "artifacts": {"frames_jsonl": str(self.source / "frames.jsonl")},
        }
        frame = {"schema_version": "1.1.0", "job_id": "original-failed-job",
                 "frame": {"index": 0, "processed_index": 0, "timestamp_ms": 0,
                           "width": 1280, "height": 720, "source_pts": 0},
                 "poses": [], "detections": [], "court": {"status": "disabled"}}
        (self.source / "frames.jsonl").write_text(json.dumps(frame) + "\n", encoding="utf-8")
        for name in ("events.jsonl", "features.jsonl"):
            (self.source / name).write_text(json.dumps({"video_id": "original-failed-job"}) + "\n", encoding="utf-8")
        (self.source / "scoring-loop-summary.json").write_text(json.dumps(loop), encoding="utf-8")
        (self.source / "summary.json").write_text(json.dumps(self.summary), encoding="utf-8")
        self.original_hashes = {path.name: sha256_file(path) for path in self.source.iterdir()}
        shutil.copytree(self.source, self.output)
        self.metadata = {
            "source_failed_job_id": "original-failed-job", "source_output_dir": str(self.source),
            "source_artifact_sha256": dict(self.original_hashes),
            "operator_attempt_id": "synthetic-cpu-attempt",
        }

    def call_recovery(self, **kwargs):
        return pipeline.finalize_pipeline_artifacts(
            self.request, self.summary, recovery_metadata=self.metadata, **kwargs,
        )

    def assert_source_unchanged(self):
        self.assertEqual({path.name: sha256_file(path) for path in self.source.iterdir()}, self.original_hashes)

    def test_recovery_exports_real_reports_preserving_raw_identity_and_inference_time(self):
        original_summary = copy.deepcopy(self.summary)
        phases = []
        seen = []

        def validate(directory):
            current = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            seen.append(current)
            self.assertEqual(current["job_id"], self.request.job_id)
            self.assertEqual(current["minimum_scoring_loop"]["provenance"]["pipeline_job_id"], "original-failed-job")
            return {"status": "passed", "validated_frames": 1}

        with (patch.object(pipeline, "validate_run_artifacts", side_effect=validate) as validator,
              patch.object(pipeline, "Yolo26Perception", side_effect=AssertionError("no models")),
              patch.object(pipeline, "run_pipeline", side_effect=AssertionError("no inference"))):
            result = self.call_recovery(report_postprocessing=phases.append)
        self.assertEqual(validator.call_count, 2)
        self.assertEqual(phases, ["scoring_readiness"])
        self.assertEqual(result["processing"], original_summary["processing"])
        self.assertEqual(result["input"], original_summary["input"])
        self.assertEqual(self.summary, original_summary)
        self.assertTrue(result["reused_inference"])
        self.assertEqual(result["source_failed_job_id"], "original-failed-job")
        self.assertEqual(result["recovery"]["status"], "completed")
        self.assertGreaterEqual(result["recovery"]["cpu_finalization_elapsed_seconds"], 0)
        self.assertIn("final_artifact_validation_seconds", result["recovery"]["cpu_stage_seconds"])
        self.assertEqual(result["artifacts"]["frames_jsonl"], "frames.jsonl")
        self.assertEqual(seen[1]["recovery"]["status"], "running")
        self.assertEqual(json.loads((self.output / "summary.json").read_text(encoding="utf-8")), result)
        readiness = json.loads((self.output / "scoring-readiness.json").read_text(encoding="utf-8"))
        self.assertEqual(readiness["job_id"], self.request.job_id)
        self.assertIn(self.request.job_id, (self.output / "analysis-report.html").read_text(encoding="utf-8"))
        for name in self.original_hashes.keys() - {"summary.json"}:
            self.assertEqual(sha256_file(self.output / name), self.original_hashes[name])
        self.assert_source_unchanged()

    def test_normal_finalization_uses_original_clock_and_same_exporters(self):
        request = replace(self.request, job_id=self.summary["job_id"])
        timings = Counter({"pose_seconds": 302.0})
        with patch.object(pipeline, "validate_run_artifacts", return_value={"status": "passed"}) as validator:
            result = pipeline.finalize_pipeline_artifacts(
                request, self.summary, registry_authority=self.authority,
                started_at=time.perf_counter() - 513, timings=timings,
            )
        self.assertEqual(validator.call_count, 1)
        self.assertGreaterEqual(result["processing"]["elapsed_seconds"], 513)
        self.assertEqual(result["processing"]["stage_seconds"]["pose_seconds"], 302.0)
        self.assertNotIn("recovery", result)
        self.assertTrue((self.output / "analysis-report.html").is_file())
        self.assert_source_unchanged()

    def test_normal_pipeline_reaches_shared_tail_with_real_cpu_artifacts(self):
        import numpy as np

        request = replace(self.request, job_id="normal-pipeline", output_dir=self.root / "normal-output",
                          processing=ProcessingConfig(write_annotated_video=False, max_frames=1))
        perception, capture = Mock(), Mock()
        perception.detect_model_path = request.models.detect
        perception.pose_model_path = request.models.pose
        perception.pose_metadata.return_value = {
            "backend": "yolo", "runtime": "pytorch", "profile": "realtime",
            "native_keypoint_format": "coco17",
        }
        perception.detection_metadata.return_value = {}
        perception.detect.return_value = []
        perception.estimate_poses.return_value = []
        perception.last_pose_association_diagnostics = None
        capture.get.return_value = 0.0
        capture.read.return_value = (True, np.zeros((120, 160, 3), dtype=np.uint8))
        metadata = SimpleNamespace(fps=30.0, frame_count=1, width=160, height=120, duration_ms=34)
        with (patch.object(pipeline, "probe_video", return_value=metadata),
              patch.object(pipeline.cv2, "VideoCapture", return_value=capture),
              patch.object(pipeline, "Yolo26Perception", side_effect=AssertionError("no model loading")),
              patch.dict(sys.modules, {"torch": None, "ultralytics": None}),
              patch.object(pipeline, "finalize_pipeline_artifacts", wraps=pipeline.finalize_pipeline_artifacts) as finalize):
            result = pipeline.run_pipeline(request, perception=perception)
        finalize.assert_called_once()
        self.assertEqual(result["job_id"], request.job_id)
        self.assertEqual(result["artifacts"]["summary_json"], "summary.json")
        self.assertEqual(result["validation"]["validated_frames"], 1)
        self.assertEqual(result["validation"]["scoring_artifacts_status"], "passed")
        self.assertTrue((request.output_dir / "analysis-report.html").is_file())
        self.assertGreaterEqual(capture.release.call_count, 1)
        self.assert_source_unchanged()

    def test_validation_failure_never_runs_remaining_exports(self):
        with (patch.object(pipeline, "validate_run_artifacts", side_effect=OutputValidationError("bad feature")),
              patch.object(pipeline, "analyze_scoring_readiness") as readiness,
              patch.object(pipeline, "write_analysis_report") as report):
            with self.assertRaisesRegex(OutputValidationError, "bad feature"):
                self.call_recovery()
        readiness.assert_not_called()
        report.assert_not_called()
        self.assert_source_unchanged()

    def test_final_validation_failure_does_not_return_completed_recovery(self):
        with patch.object(pipeline, "validate_run_artifacts", side_effect=[
            {"status": "passed"}, OutputValidationError("final output invalid"),
        ]):
            with self.assertRaisesRegex(OutputValidationError, "final output invalid"):
                self.call_recovery()
        saved = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["recovery"]["status"], "running")
        self.assert_source_unchanged()

    def test_source_hash_drift_is_rejected_before_any_write(self):
        (self.output / "frames.jsonl").write_text("tampered", encoding="utf-8")
        with patch.object(pipeline, "validate_run_artifacts") as validator:
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.call_recovery()
        validator.assert_not_called()
        self.assertEqual(sha256_file(self.output / "summary.json"), self.original_hashes["summary.json"])
        self.assert_source_unchanged()

    def test_original_directory_and_hardlinked_summary_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "original failed output"):
            pipeline.finalize_pipeline_artifacts(
                replace(self.request, output_dir=self.source), self.summary,
                recovery_metadata=self.metadata,
            )
        (self.output / "summary.json").unlink()
        (self.output / "summary.json").hardlink_to(self.source / "summary.json")
        with self.assertRaisesRegex(ValueError, "aliases the original"):
            self.call_recovery()
        self.assert_source_unchanged()

    def test_registry_binding_mismatch_is_rejected_before_summary_rewrite(self):
        authority = replace(self.authority, authority_version="different-authority")
        with self.assertRaisesRegex(ValueError, "registry/source identity"):
            self.call_recovery(registry_authority=authority)
        self.assertEqual(sha256_file(self.output / "summary.json"), self.original_hashes["summary.json"])
        self.assert_source_unchanged()

    def test_registry_change_during_export_is_rejected(self):
        manifest = self.root / "manifest.json"
        manifest.write_bytes(self.authority.manifest_path.read_bytes())
        authority = replace(self.authority, manifest_path=manifest)
        original_export = pipeline.write_analysis_report

        def export_then_change(*args):
            original_export(*args)
            manifest.write_text("changed", encoding="utf-8")

        with (patch.object(pipeline, "validate_run_artifacts", return_value={"status": "passed"}),
              patch.object(pipeline, "write_analysis_report", side_effect=export_then_change)):
            with self.assertRaisesRegex(RuntimeError, "manifest changed"):
                self.call_recovery(registry_authority=authority)
        self.assert_source_unchanged()

    def test_raw_mutation_during_export_is_rejected_before_completion(self):
        original_export = pipeline.write_analysis_report

        def export_then_change(*args):
            original_export(*args)
            (self.output / "features.jsonl").write_text("changed", encoding="utf-8")

        with (patch.object(pipeline, "validate_run_artifacts", return_value={"status": "passed"}),
              patch.object(pipeline, "write_analysis_report", side_effect=export_then_change)):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch: features.jsonl"):
                self.call_recovery()
        saved = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["recovery"]["status"], "running")
        self.assert_source_unchanged()

    def test_cancellation_prevents_writes_and_model_allocation(self):
        callback = Mock(side_effect=RuntimeError("lease lost"))
        with patch.object(pipeline, "Yolo26Perception") as loader:
            with self.assertRaisesRegex(RuntimeError, "lease lost"):
                self.call_recovery(cancellation_callback=callback)
        loader.assert_not_called()
        self.assertEqual(sha256_file(self.output / "summary.json"), self.original_hashes["summary.json"])
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
