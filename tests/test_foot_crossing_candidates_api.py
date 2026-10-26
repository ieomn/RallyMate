from __future__ import annotations

import copy
from contextlib import ExitStack
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_service import api
from tests import test_trajectory_api


class FootCrossingCandidatesApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        fixture = test_trajectory_api.TrajectoryApiTests()
        self.app, self.settings, self.database = fixture._app(Path(self.temporary.name), api_key="fixture-token")
        self.job_id = fixture._succeeded_job(self.settings, self.database)
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.client = contexts.enter_context(TestClient(self.app))
        self.headers = {"Authorization": "Bearer fixture-token"}
        self.url = f"/v1/jobs/{self.job_id}/foot-crossing-candidates"

    def test_old_job_without_reference_is_not_computed_and_history_stays_unchanged(self):
        before = copy.deepcopy(self.database.get_job(self.job_id)["summary"])
        history = {p.name: p.read_bytes() for p in (self.settings.runs_dir / self.job_id).iterdir() if p.is_file()}
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "not_computed")
        self.assertEqual(response.json()["candidates"], [])
        self.assertIsNone(response.json()["counts"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(self.database.get_job(self.job_id)["summary"], before)
        self.assertEqual({p.name: p.read_bytes() for p in (self.settings.runs_dir / self.job_id).iterdir() if p.is_file()}, history)
        self.assertFalse((self.settings.data_root / "derived-foot-crossing").exists())

    def test_authentication_job_success_and_cancel_gate_precede_reader(self):
        with patch.object(api, "load_foot_crossing_candidates") as reader:
            self.assertEqual(self.client.get(self.url).status_code, 401)
            self.assertEqual(self.client.get("/v1/jobs/missing123/foot-crossing-candidates", headers=self.headers).status_code, 404)
            job = self.database.get_job(self.job_id)
            for changed in ({**job, "status": "running"}, {**job, "cancel_requested": True}):
                with patch.object(self.database, "get_job", return_value=changed):
                    response = self.client.get(self.url, headers=self.headers)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.headers["cache-control"], "private, no-store")
            reader.assert_not_called()

    def test_expected_hash_is_validated_and_passed_only_as_comparison(self):
        payload = {"status": "binding_conflict", "reason_code": "expected_artifact_changed"}
        with patch.object(api, "load_foot_crossing_candidates", return_value=payload) as reader:
            for bad in ("../../other", "a" * 63, "z" * 64):
                self.assertEqual(self.client.get(self.url, params={"expected_artifact_sha256": bad}, headers=self.headers).status_code, 422)
            reader.assert_not_called()
            response = self.client.get(self.url, params={"expected_artifact_sha256": "A" * 64}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), payload)
            self.assertEqual(reader.call_args.args[0], self.settings.data_root)
            self.assertEqual(reader.call_args.args[1]["id"], self.job_id)
            self.assertEqual(reader.call_args.kwargs, {"expected_artifact_sha256": "A" * 64})
            self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_endpoint_does_not_open_generic_candidate_download(self):
        self.assertNotIn("foot-crossing-candidates.json", api.ALLOWED_ARTIFACTS)
        response = self.client.get(f"/v1/jobs/{self.job_id}/artifacts/foot-crossing-candidates.json", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(self.url, headers=self.headers).status_code, 405)

    def test_available_projection_traverses_real_reader_and_json_response(self):
        from tests import test_foot_crossing_candidates_service as fixtures
        job, manifest, _ = fixtures.fixture(self.settings.data_root)
        with patch.object(self.database, "get_job", return_value=job):
            response = self.client.get(f"/v1/jobs/{job['id']}/foot-crossing-candidates", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "available", payload)
        self.assertEqual(payload["counts"]["candidates"], 1)
        self.assertEqual(payload["identity"]["artifact_sha256"], manifest["artifact"]["sha256"])
        self.assertEqual(payload["candidates"][0]["source_frames"], [170, 171])
        self.assertFalse(payload["scoring_allowed"])
        self.assertIsNone(payload["technical_grade"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")


if __name__ == "__main__":
    unittest.main()
