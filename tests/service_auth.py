"""Real approved-user sessions for existing service regression fixtures."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import or_, select, update

from rallymate_service.auth_runtime import AuthConfig
from rallymate_service.models import jobs, users


TEST_SECRET = "test-only-session-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TEST_PASSWORD = "FixturePassword123!"
TEST_EMAIL = "service-fixture@example.com"


def test_auth_config(origin="http://testserver"):
    return AuthConfig(TEST_SECRET, public_origin=origin)


test_auth_config.__test__ = False


def assign_test_jobs(app, user_id: str, job_ids) -> None:
    identifiers = tuple(dict.fromkeys(job_ids))
    if not identifiers:
        return
    with app.state.account_runtime.engine.begin() as connection:
        result = connection.execute(update(jobs).where(
            jobs.c.id.in_(identifiers),
            or_(jobs.c.user_id.is_(None), jobs.c.user_id == user_id),
        ).values(user_id=user_id))
        if result.rowcount != len(identifiers):
            raise AssertionError("Every explicit fixture job must exist and belong to this test user or nobody")


def login_test_user(client: TestClient, *, job_ids=()) -> dict:
    app = client.app
    store = app.state.accounts
    admin = store.bootstrap("service-admin@example.com", TEST_PASSWORD)
    with app.state.account_runtime.engine.connect() as connection:
        user_id = connection.scalar(select(users.c.id).where(users.c.email == TEST_EMAIL))
    if user_id is None:
        user = store.register(TEST_EMAIL, TEST_PASSWORD, "Service fixture")
        user = store.update_status(admin["id"], user["id"], "APPROVED")
    else:
        user = store.by_id(user_id)
        if user is None or user["status"] != "APPROVED" or user["role"] != "USER":
            raise AssertionError("Existing service fixture account must be an approved ordinary user")
    origin = app.state.auth_config.public_origin or str(client.base_url).rstrip("/")
    response = client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                           headers={"Origin": origin})
    if response.status_code != 200:
        raise AssertionError(f"Fixture login failed: {response.status_code}: {response.text}")
    client.headers.update({"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]})
    assign_test_jobs(app, user["id"], job_ids)
    return user


@contextmanager
def authenticated_client(app, *, job_ids=(), **kwargs):
    with TestClient(app, **kwargs) as client:
        login_test_user(client, job_ids=job_ids)
        yield client
