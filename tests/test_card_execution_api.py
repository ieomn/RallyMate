"""API integration using original source-bound trajectory fragments, not saved grades."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
import rallymate_service.card_execution as execution
from rallymate_scoring.stroke_candidates import DETECTOR_VERSION
from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION


FIXTURE_PATH = Path(__file__).parent / "fixtures/card-execution-api-real-0a3.json"


class CardExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.job_id = self.fixture["provenance"]["source_run_id"]
        execution.clear_card_execution_cache()
        self.addCleanup(execution.clear_card_execution_cache)

    def app(self, root, *, api_key=None):
        detect, pose = root / "detect.pt", root / "pose.pt"
        detect.write_bytes(b"test")
        pose.write_bytes(b"test")
        settings = ServiceSettings(data_root=root / "service",
            database_path=root / "service/jobs.sqlite3", detect_model=detect, pose_model=pose,
            api_key=api_key)
        settings.ensure_directories()
        database = JobDatabase(settings.database_path)
        database.initialize()
        # Importing the existing API creates its module-level app. Supply these
        # test settings for that initialization so no deployment weights are needed.
        with patch("rallymate_service.config.load_settings", return_value=settings):
            from rallymate_service.api import create_app
        return create_app(settings, database), settings, database

    def job(self, settings, database, *, identifier=None, output=None, write_inputs=True):
        identifier = identifier or self.job_id
        output = output or settings.runs_dir / identifier
        output.mkdir(parents=True, exist_ok=True)
        video = settings.uploads_dir / (identifier + ".mp4")
        request = settings.requests_dir / (identifier + ".json")
        video.write_bytes(b"not-read-by-report-test")
        request.write_text("{}", encoding="utf-8")
        database.create_job(identifier, "input.mp4", video, request, output)
        self.assertIsNotNone(database.claim_next("test-worker", settings.lease_seconds, settings.max_attempts))
        # Preserve the historical action cache; this route must still calculate
        # card evidence freshly even when all old summary versions look current.
        summary = {"job_id": identifier, "status": "completed",
            "processing": {"processed_frames": len(self.fixture["records"])},
            "action_recognition": {"detector_version": DETECTOR_VERSION,
                "motion_analysis": {"analysis_version": ANALYSIS_VERSION}},
            "card_execution": {"version": execution.VERSION, "cards": [{"score": 100, "original_grade": "A"}],
                "overall_score": 100}}
        database.mark_succeeded(identifier, summary)
        if write_inputs:
            for name, rows in (("frames.jsonl", self.fixture["records"]),
                               ("events.jsonl", self.fixture["events"]),
                               ("primary-player.jsonl", self.fixture["timeline"])):
                (output / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return output, summary

    def test_current_and_legacy_return_complete_recomputed_real_report(self):
        expected = execution.build_card_execution_from_records(deepcopy(self.fixture["records"]),
            deepcopy(self.fixture["events"]), deepcopy(self.fixture["timeline"]), source_run_id=self.job_id)
        expected = json.loads(json.dumps(expected, allow_nan=False))
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database = self.app(Path(directory))
            output, _ = self.job(settings, database)
            before = (output / "summary.json").read_bytes()
            with TestClient(app) as client:
                reports = []
                for contract in ("current", "legacy-v1"):
                    response = client.get(f"/v1/jobs/{self.job_id}/demo-result?report_contract={contract}")
                    self.assertEqual(response.status_code, 200, response.text)
                    reports.append(response.json()["card_execution"])
            self.assertEqual(reports[0], reports[1])
            result = reports[0]
            self.assertEqual(result["cards"], expected["cards"])
            self.assertEqual(result["summary"], expected["summary"])
            self.assertEqual(len(result["cards"]), 298)
            self.assertEqual(result["summary"]["status_counts"], {"proxy_scored": 3})
            self.assertEqual(result["source_run_id"], self.job_id)
            self.assertIsNone(result["overall_score"])
            self.assertEqual((output / "summary.json").read_bytes(), before)
            json.dumps(result, allow_nan=False)

    def test_malformed_raw_frames_do_not_erase_other_saved_results(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database = self.app(Path(directory))
            output, summary = self.job(settings, database)
            with TestClient(app) as client:
                before = client.get(f"/v1/jobs/{self.job_id}/demo-result?report_contract=current").json()
                (output / "frames.jsonl").write_text('{"frame":broken}\n', encoding="utf-8")
                response = client.get(f"/v1/jobs/{self.job_id}/demo-result?report_contract=current")
            self.assertEqual(response.status_code, 200, response.text)
            after = response.json()
            state = after["card_execution"]
            self.assertEqual(state["status"], "unavailable")
            self.assertEqual(state["version"], execution.VERSION)
            self.assertEqual(state["rule_version"], "card-rules-v1.0.0")
            self.assertEqual(state["cards"], [])
            self.assertIsNone(state["overall_score"])
            self.assertTrue(any("原始帧" in text for text in state["limitations"]))
            for key in ("artifact_urls", "trajectory_url", "technique_assessment_url", "footwork_review"):
                self.assertEqual(after[key], before[key])
            self.assertEqual(database.get_job(self.job_id)["summary"], summary)
            self.assertEqual(json.loads((output / "summary.json").read_text(encoding="utf-8")), summary)

    def test_stale_summary_never_authorizes_scores_and_changed_raw_inputs_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database = self.app(Path(directory))
            output, _ = self.job(settings, database)
            with patch.object(execution, "_read_records", wraps=execution._read_records) as read, TestClient(app) as client:
                url = f"/v1/jobs/{self.job_id}/demo-result?report_contract=current"
                first = client.get(url).json()["card_execution"]
                self.assertIsNone(first["overall_score"])
                self.assertEqual(read.call_count, 3)
                events = deepcopy(self.fixture["events"])
                for event in events:
                    event["quality_flags"].append("confirmed_id_switch_present")
                (output / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
                changed = client.get(url).json()["card_execution"]
                repeated = client.get(url).json()["card_execution"]
                self.assertEqual(read.call_count, 6)
            self.assertEqual(changed, repeated)
            self.assertEqual(changed["summary"]["status_counts"], {"review_required": 3})
            self.assertTrue(all(row["score"] is None and row["original_grade"] is None
                for card in changed["cards"] for row in card["instances"]))

    def test_job_cannot_return_another_jobs_output_even_when_directory_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database = self.app(Path(directory))
            output, _ = self.job(settings, database)
            foreign_id = "22222222-2222-4222-8222-222222222222"
            self.job(settings, database, identifier=foreign_id, output=output, write_inputs=False)
            with TestClient(app) as client:
                first = client.get(f"/v1/jobs/{self.job_id}/demo-result").json()["card_execution"]
                foreign = client.get(f"/v1/jobs/{foreign_id}/demo-result").json()["card_execution"]
                repeated = client.get(f"/v1/jobs/{self.job_id}/demo-result").json()["card_execution"]
            self.assertEqual(foreign["status"], "unavailable")
            self.assertEqual(foreign["source_run_id"], foreign_id)
            self.assertEqual(foreign["cards"], [])
            self.assertEqual(first, repeated)

    def test_new_report_stays_behind_existing_bearer_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            app, settings, database = self.app(Path(directory), api_key="real-test-secret")
            self.job(settings, database)
            with patch("rallymate_service.api.build_card_execution_report", wraps=execution.build_card_execution_report) as build, TestClient(app) as client:
                url = f"/v1/jobs/{self.job_id}/demo-result?report_contract=current"
                self.assertEqual(client.get(url).status_code, 401)
                self.assertEqual(client.get(url, headers={"Authorization": "Bearer wrong"}).status_code, 401)
                build.assert_not_called()
                response = client.get(url, headers={"Authorization": "Bearer real-test-secret"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
