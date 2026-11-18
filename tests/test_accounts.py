from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

import jwt
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.engine import make_url

from rallymate_service.accounts import AccountStore, AuthError, JWT_AUDIENCE, JWT_ISSUER
from rallymate_service.migrations import migrate_database
from rallymate_service.models import admin_audit, auth_rate_limits, jobs, sessions, users


SECRET = "test-session-secret-" + "x" * 48
PASSWORD = "TennisPractice123"


class AccountContract:
    def setUp(self):
        self.engine = self.make_engine()
        self.store = AccountStore(self.engine, SECRET)

    def register(self, email="player@example.com"):
        return self.store.register(email, PASSWORD, "球友", "希望参加训练")

    def admin(self):
        return self.store.bootstrap("admin@example.com", PASSWORD)

    def assert_auth_error(self, status, code, function, *args, **kwargs):
        with self.assertRaises(AuthError) as context:
            function(*args, **kwargs)
        self.assertEqual((context.exception.status, context.exception.code), (status, code))

    def second_admin(self):
        user = self.register("second-admin@example.com")
        with self.engine.begin() as connection:
            connection.execute(update(users).where(users.c.id == user["id"]).values(role="ADMIN", status="APPROVED"))
        return self.store.by_id(user["id"])

    def test_registration_normalizes_email_and_keeps_hash_private(self):
        user = self.store.register(" Player@EXAMPLE.com ", PASSWORD, " 球友 ")
        self.assertEqual((user["email"], user["username"], user["role"], user["status"]), ("player@example.com", "球友", "USER", "PENDING"))
        self.assertNotIn("password_hash", user)
        with self.engine.connect() as connection:
            password_hash = connection.execute(select(users.c.password_hash)).scalar_one()
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(self.store.password_hasher.verify(password_hash, PASSWORD))
        self.assert_auth_error(409, "email_exists", self.store.register, "PLAYER@example.com", PASSWORD, "other")
        self.assert_auth_error(401, "invalid_credentials", self.store.authenticate, "player@example.com", "wrong password")
        self.assertEqual(self.store.authenticate("PLAYER@example.com", PASSWORD)["user"]["id"], user["id"])

    def test_validation_and_configuration_bounds(self):
        for password in ("short1", "a" * 12, "1" * 12, "a1" * 65, None):
            with self.subTest(password_length=len(password) if password else 0):
                self.assert_auth_error(422, "weak_password", self.store.register, "a@example.com", password, "name")
        for email in ("bad", "a@localhost", "a" * 321):
            self.assert_auth_error(422, "invalid_email", self.store.register, email, PASSWORD, "name")
        for name in (" ", "a" * 81, "bad\nname"):
            self.assert_auth_error(422, "invalid_username", self.store.register, "a@example.com", PASSWORD, name)
        self.assert_auth_error(422, "invalid_application_reason", self.store.register, "a@example.com", PASSWORD, "name", "x" * 2001)
        for days in (0, 31, 1.5, True):
            with self.assertRaises(ValueError):
                AccountStore(self.engine, SECRET, days)
        with self.assertRaises(ValueError):
            AccountStore(self.engine, "short")

    def test_concurrent_bootstrap_is_unique_and_never_overwrites_password(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            admins = list(pool.map(lambda _: self.store.bootstrap("admin@example.com", PASSWORD), range(4)))
        self.assertEqual(len({admin["id"] for admin in admins}), 1)
        existing = self.store.bootstrap("other@example.com", "DifferentPassword123")
        self.assertEqual(existing["id"], admins[0]["id"])
        self.assertEqual(existing["status"], "APPROVED")
        self.assertEqual(self.store.authenticate("admin@example.com", PASSWORD)["user"]["role"], "ADMIN")
        self.assert_auth_error(401, "invalid_credentials", self.store.authenticate, "admin@example.com", "DifferentPassword123")

    def test_bootstrap_never_promotes_an_existing_user(self):
        user = self.register("admin@example.com")
        self.assert_auth_error(409, "admin_bootstrap_conflict", self.store.bootstrap, "admin@example.com", PASSWORD)
        self.assertEqual(self.store.by_id(user["id"])["role"], "USER")

    def test_concurrent_registration_has_one_winner(self):
        def register(_):
            try:
                return self.register()["id"]
            except AuthError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(register, range(4)))
        self.assertEqual(outcomes.count("email_exists"), 3)
        self.assertEqual(self.store.list_users()["total"], 1)

    def test_session_reads_current_status_and_csrf_survives_refresh(self):
        user = self.register()
        admin = self.admin()
        login = self.store.authenticate(user["email"], PASSWORD)
        current = self.store.resolve_session(login["token"])
        self.assertEqual(current["user"]["status"], "PENDING")
        self.assertEqual(current["csrf_token"], login["csrf_token"])
        self.store.update_status(admin["id"], user["id"], "APPROVED")
        self.assertEqual(self.store.resolve_session(login["token"])["user"]["status"], "APPROVED")
        for status in ("REJECTED", "SUSPENDED"):
            self.store.update_status(admin["id"], user["id"], status)
            self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])
            login = self.store.authenticate(user["email"], PASSWORD)
            self.assertEqual(self.store.resolve_session(login["token"])["user"]["status"], status)
        with self.engine.connect() as connection:
            hashes = connection.execute(select(sessions.c.csrf_token_hash)).scalars().all()
            self.assertNotIn(login["csrf_token"], hashes)

    def test_jwt_rejects_wrong_algorithms_claims_signature_and_expiration(self):
        self.register()
        login = self.store.authenticate("player@example.com", PASSWORD)
        base = jwt.decode(login["token"], SECRET, algorithms=["HS256"], issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
        invalid = []
        for name in ("sub", "sid", "iat", "exp", "iss", "aud"):
            claims = dict(base)
            claims.pop(name)
            invalid.append(jwt.encode(claims, SECRET, algorithm="HS256"))
        for changes in ({"iss": "other"}, {"aud": "other"}, {"aud": [JWT_AUDIENCE]}, {"sub": "not-a-uuid"},
                        {"sid": "not-a-uuid"}, {"iat": str(base["iat"])}, {"iat": base["iat"] + 300},
                        {"exp": base["iat"] - 1}, {"exp": base["iat"] + 31 * 86400}):
            invalid.append(jwt.encode({**base, **changes}, SECRET, algorithm="HS256"))
        invalid.extend([jwt.encode(base, SECRET, algorithm="HS384"), jwt.encode(base, None, algorithm="none"),
                        jwt.encode(base, "another-secret-" + "y" * 40, algorithm="HS256"), "x" * 4097])
        for token in invalid:
            self.assert_auth_error(401, "invalid_session", self.store.resolve_session, token)
        self.assertEqual(self.store.resolve_session(login["token"])["user"]["email"], "player@example.com")

    def test_logout_and_database_expiry_revoke_access(self):
        self.register()
        login = self.store.authenticate("player@example.com", PASSWORD)
        self.store.logout(login["token"])
        self.store.logout(login["token"])
        self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])
        login = self.store.authenticate("player@example.com", PASSWORD)
        with self.engine.begin() as connection:
            connection.execute(update(sessions).values(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
        self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])

    def test_administration_failure_rolls_back_status_and_session_revocation(self):
        admin = self.admin()
        user = self.register()
        login = self.store.authenticate(user["email"], PASSWORD)
        with patch.object(self.store, "_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.store.update_status(admin["id"], user["id"], "SUSPENDED")
        self.assertEqual(self.store.resolve_session(login["token"])["user"]["status"], "PENDING")

    def test_malformed_stored_session_times_fail_closed(self):
        self.register()
        login = self.store.authenticate("player@example.com", PASSWORD)
        for expires in ("not-a-date", "2099-01-01T00:00:00"):
            with self.engine.begin() as connection:
                connection.execute(update(sessions).values(expires_at=expires))
            self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])

    def test_reset_and_change_password_revoke_every_session_and_keep_secrets_out_of_audit(self):
        admin = self.admin()
        user = self.register()
        old_logins = [self.store.authenticate(user["email"], PASSWORD) for _ in range(2)]
        reset = self.store.reset_password(admin["id"], user["id"])
        self.assertGreaterEqual(len(reset["temporary_password"]), 24)
        self.assertTrue(reset["user"]["must_change_password"])
        for login in old_logins:
            self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])
        self.assert_auth_error(401, "invalid_credentials", self.store.authenticate, user["email"], PASSWORD)
        login = self.store.authenticate(user["email"], reset["temporary_password"])
        self.assertTrue(self.store.resolve_session(login["token"])["user"]["must_change_password"])
        changed = self.store.change_password(user["id"], reset["temporary_password"], "ChangedPassword123")
        self.assertFalse(changed["must_change_password"])
        self.assert_auth_error(401, "invalid_session", self.store.resolve_session, login["token"])
        self.assertEqual(self.store.authenticate(user["email"], "ChangedPassword123")["user"]["id"], user["id"])
        with self.engine.connect() as connection:
            audit = json.dumps([dict(row) for row in connection.execute(select(admin_audit)).mappings()])
            hashes = connection.execute(select(users.c.password_hash)).scalars().all()
        for sensitive in (reset["temporary_password"], PASSWORD, "ChangedPassword123", login["token"]):
            self.assertNotIn(sensitive, audit)
            self.assertNotIn(sensitive, hashes)

    def test_password_reset_racing_verified_login_cannot_create_a_new_session(self):
        admin = self.admin()
        user = self.register()
        verified = Event()
        release = Event()
        original = self.store._verify_password

        def verify(password_hash, password):
            result = original(password_hash, password)
            verified.set()
            self.assertTrue(release.wait(10))
            return result

        with patch.object(self.store, "_verify_password", side_effect=verify), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.store.authenticate, user["email"], PASSWORD)
            try:
                self.assertTrue(verified.wait(10))
                self.store.reset_password(admin["id"], user["id"])
            finally:
                release.set()
            with self.assertRaises(AuthError) as error:
                future.result()
            self.assertEqual(error.exception.code, "invalid_credentials")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(func.count()).select_from(sessions).where(sessions.c.user_id == user["id"])).scalar_one(), 0)

    def test_last_admin_is_protected_and_admin_authority_is_checked_again(self):
        admin = self.admin()
        ordinary = self.register()
        for status in ("PENDING", "REJECTED", "SUSPENDED"):
            self.assert_auth_error(409, "last_admin", self.store.update_status, admin["id"], admin["id"], status)
        self.assert_auth_error(409, "last_admin", self.store.delete_user, admin["id"], admin["id"])
        self.assert_auth_error(403, "admin_required", self.store.update_status, ordinary["id"], admin["id"], "SUSPENDED")
        self.store.reset_password(admin["id"], admin["id"])
        self.assert_auth_error(403, "admin_required", self.store.delete_user, admin["id"], ordinary["id"])

    def test_concurrent_admin_deletions_leave_one_effective_admin(self):
        first = self.admin()
        second = self.second_admin()

        def remove(user):
            try:
                self.store.delete_user(user["id"], user["id"])
                return "deleted"
            except AuthError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(remove, (first, second)))
        self.assertCountEqual(outcomes, ["deleted", "last_admin"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(func.count()).select_from(users).where(users.c.role == "ADMIN", users.c.status == "APPROVED", users.c.deleted_at.is_(None))).scalar_one(), 1)

    def test_statistics_include_deleted_jobs_and_preserve_deleted_user_attribution(self):
        admin = self.admin()
        user = self.register()
        token = self.store.authenticate(user["email"], PASSWORD)["token"]
        with self.engine.begin() as connection:
            for attempts, deleted_at, user_id in ((2, None, user["id"]), (1, "now", user["id"]), (4, None, None)):
                connection.execute(jobs.insert().values(id=str(uuid4()), status="succeeded", original_filename="x.mp4", video_path="x.mp4",
                    request_path="r.json", output_dir="out", created_at="now", updated_at="now", attempts=attempts, user_id=user_id, deleted_at=deleted_at))
        self.assertEqual(self.store.list_users("PENDING")["items"][0]["id"], user["id"])
        self.store.delete_user(admin["id"], user["id"])
        self.assertIsNone(self.store.by_id(user["id"]))
        self.assert_auth_error(401, "invalid_session", self.store.resolve_session, token)
        self.assert_auth_error(401, "invalid_credentials", self.store.authenticate, user["email"], PASSWORD)
        stats = self.store.stats()
        self.assertEqual((stats["total"], stats["pending"]), (1, 0))
        detail = next(row for row in stats["users"] if row["user_id"] == user["id"])
        self.assertEqual((detail["submitted_jobs"], detail["execution_attempts"]), (2, 3))
        self.assertIsNotNone(detail["deleted_at"])

    def test_rate_limits_survive_store_restart_and_concurrent_attempts(self):
        def consume(_):
            try:
                self.store._rate_limit("concurrent-test", 4, 900)
                return "accepted"
            except AuthError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(consume, range(6)))
        self.assertEqual(outcomes.count("accepted"), 4)
        self.assertEqual(outcomes.count("rate_limited"), 2)
        restarted = AccountStore(self.engine, SECRET)
        self.assert_auth_error(429, "rate_limited", restarted._rate_limit, "concurrent-test", 4, 900)
        with self.engine.begin() as connection:
            row = connection.execute(select(auth_rate_limits)).mappings().one()
            self.assertEqual(row["attempts"], 4)
            self.assertEqual(len(row["key"]), 64)
            connection.execute(update(auth_rate_limits).values(window_start="2000-01-01T00:00:00+00:00"))
        restarted._rate_limit("concurrent-test", 4, 900)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(auth_rate_limits.c.attempts)).scalar_one(), 1)


class SQLiteAccountTests(AccountContract, unittest.TestCase):
    def make_engine(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        url = f"sqlite:///{(Path(temporary.name) / 'accounts.sqlite3').as_posix()}"
        migrate_database(url)
        engine = create_engine(url, connect_args={"timeout": 30})
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine


@unittest.skipUnless(os.environ.get("RALLYMATE_TEST_POSTGRES_URL"), "requires disposable PostgreSQL test URL")
class PostgresAccountTests(AccountContract, unittest.TestCase):
    def make_engine(self):
        url = make_url(os.environ["RALLYMATE_TEST_POSTGRES_URL"])
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        admin = create_engine(url)
        self.addCleanup(admin.dispose)
        schema = f"accounts_{uuid4().hex}"
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

        def cleanup():
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

        self.addCleanup(cleanup)
        scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
        migrate_database(scoped)
        engine = create_engine(scoped)
        self.addCleanup(engine.dispose)
        return engine


if __name__ == "__main__":
    unittest.main()
