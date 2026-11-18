"""Account credentials, revocable sessions, and transactional administration."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
import jwt
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from .models import admin_audit, auth_rate_limits, jobs, sessions, users


JWT_ISSUER = "rallymate"
JWT_AUDIENCE = "rallymate-web"
STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", "SUSPENDED"})


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(row) -> dict:
    result = {column.name: row[column.name] for column in users.columns if column.name != "password_hash"}
    result["must_change_password"] = bool(result["must_change_password"])
    return result


def normalize_email(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 320:
        raise AuthError(422, "invalid_email", "请输入有效的邮箱地址。")
    try:
        return validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False).ascii_email.lower()
    except EmailNotValidError:
        raise AuthError(422, "invalid_email", "请输入有效的邮箱地址。") from None


def validate_password(value: str) -> None:
    if not isinstance(value, str) or not 12 <= len(value) <= 128 or not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise AuthError(422, "weak_password", "密码须为12至128个字符，并包含字母和数字。")


class AccountStore:
    def __init__(self, engine, secret: str, session_days: int = 7):
        if not isinstance(secret, str) or not 32 <= len(secret.encode("utf-8")) <= 4096:
            raise ValueError("The session secret must contain 32 to 4096 bytes")
        if type(session_days) is not int or not 1 <= session_days <= 30:
            raise ValueError("session_days must be an integer from 1 to 30")
        self.engine = engine
        self.secret = secret
        self.session_days = session_days
        self.password_hasher = PasswordHasher()
        self._dummy_hash = self.password_hasher.hash(secrets.token_urlsafe(32))

    @contextmanager
    def _write(self):
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection

    def _lock(self, connection, name: str) -> None:
        if self.engine.dialect.name == "postgresql":
            lock_id = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
            connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})

    def _rate_limit(self, key: str, limit: int, window_seconds: int) -> None:
        key = hashlib.sha256(key.encode()).hexdigest()
        now = _now()
        with self._write() as connection:
            self._lock(connection, f"rallymate:rate:{key}")
            row = connection.execute(select(auth_rate_limits).where(auth_rate_limits.c.key == key)).mappings().first()
            if row and datetime.fromisoformat(row["window_start"]) + timedelta(seconds=window_seconds) > now:
                if row["attempts"] >= limit:
                    raise AuthError(429, "rate_limited", "请求过于频繁，请稍后再试。")
                connection.execute(update(auth_rate_limits).where(auth_rate_limits.c.key == key).values(attempts=row["attempts"] + 1))
            elif row:
                connection.execute(update(auth_rate_limits).where(auth_rate_limits.c.key == key).values(window_start=now.isoformat(), attempts=1))
            else:
                connection.execute(auth_rate_limits.insert().values(key=key, window_start=now.isoformat(), attempts=1))

    def _limit_credentials(self, action: str, email: str, rate_key: str | None) -> None:
        if rate_key is not None:
            if not isinstance(rate_key, str) or not 1 <= len(rate_key) <= 512:
                raise AuthError(422, "invalid_request", "请求参数无效。")
            self._rate_limit(f"{action}:client:{rate_key}", 30 if action == "login" else 5, 900 if action == "login" else 3600)
        self._rate_limit(f"{action}:email:{email}", 10 if action == "login" else 5, 900 if action == "login" else 3600)

    def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self.password_hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def register(self, email: str, password: str, username: str, application_reason: str | None = None, *, rate_key: str | None = None) -> dict:
        email = normalize_email(email)
        validate_password(password)
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 80 or any(ord(c) < 32 for c in username):
            raise AuthError(422, "invalid_username", "显示名称须为1至80个字符。")
        if application_reason is not None and (not isinstance(application_reason, str) or len(application_reason) > 2000):
            raise AuthError(422, "invalid_application_reason", "申请理由最多2000个字符。")
        self._limit_credentials("register", email, rate_key)
        identifier = str(uuid4())
        values = dict(id=identifier, email=email, username=username.strip(), password_hash=self.password_hasher.hash(password),
                      role="USER", status="PENDING", application_reason=application_reason.strip() if application_reason else None,
                      created_at=_now().isoformat(), must_change_password=0)
        try:
            with self._write() as connection:
                connection.execute(users.insert().values(**values))
                return _public(connection.execute(select(users).where(users.c.id == identifier)).mappings().one())
        except IntegrityError:
            raise AuthError(409, "email_exists", "该邮箱已注册。") from None

    def by_id(self, user_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None))).mappings().first()
            return _public(row) if row else None

    def bootstrap(self, email: str, password: str) -> dict:
        email = normalize_email(email)
        validate_password(password)
        password_hash = self.password_hasher.hash(password)
        try:
            with self._write() as connection:
                self._lock(connection, "rallymate:account-administration")
                existing = connection.execute(select(users).where(users.c.role == "ADMIN", users.c.deleted_at.is_(None)).order_by(users.c.created_at, users.c.id)).mappings().first()
                if existing:
                    return _public(existing)
                if connection.execute(select(users.c.id).where(users.c.email == email)).first():
                    raise AuthError(409, "admin_bootstrap_conflict", "管理员邮箱已被其他账户使用。")
                identifier = str(uuid4())
                now = _now().isoformat()
                connection.execute(users.insert().values(id=identifier, email=email, username="管理员", password_hash=password_hash,
                    role="ADMIN", status="APPROVED", created_at=now, must_change_password=0))
                self._audit(connection, identifier, identifier, "bootstrap_admin", {})
                return _public(connection.execute(select(users).where(users.c.id == identifier)).mappings().one())
        except IntegrityError:
            raise AuthError(409, "admin_bootstrap_conflict", "管理员邮箱已被其他账户使用。") from None

    def _csrf_token(self, session_id: str) -> str:
        return hmac.new(self.secret.encode(), f"csrf:{session_id}".encode(), hashlib.sha256).hexdigest()

    def authenticate(self, email: str, password: str, *, rate_key: str | None = None) -> dict:
        try:
            email = normalize_email(email)
        except AuthError:
            raise AuthError(401, "invalid_credentials", "邮箱或密码不正确。") from None
        self._limit_credentials("login", email, rate_key)
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise AuthError(401, "invalid_credentials", "邮箱或密码不正确。")
        with self.engine.connect() as connection:
            initial = connection.execute(select(users).where(users.c.email == email)).mappings().first()
        verified = self._verify_password(initial["password_hash"] if initial else self._dummy_hash, password)
        if not initial or not verified or initial["deleted_at"] is not None:
            raise AuthError(401, "invalid_credentials", "邮箱或密码不正确。")
        replacement = self.password_hasher.hash(password) if self.password_hasher.check_needs_rehash(initial["password_hash"]) else None
        with self._write() as connection:
            current = connection.execute(select(users).where(users.c.id == initial["id"]).with_for_update()).mappings().one()
            if current["deleted_at"] is not None or current["password_hash"] != initial["password_hash"]:
                raise AuthError(401, "invalid_credentials", "邮箱或密码不正确。")
            now = _now().replace(microsecond=0)
            expires = now + timedelta(days=self.session_days)
            session_id = str(uuid4())
            csrf_token = self._csrf_token(session_id)
            connection.execute(sessions.insert().values(id=session_id, user_id=current["id"], created_at=now.isoformat(),
                expires_at=expires.isoformat(), csrf_token_hash=hashlib.sha256(csrf_token.encode()).hexdigest()))
            changes = {"last_login_at": now.isoformat()}
            if replacement:
                changes["password_hash"] = replacement
            connection.execute(update(users).where(users.c.id == current["id"]).values(**changes))
            public = _public({**current, **changes})
            token = jwt.encode({"sub": current["id"], "sid": session_id, "iat": int(now.timestamp()), "exp": int(expires.timestamp()),
                                "iss": JWT_ISSUER, "aud": JWT_AUDIENCE}, self.secret, algorithm="HS256")
            return {"user": public, "token": token, "csrf_token": csrf_token, "expires_at": expires.isoformat()}

    def _claims(self, token: str) -> dict:
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 4096:
                raise ValueError("invalid token length")
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=JWT_ISSUER, audience=JWT_AUDIENCE,
                options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"], "strict_aud": True})
            if any(not isinstance(claims[name], str) or str(UUID(claims[name])) != claims[name] for name in ("sub", "sid")):
                raise ValueError("invalid identifier")
            if type(claims["iat"]) is not int or type(claims["exp"]) is not int or not 0 < claims["exp"] - claims["iat"] <= self.session_days * 86400:
                raise ValueError("invalid token lifetime")
            return claims
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
            raise AuthError(401, "invalid_session", "登录已失效，请重新登录。") from None

    def resolve_session(self, token: str) -> dict:
        claims = self._claims(token)
        with self.engine.connect() as connection:
            row = connection.execute(select(users, sessions.c.id.label("session_id"), sessions.c.created_at.label("session_created_at"),
                sessions.c.expires_at, sessions.c.csrf_token_hash).join(sessions, sessions.c.user_id == users.c.id).where(
                    users.c.id == claims["sub"], users.c.deleted_at.is_(None), sessions.c.id == claims["sid"], sessions.c.revoked_at.is_(None))).mappings().first()
        if row:
            csrf_token = self._csrf_token(row["session_id"])
            try:
                expires = datetime.fromisoformat(row["expires_at"])
                created = datetime.fromisoformat(row["session_created_at"])
                if expires.tzinfo is not None and created.tzinfo is not None and expires > _now() and int(expires.timestamp()) == claims["exp"] and int(created.timestamp()) == claims["iat"] and hmac.compare_digest(
                        row["csrf_token_hash"], hashlib.sha256(csrf_token.encode()).hexdigest()):
                    return {"user": _public(row), "session_id": row["session_id"], "csrf_token": csrf_token, "expires_at": row["expires_at"]}
            except (ValueError, TypeError):
                pass
        raise AuthError(401, "invalid_session", "登录已失效，请重新登录。")

    def logout(self, token: str) -> None:
        claims = self._claims(token)
        with self._write() as connection:
            connection.execute(update(sessions).where(sessions.c.id == claims["sid"], sessions.c.user_id == claims["sub"], sessions.c.revoked_at.is_(None)).values(revoked_at=_now().isoformat()))

    def _revoke(self, connection, user_id: str, now: str) -> None:
        connection.execute(update(sessions).where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None)).values(revoked_at=now))

    def change_password(self, user_id: str, current_password: str, new_password: str) -> dict:
        validate_password(new_password)
        self._rate_limit(f"change-password:{user_id}", 10, 900)
        if not isinstance(current_password, str) or not 1 <= len(current_password) <= 128:
            raise AuthError(401, "invalid_credentials", "当前密码不正确。")
        with self.engine.connect() as connection:
            initial = connection.execute(select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None))).mappings().first()
        if not initial or not self._verify_password(initial["password_hash"], current_password):
            raise AuthError(401, "invalid_credentials", "当前密码不正确。")
        if secrets.compare_digest(current_password.encode(), new_password.encode()):
            raise AuthError(422, "password_unchanged", "新密码须与当前密码不同。")
        password_hash = self.password_hasher.hash(new_password)
        with self._write() as connection:
            current = connection.execute(select(users).where(users.c.id == user_id).with_for_update()).mappings().first()
            if not current or current["deleted_at"] is not None or current["password_hash"] != initial["password_hash"]:
                raise AuthError(401, "invalid_credentials", "当前密码不正确。")
            now = _now().isoformat()
            changes = {"password_hash": password_hash, "password_changed_at": now, "must_change_password": 0}
            connection.execute(update(users).where(users.c.id == user_id).values(**changes))
            self._revoke(connection, user_id, now)
            return _public({**current, **changes})

    def list_users(self, status: str | None = None, page: int = 1, page_size: int = 20) -> dict:
        if status is not None and (not isinstance(status, str) or status not in STATUSES):
            raise AuthError(422, "invalid_status", "账户状态无效。")
        if type(page) is not int or not 1 <= page <= 1000000 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise AuthError(422, "invalid_pagination", "分页参数无效。")
        conditions = [users.c.deleted_at.is_(None)]
        if status is not None:
            conditions.append(users.c.status == status)
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).select_from(users).where(*conditions)).scalar_one()
            rows = connection.execute(select(users).where(*conditions).order_by(users.c.created_at.desc(), users.c.id).offset((page - 1) * page_size).limit(page_size)).mappings()
            return {"items": [_public(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def stats(self) -> dict:
        counts = select(jobs.c.user_id, func.count().label("submitted_jobs"), func.sum(jobs.c.attempts).label("execution_attempts")).group_by(jobs.c.user_id).subquery()
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).select_from(users).where(users.c.deleted_at.is_(None))).scalar_one()
            pending = connection.execute(select(func.count()).select_from(users).where(users.c.deleted_at.is_(None), users.c.status == "PENDING")).scalar_one()
            rows = connection.execute(select(users.c.id.label("user_id"), users.c.email, users.c.username, users.c.status, users.c.role, users.c.deleted_at,
                func.coalesce(counts.c.submitted_jobs, 0).label("submitted_jobs"), func.coalesce(counts.c.execution_attempts, 0).label("execution_attempts")
                ).outerjoin(counts, counts.c.user_id == users.c.id).order_by(users.c.created_at, users.c.id)).mappings()
            return {"total": total, "pending": pending, "users": [dict(row) for row in rows]}

    def _admin_target(self, connection, actor_id: str, target_id: str):
        self._lock(connection, "rallymate:account-administration")
        actor = connection.execute(select(users).where(users.c.id == actor_id)).mappings().first()
        if not actor or actor["deleted_at"] is not None or actor["role"] != "ADMIN" or actor["status"] != "APPROVED" or actor["must_change_password"]:
            raise AuthError(403, "admin_required", "此操作需要已审核的管理员账户。")
        target = connection.execute(select(users).where(users.c.id == target_id).with_for_update()).mappings().first()
        if not target or target["deleted_at"] is not None:
            raise AuthError(404, "user_not_found", "账户不存在。")
        return target

    def _protect_last_admin(self, connection, target) -> None:
        if target["role"] == "ADMIN" and target["status"] == "APPROVED":
            count = connection.execute(select(func.count()).select_from(users).where(users.c.role == "ADMIN", users.c.status == "APPROVED", users.c.deleted_at.is_(None))).scalar_one()
            if count <= 1:
                raise AuthError(409, "last_admin", "不能停用或删除最后一个有效管理员。")

    def _audit(self, connection, actor_id: str, target_id: str, action: str, details: dict) -> None:
        connection.execute(admin_audit.insert().values(id=str(uuid4()), actor_id=actor_id, target_user_id=target_id, action=action,
            created_at=_now().isoformat(), details_json=json.dumps(details, ensure_ascii=False)))

    def update_status(self, actor_id: str, target_id: str, status: str) -> dict:
        if not isinstance(status, str) or status not in STATUSES:
            raise AuthError(422, "invalid_status", "账户状态无效。")
        with self._write() as connection:
            target = self._admin_target(connection, actor_id, target_id)
            if status != "APPROVED":
                self._protect_last_admin(connection, target)
            connection.execute(update(users).where(users.c.id == target_id).values(status=status))
            if status != "APPROVED":
                self._revoke(connection, target_id, _now().isoformat())
            self._audit(connection, actor_id, target_id, "update_status", {"previous_status": target["status"], "status": status})
            return _public({**target, "status": status})

    set_user_status = update_status

    def delete_user(self, actor_id: str, target_id: str) -> dict:
        with self._write() as connection:
            target = self._admin_target(connection, actor_id, target_id)
            self._protect_last_admin(connection, target)
            now = _now().isoformat()
            connection.execute(update(users).where(users.c.id == target_id).values(deleted_at=now))
            self._revoke(connection, target_id, now)
            self._audit(connection, actor_id, target_id, "delete_user", {})
            return _public({**target, "deleted_at": now})

    def reset_password(self, actor_id: str, target_id: str) -> dict:
        temporary_password = secrets.token_urlsafe(24) + "aA9"
        password_hash = self.password_hasher.hash(temporary_password)
        with self._write() as connection:
            target = self._admin_target(connection, actor_id, target_id)
            now = _now().isoformat()
            changes = {"password_hash": password_hash, "password_changed_at": now, "must_change_password": 1}
            connection.execute(update(users).where(users.c.id == target_id).values(**changes))
            self._revoke(connection, target_id, now)
            self._audit(connection, actor_id, target_id, "reset_password", {})
            return {"user": _public({**target, **changes}), "temporary_password": temporary_password}
