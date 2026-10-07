from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fastapi.testclient import TestClient

from rallymate_scoring.stroke_candidates import detect_stroke_candidates
from rallymate_service.footwork_review import build_footwork_review
from rallymate_service.report_compatibility import project_report
from tests.test_footwork_review import event, record
from tests.test_stroke_analysis import swing
from tests import test_trajectory_api as trajectory_fixture


def consume_with_main_client(payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node >=22.13 is required to execute the original web client parsers")
    helper = Path(__file__).with_name("helpers") / "legacy_report_consumer.mjs"
    process = subprocess.run([node, str(helper)], input=json.dumps(payload), text=True,
                             encoding="utf-8", capture_output=True, timeout=30, check=True)
    return json.loads(process.stdout)


class ReportCompatibilityTests(unittest.TestCase):
    def test_partial_footwork_stays_unavailable_in_legacy_indicator_contract(self):
        # The independent schema can expose one measured feature while the
        # complete indicator remains unavailable. Old clients must not see
        # a falsely complete indicator when those new features are projected.
        native = {"footwork_review": build_footwork_review([event()], [record(
            feature_status="unavailable", scoring_status="unavailable")])}
        before = deepcopy(native)
        independent = native["footwork_review"]["episodes"][0]["indicators"][0]
        self.assertEqual(independent["measurement_status"], "partial")
        self.assertGreater(independent["measured_feature_count"], 0)
        legacy = project_report(native)
        indicator = legacy["footwork_review"]["episodes"][0]["indicators"][0]
        self.assertEqual(indicator["feature_status"], "unavailable")
        self.assertEqual(indicator["scoring_status"], "unavailable")
        self.assertEqual(indicator["features"], [])
        self.assertNotIn("measurements", indicator)
        self.assertEqual(native, before)
        self.assertEqual(project_report(native, "current"), before)
        self.assertEqual(project_report(legacy), legacy)

    def test_unsupported_classifier_is_never_mislabelled_as_rule_inference(self):
        recognition = detect_stroke_candidates(swing())
        native = recognition["motion_analysis"]
        self.assertTrue(native["families"]["baseline"]["episodes"])
        native["families"]["baseline"]["episodes"][0]["classification"]["status"] = "model_inferred"
        before = deepcopy(native)
        legacy = project_report({"motion_analysis": native})["motion_analysis"]
        self.assertEqual(legacy["compatibility"]["omitted_unsupported_episode_count"], 1)
        self.assertEqual(legacy["families"]["baseline"]["episodes"], [])
        self.assertEqual(legacy["families"]["baseline"]["summary"]["analyzed_count"], 0)
        self.assertEqual(native, before)
        native["schema_version"] = "2.0.0"
        self.assertEqual(project_report({"motion_analysis": native})["motion_analysis"], native)
        with self.assertRaises(ValueError):
            project_report({}, "not-a-contract")

    def test_original_client_consumes_all_api_views_and_artifacts_stay_native(self):
        fixtures = Path(__file__).with_name("fixtures") / "legacy-web-v1"
        provenance = json.loads((fixtures / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source_commit"], "60457524bcd828ad0a9063c5e4af0c67089f18ad")
        expected_hashes = {
            "footwork-review.ts": "54db26c2c50d6d6c84ee2deef7282e6da532a6d58c67e5394b009c369490b5ba",
            "motion-analysis.ts": "c9cafb36019e9414505dda6a4ed8ed5cb9985a6310f49bd4d34fcab75f361c9f",
        }
        self.assertEqual({item["file"]: item["sha256"] for item in provenance["files"]}, expected_hashes)
        for item in provenance["files"]:
            self.assertEqual(item["source_path"], "scoring-demo-web/app/lib/" + item["file"])
            self.assertEqual(hashlib.sha256((fixtures / item["file"]).read_bytes()).hexdigest(), item["sha256"])
        with tempfile.TemporaryDirectory() as directory:
            fixture = trajectory_fixture.TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job_id = fixture._succeeded_job(settings, database, running=True)
            output = settings.runs_dir / job_id
            recognition = detect_stroke_candidates(swing())
            self.assertEqual(recognition["motion_analysis"]["schema_version"], "1.1.0")
            summary = {"status": "completed", "job_id": job_id,
                       "processing": {"processed_frames": 60},
                       "minimum_scoring_loop": {"video_id": job_id},
                       "action_recognition": recognition}
            database.mark_succeeded(job_id, summary)
            (output / "events.jsonl").write_text(json.dumps(event(video_id=job_id)) + "\n", encoding="utf-8")
            (output / "indicator-features.jsonl").write_text(json.dumps(record(video_id=job_id)) + "\n", encoding="utf-8")
            artifact = output / "summary.json"
            artifact.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            artifact_bytes = artifact.read_bytes()
            database_before = deepcopy(database.get_job(job_id))
            base = f"/v1/jobs/{job_id}"
            with TestClient(app) as client:
                responses = {}
                for name, path in {"demo": base + "/demo-result", "assessment": base + "/technique-assessment",
                                   "job": base, "list": "/v1/jobs"}.items():
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, response.text)
                    responses[name] = response.json()
                    self.assertEqual(client.get(path + "?report_contract=invalid").status_code, 422)
                def current(path):
                    response = client.get(path + "?report_contract=current")
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()
                native = current(base + "/demo-result")
                native_job = current(base)
                native_assessment = current(base + "/technique-assessment")
                native_list = current("/v1/jobs")
                for view in [native, native_assessment, native_job["summary"], native_list["items"][0]["summary"]]:
                    self.assertIn("action_recognition", view)
                    self.assertEqual(view["action_recognition"]["motion_analysis"]["schema_version"], "1.1.0")
                self.assertEqual(native["footwork_review"]["schema_version"], "1.1.0")
                self.assertIn("measurements", native["footwork_review"]["episodes"][0]["indicators"][0])
                self.assertEqual(client.get(base + "/artifacts/summary.json").content, artifact_bytes)
                projected_artifact = client.get(base + "/artifacts/summary.json?report_contract=legacy-v1")
                self.assertEqual(projected_artifact.status_code, 200, projected_artifact.text)
                self.assertEqual(projected_artifact.headers["x-rallymate-report-contract"], "legacy-v1")
                self.assertIn("summary-legacy-v1.json", projected_artifact.headers["content-disposition"])
                # Trajectory has no motion/footwork schema and keeps its own
                # contract rather than being treated as a motion report.
                trajectory = client.get(base + "/trajectory")
                self.assertEqual(trajectory.status_code, 200, trajectory.text)
                self.assertNotIn("compatibility", trajectory.json())
            self.assertEqual(artifact.read_bytes(), artifact_bytes)
            self.assertEqual(database.get_job(job_id), database_before)
            parsed = consume_with_main_client({
                "demo": {"result": responses["demo"], "footwork_review": responses["demo"]["footwork_review"]},
                "assessment": {"assessment": responses["assessment"]},
                "nested_assessment": {"assessment": responses["demo"]["technique_assessment"]},
                "job": {"summary": responses["job"]["summary"]},
                "list": {"summary": responses["list"]["items"][0]["summary"]},
                "artifact": {"summary": projected_artifact.json()},
                "native": {"result": native, "footwork_review": native["footwork_review"]},
            })
            expected = native["action_recognition"]["motion_analysis"]["families"]["baseline"]["episodes"]
            self.assertTrue(expected)
            for name in ("demo", "assessment", "nested_assessment", "job", "list", "artifact"):
                with self.subTest(view=name):
                    episodes = parsed[name]["motion"]["families"]["baseline"]["episodes"]
                    self.assertEqual(len(episodes), len(expected))
                    self.assertEqual(episodes[0]["metrics"], expected[0]["metrics"])
                    self.assertEqual(episodes[0]["phases"], expected[0]["phases"])
                    self.assertFalse(episodes[0]["contact_confirmed"])
            self.assertEqual(len(parsed["demo"]["footwork"]), 1)
            indicator = parsed["demo"]["footwork"][0]["indicators"][0]
            self.assertEqual(indicator["features"][0]["value"], .7)
            self.assertEqual(indicator["scoring_status"], "calibration_required")
            self.assertNotIn("grade", indicator)
            self.assertIsNone(parsed["native"]["motion"])
            self.assertEqual(parsed["native"]["footwork"], [])
            self.assertIsNone(responses["demo"]["training_evaluation"]["technical_score_0_to_100"])


if __name__ == "__main__":
    unittest.main()
