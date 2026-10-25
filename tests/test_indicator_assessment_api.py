from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.user_demo import build_user_demo_result
from tests.test_stroke_analysis import swing
from tests.test_trajectory_api import TrajectoryApiTests


class IndicatorAssessmentApiTests(unittest.TestCase):
    def assertCompleteExplanations(self, result):
        assessment = result["indicator_assessment"]
        self.assertEqual(assessment["version"], "word-indicator-assessment-v1.0.0")
        self.assertEqual(assessment["counts"]["total"], 298)
        self.assertEqual(len({row["indicator_id"] for row in assessment["indicators"]}), 298)
        self.assertEqual(sum(assessment["counts"]["statuses"].values()), 298)
        for row in assessment["indicators"]:
            self.assertTrue(row["status"])
            self.assertTrue(row["explanation_zh"])
            self.assertTrue(row["source"]["sha256"])
            self.assertTrue(row["grade"]["reason_zh"])
        json.dumps(assessment, allow_nan=False)

    def test_no_events_still_returns_all_source_card_explanations(self):
        report = build_user_demo_result({"status": "completed", "job_id": "no-events"}, [])
        self.assertCompleteExplanations(report)
        self.assertEqual(report["indicator_assessment"]["counts"]["formal_graded"], 0)
        self.assertEqual(report["indicator_assessment"]["observations"], [])

    def test_api_rebuilds_source_observations_and_keeps_native_and_legacy_explanations(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job = fixture._succeeded_job(settings, database, running=True)
            output = settings.runs_dir / job
            frames = swing()
            (output / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8")
            (output / "primary-player.jsonl").write_text("".join(json.dumps({
                "processed_index": i, "source_track_id": 1, "selection_status": "selected"}) + "\n"
                for i in range(len(frames))), encoding="utf-8")
            summary = {"job_id": job, "status": "completed", "processing": {"processed_frames": len(frames)}}
            database.mark_succeeded(job, summary)
            saved = json.dumps(summary).encode()
            (output / "summary.json").write_bytes(saved)
            with TestClient(app) as client:
                result = client.get(f"/v1/jobs/{job}/demo-result?report_contract=current")
                self.assertEqual(result.status_code, 200)
                payload = result.json()
                self.assertCompleteExplanations(payload)
                self.assertIn("card_execution", payload)
                self.assertIsNone(payload["card_execution"]["overall_score"])
                observations = payload["indicator_assessment"]["observations"]
                self.assertTrue(observations)
                self.assertTrue(any(item["source"] == "motion_joint_measurement" for item in observations))
                self.assertEqual(payload["indicator_assessment"], client.get(f"/v1/jobs/{job}/demo-result").json()["indicator_assessment"])
                self.assertEqual((output / "summary.json").read_bytes(), saved)

    def test_card_execution_failure_preserves_report_without_zero_score(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job = fixture._succeeded_job(settings, database, running=True)
            summary = {"job_id": job, "status": "completed", "processing": {"processed_frames": 0}}
            database.mark_succeeded(job, summary)
            (settings.runs_dir / job / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            with patch("rallymate_service.api.build_card_execution_report", side_effect=ValueError("invalid raw evidence")), \
                    TestClient(app) as client:
                response = client.get(f"/v1/jobs/{job}/demo-result?report_contract=current")
            self.assertEqual(response.status_code, 200)
            self.assertCompleteExplanations(response.json())
            state = response.json()["card_execution"]
            self.assertEqual(state["status"], "unavailable")
            self.assertEqual(state["rule_version"], "card-rules-v1.0.0")
            self.assertTrue(state["limitations"])
            self.assertIsNone(state["overall_score"])
            self.assertEqual(state["cards"], [])


if __name__ == "__main__":
    unittest.main()
