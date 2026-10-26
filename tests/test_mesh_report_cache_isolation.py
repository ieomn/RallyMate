"""A background preview cannot invalidate an in-flight numerical report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Event
import tempfile
import unittest

from fastapi.testclient import TestClient

from rallymate_service.mesh_preview import MESH_ARTIFACTS
from tests import test_report_delivery_independent as delivery


class MeshReportCacheIsolationTests(unittest.TestCase):
    def setup_case(self, root):
        helper = delivery.IndependentReportDeliveryApiTests()
        app, settings, database, job, folder, marker = helper.setup_case(root)
        summary = database.get_job(job)["summary"]
        summary["artifacts"] = {"sam3d_mesh": {"status": "pending", "task_sha256": "c" * 64}}
        summary["minimum_scoring_loop"] = {"provenance": {"video_sha256": "a" * 64}}
        with database.connect() as connection:
            connection.execute("UPDATE jobs SET summary_json=? WHERE id=?",
                               (json.dumps(summary), job))
            connection.commit()
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        url = f"/v1/jobs/{job}/demo-result?report_contract=current&report_transport=model-proof-pool-v1&report_delivery=deferred-v1"
        return helper, app, database, job, folder, marker, url

    @staticmethod
    def publish_mesh(folder, job):
        body = b"actual-media-would-be-written-by-the-renderer"
        for name in MESH_ARTIFACTS - {"sam3d-mesh-manifest.json"}:
            (folder / name).write_bytes(body)
        manifest = {
            "source_job_id": job, "task_sha256": "c" * 64,
            "source_video_sha256": "a" * 64, "source_frame_start": 0,
            "source_frame_end": 90, "frame_count": 90,
            "source_timestamp_origin_ms": 0, "source_relative_origin_ms": 0,
            "timing_preserved": True, "renderer": "official-sam3d-pyrender-persistent-context",
            "files": {name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                      for name in MESH_ARTIFACTS - {"sam3d-mesh-manifest.json"}},
        }
        (folder / "sam3d-mesh-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_publish_during_report_build_does_not_discard_or_rebuild_main_report(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, app, db, job, folder, marker, url = self.setup_case(Path(directory))
            entered, release = Event(), Event()
            calls = []

            def build(summary, records, **kwargs):
                calls.append(1)
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture report builder timed out")
                return {"job_id": job, "training_evaluation": {"score_0_to_100": 68}}

            with helper.lightweight_builders(marker, calls, overrides={"build_user_demo_result": build}), TestClient(app) as client:
                try:
                    self.assertEqual(client.get(url).status_code, 202)
                    self.assertTrue(entered.wait(1))
                    summary_before = db.get_job(job)["summary"]
                    self.publish_mesh(folder, job)
                    self.assertEqual(db.get_job(job)["summary"], summary_before)
                    self.assertEqual(client.get(f"/v1/jobs/{job}/mesh-preview").json()["status"], "ready")
                finally:
                    release.set()
                ready = helper.ready(client, url)
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(calls, [1], "mesh publication must not create another report cache key")
                self.assertEqual(ready.json()["training_evaluation"]["score_0_to_100"], 68)
                self.assertEqual(ready.json()["sam3d_mesh_status"], "pending")
                self.assertIsNone(ready.json()["sam3d_mesh"])
                self.assertFalse(MESH_ARTIFACTS.intersection(ready.json()["artifact_urls"]))
                self.assertEqual(client.get(url).content, ready.content)

    def test_cached_and_fresh_report_bytes_are_stable_across_mesh_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, app, _, job, folder, marker, url = self.setup_case(Path(directory))
            calls = []
            with helper.lightweight_builders(marker, calls), TestClient(app) as client:
                before = helper.ready(client, url)
                self.publish_mesh(folder, job)
                cached = helper.ready(client, url)
                self.assertEqual(cached.content, before.content)
                self.assertEqual(calls, [1])
                fresh = client.get(url.replace("&report_delivery=deferred-v1", ""))
                self.assertEqual(fresh.json(), before.json())
                preview = client.get(f"/v1/jobs/{job}/mesh-preview").json()
                self.assertEqual(preview["status"], "ready")
                self.assertEqual(preview["sam3d_mesh"]["frame_count"], 90)

    def test_background_failure_does_not_invalidate_report_or_block_its_score(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, app, _, job, folder, marker, url = self.setup_case(Path(directory))
            calls = []
            with helper.lightweight_builders(marker, calls), TestClient(app) as client:
                before = helper.ready(client, url)
                (folder / "sam3d-mesh-state.json").write_text(
                    json.dumps({"status": "failed", "source_job_id": job, "task_sha256": "c" * 64}),
                    encoding="utf-8")
                self.assertEqual(client.get(url).content, before.content)
                self.assertEqual(calls, [1])
                self.assertEqual(client.get(f"/v1/jobs/{job}/mesh-preview").json()["status"], "failed")

    def test_frames_and_scores_remain_dependencies_after_mesh_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, app, _, job, folder, marker, url = self.setup_case(Path(directory))
            calls = []
            with helper.lightweight_builders(marker, calls), TestClient(app) as client:
                helper.ready(client, url)
                self.publish_mesh(folder, job)
                for count, name in enumerate(("frames.jsonl", "scores.jsonl"), start=2):
                    with self.subTest(name=name):
                        path = folder / name
                        with path.open("ab") as handle:
                            handle.write(b"\n")
                        helper.ready(client, url)
                        self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()
