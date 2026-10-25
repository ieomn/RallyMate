from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_trajectory_api import TrajectoryApiTests


class TennisAssessmentApiTests(unittest.TestCase):
    def test_historical_assessment_is_bound_read_only_and_does_not_build_large_report(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory), api_key="test-secret")
            job_id = fixture._succeeded_job(settings, database)
            before = deepcopy(database.get_job(job_id))
            output = settings.runs_dir / job_id
            before_frames = (output / "frames.jsonl").read_bytes()
            expected = {"version": "tennis-action-assessment-v1.0.0", "source_run_id": job_id,
                        "status": "scored", "overall_score": 71.4, "dimensions": []}
            with patch("rallymate_service.api.build_tennis_assessment", return_value=expected) as build, \
                    patch("rallymate_service.api.build_user_demo_result", side_effect=AssertionError("large report")), \
                    TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/tennis-assessment"
                self.assertEqual(client.get(url).status_code, 401)
                response = client.get(url, headers={"Authorization": "Bearer test-secret"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(client.post(url, headers={"Authorization": "Bearer test-secret"}).status_code, 405)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(build.call_args.args[1], output)
                self.assertEqual(build.call_args.kwargs["job_id"], job_id)
            self.assertEqual(database.get_job(job_id), before)
            self.assertEqual((output / "frames.jsonl").read_bytes(), before_frames)

    def test_running_and_unknown_jobs_do_not_receive_finished_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job_id = fixture._succeeded_job(settings, database, running=True)
            with patch("rallymate_service.api.build_tennis_assessment") as build, TestClient(app) as client:
                self.assertEqual(client.get(f"/v1/jobs/{job_id}/tennis-assessment").status_code, 409)
                self.assertEqual(client.get("/v1/jobs/00000000-0000-4000-8000-000000000000/tennis-assessment").status_code, 404)
                build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
