from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from rallymate_service import api
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.mesh_preview import MESH_ARTIFACTS, mesh_preview_descriptor, mesh_preview_status


JOB = "66883aeb-e0c3-4065-80fd-f67aa108b208"
BODY = b"official mesh replay fixture bytes"


class MeshPreviewApiTests(unittest.TestCase):
    def create_job(self, root):
        settings = ServiceSettings(data_root=root / "data", database_path=root / "data/jobs.sqlite3",
                                   detect_model=root / "detect.pt", pose_model=root / "pose.pt",
                                   api_key="mesh-preview-fixture-key")
        settings.ensure_directories()
        output = settings.runs_dir / JOB
        output.mkdir()
        for name in MESH_ARTIFACTS:
            (output / name).write_bytes(BODY)
        manifest = {
            "source_video_sha256": "a" * 64, "source_frame_start": 540,
            "source_frame_end": 630, "frame_count": 90,
            "source_timestamp_origin_ms": 18000, "source_relative_origin_ms": 18000,
            "timing_preserved": True,
            "renderer": "official-sam3d-pyrender-persistent-context",
            "files": {name: {"bytes": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest()}
                      for name in MESH_ARTIFACTS - {"sam3d-mesh-manifest.json"}},
        }
        (output / "sam3d-mesh-manifest.json").write_text(json.dumps(manifest), "utf-8")
        summary = {
            "artifacts": {"sam3d_mesh": {"status": "ready"}},
            "minimum_scoring_loop": {"provenance": {"video_sha256": "a" * 64}},
            "action_recognition": {"detector_version": api.DETECTOR_VERSION,
                                   "motion_analysis": {"analysis_version": api.ANALYSIS_VERSION}},
        }
        db = JobDatabase(settings.database_path)
        db.initialize()
        db.create_job(JOB, "clip.mp4", settings.uploads_dir / f"{JOB}.mp4",
                      settings.requests_dir / f"{JOB}.json", output)
        db.claim_next("mesh-test", settings.lease_seconds, settings.max_attempts)
        db.mark_succeeded(JOB, summary)
        return settings, db, output, manifest

    def test_real_range_authentication_and_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, db, _, _ = self.create_job(Path(directory))
            headers = {"Authorization": f"Bearer {settings.api_key}"}
            with TestClient(api.create_app(settings, db)) as client:
                route = f"/v1/jobs/{JOB}/artifacts/sam3d-mesh.mp4"
                self.assertEqual(client.get(route).status_code, 401)
                response = client.get(route, headers={**headers, "Range": "bytes=2-8"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, BODY[2:9])
                self.assertEqual(response.headers["content-range"], f"bytes 2-8/{len(BODY)}")
                self.assertIn("inline", response.headers["content-disposition"])
                self.assertIn("no-store", response.headers["cache-control"])
                public = client.get(f"/v1/jobs/{JOB}", headers=headers).json()
                self.assertEqual(public["sam3d_mesh"]["source_timestamp_origin_ms"], 18000)
                self.assertEqual(public["sam3d_mesh"]["video_url"], route)
                self.assertTrue(MESH_ARTIFACTS.issubset(public["artifact_urls"]))
                state = client.get(f"/v1/jobs/{JOB}/mesh-preview", headers=headers)
                self.assertEqual(state.status_code, 200)
                self.assertEqual(state.json()["status"], "ready")
                self.assertNotIn("summary", state.json())
                self.assertLess(len(state.content), 2048)

    def test_incomplete_export_is_not_published_or_directly_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            settings, db, output, _ = self.create_job(Path(directory))
            (output / "sam3d-mesh-side.mp4").unlink()
            self.assertIsNone(mesh_preview_descriptor(db.get_job(JOB)))
            with TestClient(api.create_app(settings, db)) as client:
                headers = {"Authorization": f"Bearer {settings.api_key}"}
                public = client.get(f"/v1/jobs/{JOB}", headers=headers).json()
                self.assertIsNone(public["sam3d_mesh"])
                self.assertFalse(MESH_ARTIFACTS.intersection(public["artifact_urls"]))
                self.assertEqual(client.get(f"/v1/jobs/{JOB}/artifacts/sam3d-mesh.mp4", headers=headers).status_code, 404)

    def test_source_mismatch_invalid_timing_and_cancel_do_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            _, db, output, manifest = self.create_job(Path(directory))
            job = db.get_job(JOB)
            for patch in [
                {"source_video_sha256": "b" * 64},
                {"source_frame_start": 630},
                {"frame_count": True},
                {"source_timestamp_origin_ms": float("nan")},
                {"timing_preserved": False},
                {"renderer": "synthetic-keypoint-body"},
            ]:
                with self.subTest(patch=patch):
                    (output / "sam3d-mesh-manifest.json").write_text(json.dumps({**manifest, **patch}), "utf-8")
                    self.assertIsNone(mesh_preview_descriptor(job))
            (output / "sam3d-mesh-manifest.json").write_text(json.dumps(manifest), "utf-8")
            self.assertIsNone(mesh_preview_descriptor({**job, "cancel_requested": 1}))
            self.assertIsNone(mesh_preview_descriptor({**job, "status": "running"}))

    def test_background_completion_does_not_require_rewriting_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            _, db, output, manifest = self.create_job(Path(directory))
            job = db.get_job(JOB)
            job["summary"]["artifacts"]["sam3d_mesh"] = {"status": "pending", "task_sha256": "c" * 64}
            manifest.update(source_job_id=JOB, task_sha256="c" * 64)
            (output / "sam3d-mesh-manifest.json").write_text(json.dumps(manifest), "utf-8")
            before = json.dumps(job["summary"], sort_keys=True)
            descriptor = mesh_preview_descriptor(job)
            self.assertIsNotNone(descriptor)
            self.assertEqual(mesh_preview_status(job, descriptor), "ready")
            self.assertEqual(json.dumps(job["summary"], sort_keys=True), before)
            (output / "sam3d-mesh-side.mp4").unlink()
            self.assertEqual(mesh_preview_status(job), "pending")
            (output / "sam3d-mesh-state.json").write_text(json.dumps({
                "status": "failed", "source_job_id": JOB, "task_sha256": "c" * 64}), "utf-8")
            self.assertEqual(mesh_preview_status(job), "failed")


if __name__ == "__main__":
    unittest.main()
