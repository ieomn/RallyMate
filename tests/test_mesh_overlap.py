from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_service.database import JobDatabase
from rallymate_service.mesh_overlap import PostprocessMesh
from rallymate_service.mesh_render_worker import MeshRenderCoordinator, MeshRenderDeferred, RenderLock
from rallymate_vision.mesh_render_task import TASK_NAME, STATE_NAME, create_mesh_render_task
from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
from rallymate_vision.pose.metadata import sha256_file


class MeshOverlapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runs = self.root / "runs"
        self.output = self.runs / "job-1"
        self.output.mkdir(parents=True)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.env = patch.dict(os.environ, {"RALLYMATE_SAM3D_RUNTIME": str(self.runtime),
            "RALLYMATE_POSTPROCESS_MESH_OVERLAP": "1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.database = JobDatabase(self.root / "jobs.sqlite3")
        self.database.initialize()
        (self.output / "sam3d-mesh-frames.jsonl").write_text('{"complete":true}\n')
        self.descriptor = create_mesh_render_task(job_id="job-1", source=self.source,
            output_dir=self.output, runtime_dir=self.runtime, first_frame=0, last_frame=29,
            stride=1, source_sha256=sha256_file(self.source), model={"backend": "sam3d"})
        self.summary = {"job_id": "job-1", "artifacts": {"sam3d_mesh": self.descriptor}, "scores": [88.55]}
        self.database.create_job("job-1", self.source.name, self.source, self.root / "request.json", self.output)
        self.settings = SimpleNamespace(runs_dir=self.runs)

    def claim(self):
        self.database.claim_next("test", 60, 1)

    def renderer(self, **kwargs):
        kwargs["check_cancelled"]()
        return {"status": "ready", "source_video_sha256": kwargs["source_sha256"],
                "ledger_sha256": self.descriptor["ledger_sha256"]}

    def start(self, renderer=None, **kwargs):
        coordinator = MeshRenderCoordinator(self.settings, self.database, renderer=renderer or self.renderer)
        return PostprocessMesh(coordinator, "job-1", self.descriptor,
            worker_id="test", attempts=1, **kwargs)

    def state(self):
        return json.loads((self.output / STATE_NAME).read_text())

    def test_runs_before_score_publication_and_survives_same_attempt_success(self):
        self.claim()
        entered, release = Event(), Event()
        def render(**kwargs):
            self.assertEqual(self.database.execution_state("job-1")["status"], "running")
            entered.set()
            self.assertTrue(release.wait(5))
            return self.renderer(**kwargs)
        work = self.start(render)
        self.addCleanup(work.finish, cancel=True)
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.state()["reason"], "overlap_cpu_postprocessing")
        self.database.mark_succeeded("job-1", self.summary, worker_id="test", attempts=1)
        release.set()
        work.finish()
        self.assertEqual(self.state()["status"], "ready")
        self.assertEqual(self.database.get_job("job-1")["summary"], self.summary)
        self.assertFalse(work.thread.is_alive())

    def test_queued_or_other_attempt_cannot_dispatch(self):
        with self.assertRaises(MeshRenderDeferred):
            self.start()
        self.database.claim_next("other", 60, 1)
        with self.assertRaises(MeshRenderDeferred):
            self.start()

    def test_changed_ledger_cannot_dispatch(self):
        self.claim()
        (self.output / "sam3d-mesh-frames.jsonl").write_text("changed")
        with self.assertRaisesRegex(ValueError, "ledger changed"):
            self.start()

    def test_cancel_joins_renderer_before_return(self):
        self.claim()
        entered, stopped = Event(), Event()
        def render(**kwargs):
            entered.set()
            try:
                while True:
                    kwargs["check_cancelled"]()
                    Event().wait(.005)
            finally:
                stopped.set()
        work = self.start(render)
        self.assertTrue(entered.wait(5))
        work.finish(cancel=True)
        self.assertTrue(stopped.is_set())
        self.assertFalse(work.thread.is_alive())
        self.assertEqual(self.state()["status"], "pending")

    def test_cleanup_failure_propagates_to_gpu_owner(self):
        self.claim()
        def fail(**kwargs):
            raise ProcessTreeCleanupError("owned tree did not exit")
        work = self.start(fail)
        with self.assertRaisesRegex(ProcessTreeCleanupError, "owned tree"):
            work.finish()
        self.assertEqual(self.state()["reason"], "renderer_tree_cleanup_failed")

    def test_render_error_preserves_scoring_and_does_not_escape(self):
        self.claim()
        def fail(**kwargs):
            raise ValueError("bad media")
        work = self.start(fail)
        work.finish()
        self.assertEqual(self.state()["status"], "failed")
        self.assertEqual(self.database.execution_state("job-1")["status"], "running")

    def test_foreign_running_job_stops_render_but_queued_job_does_not(self):
        self.claim()
        self.database.create_job("job-2", self.source.name, self.source, self.root / "other.json", self.runs / "job-2")
        work = self.start()
        work.finish()
        self.assertEqual(self.state()["status"], "ready")
        self.database.claim_next("other", 60, 1)
        work = self.start()
        work.finish()
        self.assertEqual(self.state()["status"], "pending")

    def test_worker_publishes_scores_then_joins_before_next_job(self):
        from rallymate_service import worker
        entered, release = Event(), Event()
        def render(**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.renderer(**kwargs)
        coordinator = MeshRenderCoordinator(self.settings, self.database, renderer=render)
        settings = SimpleNamespace(validate_license=lambda: None, detect_model=self.source,
            pose_model=self.source, pose_config=None, lease_seconds=60, max_attempts=1)
        request = SimpleNamespace(upstream_metadata={})
        def runner(request, mesh_ready_callback=None):
            mesh_ready_callback(self.descriptor)
            self.assertTrue(entered.wait(5))
            return self.summary
        results = []
        with patch.object(worker, "load_request", return_value=request):
            parent = Thread(target=lambda: results.append(worker.process_one(
                settings, self.database, "test", runner, mesh_coordinator=coordinator)))
            parent.start()
            self.assertTrue(entered.wait(5))
            for _ in range(200):
                if self.database.execution_state("job-1")["status"] == "succeeded":
                    break
                Event().wait(.005)
            self.assertEqual(self.database.execution_state("job-1")["status"], "succeeded")
            self.assertTrue(parent.is_alive())
            release.set()
            parent.join(5)
        self.assertFalse(parent.is_alive())
        self.assertEqual(results, [True])
        self.assertEqual(self.state()["status"], "ready")

    def test_worker_failure_stops_renderer_before_marking_failed(self):
        from rallymate_service import worker
        entered, stopped = Event(), Event()
        def render(**kwargs):
            entered.set()
            try:
                while True:
                    kwargs["check_cancelled"]()
                    Event().wait(.005)
            finally:
                stopped.set()
        coordinator = MeshRenderCoordinator(self.settings, self.database, renderer=render)
        settings = SimpleNamespace(validate_license=lambda: None, detect_model=self.source,
            pose_model=self.source, pose_config=None, lease_seconds=60, max_attempts=1)
        def runner(request, mesh_ready_callback=None):
            mesh_ready_callback(self.descriptor)
            self.assertTrue(entered.wait(5))
            raise ValueError("scoring failed")
        mark_failed = self.database.mark_failed
        def checked_mark_failed(*args, **kwargs):
            self.assertTrue(stopped.is_set())
            return mark_failed(*args, **kwargs)
        with patch.object(worker, "load_request", return_value=SimpleNamespace(upstream_metadata={})), \
                patch.object(self.database, "mark_failed", side_effect=checked_mark_failed):
            self.assertTrue(worker.process_one(settings, self.database, "test", runner,
                mesh_coordinator=coordinator))
        self.assertEqual(self.database.execution_state("job-1")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
