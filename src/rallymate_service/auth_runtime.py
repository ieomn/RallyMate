"""Account protection around existing API, upload and report implementations."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from starlette._utils import get_route_path
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from .accounts import AccountStore, AuthError
from .auth_api import authenticate_request, auth_error_response, install_auth_routes
from .migrations import migrate_database, validate_schema
from .ownership import (
    UserScopedJobDatabase, UserScopedUploadStore, assert_job_owner, assert_upload_owner, current_user_id,
)

LOGGER = logging.getLogger("rallymate.auth")


@dataclass(frozen=True)
class AuthConfig:
    secret: str = field(repr=False)
    session_days: int = 7
    cookie_secure: bool = False
    public_origin: str | None = None
    cookie_name: str = "rallymate_session"

    def __post_init__(self):
        if (not isinstance(self.secret, str) or not 32 <= len(self.secret.encode("utf-8")) <= 4096
                or len(set(self.secret)) < 12):
            raise ValueError("JWT_SECRET must contain 32 to 4096 bytes of random secret material")
        if type(self.session_days) is not int or not 1 <= self.session_days <= 7:
            raise ValueError("JWT_SESSION_DAYS must be between 1 and 7")
        if self.public_origin:
            try:
                parsed = urlsplit(self.public_origin)
                _ = parsed.port
            except ValueError:
                raise ValueError("PUBLIC_ORIGIN must be a valid HTTP(S) origin") from None
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                    or "\\" in self.public_origin or any(c.isspace() for c in self.public_origin)):
                raise ValueError("PUBLIC_ORIGIN must be an HTTP(S) origin without credentials or a path")
            if self.cookie_secure and parsed.scheme != "https":
                raise ValueError("Secure account cookies require an HTTPS PUBLIC_ORIGIN")

    @classmethod
    def from_environment(cls, settings):
        production = settings.environment in {"production", "staging"}
        raw_secure = os.getenv("AUTH_COOKIE_SECURE", "true" if production else "false").lower()
        if raw_secure not in {"true", "false", "1", "0"}:
            raise ValueError("AUTH_COOKIE_SECURE must be true or false")
        secure = raw_secure in {"true", "1"}
        if production and not secure:
            raise ValueError("Production account cookies must be Secure")
        origin = os.getenv("PUBLIC_ORIGIN") or settings.public_base_url
        if production and not origin:
            raise ValueError("PUBLIC_ORIGIN is required in production")
        return cls(secret=os.getenv("JWT_SECRET", ""),
                   session_days=int(os.getenv("JWT_SESSION_DAYS", "7")), cookie_secure=secure,
                   public_origin=origin.rstrip("/") if origin else None)


class AccountBoundaryMiddleware:
    def __init__(self, app, *, runtime):
        self.app, self.runtime = app, runtime

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Match the router's path when this application has an ASGI mount/root_path.
        path = get_route_path(scope)
        protected = path.startswith(("/v1/", "/api/auth/", "/api/admin/", "/api/records"))
        if not protected or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        token = None
        response_started = False

        async def private_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        try:
            if not self.runtime.ready:
                raise AuthError(503, "accounts_unavailable", "账号服务尚未完成初始化。")
            if path.startswith(("/v1/", "/api/records")):
                session = await run_in_threadpool(
                    authenticate_request, request, self.runtime.store, self.runtime.config,
                )
                user_id = session["user"]["id"]
                request.state.account_session = session
                token = current_user_id.set(user_id)
                parts = path.split("/")
                if len(parts) >= 4 and parts[3] and parts[1:3] == ["v1", "jobs"]:
                    await run_in_threadpool(assert_job_owner, self.runtime.engine, user_id, parts[3])
                if len(parts) >= 4 and parts[3] and parts[1:3] == ["v1", "uploads"]:
                    await run_in_threadpool(assert_upload_owner, self.runtime.engine, user_id, parts[3])
            # Only account/control JSON is buffered; video bodies remain streaming.
            if path.startswith(("/api/auth/", "/api/admin/")) and scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                data = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(data) + len(chunk) > 65536:
                        raise AuthError(413, "account_request_too_large", "账号请求内容过大。")
                    data.extend(chunk)
                    if not message.get("more_body", False):
                        break
                delivered = False

                async def buffered_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(data), "more_body": False}
                    return await receive()

                await self.app(scope, buffered_receive, private_send)
            else:
                await self.app(scope, receive, private_send)
        except AuthError as exc:
            if response_started:
                raise
            await auth_error_response(exc)(scope, receive, private_send)
        except HTTPException as exc:
            if response_started:
                raise
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                               headers=exc.headers)(scope, receive, private_send)
        finally:
            if token is not None:
                current_user_id.reset(token)


class AccountRuntime:
    def __init__(self, settings, database, config):
        self.config = config
        self.ready = False
        self.owns_engine = not hasattr(database, "engine")
        if self.owns_engine:
            self.url = URL.create("sqlite", database=str(Path(database.path).resolve()))
            self.engine = create_engine(self.url, connect_args={"timeout": 30, "check_same_thread": False},
                                        hide_parameters=True)

            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")
        else:
            self.engine, self.url = database.engine, database.engine.url
        self.store = AccountStore(self.engine, config.secret, session_days=config.session_days)

    def initialize(self):
        if self.engine.dialect.name == "sqlite":
            migrate_database(self.url)
        else:
            validate_schema(self.engine)
        email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
        if bool(email) != bool(password):
            raise ValueError("ADMIN_EMAIL and ADMIN_PASSWORD must be configured together")
        if email and password:
            self.store.bootstrap(email, password)
        self.ready = True

    def close(self):
        self.ready = False
        if self.owns_engine:
            self.engine.dispose()


def install_account_system(app, settings, database, uploads, *, config=None):
    config = config or AuthConfig.from_environment(settings)
    runtime = AccountRuntime(settings, database, config)
    scoped_database = UserScopedJobDatabase(database, runtime.engine,
        max_active_jobs_per_user=getattr(settings, "max_active_jobs_per_user", None))
    scoped_uploads = UserScopedUploadStore(uploads, runtime.engine,
        max_active_uploads_per_user=getattr(settings, "max_active_uploads_per_user", None))
    app.state.accounts = runtime.store
    app.state.auth_config = config
    app.state.account_runtime = runtime
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            await run_in_threadpool(runtime.initialize)
            async with previous_lifespan(application):
                yield
        finally:
            await run_in_threadpool(runtime.close)

    app.router.lifespan_context = lifespan
    install_auth_routes(app, runtime.store, config)
    app.add_middleware(AccountBoundaryMiddleware, runtime=runtime)

    @app.exception_handler(Exception)
    async def unhandled_exception(request, exc):
        LOGGER.error("Unhandled service request failure", exc_info=exc)
        return JSONResponse({"detail": {"code": "internal_error", "message": "服务暂时无法完成请求，请稍后重试。",
                                      "request_id": getattr(request.state, "request_id", None)}},
                            status_code=500, headers={"Cache-Control": "no-store"})

    return scoped_database, scoped_uploads
