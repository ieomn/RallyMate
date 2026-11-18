"""Full API authorization tests; all storage and transport fixtures stay local."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from rallymate_service.database import JobDatabase
from rallymate_service.migrations import migrate_database
from rallymate_service.models import jobs, upload_owners
from rallymate_service.uploads import MIN_CHUNK_BYTES


SECRET = "test-only-boundary-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PASSWORD = "BoundaryAccount123!"
ORIGIN = "http://rallymate.test"
INTERNAL_KEY = "boundary-internal-service-key"


class AuthBoundaryTests(unittest.TestCase):
    postgres = False

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {
            "JWT_SECRET": SECRET, "DATABASE_URL": "", "RALLYMATE_DATABASE_URL": "",
            "ADMIN_EMAIL": "", "ADMIN_PASSWORD": "", "AUTH_COOKIE_SECURE": "false",
            "RALLYMATE_DATA_ROOT": str(self.root / "global-import"),
            "RALLYMATE_DATABASE_PATH": str(self.root / "global-import/jobs.sqlite3"),
            "RALLYMATE_UPLOAD_CHUNK_BYTES": str(MIN_CHUNK_BYTES),
        }))
        # api's module-level app also reads environment settings, so import only after isolation.
        from rallymate_service import api
        from rallymate_service.auth_runtime import AuthConfig
        from rallymate_service.config import ServiceSettings
        self.api = api
        detect, pose = self.root / "detect.pt", self.root / "pose.pt"
        detect.touch()
        pose.touch()
        self.settings = ServiceSettings(data_root=self.root / "service",
            database_path=self.root / "service/jobs.sqlite3", detect_model=detect, pose_model=pose,
            api_key=INTERNAL_KEY, max_upload_bytes=4 * 1024 * 1024)
        if self.postgres:
            url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg")
            base = create_engine(url, hide_parameters=True)
            self.stack.callback(base.dispose)
            schema = "auth_boundary_test_" + uuid4().hex
            with base.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            def drop_schema():
                with base.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            self.stack.callback(drop_schema)
            query = dict(url.query)
            query["options"] = str(query.get("options", "")) + f" -csearch_path={schema}"
            url = url.set(query=query)
            migrate_database(url)
            from rallymate_service.postgres_database import PostgresJobDatabase
            self.delegate = PostgresJobDatabase(url.render_as_string(hide_password=False))
            self.stack.callback(self.delegate.dispose)
        else:
            self.delegate = JobDatabase(self.settings.database_path)
        self.executor = {"status": "ready", "available": True, "heartbeat_at": None}
        self.stop_calls = []
        def stop_executor():
            self.stop_calls.append(True)
            self.executor.update(status="stopped", available=False)
            return dict(self.executor)
        self.app = api.create_app(self.settings, self.delegate,
            auth_config=AuthConfig(SECRET, public_origin=ORIGIN),
            executor_status=lambda: dict(self.executor), executor_shutdown=stop_executor)
        self.client = self.stack.enter_context(TestClient(self.app, base_url=ORIGIN,
            client=("127.0.0.1", 50000)))
        self.store = self.app.state.accounts
        self.engine = self.app.state.account_runtime.engine
        self.admin = self.store.bootstrap("admin@example.com", PASSWORD)
        self.user_a = self.store.register("a@example.com", PASSWORD, "User A")
        self.user_b = self.store.register("b@example.com", PASSWORD, "User B")
        for user in (self.user_a, self.user_b):
            self.store.update_status(self.admin["id"], user["id"], "APPROVED")

    def new_client(self):
        client = TestClient(self.app, base_url=ORIGIN, client=("127.0.0.1", 50001))
        self.stack.callback(client.close)
        return client

    def login(self, email="a@example.com"):
        client = self.new_client()
        response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD},
            headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client

    def seed_job(self, user_id=None, *, content=b"0123456789abcdefghij", created_at=None):
        identifier = str(uuid4())
        video = self.settings.uploads_dir / (identifier + ".mp4")
        request = self.settings.requests_dir / (identifier + ".json")
        output = self.settings.runs_dir / identifier
        video.write_bytes(content)
        request.write_text("{}", encoding="utf-8")
        output.mkdir()
        self.delegate.create_job(identifier, "fixture.mp4", video, request, output, user_id=user_id)
        summary = {"schema_version": "1.0.0", "job_id": identifier,
            "input": {"video_path": str(video), "upstream_metadata": {"upload_bytes": len(content)}},
            "minimum_scoring_loop": {"provenance": {"pipeline_job_id": identifier,
                "video_path": str(video), "video_sha256": hashlib.sha256(content).hexdigest()}}}
        raw_summary = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
        (output / "summary.json").write_bytes(raw_summary)
        values = {"status": "succeeded", "summary_json": raw_summary.decode(), "attempts": 1}
        if created_at is not None:
            values["created_at"] = created_at
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == identifier).values(**values))
        return identifier, raw_summary

    def v1_routes(self, *, owned_only=False):
        return [route for route in self.app.routes if isinstance(route, APIRoute)
            and route.path.startswith("/v1/")
            and (not owned_only or "{job_id}" in route.path or "{upload_id}" in route.path)]

    @staticmethod
    def concrete_path(route, job_id, upload_id):
        replacements = {"job_id": job_id, "upload_id": upload_id, "artifact_name": "summary.json",
            "episode_id": "0", "event_id": "0", "index": "0"}
        return re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda match: replacements[match.group(1)], route.path)

    @contextmanager
    def unreachable_routes(self, routes):
        called = []
        async def forbidden(scope, receive, send):
            called.append(scope["path"])
            raise AssertionError("Unauthorized request reached a core API handler")
        with ExitStack() as patches:
            for route in routes:
                patches.enter_context(patch.object(route, "app", forbidden))
            yield
        self.assertEqual(called, [])

    def check_route_denials(self, client, routes, expected, job_id, upload_id, *, prefix=""):
        for route in routes:
            for method in sorted(route.methods - {"OPTIONS"}):
                path = prefix + self.concrete_path(route, job_id, upload_id)
                with self.subTest(method=method, path=route.path, expected=expected):
                    response = client.request(method, path, content=b"unparsed transport body")
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_all_v1_routes_reject_anonymous_and_unapproved_before_core(self):
        routes = self.v1_routes()
        self.assertGreater(len(routes), 25)
        identifier = str(uuid4())
        with self.unreachable_routes(routes):
            self.check_route_denials(self.client, routes, 401, identifier, identifier)
            self.client.headers["Authorization"] = "Bearer " + INTERNAL_KEY
            self.check_route_denials(self.client, routes, 401, identifier, identifier)
            self.client.headers.pop("Authorization")
            for status in ("PENDING", "REJECTED", "SUSPENDED"):
                user = self.store.register(status.lower() + "@example.com", PASSWORD, status)
                if status != "PENDING":
                    self.store.update_status(self.admin["id"], user["id"], status)
                client = self.login(user["email"])
                self.check_route_denials(client, routes, 403, identifier, identifier)

    def test_all_job_and_upload_routes_reject_other_owner_before_core(self):
        identifier, _ = self.seed_job(self.user_a["id"])
        owner, outsider = self.login(), self.login("b@example.com")
        upload_id = str(uuid4())
        payload = {"upload_id": upload_id, "filename": "fixture.mp4", "size": 3}
        self.assertEqual(owner.post("/v1/uploads", json=payload).status_code, 201)
        routes = self.v1_routes(owned_only=True)
        self.assertGreater(len(routes), 20)
        with self.unreachable_routes(routes):
            self.check_route_denials(outsider, routes, 404, identifier, upload_id)
        self.assertEqual(outsider.post("/v1/uploads", json=payload).status_code, 404)
        self.assertEqual(outsider.get("/api/records").json()["total"], 0)

    def test_personal_history_paginates_and_hides_unowned_history(self):
        first, _ = self.seed_job(self.user_a["id"], created_at="2026-11-17T01:00:00+00:00")
        second, _ = self.seed_job(self.user_a["id"], created_at="2026-11-17T02:00:00+00:00")
        foreign, _ = self.seed_job(self.user_b["id"])
        legacy, _ = self.seed_job()
        client = self.login()
        first_page = client.get("/api/records?limit=1&offset=0")
        second_page = client.get("/api/records?limit=1&offset=1")
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.json()["total"], 2)
        self.assertEqual([row["id"] for row in first_page.json()["items"]], [second])
        self.assertEqual([row["id"] for row in second_page.json()["items"]], [first])
        self.assertEqual(set(first_page.json()["items"][0]) & {"summary", "summary_json", "video_path", "request_path", "output_dir"}, set())
        self.assertEqual({row["id"] for row in client.get("/v1/jobs").json()["items"]}, {first, second})
        self.assertEqual(client.get(f"/v1/jobs/{foreign}/status").status_code, 404)
        self.assertEqual(client.get(f"/v1/jobs/{legacy}/status").status_code, 404)
        self.assertEqual(client.get("/api/records?offset=-1").status_code, 422)

    def test_cookie_writes_require_same_origin_and_correct_csrf(self):
        client = self.login()
        payload = {"upload_id": str(uuid4()), "filename": "fixture.mp4", "size": 3}
        valid_csrf = client.headers.pop("X-CSRF-Token")
        for headers in ({}, {"X-CSRF-Token": "incorrect"},
                {"X-CSRF-Token": valid_csrf, "Origin": "https://untrusted.example"}):
            self.assertEqual(client.post("/v1/uploads", json=payload, headers=headers).status_code, 403)
        client.headers.pop("Origin")
        self.assertEqual(client.post("/v1/uploads", json=payload, headers={"X-CSRF-Token": valid_csrf}).status_code, 403)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(select(func.count()).select_from(upload_owners)), 0)
        self.assertEqual(client.post("/v1/uploads", json=payload,
            headers={"Origin": ORIGIN, "X-CSRF-Token": valid_csrf}).status_code, 201)

    def test_real_chunks_enqueue_once_bind_owner_and_preserve_private_artifacts(self):
        client, outsider = self.login(), self.login("b@example.com")
        identifier = str(uuid4())
        content = b"video transport fixture" * 12000
        payload = {"upload_id": identifier, "filename": "phone.mp4", "size": len(content),
            "options": {"court_mode": "disabled"}}
        created = client.post("/v1/uploads", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        chunk_size = created.json()["chunk_size"]
        upload_url = f"/v1/uploads/{identifier}"
        self.assertEqual(client.post(upload_url + "/complete").status_code, 409)
        for index in reversed(range(created.json()["chunk_count"])):
            chunk = content[index * chunk_size:(index + 1) * chunk_size]
            headers = {"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(), "X-Upload-Acknowledgement": "chunk-v1"}
            sent = client.put(upload_url + f"/chunks/{index}", content=chunk, headers=headers)
            self.assertEqual(sent.status_code, 200, sent.text)
            self.assertEqual(sent.json()["acknowledgement"], "chunk-v1")
            self.assertIsNone(self.delegate.get_job(identifier))
        self.assertEqual(client.get(upload_url).json()["received_bytes"], len(content))
        with patch.object(self.api, "probe_video", return_value=SimpleNamespace(duration_ms=1000)) as probe:
            with patch.object(self.delegate, "create_job", wraps=self.delegate.create_job) as create:
                submitted = client.post(upload_url + "/complete")
                self.assertEqual(submitted.status_code, 202, submitted.text)
                self.assertEqual(client.post(upload_url + "/complete").status_code, 202)
            self.assertEqual(create.call_count, 1)
            self.assertEqual(create.call_args.kwargs["user_id"], self.user_a["id"])
            self.assertEqual(probe.call_count, 1)
        job = self.delegate.get_job(identifier)
        self.assertEqual(job["user_id"], self.user_a["id"])
        self.assertEqual(Path(job["video_path"]).read_bytes(), content)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(select(upload_owners.c.user_id).where(upload_owners.c.upload_id == identifier)), job["user_id"])
        self.assertEqual(outsider.get(upload_url).status_code, 404)
        self.assertEqual(client.delete(f"/v1/jobs/{identifier}").status_code, 409)
        summary = {"job_id": identifier,
            "input": {"video_path": job["video_path"], "upstream_metadata": {"upload_bytes": len(content)}},
            "minimum_scoring_loop": {"provenance": {"pipeline_job_id": identifier,
                "video_path": job["video_path"], "video_sha256": hashlib.sha256(content).hexdigest()}}}
        raw = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
        output = Path(job["output_dir"])
        output.mkdir(parents=True)
        (output / "summary.json").write_bytes(raw)
        with self.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.id == identifier).values(status="succeeded", summary_json=raw.decode(), attempts=1))
        original_url = f"/v1/jobs/{identifier}/original-video"
        artifact_url = f"/v1/jobs/{identifier}/artifacts/summary.json"
        partial = client.get(original_url, headers={"Range": "bytes=2-8"})
        self.assertEqual(partial.status_code, 206, partial.text)
        self.assertEqual(partial.content, content[2:9])
        self.assertEqual(partial.headers["content-range"], f"bytes 2-8/{len(content)}")
        artifact = client.get(artifact_url)
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(artifact.content, raw)
        self.assertEqual(artifact.headers["cache-control"], "private, no-store")
        self.assertEqual(outsider.get(original_url).status_code, 404)
        self.assertEqual(outsider.get(artifact_url).status_code, 404)
        deleted = client.delete(f"/v1/jobs/{identifier}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        with self.unreachable_routes(self.v1_routes(owned_only=True)):
            self.check_route_denials(client, self.v1_routes(owned_only=True), 404, identifier, identifier)
        self.assertEqual(client.get("/api/records").json()["total"], 0)
        self.assertEqual(Path(job["video_path"]).read_bytes(), content)
        self.assertEqual((output / "summary.json").read_bytes(), raw)

    def test_logout_and_admin_revocation_close_previously_accessible_urls(self):
        identifier, _ = self.seed_job(self.user_a["id"])
        client = self.login()
        paths = (f"/v1/jobs/{identifier}/status", f"/v1/jobs/{identifier}/original-video",
            f"/v1/jobs/{identifier}/artifacts/summary.json", "/api/records")
        for path in paths:
            self.assertEqual(client.get(path).status_code, 200)
        old_cookie = client.cookies.get("rallymate_session")
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        for path in paths:
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Cookie": "rallymate_session=" + old_cookie}).status_code, 401)
        fresh = self.login()
        self.store.update_status(self.admin["id"], self.user_a["id"], "SUSPENDED")
        for path in paths:
            self.assertEqual(fresh.get(path).status_code, 401)
        suspended = self.login()
        for path in paths:
            self.assertEqual(suspended.get(path).status_code, 403)

    def test_account_jwt_cannot_replace_internal_maintenance_key(self):
        client = self.login()
        url = "/internal/maintenance/stop-executor"
        self.assertEqual(client.post(url).status_code, 401)
        token = client.cookies.get("rallymate_session")
        self.assertEqual(client.post(url, headers={"Authorization": "Bearer " + token}).status_code, 401)
        self.assertEqual(self.stop_calls, [])
        response = self.client.post(url, headers={"Authorization": "Bearer " + INTERNAL_KEY})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.stop_calls), 1)

    def test_mounts_and_proxy_root_paths_keep_account_and_ownership_boundaries(self):
        identifier, raw = self.seed_job(self.user_a["id"])
        host = FastAPI()
        host.mount("/service", self.app)
        cases = (
            ("mounted", host, "", "/service"),
            ("proxy_prefix", self.app, "/proxy", "/proxy"),
            ("proxy_stripped", self.app, "/proxy", ""),
            ("proxy_and_mount", host, "/proxy", "/proxy/service"),
        )
        for name, application, root_path, prefix in cases:
            with self.subTest(deployment=name):
                def new_browser():
                    client = TestClient(application, base_url=ORIGIN, root_path=root_path,
                        client=("127.0.0.1", 50002))
                    self.stack.callback(client.close)
                    return client
                anonymous = new_browser()
                all_routes = self.v1_routes()
                with self.unreachable_routes(all_routes):
                    self.check_route_denials(anonymous, all_routes, 401, identifier, identifier, prefix=prefix)
                    anonymous.headers["Authorization"] = "Bearer " + INTERNAL_KEY
                    self.check_route_denials(anonymous, all_routes, 401, identifier, identifier, prefix=prefix)

                def authenticated_browser(email):
                    client = new_browser()
                    response = client.post(prefix + "/api/auth/login",
                        json={"email": email, "password": PASSWORD}, headers={"Origin": ORIGIN})
                    self.assertEqual(response.status_code, 200, response.text)
                    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
                    return client
                owner = authenticated_browser("a@example.com")
                outsider = authenticated_browser("b@example.com")
                self.assertEqual(owner.get(prefix + f"/v1/jobs/{identifier}/status").status_code, 200)
                self.assertEqual(owner.get(prefix + "/api/records").json()["total"], 1)
                original = owner.get(prefix + f"/v1/jobs/{identifier}/original-video", headers={"Range": "bytes=0-2"})
                self.assertEqual(original.status_code, 206)
                self.assertEqual(original.content, b"012")
                artifact = owner.get(prefix + f"/v1/jobs/{identifier}/artifacts/summary.json")
                self.assertEqual(artifact.status_code, 200)
                self.assertEqual(artifact.content, raw)
                upload_id = str(uuid4())
                payload = {"upload_id": upload_id, "filename": "fixture.mp4", "size": 3}
                created = owner.post(prefix + "/v1/uploads", json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(owner.get(prefix + f"/v1/uploads/{upload_id}").status_code, 200)
                owned_routes = self.v1_routes(owned_only=True)
                with self.unreachable_routes(owned_routes):
                    self.check_route_denials(outsider, owned_routes, 404, identifier, upload_id, prefix=prefix)
                self.assertEqual(outsider.post(prefix + "/v1/uploads", json=payload).status_code, 404)
                self.assertEqual(owner.post(prefix + "/v1/uploads", json=payload,
                    headers={"Origin": "https://untrusted.example"}).status_code, 403)


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
class PostgreSQLAuthBoundaryTests(AuthBoundaryTests):
    postgres = True


if __name__ == "__main__":
    unittest.main()

