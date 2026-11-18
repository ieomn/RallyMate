from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest
from starlette.responses import StreamingResponse

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.error_tracking import scrub_event
from rallymate_service.observability import HttpMetricsMiddleware, WorkerLogObserver, observe_worker
from rallymate_service.worker import process_one
from tests.service_auth import test_auth_config as auth_config


TOKEN = "metrics-test-only-token-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", TOKEN)
    model = tmp_path / "fixture.pt"
    model.write_bytes(b"fixture")
    settings = ServiceSettings(data_root=tmp_path, database_path=tmp_path / "jobs.sqlite3",
                               detect_model=model, pose_model=model)
    database = JobDatabase(settings.database_path)
    app = create_app(settings, database, auth_config=auth_config())
    with TestClient(app) as client:
        yield client, app, database, settings


def test_metrics_auth_route_cardinality_and_database_state(api):
    client, app, database, _ = api
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/jobs/private-one").status_code == 401
    assert client.get("/v1/jobs/private-two").status_code == 401
    assert client.get("/unknown-secret-path").status_code == 404
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert 'route="/v1/jobs/{job_id}",status="401"} 2.0' in body
    assert "private-one" not in body and "private-two" not in body
    assert "unknown-secret-path" not in body and TOKEN not in body
    assert "rallymate_database_available 1.0" in body
    assert 'rallymate_jobs{status="queued"} 0.0' in body
    assert "rallymate_job_attempts_total 0.0" in body
    app.state.account_runtime.ready = False
    assert "rallymate_database_available 0.0" in client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).text


def test_metrics_disabled_and_token_file_validation(tmp_path, monkeypatch):
    from rallymate_service.observability import install_api_observability
    app = FastAPI()
    app.state.account_runtime = SimpleNamespace(ready=False)
    settings = SimpleNamespace(environment="development")
    install_api_observability(app, settings)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404
    token_file = tmp_path / "metrics_token"
    token_file.write_text("weak", encoding="utf-8")
    monkeypatch.setenv("METRICS_TOKEN_FILE", str(token_file))
    with pytest.raises(ValueError, match="32 random"):
        install_api_observability(FastAPI(), settings)
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    app = FastAPI()
    app.state.account_runtime = SimpleNamespace(ready=False)
    install_api_observability(app, settings)
    with TestClient(app) as client:
        assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_response_body_time_includes_stream_and_cancellation():
    async def exchange(disconnect):
        registry = CollectorRegistry()
        app = FastAPI()
        @app.get("/stream/{identifier}")
        def route(identifier):
            return StreamingResponse(iter([b"first", b"second"]))
        clock = [0.0]
        async def inner(scope, receive, send):
            clock[0] = 2.0
            await send({"type": "http.response.start", "status": 200, "headers": []})
            clock[0] = 7.0
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            if disconnect:
                raise asyncio.CancelledError()
            clock[0] = 11.0
            await send({"type": "http.response.body", "body": b"second"})
        async def send(message):
            pass
        async def receive():
            return {"type": "http.request", "body": b""}
        middleware = HttpMetricsMiddleware(inner, application=app, registry=registry)
        from unittest.mock import patch
        with patch("rallymate_service.observability.time.perf_counter", side_effect=lambda: clock[0]):
            try:
                await middleware({"type": "http", "path": "/stream/private", "root_path": "", "method": "GET"}, receive, send)
            except asyncio.CancelledError:
                pass
        return generate_latest(registry).decode()
    complete = asyncio.run(exchange(False))
    assert 'rallymate_http_time_to_headers_seconds_sum{method="GET",route="/stream/{identifier}"} 2.0' in complete
    assert 'rallymate_http_request_duration_seconds_sum{method="GET",route="/stream/{identifier}"} 11.0' in complete
    assert 'status="200"} 1.0' in complete
    cancelled = asyncio.run(exchange(True))
    assert 'status="499"} 1.0' in cancelled
    assert 'rallymate_http_request_duration_seconds_sum{method="GET",route="/stream/{identifier}"} 7.0' in cancelled


def test_real_worker_failure_produces_one_attempt_and_safe_user_context(api, tmp_path, caplog):
    _, app, database, settings = api
    store = app.state.account_runtime.store
    user = store.register("observer@example.com", "ObserverPassword123!", "Observer", None)
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    database.create_job("observed-job", "private-video.mp4", settings.detect_model,
                        request_path, tmp_path / "output", user_id=user["id"])
    registry = CollectorRegistry()
    ticks = iter([10.0, 14.5])
    observer = WorkerLogObserver(database, settings, registry, clock=lambda: next(ticks))
    logger = logging.getLogger("rallymate.worker")
    logger.addHandler(observer)
    try:
        with caplog.at_level(logging.INFO, logger="rallymate.worker"):
            assert process_one(settings, database, "observer-worker")
    finally:
        logger.removeHandler(observer)
    body = generate_latest(registry).decode()
    assert 'rallymate_inference_attempts_total{status="failed"} 1.0' in body
    assert 'rallymate_inference_attempt_duration_seconds_sum{status="failed"} 4.5' in body
    record = next(record for record in caplog.records if record.msg == "job %s failed")
    event = scrub_event({"exception": {"values": [{"type": "ValidationError", "value": "private-video.mp4"}]}}, {"log_record": record})
    assert event["user"] == {"id": user["id"]}
    assert event["contexts"]["rallymate"]["job_id"] == "observed-job"
    assert event["contexts"]["rallymate"]["attempt"] == 1
    assert "private-video.mp4" not in json.dumps(event)
    assert observer.active == {}


def test_error_tracking_removes_sensitive_data_but_retains_stack():
    private = "temporary password session request and video bytes"
    event = {"request": {"method": "POST", "url": "https://user:password@example.com/api/auth/login?token=private#x",
                         "data": private, "headers": {"cookie": private}, "cookies": private},
             "user": {"id": "user-uuid", "email": private, "ip_address": private},
             "extra": {"password": private}, "breadcrumbs": {"values": [private]},
             "spans": [{"description": private}], "logentry": {"formatted": private}, "message": private,
             "contexts": {"rallymate": {"job_id": "job-id", "attempt": 1, "password": private}, "custom": private},
             "tags": {"service": "worker", "email": private},
             "exception": {"values": [{"type": "RuntimeError", "value": private, "stacktrace": {"frames": [
                 {"filename": "worker.py", "lineno": 432, "function": "process_one", "vars": {"password": private},
                  "pre_context": [private], "post_context": [private]}]}}]}}
    clean = scrub_event(event)
    assert private not in json.dumps(clean)
    assert clean["request"] == {"method": "POST", "url": "https://example.com/api/auth/login"}
    assert clean["user"] == {"id": "user-uuid"}
    assert clean["exception"]["values"][0]["stacktrace"]["frames"][0] == {
        "filename": "worker.py", "lineno": 432, "function": "process_one"}


def test_worker_observer_cleanup_and_no_sentry_without_dsn(api, monkeypatch):
    _, _, database, settings = api
    from unittest.mock import patch
    logger = logging.getLogger("rallymate.worker")
    original = list(logger.handlers)
    with patch("sentry_sdk.init") as initialize:
        with pytest.raises(RuntimeError, match="fixture"):
            with observe_worker(settings, database):
                assert len(logger.handlers) == len(original) + 1
                raise RuntimeError("fixture")
        initialize.assert_not_called()
    assert logger.handlers == original
