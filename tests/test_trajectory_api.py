from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase


def _frame(index: int, ball_x: float, racket_x: float) -> dict:
    return {
        "schema_version": "1.1.0",
        "frame": {
            "index": index,
            "processed_index": index,
            "timestamp_ms": index * 100,
            "width": 640,
            "height": 360,
        },
        "detections": [
            {
                "class_name": "ball",
                "track_id": 11,
                "confidence": 0.9,
                "center_normalized": [ball_x, 0.5],
            },
            {
                "class_name": "racket",
                "track_id": 12,
                "confidence": 0.8,
                "bbox_normalized": [racket_x, 0.25, racket_x + 0.1, 0.65],
            },
        ],
    }


class TrajectoryApiTests(unittest.TestCase):
    def _app(self, root: Path, *, api_key: str | None = None):
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
        )
        settings.ensure_directories()
        database = JobDatabase(settings.database_path)
        app = create_app(settings, database)
        return app, settings, database

    def _succeeded_job(self, settings: ServiceSettings, database: JobDatabase) -> str:
        database.initialize()
        job_id = "22222222-2222-4222-8222-222222222222"
        video_path = settings.uploads_dir / "input.mp4"
        request_path = settings.requests_dir / f"{job_id}.json"
        output_dir = settings.runs_dir / job_id
        video_path.write_bytes(b"video")
        request_path.write_text("{}", encoding="utf-8")
        output_dir.mkdir(parents=True)
        database.create_job(job_id, "input.mp4", video_path, request_path, output_dir)
        claimed = database.claim_next(
            "test-worker", settings.lease_seconds, settings.max_attempts
        )
        assert claimed is not None
        (output_dir / "frames.jsonl").write_text(
            "".join(
                json.dumps(_frame(index, 0.2 + index * 0.1, 0.2 + index * 0.01)) + "\n"
                for index in range(4)
            ),
            encoding="utf-8",
        )
        database.mark_succeeded(
            job_id,
            {
                "status": "completed",
                "job_id": job_id,
                "processing": {"processed_frames": 4},
            },
        )
        return job_id

    def test_trajectory_route_is_read_only_and_compatible_with_public_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, settings, database = self._app(root)
            job_id = self._succeeded_job(settings, database)
            with TestClient(app) as client:
                job = client.get(f"/v1/jobs/{job_id}")
                self.assertEqual(job.status_code, 200, job.text)
                self.assertEqual(
                    job.json()["trajectory_url"], f"/v1/jobs/{job_id}/trajectory"
                )
                response = client.get(
                    f"/v1/jobs/{job_id}/trajectory?sample_limit=2&prediction_horizon_ms=200"
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(
                    payload["result_kind"],
                    "trajectory_and_racket_observability_preview",
                )
                self.assertEqual(
                    payload["ball"]["prediction_status"], "heuristic_preview"
                )
                self.assertEqual(
                    payload["racket"]["keypoint_status"],
                    "not_available_from_current_detection_contract",
                )
                self.assertLessEqual(len(payload["ball"]["observed"]), 2)
                schema = json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "contracts"
                        / "trajectory-preview.schema.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    [], list(Draft202012Validator(schema).iter_errors(payload))
                )
                self.assertEqual(
                    client.get(
                        f"/v1/jobs/{job_id}/trajectory?sample_limit=0"
                    ).status_code,
                    422,
                )

    def test_trajectory_route_requires_frames_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, settings, database = self._app(root)
            database.initialize()
            job_id = "33333333-3333-4333-8333-333333333333"
            video_path = settings.uploads_dir / "input.mp4"
            request_path = settings.requests_dir / f"{job_id}.json"
            output_dir = settings.runs_dir / job_id
            video_path.write_bytes(b"video")
            request_path.write_text("{}", encoding="utf-8")
            output_dir.mkdir(parents=True)
            database.create_job(
                job_id, "input.mp4", video_path, request_path, output_dir
            )
            database.claim_next(
                "test-worker", settings.lease_seconds, settings.max_attempts
            )
            database.mark_succeeded(job_id, {"status": "completed", "job_id": job_id})
            with TestClient(app) as client:
                response = client.get(f"/v1/jobs/{job_id}/trajectory")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"], "trajectory_requires_frames_artifact"
        )

    def test_trajectory_route_keeps_bearer_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, settings, database = self._app(root, api_key="secret")
            job_id = self._succeeded_job(settings, database)
            with TestClient(app) as client:
                self.assertEqual(
                    client.get(f"/v1/jobs/{job_id}/trajectory").status_code, 401
                )
                response = client.get(
                    f"/v1/jobs/{job_id}/trajectory",
                    headers={"Authorization": "Bearer secret"},
                )
                self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
