from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL

from rallymate_service.accounts import AccountStore
from rallymate_service.auth_api import authenticate_request, install_auth_routes
from rallymate_service.migrations import migrate_database
from rallymate_service.models import admin_audit, sessions, users


ORIGIN = "https://rallymate.example"
PASSWORD = "TennisAccount123!"
ADMIN_PASSWORD = "AdminAccount456!"
SECRET = "test-only-credential-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
USER_FIELDS = {"id", "email", "username", "role", "status", "application_reason",
               "created_at", "last_login_at", "must_change_password"}


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        url = URL.create("sqlite", database=str(Path(temporary.name) / "accounts.sqlite3"))
        migrate_database(url)
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        self.store = AccountStore(self.engine, SECRET)
        self.admin = self.store.bootstrap("admin@example.com", ADMIN_PASSWORD)
        self.config = SimpleNamespace(secret=SECRET, session_days=7, cookie_secure=True,
                                      public_origin=ORIGIN, cookie_name="rallymate_session")
        self.app = FastAPI()
        install_auth_routes(self.app, self.store, self.config)

        @self.app.api_route("/protected", methods=["GET", "POST"])
        def protected(request: Request):
            session = authenticate_request(request, self.store, self.config)
            return {"user_id": session["user"]["id"]}

        self.client = self.new_client()

    def new_client(self):
        client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(client.close)
        return client

    def register(self, email="player@example.com", *, client=None, **extra):
        response = (client or self.client).post("/api/auth/register", json={
            "email": email, "password": PASSWORD, "username": "网球用户", **extra,
        }, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def login(self, email="player@example.com", password=PASSWORD, *, client=None):
        response = (client or self.client).post("/api/auth/login", json={
            "email": email, "password": password,
        }, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    @staticmethod
    def headers(login):
        return {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}

    def approve(self, user):
        self.store.update_status(self.admin["id"], user["id"], "APPROVED")

    def admin_client(self):
        client = self.new_client()
        return client, self.login("admin@example.com", ADMIN_PASSWORD, client=client)

    def test_registration_opens_regular_account_and_signs_in_immediately(self):
        registration = self.register("  Player@Example.com  ")
        user = registration.json()["user"]
        self.assertEqual(set(user), USER_FIELDS)
        self.assertEqual(user["email"], "player@example.com")
        self.assertEqual((user["role"], user["status"]), ("USER", "APPROVED"))
        self.assertIsNone(user["application_reason"])
        self.assertGreaterEqual(user["last_login_at"], user["created_at"])
        for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=604800"):
            self.assertIn(attribute, registration.headers["set-cookie"])
        self.assertEqual(set(registration.json()), {"user", "csrf_token", "expires_at"})
        self.assertEqual(registration.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/api/auth/me").json(), registration.json())
        self.assertEqual(self.client.get("/protected").status_code, 200)
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", headers=self.headers(registration)).status_code, 200)
        login = self.login()
        self.assertEqual(set(login.json()), set(registration.json()))
        self.assertEqual(self.client.get("/protected").status_code, 200)

    def test_preexisting_pending_account_needs_approval_to_open_workspace(self):
        user = self.register(application_reason=" 想学习网球 ").json()["user"]
        self.assertEqual(user["application_reason"], "想学习网球")
        self.store.update_status(self.admin["id"], user["id"], "PENDING")
        self.login()
        denied = self.client.get("/protected")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "account_pending")
        self.approve(user)
        self.assertEqual(self.client.get("/protected").status_code, 200)

    def test_unauthenticated_requests_and_invalid_inputs_do_not_expose_passwords(self):
        denied = self.client.get("/api/auth/me")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers["cache-control"], "no-store")
        variants = [
            {"role": "ADMIN"}, {"status": "APPROVED"}, {"password_hash": "injected"},
            {"username": ["not-a-string"]}, {"email": "not-an-email"},
            {"password": "short9"}, {"password": "A" * 12}, {"username": "   "},
        ]
        for changes in variants:
            payload = {"email": "player@example.com", "password": PASSWORD, "username": "Player", **changes}
            response = self.client.post("/api/auth/register", json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(payload["password"], response.text)
            self.assertNotIn("password_hash", str(self.store.list_users()["items"]))
        self.assertEqual(self.store.list_users()["total"], 1)

    def test_duplicate_registration_and_wrong_login_have_clean_errors(self):
        self.register(client=self.new_client())
        duplicate = self.client.post("/api/auth/register", json={"email": "PLAYER@example.com", "password": PASSWORD, "username": "Different"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn("set-cookie", duplicate.headers)
        for email in ("player@example.com", "absent@example.com"):
            response = self.client.post("/api/auth/login", json={"email": email, "password": "WrongPassword123"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"]["code"], "invalid_credentials")
            self.assertNotIn("WrongPassword123", response.text)

    def test_registration_rate_limit_does_not_open_another_account_or_session(self):
        for index in range(5):
            self.register(f"player-{index}@example.com", client=self.new_client())
        limited = self.client.post("/api/auth/register", json={
            "email": "another@example.com", "password": PASSWORD, "username": "Another",
        }, headers={"Origin": ORIGIN})
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["code"], "rate_limited")
        self.assertNotIn("set-cookie", limited.headers)
        self.assertEqual(self.store.list_users()["total"], 6)  # 5 standard users and the bootstrap admin.

    def test_cross_site_public_writes_are_blocked_and_nonbrowser_login_is_allowed(self):
        payload = {"email": "player@example.com", "password": PASSWORD, "username": "Player"}
        for origin in ("https://attacker.example", "null", ORIGIN + ".attacker.example", "https://rallymate.example:8443"):
            response = self.client.post("/api/auth/register", json=payload, headers={"Origin": origin})
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/auth/register", json=payload, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/api/auth/register", json=payload).status_code, 201)
        login = self.new_client().post("/api/auth/login", json={"email": payload["email"], "password": PASSWORD})
        self.assertEqual(login.status_code, 200)

    def test_development_origin_uses_host_when_application_is_mounted(self):
        self.config.public_origin = None
        application = FastAPI()
        application.mount("/service", self.app)
        client = TestClient(application, base_url=ORIGIN)
        self.addCleanup(client.close)
        payload = {"email": "admin@example.com", "password": ADMIN_PASSWORD}
        denied = client.post("/service/api/auth/login", json=payload, headers={"Origin": "https://attacker.example"})
        self.assertEqual(denied.status_code, 403)
        allowed = client.post("/service/api/auth/login", json=payload, headers={"Origin": ORIGIN})
        self.assertEqual(allowed.status_code, 200)

    def test_cookie_writes_require_both_origin_and_csrf(self):
        login = self.register()
        variants = [{}, {"Origin": ORIGIN}, {"X-CSRF-Token": login.json()["csrf_token"]},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": login.json()["csrf_token"]},
                    {"Origin": ORIGIN, "X-CSRF-Token": "wrong"}]
        for headers in variants:
            response = self.client.post("/protected", headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/protected", headers=self.headers(login)).status_code, 200)

    def test_bearer_jwt_and_cookie_precedence_with_gateway_key(self):
        login = self.register()
        token = self.client.cookies.get(self.config.cookie_name)
        client = self.new_client()
        bearer = {"Authorization": "Bearer " + token}
        self.assertEqual(client.get("/protected", headers=bearer).status_code, 200)
        self.assertEqual(client.post("/protected", headers=bearer).status_code, 200)
        self.assertEqual(client.post("/protected", headers={**bearer, "Origin": "https://attacker.example"}).status_code, 403)
        gateway = {**self.headers(login), "Authorization": "Bearer shared-gateway-key"}
        self.assertEqual(self.client.post("/protected", headers=gateway).status_code, 200)
        self.assertEqual(self.client.post("/protected", headers=bearer).status_code, 403)
        self.assertEqual(client.get("/protected", headers={"Authorization": "Bearer shared-gateway-key"}).status_code, 401)

    def test_logout_revokes_token_and_clears_cookie(self):
        login = self.register()
        token = self.client.cookies.get(self.config.cookie_name)
        response = self.client.post("/api/auth/logout", headers=self.headers(login))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        replay = self.client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
        self.assertEqual(replay.status_code, 401)

    def test_regular_user_cannot_list_manage_or_reset_accounts(self):
        login = self.register()
        for method, path, payload in (
            ("GET", "/api/admin/users", None), ("GET", "/api/admin/stats", None),
            ("PATCH", f"/api/admin/users/{self.admin['id']}/status", {"status": "SUSPENDED"}),
            ("DELETE", f"/api/admin/users/{self.admin['id']}", None),
            ("POST", f"/api/admin/users/{self.admin['id']}/reset-password", None),
        ):
            response = self.client.request(method, path, json=payload, headers=self.headers(login))
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.store.by_id(self.admin["id"])["status"], "APPROVED")

    def test_admin_filters_counts_and_protected_status_changes(self):
        first = self.register().json()["user"]
        second = self.register("second@example.com", client=self.new_client()).json()["user"]
        for account in (first, second):
            self.store.update_status(self.admin["id"], account["id"], "PENDING")
        admin, login = self.admin_client()
        listing = admin.get("/api/admin/users?status=PENDING&page=1&page_size=1")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["total"], 2)
        self.assertEqual(len(listing.json()["items"]), 1)
        stats = admin.get("/api/admin/stats")
        self.assertEqual(stats.json()["total"], 3)
        self.assertEqual(stats.json()["pending"], 2)
        self.assertTrue(all({"submitted_jobs", "execution_attempts"}.issubset(row) for row in stats.json()["users"]))
        for query in ("page=0", "page_size=101", "status=ADMIN"):
            self.assertEqual(admin.get("/api/admin/users?" + query).status_code, 422)
        injected = admin.patch(f"/api/admin/users/{first['id']}/status", json={"status": "APPROVED", "role": "ADMIN"}, headers=self.headers(login))
        self.assertEqual(injected.status_code, 422)
        changed = admin.patch(f"/api/admin/users/{first['id']}/status", json={"status": "APPROVED"}, headers=self.headers(login))
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["user"]["role"], "USER")
        self.assertEqual(admin.get("/api/admin/users?status=PENDING").json()["total"], 1)

    def test_suspend_and_delete_revoke_existing_sessions(self):
        user = self.register().json()["user"]
        admin, login = self.admin_client()
        response = admin.patch(f"/api/admin/users/{user['id']}/status", json={"status": "SUSPENDED"}, headers=self.headers(login))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.approve(user)
        self.client.cookies.clear()
        self.login()
        response = admin.delete(f"/api/admin/users/{user['id']}", headers=self.headers(login))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(admin.get("/api/admin/users").json()["total"], 1)

    def test_last_admin_cannot_be_deleted_rejected_or_suspended(self):
        admin, login = self.admin_client()
        for status in ("PENDING", "REJECTED", "SUSPENDED"):
            response = admin.patch(f"/api/admin/users/{self.admin['id']}/status", json={"status": status}, headers=self.headers(login))
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "last_admin")
        self.assertEqual(admin.delete(f"/api/admin/users/{self.admin['id']}", headers=self.headers(login)).status_code, 409)

    def test_reset_password_is_returned_once_revokes_sessions_and_requires_change(self):
        user = self.register().json()["user"]
        admin, login = self.admin_client()
        reset = admin.post(f"/api/admin/users/{user['id']}/reset-password", headers=self.headers(login))
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.headers["cache-control"], "no-store")
        temporary = reset.json()["temporary_password"]
        self.assertTrue(reset.json()["user"]["must_change_password"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.client.cookies.clear()
        old = self.client.post("/api/auth/login", json={"email": user["email"], "password": PASSWORD})
        self.assertEqual(old.status_code, 401)
        login = self.login(password=temporary)
        self.assertTrue(self.client.get("/api/auth/me").json()["user"]["must_change_password"])
        blocked = self.client.get("/protected")
        self.assertEqual(blocked.json()["detail"]["code"], "password_change_required")
        changed = self.client.post("/api/auth/change-password", json={"current_password": temporary, "new_password": "ReplacementPassword987!"}, headers=self.headers(login))
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()["user"]["must_change_password"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login(password="ReplacementPassword987!")
        self.assertEqual(self.client.get("/protected").status_code, 200)
        self.assertNotIn(temporary, admin.get("/api/admin/users").text)
        with self.engine.connect() as connection:
            stored = list(connection.execute(select(users))) + list(connection.execute(select(admin_audit))) + list(connection.execute(select(sessions)))
        self.assertNotIn(temporary, str(stored))

    def test_admin_with_temporary_password_must_change_it_before_admin_access(self):
        reset = self.store.reset_password(self.admin["id"], self.admin["id"])
        login = self.login("admin@example.com", reset["temporary_password"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        blocked = self.client.get("/api/admin/users")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"]["code"], "password_change_required")
        changed = self.client.post("/api/auth/change-password", json={"current_password": reset["temporary_password"], "new_password": "ChangedAdminPassword123!"}, headers=self.headers(login))
        self.assertEqual(changed.status_code, 200)
        self.login("admin@example.com", "ChangedAdminPassword123!")
        self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_valid_session_cannot_be_replaced_without_csrf(self):
        login = self.register()
        payload = {"email": "admin@example.com", "password": ADMIN_PASSWORD}
        registration = {"email": "second@example.com", "password": PASSWORD, "username": "Second"}
        blocked_signup = self.client.post("/api/auth/register", json=registration, headers={"Origin": ORIGIN})
        self.assertEqual(blocked_signup.status_code, 403)
        self.assertEqual(self.store.list_users()["total"], 2)
        blocked = self.client.post("/api/auth/login", json=payload, headers={"Origin": ORIGIN})
        self.assertEqual(blocked.status_code, 403)
        allowed = self.client.post("/api/auth/login", json=payload, headers=self.headers(login))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["user"]["role"], "ADMIN")


if __name__ == "__main__":
    unittest.main()
