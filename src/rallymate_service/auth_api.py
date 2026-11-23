"""Account routes and the shared cookie-session authorization boundary."""
from __future__ import annotations

import hmac
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from rallymate_service.accounts import AuthError


UserStatus = Literal["PENDING", "APPROVED", "REJECTED", "SUSPENDED"]
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_USER_FIELDS = (
    "id", "email", "username", "role", "status", "application_reason",
    "created_at", "last_login_at", "must_change_password",
)


def public_user(user: dict) -> dict:
    return {name: user.get(name) for name in _PUBLIC_USER_FIELDS}


def auth_error_response(error: AuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if error.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=error.status,
        content={"detail": {"code": error.code, "message": error.message}},
        headers=headers,
    )


def _origin(value: str):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or "\\" in value or any(character.isspace() for character in value)):
            return None
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def validate_request_origin(request: Request, auth_config) -> None:
    if request.method in _SAFE_METHODS:
        return
    received = request.headers.get("origin")
    expected = auth_config.public_origin or f"{request.url.scheme}://{request.url.netloc}"
    if received is None:
        if (request.cookies.get(auth_config.cookie_name)
                or request.headers.get("sec-fetch-site") not in {None, "none"}):
            raise AuthError(403, "origin_required", "此操作需要同源请求。")
    elif _origin(received) is None or _origin(received) != _origin(expected):
        raise AuthError(403, "origin_forbidden", "请求来源不受信任。")


def verify_csrf(request: Request, session: dict, auth_config) -> None:
    if request.method in _SAFE_METHODS:
        return
    validate_request_origin(request, auth_config)
    supplied = request.headers.get("x-csrf-token", "")
    expected = session.get("csrf_token", "")
    if not supplied or len(supplied) > 128 or not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise AuthError(403, "csrf_invalid", "请求校验失败，请刷新页面后重试。")


def _session_token(request: Request, auth_config) -> str | None:
    cookie = request.cookies.get(auth_config.cookie_name)
    if cookie:
        return cookie
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def authenticate_request(
    request: Request, store, auth_config, *, require_approved: bool = True,
    require_admin: bool = False, allow_password_change: bool = False,
) -> dict:
    token = _session_token(request, auth_config)
    if not token:
        raise AuthError(401, "authentication_required", "请先登录。")
    session = store.resolve_session(token)
    user = session["user"]
    from .error_tracking import set_account_context
    set_account_context(user)
    if (require_approved or require_admin) and user["status"] != "APPROVED":
        messages = {
            "PENDING": "账号正在等待管理员审核。",
            "REJECTED": "此账号未启用，请联系管理员。",
            "SUSPENDED": "账号已被停用。",
        }
        raise AuthError(403, "account_" + user["status"].lower(), messages.get(user["status"], "账号不可用。"))
    if require_admin and user["role"] != "ADMIN":
        raise AuthError(403, "admin_required", "此操作需要管理员权限。")
    if user.get("must_change_password") and not allow_password_change:
        raise AuthError(403, "password_change_required", "请先修改临时密码。")
    if request.cookies.get(auth_config.cookie_name):
        verify_csrf(request, session, auth_config)
    else:
        validate_request_origin(request, auth_config)
    return session


def _public_write_guard(request: Request, store, auth_config) -> None:
    validate_request_origin(request, auth_config)
    token = request.cookies.get(auth_config.cookie_name)
    if token:
        try:
            session = store.resolve_session(token)
        except AuthError as error:
            if error.status != 401:
                raise
        else:
            verify_csrf(request, session, auth_config)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Register(_Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    username: str = Field(min_length=1, max_length=80)
    application_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("email", "username")
    @classmethod
    def trimmed(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("application_reason")
    @classmethod
    def optional_reason(cls, value):
        if value is None:
            return None
        return value.strip() or None


class _Login(_Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class _PasswordChange(_Input):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class _StatusChange(_Input):
    status: UserStatus


class _AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request):
            try:
                response = await handler(request)
            except RequestValidationError as error:
                # Validation payloads must not echo passwords, tokens or bodies.
                response = JSONResponse(status_code=422, content={"detail": {
                    "code": "invalid_request", "message": "请检查提交的字段。",
                    "fields": [{"field": ".".join(str(part) for part in item["loc"]),
                                "message": item["msg"]} for item in error.errors()],
                }})
            response.headers["Cache-Control"] = "no-store"
            return response

        return guarded


def install_auth_routes(app, store, auth_config) -> None:
    router = APIRouter(route_class=_AuthRoute)

    async def handle_auth_error(request, error):
        return auth_error_response(error)

    app.add_exception_handler(AuthError, handle_auth_error)

    def clear_cookie(response):
        response.delete_cookie(auth_config.cookie_name, path="/", secure=auth_config.cookie_secure,
                               httponly=True, samesite="lax")

    def session_response(session, status_code=200):
        response = JSONResponse({"user": public_user(session["user"]),
                                 "csrf_token": session["csrf_token"], "expires_at": session["expires_at"]},
                                status_code=status_code)
        response.set_cookie(auth_config.cookie_name, session["token"], max_age=auth_config.session_days * 86400,
                            path="/", secure=auth_config.cookie_secure, httponly=True, samesite="lax")
        return response

    @router.post("/api/auth/register", status_code=201)
    def register(payload: _Register, request: Request):
        _public_write_guard(request, store, auth_config)
        session = store.register(**payload.model_dump(), sign_in=True, rate_key=request.client.host if request.client else None)
        return session_response(session, status_code=201)

    @router.post("/api/auth/login")
    def login(payload: _Login, request: Request):
        _public_write_guard(request, store, auth_config)
        session = store.authenticate(**payload.model_dump(), rate_key=request.client.host if request.client else None)
        return session_response(session)

    @router.get("/api/auth/me")
    def me(request: Request):
        session = authenticate_request(request, store, auth_config, require_approved=False, allow_password_change=True)
        return {"user": public_user(session["user"]), "csrf_token": session["csrf_token"],
                "expires_at": session["expires_at"]}

    @router.post("/api/auth/logout")
    def logout(request: Request, payload: _Input = Body(default=_Input())):
        authenticate_request(request, store, auth_config, require_approved=False, allow_password_change=True)
        store.logout(_session_token(request, auth_config))
        response = JSONResponse({"message": "已退出登录。"})
        clear_cookie(response)
        return response

    @router.post("/api/auth/change-password")
    def change_password(payload: _PasswordChange, request: Request):
        session = authenticate_request(request, store, auth_config, require_approved=False, allow_password_change=True)
        user = store.change_password(session["user"]["id"], **payload.model_dump())
        response = JSONResponse({"user": public_user(user), "message": "密码已更新，请重新登录。"})
        clear_cookie(response)
        return response

    @router.get("/api/admin/users")
    def list_users(request: Request, status: UserStatus | None = None,
                   page: int = Query(default=1, ge=1, le=1_000_000),
                   page_size: int = Query(default=20, ge=1, le=100)):
        authenticate_request(request, store, auth_config, require_admin=True)
        result = store.list_users(status=status, page=page, page_size=page_size)
        return {**result, "items": [{**public_user(user), **{
            key: user[key] for key in ("submitted_jobs", "execution_attempts") if key in user
        }} for user in result["items"]]}

    @router.get("/api/admin/stats")
    def stats(request: Request):
        authenticate_request(request, store, auth_config, require_admin=True)
        return store.stats()

    @router.patch("/api/admin/users/{user_id}/status")
    def change_status(payload: _StatusChange, request: Request, user_id: str = Path(min_length=1, max_length=64)):
        session = authenticate_request(request, store, auth_config, require_admin=True)
        user = store.update_status(session["user"]["id"], user_id, payload.status)
        return {"user": public_user(user)}

    @router.delete("/api/admin/users/{user_id}")
    def delete_user(request: Request, user_id: str = Path(min_length=1, max_length=64),
                    payload: _Input = Body(default=_Input())):
        session = authenticate_request(request, store, auth_config, require_admin=True)
        return {"user": public_user(store.delete_user(session["user"]["id"], user_id))}

    @router.post("/api/admin/users/{user_id}/reset-password")
    def reset_password(request: Request, user_id: str = Path(min_length=1, max_length=64),
                       payload: _Input = Body(default=_Input())):
        session = authenticate_request(request, store, auth_config, require_admin=True)
        result = store.reset_password(session["user"]["id"], user_id)
        return {"user": public_user(result["user"]), "temporary_password": result["temporary_password"]}

    app.include_router(router)
