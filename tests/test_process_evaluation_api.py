from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION
from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts
from tests.test_stroke_analysis import swing
from tests import test_trajectory_api as trajectory_fixture


class ProcessEvaluationApiTests(unittest.TestCase):
    def test_historical_job_rebuilds_process_once_without_rewriting_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = trajectory_fixture.TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job_id = fixture._succeeded_job(settings, database, running=True)
            output = settings.runs_dir / job_id
            frames = swing()
            (output / "frames.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8",
            )
            (output / "primary-player.jsonl").write_text(
                "".join(json.dumps({"processed_index": i, "source_track_id": 1,
                                     "selection_status": "selected"}) + "\n"
                        for i in range(len(frames))), encoding="utf-8",
            )
            summary = {"job_id": job_id, "status": "completed",
                       "processing": {"processed_frames": len(frames)},
                       "action_recognition": {"motion_analysis": {
                           "analysis_version": "stroke-motion-analysis-v2.0.0",
                       }}}
            database.mark_succeeded(job_id, summary)
            artifact = output / "summary.json"
            artifact.write_text(json.dumps(summary), encoding="utf-8")
            before_bytes = artifact.read_bytes()
            before_job = deepcopy(database.get_job(job_id))
            with patch("rallymate_service.api.recognize_strokes_from_artifacts",
                       wraps=recognize_strokes_from_artifacts) as rebuild, TestClient(app) as client:
                response = client.get(f"/v1/jobs/{job_id}/demo-result?report_contract=current")
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                motion = payload["action_recognition"]["motion_analysis"]
                self.assertEqual(motion["analysis_version"], ANALYSIS_VERSION)
                self.assertTrue(motion["families"]["baseline"]["episodes"])
                self.assertIn("process_motion", motion["families"]["baseline"]["episodes"][0])
                process = payload["process_evaluation"]
                self.assertEqual(process["version"], "process-evaluation-v2.0.0")
                self.assertEqual(process["relative_reference"]["version"], "relative-reference-v3.0.0")
                self.assertIsNone(process["relative_reference"]["overall_score"])
                self.assertFalse(process["relative_reference"]["recalculation"]["cached_scores_used"])
                self.assertEqual(len(process["dimensions"]), 6)
                self.assertIsNone(process["overall_score"])
                self.assertFalse(process["is_calibrated"])
                self.assertTrue(any(row["criteria"] for row in process["dimensions"]))
                legacy = client.get(f"/v1/jobs/{job_id}/demo-result")
                self.assertEqual(legacy.status_code, 200, legacy.text)
                self.assertEqual(legacy.json()["process_evaluation"], process)
                self.assertEqual(rebuild.call_count, 1)
            self.assertEqual(artifact.read_bytes(), before_bytes)
            self.assertEqual(database.get_job(job_id), before_job)


if __name__ == "__main__":
    unittest.main()
