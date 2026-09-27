from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION
from rallymate_scoring.stroke_candidates import DETECTOR_VERSION, recognize_strokes_from_artifacts
from tests import test_trajectory_api as fixture
from tests.test_stroke_candidates import swing


class MotionApiTests(unittest.TestCase):
    def test_old_result_backfills_measured_motion_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database)
            output = settings.runs_dir / job_id
            frames = swing()
            (output / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8")
            timeline = [{"processed_index": row["frame"]["index"], "source_track_id": 1,
                         "selection_status": "selected"} for row in frames]
            (output / "primary-player.jsonl").write_text("".join(json.dumps(row) + "\n" for row in timeline), encoding="utf-8")
            with patch("rallymate_service.api.recognize_strokes_from_artifacts", wraps=recognize_strokes_from_artifacts) as recognition:
                with TestClient(app) as client:
                    job = client.get(f"/v1/jobs/{job_id}").json()
                    analysis = job["summary"]["action_recognition"]
                    self.assertEqual(analysis["detector_version"], DETECTOR_VERSION)
                    self.assertEqual(analysis["motion_analysis"]["analysis_version"], ANALYSIS_VERSION)
                    self.assertEqual(len(analysis["motion_analysis"]["families"]["baseline"]["episodes"]), 1)
                    report = client.get(f"/v1/jobs/{job_id}/demo-result")
                    self.assertEqual(report.status_code, 200, report.text)
                    self.assertEqual(report.json()["action_recognition"]["motion_analysis"], analysis["motion_analysis"])
                    assessment = report.json()["technique_assessment"]
                    self.assertEqual(assessment["family_summary"]["baseline"]["recognition_status"], "motion_analyzed")
                    self.assertEqual(assessment["family_summary"]["baseline"]["observed_count"], 0)
                    self.assertIsNone(analysis["confirmed_contact_count"])
                    self.assertEqual(recognition.call_count, 1)
                    # A changed artifact must invalidate the read-time cache.
                    (output / "frames.jsonl").write_text("", encoding="utf-8")
                    changed = client.get(f"/v1/jobs/{job_id}").json()
                    self.assertEqual(changed["summary"]["action_recognition"]["motion_analysis"]["status"], "insufficient_evidence")
                    self.assertEqual(recognition.call_count, 2)
            self.assertNotIn("action_recognition", database.get_job(job_id)["summary"])


if __name__ == "__main__":
    unittest.main()
