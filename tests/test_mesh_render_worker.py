from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_service.database import JobDatabase
from rallymate_service.mesh_render_worker import MeshRenderCoordinator, MeshRenderDeferred, RenderLock
from rallymate_vision.mesh_render_task import TASK_NAME, STATE_NAME, CANCEL_NAME, create_mesh_render_task
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import LEDGER_NAME, MANIFEST_NAME, render_mesh_subprocess


class MeshRenderWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        env = patch.dict(os.environ, {"RALLYMATE_SAM3D_RUNTIME": str(self.runtime)})
        env.start(); self.addCleanup(env.stop)
        self.database = JobDatabase(self.root / "jobs.sqlite3")
        self.database.initialize()
        self.settings = SimpleNamespace(runs_dir=self.runs)
        self.calls = []

    def job(self, identifier="job-1", *, completed=True):
        output = self.runs / identifier
        output.mkdir()
        source = self.root / f"{identifier}.mp4"
        source.write_bytes(b"source-video")
        (output / LEDGER_NAME).write_text('{"source":"retained geometry"}\n', encoding="utf-8")
        descriptor = create_mesh_render_task(job_id=identifier, source=source, output_dir=output,
            runtime_dir=self.runtime, first_frame=0, last_frame=29, stride=1,
            source_sha256=sha256_file(source), model={"backend": "sam3d", "weights_sha256": "a" * 64})
        summary = {"job_id": identifier, "artifacts": {"sam3d_mesh": descriptor}, "score": 53.5}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        self.database.create_job(identifier, source.name, source, self.root / f"{identifier}.request", output)
        if completed:
            self.database.claim_next("test", 60, 1, expected_job_id=identifier)
            self.database.mark_succeeded(identifier, summary, worker_id="test", attempts=1)
        return output

    def renderer(self, **kwargs):
        self.calls.append(kwargs["output_dir"].name)
        kwargs["check_cancelled"]()
        return {"status": "ready", "source_video_sha256": kwargs["source_sha256"],
                "ledger_sha256": sha256_file(kwargs["output_dir"] / LEDGER_NAME)}

    def coordinator(self, renderer=None):
        return MeshRenderCoordinator(self.settings, self.database, renderer=renderer or self.renderer)

    def state(self, output):
        return json.loads((output / STATE_NAME).read_text(encoding="utf-8"))

    def test_scoring_is_already_succeeded_and_render_only_updates_sidecars(self):
        output = self.job()
        before = (output / "summary.json").read_bytes()
        database_before = self.database.get_job("job-1")["summary"]
        self.assertTrue(self.coordinator().process_one())
        self.assertEqual(self.state(output)["status"], "ready")
        manifest = json.loads((output / MANIFEST_NAME).read_text())
        self.assertEqual(manifest["source_job_id"], "job-1")
        self.assertEqual(manifest["task_sha256"], sha256_file(output / TASK_NAME))
        self.assertEqual((output / "summary.json").read_bytes(), before)
        self.assertEqual(self.database.get_job("job-1")["summary"], database_before)
        self.assertFalse(self.coordinator().process_one())
        self.assertEqual(self.calls, ["job-1"])

    def test_queued_and_running_inference_prevent_render_start(self):
        self.job()
        self.job("job-2", completed=False)
        self.assertFalse(self.coordinator().process_one())
        self.database.claim_next("infer", 60, 1, expected_job_id="job-2")
        self.assertFalse(self.coordinator().process_one())
        self.assertEqual(self.calls, [])

    def test_incoming_inference_preempts_render_and_request_recovers(self):
        output = self.job()
        def render(**kwargs):
            self.job("job-2", completed=False)
            kwargs["check_cancelled"]()
            self.fail("queued inference must preempt the render")
        self.assertTrue(self.coordinator(render).process_one())
        state = self.state(output)
        self.assertEqual((state["status"], state["reason"], state["retryable"]), ("pending", "preempted_for_inference", True))
        self.assertEqual(self.database.get_job("job-1")["status"], "succeeded")
        self.database.request_cancel("job-2")
        self.assertTrue(self.coordinator().process_one())
        self.assertEqual(self.state(output)["status"], "ready")

    def test_shutdown_defers_and_new_coordinator_resumes_without_reinference(self):
        output = self.job()
        stop = Event()
        def render(**kwargs):
            stop.set(); kwargs["check_cancelled"]()
        self.assertTrue(self.coordinator(render).process_one(stop))
        self.assertEqual(self.state(output)["reason"], "service_stopping")
        self.assertEqual(self.state(output)["status"], "pending")
        self.assertFalse(self.coordinator().process_one(stop))
        self.assertTrue(self.coordinator().process_one())
        self.assertEqual(self.state(output)["status"], "ready")

    def test_stale_rendering_state_is_recovered_and_lock_file_is_not_a_latch(self):
        output = self.job()
        (output / STATE_NAME).write_text(json.dumps({"status": "rendering", "task_sha256": sha256_file(output / TASK_NAME)}))
        (self.runs / ".sam3d-mesh-render.lock").write_bytes(b"stale")
        self.assertTrue(self.coordinator().process_one())
        self.assertEqual(self.state(output)["status"], "ready")

    def test_os_lock_excludes_another_renderer_and_releases_on_close(self):
        self.job()
        lock = RenderLock(self.runs / ".sam3d-mesh-render.lock")
        self.assertTrue(lock.acquire())
        try:
            self.assertFalse(self.coordinator().process_one())
        finally:
            lock.close()
        self.assertTrue(self.coordinator().process_one())

    def test_explicit_preview_cancel_stops_renderer_but_keeps_scored_job(self):
        output = self.job()
        coordinator = self.coordinator()
        def render(**kwargs):
            coordinator.cancel("job-1")
            kwargs["check_cancelled"]()
        coordinator.renderer = render
        self.assertTrue(coordinator.process_one())
        self.assertEqual(self.state(output)["status"], "cancelled")
        self.assertTrue((output / CANCEL_NAME).exists())
        self.assertEqual(self.database.get_job("job-1")["status"], "succeeded")
        self.assertFalse(self.coordinator().process_one())

    def test_render_failure_is_optional_and_does_not_busy_retry(self):
        output = self.job()
        def render(**kwargs):
            raise RuntimeError("GPU renderer failure")
        with self.assertLogs("rallymate.mesh", level="ERROR"):
            self.assertTrue(self.coordinator(render).process_one())
        self.assertEqual(self.state(output)["status"], "failed")
        self.assertEqual(self.database.get_job("job-1")["status"], "succeeded")
        self.assertFalse(self.coordinator().process_one())

    def test_mutated_task_source_and_ledger_fail_before_renderer(self):
        for defect in ("task", "source", "ledger"):
            with self.subTest(defect=defect):
                identifier = "job-" + defect
                output = self.job(identifier)
                if defect == "task":
                    request = json.loads((output / TASK_NAME).read_text())
                    request["render"]["output_dir"] = str(self.root / "foreign")
                    (output / TASK_NAME).write_text(json.dumps(request))
                elif defect == "source":
                    (self.root / f"{identifier}.mp4").write_bytes(b"changed-video")
                else:
                    (output / LEDGER_NAME).write_text("changed-ledger")
                with self.assertLogs("rallymate.mesh", level="WARNING"):
                    self.assertFalse(self.coordinator().process_one())
                self.assertEqual(self.state(output)["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_subprocess_is_terminated_before_preemption_returns(self):
        output = self.job()
        class Process:
            returncode = None
            terminated = False
            def poll(self): return self.returncode
            def stop(self): self.terminated = True; self.returncode = -1
            def close(self): pass
        process = Process()
        def preempt(): raise MeshRenderDeferred("preempted_for_inference")
        with patch("rallymate_vision.owned_subprocess.OwnedProcess", return_value=process), self.assertRaises(MeshRenderDeferred):
            render_mesh_subprocess(self.root / "job-1.mp4", output, self.runtime,
                first_frame=0, last_frame=29, stride=1, source_sha256="a" * 64, model={}, check_cancelled=preempt)
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
