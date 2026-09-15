from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceConfigError, ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.worker import process_one


class ServiceTests(unittest.TestCase):
    def _settings(self, root: Path, api_key: str | None = None) -> ServiceSettings:
        detect = root / "detect.pt"
        pose = root / "pose.pt"
        detect.write_bytes(b"test")
        pose.write_bytes(b"test")
        settings = ServiceSettings(
            data_root=root / "service",
            database_path=root / "service" / "jobs.sqlite3",
            detect_model=detect,
            pose_model=pose,
            api_key=api_key,
            max_upload_bytes=5 * 1024 * 1024,
            max_video_duration_seconds=10,
        )
        settings.ensure_directories()
        return settings

    def _video(self, root: Path) -> Path:
        path = root / "input.mp4"
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (160, 120)
        )
        self.assertTrue(writer.isOpened())
        for value in (0, 60, 120):
            writer.write(np.full((120, 160, 3), value, dtype=np.uint8))
        writer.release()
        return path

    def test_upload_queue_worker_and_status_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            database = JobDatabase(settings.database_path)
            app = create_app(settings, database)
            video = self._video(root)
            with TestClient(app) as client:
                console = client.get("/")
                self.assertEqual(console.status_code, 200)
                self.assertIn("动作表现参考分（Beta）", console.text)
                self.assertIn("分析完成度", console.text)
                self.assertIn("动作表现参考分为 Beta 反馈", console.text)
                self.assertIn("不生成虚构 A～E", console.text)
                self.assertEqual(
                    client.get("/openapi.json").json()["info"]["version"], "1.2.0"
                )
                capabilities = client.get("/v1/model-capabilities")
                self.assertEqual(capabilities.status_code, 200)
                self.assertEqual(capabilities.json()["indicator_count"], 298)
                self.assertEqual(
                    capabilities.json()["minimum_scoring_loop"]["indicator_count"],
                    13,
                )
                self.assertEqual(
                    set(capabilities.json()["minimum_scoring_loop"]["feasibility_levels"].values()),
                    {"F2"},
                )
                self.assertEqual(
                    capabilities.json()["minimum_scoring_loop"]["registry_lifecycle"]["role"],
                    "runtime_feasibility",
                )
                with video.open("rb") as handle:
                    response = client.post(
                        "/v1/jobs",
                        files={"video": ("tennis.mp4", handle, "video/mp4")},
                        data={"court_mode": "auto"},
                    )
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()["id"]
                self.assertEqual(response.json()["status"], "queued")
                self.assertIn("progress", response.json())

                def fake_runner(request, registry_lifecycle_manifest_path=None):
                    self.assertEqual(
                        request.scoring.feasibility_registry,
                        settings.resolved_scoring_feasibility_registry,
                    )
                    self.assertEqual(
                        registry_lifecycle_manifest_path,
                        settings.resolved_scoring_registry_lifecycle_manifest,
                    )
                    self.assertEqual(request.scoring.calibration_assets, [])
                    request.output_dir.mkdir(parents=True, exist_ok=True)
                    summary = {
                        "status": "completed",
                        "job_id": request.job_id,
                        "processing": {"processed_frames": 3},
                        "minimum_scoring_loop": {
                            "event_counts": {"FS01": 1, "FS02": 1, "FS09": 1},
                            "indicator_feature_validity": {},
                        },
                        "scoring_state": {
                            "scoring_status": "calibration_required",
                            "grade": None,
                        },
                        "models": {
                            "pose_deployment_preset": "rtmpose-m-halpe26-online",
                            "pose_format": "halpe26",
                        },
                    }
                    indicator_ids = {
                        "FS01": ("M02", "M03", "M04", "M05"),
                        "FS02": ("M02", "M03", "M04", "M05"),
                        "FS09": ("M01", "M02", "M03", "M04", "M05"),
                    }
                    indicator_records = []
                    for event_code, metric_codes in indicator_ids.items():
                        for metric_code in metric_codes:
                            record = {
                                "schema_version": "1.0.0",
                                "indicator_id": f"{event_code}-{metric_code}",
                                "event_id": f"{event_code.lower()}-001",
                                "event_code": event_code,
                                "feature_status": "measured",
                                "features": [],
                            }
                            if event_code == "FS01" and metric_code == "M03":
                                record["features"] = [
                                    {
                                        "feature_name": "bilateral_foot_rise_min_body",
                                        "value": 0.12,
                                        "valid": True,
                                    }
                                ]
                            indicator_records.append(record)
                    (request.output_dir / "indicator-features.jsonl").write_text(
                        "".join(
                            json.dumps(record, ensure_ascii=False) + "\n"
                            for record in indicator_records
                        ),
                        encoding="utf-8",
                    )
                    (request.output_dir / "summary.json").write_text(
                        "{}", encoding="utf-8"
                    )
                    (request.output_dir / "annotated.mp4").write_bytes(b"preview")
                    (request.output_dir / "analysis-report.html").write_text(
                        "<!doctype html><title>analysis</title>", encoding="utf-8"
                    )
                    (request.output_dir / "calculation-readiness.json").write_text(
                        "{}", encoding="utf-8"
                    )
                    (request.output_dir / "indicator-measurement-portfolio.json").write_text(
                        "{}", encoding="utf-8"
                    )
                    (request.output_dir / "scoring-cycle-measurement.json").write_text(
                        "{}", encoding="utf-8"
                    )
                    return summary

                self.assertTrue(
                    process_one(settings, database, "test-worker", fake_runner)
                )
                status = client.get(f"/v1/jobs/{job_id}")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["status"], "succeeded")
                self.assertEqual(
                    status.json()["summary"]["processing"]["processed_frames"], 3
                )
                self.assertEqual(status.json()["progress"]["percent"], 100)
                self.assertEqual(
                    status.json()["demo_result_url"],
                    f"/v1/jobs/{job_id}/demo-result",
                )
                self.assertIn(
                    "indicator-features.jsonl", status.json()["artifact_urls"]
                )
                demo_result = client.get(status.json()["demo_result_url"])
                self.assertEqual(demo_result.status_code, 200, demo_result.text)
                demo_payload = demo_result.json()
                self.assertEqual(demo_payload["job_id"], job_id)
                self.assertEqual(demo_payload["schema_version"], "1.2.0")
                self.assertEqual(
                    demo_payload["training_evaluation"]["evaluation_version"],
                    "rallymate-training-evaluation-beta-v1.0.0",
                )
                self.assertEqual(
                    demo_payload["training_evaluation"]["total_indicator_count"], 13
                )
                self.assertTrue(
                    all(
                        item["name_zh"]
                        and item["observation_zh"]
                        and item["suggestion_zh"]
                        for item in demo_payload["training_evaluation"][
                            "indicator_evaluations"
                        ]
                    )
                )
                self.assertEqual(
                    demo_payload["final_demo_score"]["value_0_to_100"], 82
                )
                self.assertEqual(
                    demo_payload["final_demo_score"]["semantics"],
                    "recognizable_motion_outline_and_amplitude_information_formation_only",
                )
                self.assertFalse(
                    demo_payload["final_demo_score"]["is_formal_technique_score"]
                )
                self.assertFalse(
                    demo_payload["final_demo_score"]["is_coach_score"]
                )
                self.assertFalse(
                    demo_payload["final_demo_score"]["is_recognition_accuracy"]
                )
                self.assertEqual(
                    demo_payload["analysis_quality"]["value_0_to_100"], 100
                )
                self.assertEqual(demo_payload["display_score"]["label_zh"], "分析完成度")
                self.assertEqual(demo_payload["display_score"]["value_0_to_100"], 100)
                self.assertFalse(
                    demo_payload["display_score"]["is_formal_technique_score"]
                )
                self.assertIn("不是球员技术水平", demo_payload["display_score"]["meaning_zh"])
                self.assertFalse(demo_payload["formal_scoring"]["available"])
                self.assertIsNone(demo_payload["formal_scoring"]["grade"])
                self.assertEqual(
                    [action["event_code"] for action in demo_payload["actions"]],
                    ["FS01", "FS02", "FS09"],
                )
                self.assertTrue(
                    all(
                        action["status"] == "measured"
                        for action in demo_payload["actions"]
                    )
                )
                self.assertEqual(
                    [
                        action["formation_assessment"]["status"]
                        for action in demo_payload["actions"]
                    ],
                    [
                        "well_formed_information",
                        "partially_formed_information",
                        "partially_formed_information",
                    ],
                )
                self.assertTrue(
                    all(action["summary_zh"] for action in demo_payload["actions"])
                )
                self.assertEqual(
                    demo_payload["actions"][0]["amplitudes"][0]["median_value"],
                    12.0,
                )
                artifact = client.get(
                    f"/v1/jobs/{job_id}/artifacts/summary.json"
                )
                self.assertEqual(artifact.status_code, 200)
                self.assertTrue(
                    artifact.headers["content-disposition"].startswith("attachment;")
                )
                inline_video = client.get(
                    f"/v1/jobs/{job_id}/artifacts/annotated.mp4"
                )
                self.assertEqual(inline_video.status_code, 200)
                self.assertTrue(
                    inline_video.headers["content-disposition"].startswith("inline;")
                )
                inline_report = client.get(
                    f"/v1/jobs/{job_id}/artifacts/analysis-report.html"
                )
                self.assertEqual(inline_report.status_code, 200)
                self.assertTrue(
                    inline_report.headers["content-disposition"].startswith("inline;")
                )
                calculation_artifact = client.get(
                    f"/v1/jobs/{job_id}/artifacts/calculation-readiness.json"
                )
                self.assertEqual(calculation_artifact.status_code, 200)
                self.assertIn(
                    "calculation-readiness.json", status.json()["artifact_urls"]
                )
                portfolio_artifact = client.get(
                    f"/v1/jobs/{job_id}/artifacts/indicator-measurement-portfolio.json"
                )
                self.assertEqual(portfolio_artifact.status_code, 200)
                self.assertIn(
                    "indicator-measurement-portfolio.json",
                    status.json()["artifact_urls"],
                )
                cycle_artifact = client.get(
                    f"/v1/jobs/{job_id}/artifacts/scoring-cycle-measurement.json"
                )
                self.assertEqual(cycle_artifact.status_code, 200)
                self.assertIn(
                    "scoring-cycle-measurement.json",
                    status.json()["artifact_urls"],
                )
                jobs = client.get("/v1/jobs")
                self.assertEqual(jobs.status_code, 200)
                self.assertEqual(jobs.json()["count"], 1)

    def test_succeeded_job_without_indicator_features_returns_empty_demo_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            database = JobDatabase(settings.database_path)
            app = create_app(settings, database)
            job_id = "11111111-1111-4111-8111-111111111111"
            output_dir = settings.runs_dir / job_id
            output_dir.mkdir(parents=True)

            with TestClient(app) as client:
                database.create_job(
                    job_id,
                    "old-video.mp4",
                    settings.uploads_dir / "old-video.mp4",
                    settings.requests_dir / f"{job_id}.json",
                    output_dir,
                )
                claimed = database.claim_next(
                    "test-worker",
                    settings.lease_seconds,
                    settings.max_attempts,
                )
                self.assertEqual(claimed["id"], job_id)
                database.mark_succeeded(
                    job_id,
                    {
                        "status": "completed",
                        "job_id": job_id,
                        "processing": {"processed_frames": 1},
                    },
                )

                # A completed run may legitimately have no measurable motion
                # records.  The result endpoint must still return a truthful
                # empty report so clients do not invent a score or force a
                # duplicate upload.
                (output_dir / "indicator-features.jsonl").write_text("", encoding="utf-8")
                response = client.get(f"/v1/jobs/{job_id}/demo-result")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["job_id"], job_id)
            self.assertEqual(payload["status"], "ready")
            self.assertIsNone(payload["training_evaluation"]["score_0_to_100"])
            self.assertFalse(payload["training_evaluation"]["available"])
            self.assertEqual(payload["analysis_quality"]["value_0_to_100"], 0)
            self.assertFalse(payload["formal_scoring"]["available"])
            self.assertIsNone(payload["formal_scoring"]["grade"])

    def test_bearer_auth_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root, api_key="secret")
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app) as client:
                self.assertEqual(client.get("/health/ready").status_code, 401)
                response = client.get(
                    "/health/ready",
                    headers={"Authorization": "Bearer secret"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "ready")

    def test_production_rejects_development_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ServiceSettings(
                data_root=root,
                database_path=root / "jobs.sqlite3",
                detect_model=root / "detect.pt",
                pose_model=root / "pose.pt",
                environment="production",
                model_license_ack="development",
            )
            with self.assertRaises(ServiceConfigError):
                settings.validate_license()

    def test_trusted_ledger_cannot_live_in_job_writable_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for writable in ("uploads", "requests", "runs"):
                settings = ServiceSettings(
                    data_root=root / "service",
                    database_path=root / "service" / "jobs.sqlite3",
                    detect_model=root / "detect.pt",
                    pose_model=root / "pose.pt",
                    scoring_trusted_promotion_ledger=(
                        root / "service" / writable / "trusted-ledger.json"
                    ),
                )
                with self.subTest(writable=writable), self.assertRaisesRegex(
                    ServiceConfigError, "job-writable"
                ):
                    settings.validate_license()

    def test_registry_authority_inputs_cannot_live_in_job_writable_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field_name in (
                "scoring_registry_lifecycle_manifest",
                "scoring_feasibility_registry",
            ):
                settings = ServiceSettings(
                    data_root=root / "service",
                    database_path=root / "service" / "jobs.sqlite3",
                    detect_model=root / "detect.pt",
                    pose_model=root / "pose.pt",
                    **{
                        field_name: root
                        / "service"
                        / "requests"
                        / "operator-input.json"
                    },
                )
                with self.subTest(field_name=field_name), self.assertRaisesRegex(
                    ServiceConfigError, "job-writable"
                ):
                    settings.validate_license()

    def test_lifecycle_resolved_artifact_cannot_live_in_job_writable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "service"
            artifact_path = data_root / "uploads" / "runtime-registry.json"
            artifact_path.parent.mkdir(parents=True)
            shutil.copyfile(
                Path(__file__).resolve().parents[1]
                / "metric-feasibility-pose-wave-v2.json",
                artifact_path,
            )
            manifest = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "registry-lifecycle.json"
                ).read_text(encoding="utf-8")
            )
            manifest["roles"]["runtime_feasibility"]["relative_path"] = (
                "service/uploads/runtime-registry.json"
            )
            manifest_path = root / "registry-lifecycle.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            settings = ServiceSettings(
                data_root=data_root,
                database_path=data_root / "jobs.sqlite3",
                detect_model=root / "detect.pt",
                pose_model=root / "pose.pt",
                scoring_registry_lifecycle_manifest=manifest_path,
            )
            with self.assertRaisesRegex(ServiceConfigError, "job-writable"):
                settings.validate_license()

    def test_historical_registry_override_is_not_ready_without_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            settings = ServiceSettings(
                **{
                    **settings.__dict__,
                    "scoring_feasibility_registry": Path(__file__).resolve().parents[1]
                    / "metric-feasibility.json",
                }
            )
            app = create_app(settings, JobDatabase(settings.database_path))
            with TestClient(app, raise_server_exceptions=False) as client:
                ready = client.get("/health/ready")
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(ready.json()["status"], "not_ready")
                self.assertTrue(
                    any("not authorized" in reason for reason in ready.json()["reasons"])
                )
                self.assertEqual(client.get("/v1/model-capabilities").status_code, 500)


if __name__ == "__main__":
    unittest.main()
