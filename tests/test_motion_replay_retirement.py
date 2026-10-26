from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import load_settings
from rallymate_service.database import JobDatabase
from tests import test_service, test_trajectory_api


class MotionReplayRetirementTests(unittest.TestCase):
    def test_product_has_no_replay_routes_runtime_or_database_even_with_legacy_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(test_service.ServiceTests()._settings(root), motion_replay_enabled=True,
                               motion_replay_auto_generate=True)
            database = JobDatabase(settings.database_path)
            with patch("rallymate_service.motion_replay_runtime.MotionReplayRuntime", side_effect=AssertionError("retired runtime")) as runtime, \
                 patch("rallymate_service.motion_replay_runtime.Thread", side_effect=AssertionError("retired thread")) as thread, \
                 patch("rallymate_service.motion_replay_store.MotionReplayStore.initialize", side_effect=AssertionError("retired database")) as initialize, \
                 patch("rallymate_service.local_secrets.load_minimax_key", side_effect=AssertionError("retired credential probe")) as credentials:
                app = create_app(settings, database)
                with TestClient(app) as client:
                    self.assertFalse(hasattr(app.state, "motion_replays"))
                    paths = client.get("/openapi.json").json()["paths"]
                    self.assertFalse(any("motion-replays" in path for path in paths))
                    meta = client.get("/v1/meta").json()
                    self.assertNotIn("motion_replay", meta["capabilities"])
                    replay_id = "a" * 32
                    routes = (("POST", "/v1/jobs/job-1/motion-replays"),
                              ("GET", "/v1/jobs/job-1/motion-replays"),
                              ("GET", f"/v1/motion-replays/{replay_id}"),
                              ("POST", f"/v1/motion-replays/{replay_id}/retry"),
                              ("GET", f"/v1/motion-replays/{replay_id}/media/generated.mp4"))
                    for method, path in routes:
                        with self.subTest(method=method, path=path):
                            self.assertEqual(client.request(method, path).status_code, 404)
                runtime.assert_not_called()
                thread.assert_not_called()
                initialize.assert_not_called()
                credentials.assert_not_called()
            self.assertFalse((settings.data_root / "motion-replays.sqlite3").exists())
            self.assertFalse((settings.data_root / "motion-replay-scheduler.sqlite3").exists())
            self.assertFalse((settings.data_root / "motion-replays").exists())

    def test_new_upload_and_completion_do_not_create_replay_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = test_service.ServiceTests()
            settings = replace(fixture._settings(root), motion_replay_enabled=True, motion_replay_auto_generate=True)
            database = JobDatabase(settings.database_path)
            app = create_app(settings, database)
            video = fixture._video(root)
            original = video.read_bytes()
            with TestClient(app) as client:
                with video.open("rb") as handle:
                    response = client.post("/v1/jobs", files={"video": ("tennis.mp4", handle, "video/mp4")})
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()["id"]
                claimed = database.claim_next("analysis-only", settings.lease_seconds, settings.max_attempts)
                self.assertEqual(claimed["id"], job_id)
                database.mark_succeeded(job_id, {"status": "completed", "job_id": job_id})
                self.assertEqual(client.get(f"/v1/jobs/{job_id}").json()["status"], "succeeded")
            self.assertEqual(video.read_bytes(), original)
            self.assertFalse((settings.data_root / "motion-replays.sqlite3").exists())
            self.assertFalse((settings.data_root / "motion-replay-scheduler.sqlite3").exists())

    def test_history_is_untouched_and_source_preview_trajectory_and_scores_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = test_trajectory_api.TrajectoryApiTests()
            app, settings, database = fixture._app(root)
            job_id = fixture._succeeded_job(settings, database)
            history = settings.data_root / "motion-replays.sqlite3"
            history.write_bytes(b"historical replay database must remain untouched")
            historical_media = settings.data_root / "motion-replays" / ("b" * 32) / "generated.mp4"
            historical_media.parent.mkdir(parents=True)
            historical_media.write_bytes(b"historical generated media")
            source = settings.uploads_dir / "input.mp4"
            source_bytes = source.read_bytes()
            preview = settings.runs_dir / job_id / "annotated.mp4"
            preview.write_bytes(b"existing original-video preview")
            with TestClient(app) as client:
                media = client.get(f"/v1/jobs/{job_id}/artifacts/annotated.mp4")
                self.assertEqual(media.status_code, 200)
                self.assertEqual(media.content, preview.read_bytes())
                trajectory = client.get(f"/v1/jobs/{job_id}/trajectory")
                self.assertEqual(trajectory.status_code, 200, trajectory.text)
                result = client.get(f"/v1/jobs/{job_id}/demo-result?report_contract=current")
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["indicator_assessment"]["counts"]["total"], 298)
                self.assertEqual(len(result.json()["process_evaluation"]["display_dimension_ids"]), 5)
            self.assertEqual(history.read_bytes(), b"historical replay database must remain untouched")
            self.assertEqual(historical_media.read_bytes(), b"historical generated media")
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_environment_defaults_do_not_enable_generation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "RALLYMATE_DATA_ROOT": directory, "RALLYMATE_MODEL_LICENSE_ACK": "development",
        }, clear=True):
            settings = load_settings()
            self.assertFalse(settings.motion_replay_enabled)
            self.assertFalse(settings.motion_replay_auto_generate)


if __name__ == "__main__":
    unittest.main()
