from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.test_trajectory_api import TrajectoryApiTests


class ReferenceComparisonApiTests(unittest.TestCase):
    def test_authenticated_read_rebuilds_each_source_once_without_mutating_job(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory), api_key="test-secret")
            job_id = fixture._succeeded_job(settings, database)
            before = deepcopy(database.get_job(job_id))
            report = {"source_run_id": job_id, "episodes": []}
            expected = {"status": "scored", "reference_similarity_score": 84.5}
            query = {"episode_id": "candidate", "reference_job_id": job_id,
                     "reference_episode_id": "reference"}
            with patch("rallymate_service.api.build_tennis_assessment", return_value=report) as build, \
                    patch("rallymate_service.api.compare_action_to_reference", return_value=expected) as compare, \
                    patch("rallymate_service.api.build_user_demo_result", side_effect=AssertionError("large report")), \
                    TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/reference-comparison"
                self.assertEqual(client.get(url, params=query).status_code, 401)
                response = client.get(url, params=query, headers={"Authorization": "Bearer test-secret"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.json(), expected)
                self.assertEqual(build.call_count, 1)
                compare.assert_called_once_with(report, "candidate", report, "reference")
                self.assertEqual(client.post(url, json=query, headers={"Authorization": "Bearer test-secret"}).status_code, 405)
            self.assertEqual(database.get_job(job_id), before)

    def test_both_jobs_must_exist_and_be_complete_before_scoring(self):
        for missing_reference in (False, True):
            with self.subTest(missing_reference=missing_reference), tempfile.TemporaryDirectory() as directory:
                fixture = TrajectoryApiTests()
                app, settings, database = fixture._app(Path(directory))
                job_id = fixture._succeeded_job(settings, database, running=True)
                with patch("rallymate_service.api.build_tennis_assessment") as build, TestClient(app) as client:
                    response = client.get(f"/v1/jobs/{job_id}/reference-comparison", params={
                        "episode_id": "a", "reference_job_id": "missing" if missing_reference else job_id,
                        "reference_episode_id": "b"})
                    self.assertEqual(response.status_code, 404 if missing_reference else 409)
                    build.assert_not_called()

    def test_invalid_evidence_is_422_and_missing_parameters_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job_id = fixture._succeeded_job(settings, database)
            with patch("rallymate_service.api.build_tennis_assessment", return_value={}), \
                    patch("rallymate_service.api.compare_action_to_reference", side_effect=ValueError("phase_unit_mismatch")), \
                    TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/reference-comparison"
                self.assertEqual(client.get(url).status_code, 422)
                response = client.get(url, params={"episode_id": "a", "reference_job_id": job_id,
                                                  "reference_episode_id": "b"})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"], {
                    "code": "reference_comparison_invalid", "reason": "phase_unit_mismatch"})


if __name__ == "__main__":
    unittest.main()
