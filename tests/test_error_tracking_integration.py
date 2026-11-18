"""Exercise the real SDK in fresh processes so global integrations cannot cross tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SECRET = "sentry-fixture-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PRIVATE = "private-body-marker-98b214fa"
SOURCE_PRIVATE = "literal-source-secret-52d209ac"


@pytest.mark.parametrize("scenario", ["api", "worker", "telemetry", "disabled"])
def test_real_sentry_transport_boundary(scenario, tmp_path):
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith(("SENTRY_", "METRICS_", "ADMIN_")):
            environment.pop(name)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))),
        JWT_SECRET=SECRET, DATABASE_URL="", RALLYMATE_DATABASE_URL="",
        RALLYMATE_DATA_ROOT=str(tmp_path / "global"),
        RALLYMATE_DATABASE_PATH=str(tmp_path / "global" / "jobs.sqlite3"),
        RALLYMATE_ENV="development", AUTH_COOKIE_SECURE="false",
        RALLYMATE_WORKER_METRICS_PORT="0", SENTRY_TRACES_SAMPLE_RATE="1",
        SENTRY_ENVIRONMENT="sentry-integration", SENTRY_RELEASE="fixture-release",
        SENTRY_DSN="" if scenario == "disabled" else "https://fixture-key@sentry.invalid/1",
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), scenario, str(tmp_path)],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"{scenario}: passed"


def _transport():
    import sentry_sdk
    from sentry_sdk.transport import Transport
    from unittest.mock import patch

    class MemoryTransport(Transport):
        def __init__(self):
            super().__init__()
            self.envelopes = []

        def capture_envelope(self, envelope):
            self.envelopes.append(envelope)

    transport = MemoryTransport()
    real_init = sentry_sdk.init

    def initialize(*args, **kwargs):
        kwargs["transport"] = transport
        return real_init(*args, **kwargs)

    return transport, patch("sentry_sdk.init", side_effect=initialize)


def _events(transport, kind="event"):
    return [json.loads(item.payload.get_bytes()) for envelope in transport.envelopes
            for item in envelope.items if item.type == kind]


def _assert_private_absent(transport, *private):
    serialized = b"\n".join(envelope.serialize() for envelope in transport.envelopes)
    for index, value in enumerate((SECRET, PRIVATE, SOURCE_PRIVATE, *private)):
        assert str(value).encode() not in serialized, f"Private marker {index} reached the transport"


def _assert_stack(event, function):
    frames = [frame for exception in event["exception"]["values"]
              for frame in exception.get("stacktrace", {}).get("frames", [])]
    assert any(frame["function"] == function and frame.get("lineno", 0) > 0 for frame in frames)
    assert all(not set(frame).intersection({"vars", "context_line", "pre_context", "post_context"})
               for frame in frames)


def _application(tmp_path):
    from rallymate_service.api import create_app
    from rallymate_service.config import ServiceSettings
    from rallymate_service.database import JobDatabase
    from tests.service_auth import test_auth_config

    model = tmp_path / "fixture.pt"
    model.write_bytes(b"fixture")
    settings = ServiceSettings(data_root=tmp_path, database_path=tmp_path / "jobs.sqlite3",
                               detect_model=model, pose_model=model)
    database = JobDatabase(settings.database_path)
    return create_app(settings, database, auth_config=test_auth_config()), settings, database


def _api(tmp_path, transport):
    import sentry_sdk
    from fastapi.testclient import TestClient
    from tests.service_auth import login_test_user, TEST_PASSWORD

    app, _, _ = _application(tmp_path)

    @app.post("/v1/observability-test-error")
    async def private_failure(body: dict):
        sentry_sdk.add_breadcrumb(message=body["body"])
        sentry_sdk.set_extra("private_input", body)
        raise RuntimeError("literal-source-secret-52d209ac " + body["body"])

    @app.get("/observability-test-public-error")
    def anonymous_failure():
        raise RuntimeError("literal-source-secret-52d209ac")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/v1/observability-test-error", json={"body": PRIVATE}).status_code == 401
        user = login_test_user(client)
        session = client.cookies.get("rallymate_session")
        csrf = client.headers["X-CSRF-Token"]
        response = client.post("/v1/observability-test-error?token=" + PRIVATE,
                               json={"body": PRIVATE, "password": TEST_PASSWORD},
                               headers={"X-Private": PRIVATE})
        assert response.status_code == 500, response.status_code
        store = app.state.accounts
        admin = store.bootstrap("service-admin@example.com", TEST_PASSWORD)
        second = store.register("second-observer@example.com", TEST_PASSWORD, "Second observer")
        store.update_status(admin["id"], second["id"], "APPROVED")
        login = client.post("/api/auth/login", json={"email": second["email"], "password": TEST_PASSWORD})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        assert client.post("/v1/observability-test-error", json={"body": PRIVATE}).status_code == 500
        client.cookies.clear()
        assert client.get("/observability-test-public-error").status_code == 500
    sentry_sdk.flush()
    events = _events(transport)
    assert len(events) == 3, f"Expected one event per API error, received {len(events)}"
    event = events[0]
    assert event["user"] == {"id": user["id"]}, "Authenticated UUID missing from API exception"
    assert event["environment"] == "sentry-integration"
    assert event["release"] == "fixture-release"
    assert event["tags"]["service"] == "api"
    assert event["request"] == {"method": "POST", "url": "http://testserver/v1/observability-test-error"}
    _assert_stack(event, "private_failure")
    assert events[1]["user"] == {"id": second["id"]}, "Second account inherited the first account"
    assert events[2]["user"] == {}, "Anonymous request inherited an account"
    _assert_stack(events[2], "anonymous_failure")
    _assert_private_absent(transport, TEST_PASSWORD, user["email"], second["email"], session, csrf)
    assert _events(transport, "transaction"), "Configured request tracing must remain usable"


def _worker(tmp_path, transport):
    import logging
    import sentry_sdk
    from fastapi.testclient import TestClient
    from rallymate_service.observability import observe_worker
    from rallymate_service.worker import process_one
    from tests.service_auth import login_test_user, TEST_PASSWORD

    app, settings, database = _application(tmp_path)
    logger = logging.getLogger("rallymate.worker")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    with TestClient(app) as client:
        user = login_test_user(client)
        request_path = tmp_path / "request.json"
        request_path.write_text(json.dumps({"private": PRIVATE}), encoding="utf-8")
        database.create_job("sentry-observed-job", PRIVATE + ".mp4", settings.detect_model,
                            request_path, tmp_path / "output", user_id=user["id"])
        with observe_worker(settings, database):
            assert process_one(settings, database, "sentry-observer-worker")
        assert database.get_job("sentry-observed-job")["status"] == "failed"
    sentry_sdk.flush()
    events = [event for event in _events(transport) if event.get("logger") == "rallymate.worker"]
    assert len(events) == 1, f"Expected one worker error event, received {len(events)}"
    event = events[0]
    assert event["user"] == {"id": user["id"]}
    assert event["contexts"]["rallymate"] == {
        "job_id": "sentry-observed-job", "attempt": 1,
        "pose_backend": settings.pose_backend, "device": str(settings.device),
    }
    assert event["tags"]["service"] == "worker"
    assert event["environment"] == "sentry-integration"
    _assert_stack(event, "process_one")
    _assert_private_absent(transport, TEST_PASSWORD, user["email"])


def _telemetry(tmp_path, transport):
    import sentry_sdk
    from types import SimpleNamespace
    from rallymate_service.error_tracking import initialize_error_tracking

    initialize_error_tracking(SimpleNamespace(environment="development"), "api")
    sentry_sdk.set_user({"id": "safe-user-id", "email": PRIVATE})
    sentry_sdk.set_context("private", {"data": PRIVATE})
    sentry_sdk.add_breadcrumb(message=PRIVATE)
    sentry_sdk.get_current_scope().add_attachment(bytes=PRIVATE.encode(), filename="input.txt")
    with sentry_sdk.start_transaction(name=PRIVATE, op="http.server"):
        with sentry_sdk.start_span(op="gen_ai.chat", description=PRIVATE) as span:
            span.set_data("gen_ai.prompt", PRIVATE)
        sentry_sdk.logger.error(PRIVATE, attributes={"body": PRIVATE})
        sentry_sdk.metrics.count("fixture", 1, attributes={"body": PRIVATE})
        try:
            raise ValueError("literal-source-secret-52d209ac")
        except ValueError:
            sentry_sdk.capture_exception()
    sentry_sdk.flush()
    kinds = {item.type for envelope in transport.envelopes for item in envelope.items}
    assert kinds <= {"event", "transaction", "client_report"}, f"Unscrubbed envelope channels: {sorted(kinds)}"
    assert {"event", "transaction"} <= kinds
    _assert_stack(_events(transport)[0], "_telemetry")
    _assert_private_absent(transport)


def _disabled(tmp_path):
    import sentry_sdk
    from types import SimpleNamespace
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from rallymate_service.error_tracking import initialize_error_tracking, request_scope, set_account_context

    with patch("sentry_sdk.init") as initialize, patch("socket.create_connection") as connect:
        initialize_error_tracking(SimpleNamespace(environment="development"), "worker")
        app, _, _ = _application(tmp_path)
        with TestClient(app) as client:
            assert client.get("/v1/jobs/example").status_code == 401
        with request_scope():
            set_account_context({"id": "safe-user-id"})
            assert sentry_sdk.capture_exception(RuntimeError(PRIVATE)) is None
        initialize.assert_not_called()
        connect.assert_not_called()
        assert not sentry_sdk.get_client().is_active()


if __name__ == "__main__":
    from unittest.mock import patch
    scenario, directory = sys.argv[1:]
    if scenario == "disabled":
        _disabled(Path(directory))
    else:
        transport, initialize = _transport()
        with initialize, patch("sentry_sdk.transport.HttpTransport._send_request") as network:
            globals()["_" + scenario](Path(directory), transport)
            network.assert_not_called()
    print(f"{scenario}: passed")
