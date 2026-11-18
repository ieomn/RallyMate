from __future__ import annotations

from contextlib import contextmanager
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette import formparsers

from rallymate_service import api
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.auth_runtime import AuthConfig


TOKEN = "audit-fixture-" + "x" * 40
JOB_ID = "12345678-1234-4234-8234-123456789abc"


class ApiAuditBoundaryTests(unittest.TestCase):
    @contextmanager
    def client(self, *, authenticated=True, **overrides):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = dict(data_root=root / "data", database_path=root / "data/jobs.sqlite3",
                          detect_model=root / "detect.pt", pose_model=root / "pose.pt",
                          api_key=TOKEN, max_upload_bytes=32)
            values.update(overrides)
            settings = ServiceSettings(**values)
            database = JobDatabase(settings.database_path)
            origin = next(iter(settings.cors_origins), None) or settings.public_base_url or "http://testserver"
            app = api.create_app(settings, database, auth_config=AuthConfig(
                "test-only-audit-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ", public_origin=origin))
            with TestClient(app) as client:
                store = app.state.accounts
                password = "AuditFixturePassword123!"
                admin = store.bootstrap("admin@example.com", password)
                user = store.register("player@example.com", password, "Audit player")
                store.update_status(admin["id"], user["id"], "APPROVED")
                client.auth_token = store.authenticate("player@example.com", password)["token"]
                client.auth_user_id = user["id"]
                if authenticated:
                    client.headers["Authorization"] = f"Bearer {client.auth_token}"
                yield client, settings, database

    def test_multipart_authentication_precedes_form_parsing(self):
        with self.client(authenticated=False) as (client, _, _):
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                with self.subTest(headers=headers), patch.object(
                    api.Request, "form", side_effect=AssertionError("must not parse an unauthenticated body")
                ):
                    response = client.post("/v1/jobs", files={"video": ("a.mp4", b"video")}, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_non_ascii_authorization_is_rejected_without_server_error(self):
        with self.client() as (client, _, _):
            response = client.get("/v1/jobs", headers=[(b"authorization", b"Bearer \xff")])
            self.assertEqual(response.status_code, 401)

    def test_public_upload_without_configured_key_does_not_parse_body(self):
        with self.client(authenticated=False, api_key=None, public_base_url="https://api.example.com") as (client, _, _), patch.object(
            api.Request, "form", side_effect=AssertionError("must not parse a public unprotected body")
        ):
            response = client.post("/v1/jobs", files={"video": ("a.mp4", b"video")})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"]["code"], "authentication_required")

    def test_upload_metadata_authentication_precedes_body_parsing(self):
        with self.client(authenticated=False) as (client, _, _), patch.object(
            api.Request, "body", side_effect=AssertionError("must not buffer unauthenticated JSON")
        ):
            response = client.post("/v1/uploads", json={"upload_id": JOB_ID, "filename": "video.mp4", "size": 8})
            self.assertEqual(response.status_code, 401)

    def test_upload_metadata_actual_size_is_bounded_and_small_input_works(self):
        with self.client() as (client, _, _):
            headers = {"Authorization": f"Bearer {client.auth_token}", "Content-Type": "application/json", "Content-Length": "1"}
            response = client.post("/v1/uploads", content=iter([b'{"padding":"', b"x" * (1024 * 1024 + 1), b'"}']), headers=headers)
            self.assertEqual(response.status_code, 413)
            response = client.post("/v1/uploads", json={"upload_id": JOB_ID, "filename": "video.mp4", "size": 8},
                                   headers={"Authorization": f"Bearer {client.auth_token}"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["upload_id"], JOB_ID)

    def test_announced_oversize_is_rejected_before_form_parsing(self):
        with self.client() as (client, _, _), patch.object(
            api.Request, "form", side_effect=AssertionError("must not parse an oversized request")
        ):
            response = client.post("/v1/jobs", content=b"", headers={
                "Authorization": f"Bearer {client.auth_token}", "Content-Length": str(2 * 1024 * 1024),
                "Content-Type": "multipart/form-data; boundary=audit-boundary"})
            self.assertEqual(response.status_code, 413)

    def test_streamed_multipart_limit_does_not_trust_content_length(self):
        boundary = b"audit-boundary"
        prefix = b"--" + boundary + b'\r\nContent-Disposition: form-data; name="video"; filename="a.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
        def chunks():
            yield prefix
            for _ in range(18):
                yield b"x" * 65536
            yield b"\r\n--" + boundary + b"--\r\n"
        with self.client() as (client, _, _), patch.object(api, "probe_video") as probe:
            for length in (None, "1"):
                with self.subTest(content_length=length), patch.object(StarletteUploadFile, "read", side_effect=AssertionError("must reject during parsing")):
                    headers = {"Authorization": f"Bearer {client.auth_token}", "Content-Type": "multipart/form-data; boundary=audit-boundary"}
                    if length is not None:
                        headers["Content-Length"] = length
                    response = client.post("/v1/jobs", content=chunks(), headers=headers)
                    self.assertEqual(response.status_code, 413)
            probe.assert_not_called()

    def test_stream_limit_closes_spooled_file_and_stops_receiving(self):
        created = []
        tempfile_factory = formparsers.SpooledTemporaryFile
        def make_file(*args, **kwargs):
            handle = tempfile_factory(*args, **kwargs)
            created.append(handle)
            return handle

        async def exercise(app):
            bodies = [b'--audit-boundary\r\nContent-Disposition: form-data; name="video"; filename="a.mp4"\r\n\r\n']
            bodies.extend([b"x" * (256 * 1024)] * 8)
            read_count = 0
            messages = []
            async def receive():
                nonlocal read_count
                if read_count >= len(bodies):
                    return {"type": "http.disconnect"}
                body = bodies[read_count]
                read_count += 1
                return {"type": "http.request", "body": body, "more_body": True}
            async def send(message):
                messages.append(message)
            scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
                     "path": "/v1/jobs", "raw_path": b"/v1/jobs", "root_path": "", "query_string": b"",
                     "server": ("testserver", 80), "client": ("testclient", 50000),
                     "headers": [(b"authorization", f"Bearer {client.auth_token}".encode()),
                                 (b"content-type", b"multipart/form-data; boundary=audit-boundary")]}
            await app(scope, receive, send)
            self.assertEqual(next(item["status"] for item in messages if item["type"] == "http.response.start"), 413)
            self.assertLess(read_count, len(bodies))

        with self.client() as (client, _, _), patch.object(formparsers, "SpooledTemporaryFile", side_effect=make_file):
            asyncio.run(exercise(client.app))
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_small_authenticated_upload_still_enqueues(self):
        with self.client() as (client, settings, database), patch.object(
            api, "probe_video", return_value=SimpleNamespace(duration_ms=1000)
        ):
            response = client.post("/v1/jobs", files={"video": ("a.mp4", b"video", "video/mp4")},
                                   headers={"Authorization": f"Bearer {client.auth_token}"})
            self.assertEqual(response.status_code, 202, response.text)
            job = database.get_job(response.json()["id"])
            self.assertEqual(Path(job["video_path"]).read_bytes(), b"video")
            self.assertEqual(job["status"], "queued")

    def test_file_size_limit_still_applies_below_request_envelope_limit(self):
        with self.client() as (client, settings, database), patch.object(api, "probe_video") as probe:
            response = client.post("/v1/jobs", files={"video": ("a.mp4", b"x" * 33)},
                                   headers={"Authorization": f"Bearer {client.auth_token}"})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(database.list_jobs(), [])
            self.assertEqual(list(settings.uploads_dir.glob("*.mp4")), [])
            probe.assert_not_called()

    def test_cors_and_trailing_slash_remain_compatible(self):
        origin = "https://app.example.com"
        with self.client(authenticated=False, cors_origins=(origin,)) as (client, _, _), patch.object(
            api, "probe_video", return_value=SimpleNamespace(duration_ms=1000)
        ):
            preflight = client.options("/v1/jobs", headers={"Origin": origin,
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"})
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
            denied = client.post("/v1/jobs", headers={"Origin": origin}, files={"video": ("a.mp4", b"video")})
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(denied.headers["access-control-allow-origin"], origin)
            accepted = client.post("/v1/jobs/", headers={"Origin": origin, "Authorization": f"Bearer {client.auth_token}"},
                                   files={"video": ("a.mp4", b"video")})
            self.assertEqual(accepted.status_code, 202, accepted.text)
            self.assertEqual(accepted.history[0].status_code, 307)

    def test_progress_redaction_covers_full_job_and_status(self):
        message = r"failed to read C:\Users\Private Person\private-video.mp4"
        for public in (False, True):
            with self.subTest(public=public), self.client(
                public_base_url="https://api.example.com" if public else None
            ) as (client, settings, database):
                database.create_job(JOB_ID, "video.mp4", settings.uploads_dir / f"{JOB_ID}.mp4",
                                    settings.requests_dir / f"{JOB_ID}.json", settings.runs_dir / JOB_ID,
                                    user_id=client.auth_user_id)
                with database.connect() as connection:
                    connection.execute("UPDATE jobs SET status='failed', error=?, progress_json=? WHERE id=?",
                                       (message, json.dumps({"phase": "failed", "message": message}), JOB_ID))
                    connection.commit()
                for endpoint in (f"/v1/jobs/{JOB_ID}", f"/v1/jobs/{JOB_ID}/status", "/v1/jobs"):
                    response = client.get(endpoint, headers={"Authorization": f"Bearer {client.auth_token}"})
                    self.assertEqual(response.status_code, 200, response.text)
                    if public:
                        self.assertNotIn("Private Person", response.text)
                        self.assertNotIn("private-video.mp4", response.text)
                    else:
                        self.assertIn("Private Person", response.text)


if __name__ == "__main__":
    unittest.main()
