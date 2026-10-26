from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.advice_context import (
    ADVICE_CONTEXT_MAX_BYTES, AdviceContextTooLarge, project_advice_context,
)
from tests import test_trajectory_api as fixture


def assessment():
    return {
        "job_id": "job_12345678",
        "family_summary": {"baseline": {"recognition_status": "motion_analyzed"}},
        "techniques": [{"family": "baseline", "name_zh": "底线正手", "observed": False,
                        "status": "not_observed", "phase_statuses": []},
                       {"family": "footwork", "observed": True, "score_0_to_100": 99}],
        "action_recognition": {"motion_analysis": {
            "schema_version": "1.1.0", "status": "available", "contact_confirmed": False,
            "families": {"baseline": {"status": "analyzed", "episodes": [{
                "episode_id": "stroke_1", "family": "baseline", "method": "rule_based",
                "start_ms": 0, "peak_ms": 100, "end_ms": 200, "contact_confirmed": False,
                "analysis_status": "partial", "classification": {"status": "unclassified", "label": "unclassified"},
                "evidence": {"pose_samples": 4, "large_unused_proof": ["unused"] * 10000},
                "metrics": {"wrist_path_torso": 0, "confidence": None},
                "phases": [{"phase": "preparation", "status": "measured", "start_ms": 0, "end_ms": 90}],
            }]}, "serve": {"status": "analyzed", "episodes": []}},
        }},
    }


class AdviceContextTests(unittest.TestCase):
    def test_projection_preserves_consumed_values_and_does_not_mutate(self):
        source = assessment()
        before = deepcopy(source)
        result = project_advice_context(source["job_id"], source, "底线击球")
        self.assertEqual(source, before)
        self.assertEqual(result["advice_context_version"], "advice-context-v1")
        self.assertEqual(result["family"], "baseline")
        motion = result["action_recognition"]["motion_analysis"]
        self.assertEqual(motion["schema_version"], "1.0.0")
        self.assertEqual(set(motion["families"]), {"baseline"})
        episode = motion["families"]["baseline"]["episodes"][0]
        self.assertEqual(episode["metrics"], {"wrist_path_torso": 0})
        self.assertEqual(episode["evidence"], {"pose_samples": 4})
        self.assertEqual(episode["phases"], before["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]["phases"])
        self.assertFalse(motion["contact_confirmed"])
        self.assertNotIn("score_0_to_100", json.dumps(result))
        self.assertLess(len(json.dumps(result).encode()), 4000)

    def test_model_classification_is_not_promoted_to_legacy_rule_evidence(self):
        source = assessment()
        episode = source["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"][0]
        episode["classification"] = {"status": "model_inferred", "label": "forehand"}
        result = project_advice_context(source["job_id"], source, "底线击球")
        family = result["action_recognition"]["motion_analysis"]["families"]["baseline"]
        self.assertEqual(family["episodes"], [])
        self.assertEqual(family["status"], "insufficient_evidence")

    def test_all_episodes_are_retained_or_limit_fails_without_sampling(self):
        source = assessment()
        episodes = source["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"]
        episodes[:] = [{**episodes[0], "episode_id": f"stroke_{index}"} for index in range(40)]
        result = project_advice_context(source["job_id"], source, "底线击球")
        self.assertEqual(len(result["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"]), 40)
        with patch("rallymate_service.advice_context.ADVICE_CONTEXT_MAX_BYTES", 1000):
            with self.assertRaises(AdviceContextTooLarge):
                project_advice_context(source["job_id"], source, "底线击球")

    def test_invalid_family_and_nonfinite_json_fail_closed(self):
        with self.assertRaises(ValueError):
            project_advice_context("job_12345678", assessment(), "unknown")
        source = assessment()
        source["techniques"][0]["status"] = float("nan")
        with self.assertRaises(AdviceContextTooLarge):
            project_advice_context(source["job_id"], source, "底线击球")

    def test_endpoint_reuses_assessment_without_building_full_report(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database)
            with patch("rallymate_service.api.build_user_demo_result", side_effect=AssertionError("full report must not be built")):
                with TestClient(app) as client:
                    native = client.get(f"/v1/jobs/{job_id}/technique-assessment?report_contract=current")
                    response = client.get(f"/v1/jobs/{job_id}/advice-evidence", params={"technique": "步伐"})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json(), project_advice_context(job_id, native.json(), "步伐"))
                    self.assertLessEqual(len(response.content), ADVICE_CONTEXT_MAX_BYTES)
                    self.assertEqual(client.get(f"/v1/jobs/{job_id}/advice-evidence", params={"technique": "unknown"}).status_code, 422)
                    self.assertEqual(client.get("/v1/jobs/missing-job/advice-evidence", params={"technique": "步伐"}).status_code, 404)
                    with patch("rallymate_service.api.project_advice_context", side_effect=AdviceContextTooLarge("limit")):
                        self.assertEqual(client.get(f"/v1/jobs/{job_id}/advice-evidence", params={"technique": "步伐"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
