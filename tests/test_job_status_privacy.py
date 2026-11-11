from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service import api
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.original_video import VerifiedOriginalVideo


JOB_ID = "12345678-1234-4234-8234-123456789abc"
PRIVATE_FILENAME = r"C:\Users\Private Person\Family lesson.MP4"


class JobStatusPrivacyTests(unittest.TestCase):
    def _assert_status_projection(self, *, public_base_url=None, environment="development"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ServiceSettings(
                data_root=root / "data", database_path=root / "data/jobs.sqlite3",
                detect_model=root / "detect.pt", pose_model=root / "pose.pt",
                api_key="status-fixture-9d8f8e4a965e4690bc7050797dc05710",
                public_base_url=public_base_url, environment=environment,
                model_license_ack="enterprise",
            )
            settings.ensure_directories()
            database = JobDatabase(settings.database_path)
            database.initialize()
            database.create_job(
                JOB_ID, PRIVATE_FILENAME, settings.uploads_dir / f"{JOB_ID}.mp4",
                settings.requests_dir / f"{JOB_ID}.json", settings.runs_dir / JOB_ID,
            )
            # Even an unreadable legacy summary must not affect status polling.
            with database.connect() as connection:
                connection.execute(
                    "UPDATE jobs SET status='succeeded', summary_json=?, progress_json=NULL WHERE id=?",
                    ("intentionally not valid JSON", JOB_ID),
                )
                connection.commit()

            connect = database.connect

            @contextmanager
            def deny_summary_reads():
                with connect() as connection:
                    connection.set_authorizer(
                        lambda operation, table, column, *_: sqlite3.SQLITE_DENY
                        if operation == sqlite3.SQLITE_READ and table == "jobs" and column == "summary_json"
                        else sqlite3.SQLITE_OK
                    )
                    yield connection

            app = api.create_app(settings, database)
            with TestClient(app) as client:
                with patch.object(database, "connect", deny_summary_reads), \
                     patch.object(database, "get_job", side_effect=AssertionError("status must not load summary")), \
                     patch.object(api, "_public_job", side_effect=AssertionError("status must remain lightweight")):
                    response = client.get(
                        f"/v1/jobs/{JOB_ID}/status",
                        headers={"Authorization": f"Bearer {settings.api_key}"},
                    )
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            redacted = bool(public_base_url) or environment == "production"
            expected = f"video-{JOB_ID[:8]}.mp4" if redacted else PRIVATE_FILENAME
            self.assertEqual(result["original_filename"], expected)
            self.assertEqual(
                result["original_filename"],
                api._public_job(
                    {"id": JOB_ID, "original_filename": PRIVATE_FILENAME},
                    redact_summary=redacted,
                )["original_filename"],
            )
            self.assertEqual(result["id"], JOB_ID)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["progress"], {"phase": "succeeded", "percent": 100})
            self.assertIs(result["cancel_requested"], False)
            self.assertNotIn("summary", result)
            self.assertNotIn("summary_json", result)
            self.assertEqual(response.headers["cache-control"], "no-store")
            if redacted:
                self.assertNotIn("Private Person", response.text)
                self.assertNotIn("Family lesson", response.text)

    def test_public_status_uses_opaque_filename_without_reading_summary(self):
        self._assert_status_projection(public_base_url="https://service.example")

    def test_production_status_uses_opaque_filename_without_reading_summary(self):
        self._assert_status_projection(environment="production")

    def test_development_status_preserves_filename_without_reading_summary(self):
        self._assert_status_projection()

    def test_original_video_descriptor_does_not_expose_filename_or_path(self):
        descriptor = VerifiedOriginalVideo(Path(PRIVATE_FILENAME), JOB_ID, "a" * 64, 10).public(JOB_ID)
        self.assertEqual(descriptor, {
            "url": f"/v1/jobs/{JOB_ID}/original-video",
            "sha256": "a" * 64, "bytes": 10, "source_job_id": JOB_ID,
        })


if __name__ == "__main__":
    unittest.main()
