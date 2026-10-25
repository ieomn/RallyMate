"""Opt-in, bounded pose request observations; never log request/response content.

The ASGI events describe calls to the server's send callable, not browser receipt.
The interval after route_return includes framework scheduling/validation and must
not be interpreted as pure encoding time. DB timing includes SELECT and JSON.
phase_delta_ms is wall time since the preceding observation; span_ms is wall
time between the named start/end boundaries. Neither is exclusive CPU time.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
import os
import re
import time


LOGGER = logging.getLogger("uvicorn.error")
_STATE_KEY = "_pose_request_diagnostics"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ROUTE = re.compile(r"/v1/jobs/" + _UUID + r"/pose-preview", re.IGNORECASE)
# Match the web diagnostic convention, plus the API's existing uuid4().hex IDs.
_REQUEST_ID = re.compile(r"(?:" + _UUID + r"|[0-9a-f]{32}|web-[a-z0-9]{1,16}-[a-z0-9]{1,16})", re.IGNORECASE)
PHASES = frozenset({
    "api_entry", "db_get_job_start", "db_get_job_end", "pose_lock_wait",
    "pose_lock_acquired", "build_start", "build_end", "route_return",
    "response_start_send", "response_start_sent", "body_final_send",
    "body_final_sent", "disconnect", "send_error", "app_error",
})
ERRORS = frozenset({"none", "cancelled", "database_error", "build_error", "send_error", "application_error"})
_REFERENCES = {
    "db_get_job_end": "db_get_job_start", "pose_lock_acquired": "pose_lock_wait",
    "build_end": "build_start", "route_return": "api_entry",
    "response_start_send": "route_return", "response_start_sent": "response_start_send",
    "body_final_sent": "body_final_send",
}
_STARTS = frozenset({"api_entry", "db_get_job_start", "pose_lock_wait", "build_start",
                     "route_return", "response_start_send", "body_final_send"})
_MAX_COUNT = 2**53 - 1


def pose_request_diagnostics_enabled() -> bool:
    try:
        return os.environ.get("RALLYMATE_POSE_REQUEST_DIAGNOSTICS") == "1"
    except BaseException:
        return False


def _write_record(record: dict) -> None:
    LOGGER.info("pose_request_diagnostic %s", json.dumps(record, allow_nan=False, separators=(",", ":")))


class _Trace:
    def __init__(self):
        self.started = time.perf_counter()
        self.previous = self.started
        self.request_id = None
        self.starts = {}
        self.observations = 0

    def record(self, phase, *, status=None, cache_hit=None, cancelled=False,
               error="none", body_bytes=None, body_messages=None):
        # This entire observation (including clocks and sink) is non-authoritative.
        try:
            if self.request_id is None or phase not in PHASES or error not in ERRORS or self.observations >= 32:
                return
            now = time.perf_counter()
            elapsed = (now - self.started) * 1000
            reference = self.starts.get(_REFERENCES.get(phase))
            if phase in _STARTS:
                self.starts[phase] = now
            if not math.isfinite(elapsed) or elapsed < 0:
                return
            record = {
                "event": "pose_request_diagnostic", "route": "pose_preview",
                "request_id": self.request_id, "phase": phase,
                "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "elapsed_ms": round(elapsed, 3), "cancelled": cancelled is True,
                "error": error,
            }
            delta = (now - self.previous) * 1000
            self.previous = now
            if math.isfinite(delta) and delta >= 0:
                record["phase_delta_ms"] = round(delta, 3)
            if reference is not None:
                duration = (now - reference) * 1000
                if math.isfinite(duration) and duration >= 0:
                    record["span_ms"] = round(duration, 3)
            if type(status) is int and 100 <= status <= 599:
                record["status"] = status
            if type(cache_hit) is bool:
                record["cache_hit"] = cache_hit
            for name, value in (("body_bytes", body_bytes), ("body_messages", body_messages)):
                if type(value) is int and 0 <= value <= _MAX_COUNT:
                    record[name] = value
            self.observations += 1
            _write_record(record)
        except BaseException:
            pass


def _trace(request):
    try:
        return request.scope.get("state", {}).get(_STATE_KEY)
    except BaseException:
        return None


def bind_pose_request_diagnostics(request) -> None:
    """Reuse the ID assigned by the existing middleware; never mint another ID."""
    try:
        trace = _trace(request)
        request_id = request.state.request_id
        if trace is not None and type(request_id) is str and _REQUEST_ID.fullmatch(request_id):
            trace.request_id = request_id
            trace.record("api_entry")
    except BaseException:
        pass


def record_pose_phase(request, phase: str, **fields) -> None:
    try:
        trace = _trace(request)
        if trace is not None:
            trace.record(phase, **fields)
    except BaseException:
        pass


@contextmanager
def pose_diagnostic_stage(request, stage: str):
    """Observe a business call without changing its return/exception/cancellation."""
    record_pose_phase(request, stage + "_start")
    try:
        yield
    except BaseException as error:
        cancelled = isinstance(error, asyncio.CancelledError)
        category = "cancelled" if cancelled else ("database_error" if stage == "db_get_job" else "build_error")
        record_pose_phase(request, stage + "_end", error=category, cancelled=cancelled)
        raise
    else:
        record_pose_phase(request, stage + "_end")


class PoseRequestDiagnosticsMiddleware:
    """Pass through each original ASGI message once, without buffering its body."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        trace = None
        try:
            if scope.get("type") == "http" and scope.get("method") == "GET" and _ROUTE.fullmatch(scope.get("path", "")):
                trace = _Trace()
                scope.setdefault("state", {})[_STATE_KEY] = trace
        except BaseException:
            trace = None
        if trace is None:
            await self.app(scope, receive, send)
            return
        body_bytes = body_messages = 0

        async def observed_receive():
            message = await receive()
            try:
                if message.get("type") == "http.disconnect":
                    # A received disconnect does not prove the sync builder stopped.
                    trace.record("disconnect")
            except BaseException:
                pass
            return message

        async def observed_send(message):
            nonlocal body_bytes, body_messages
            kind = None
            final_body = False
            try:
                kind = message.get("type")
                final_body = kind == "http.response.body" and not message.get("more_body", False)
                if kind == "http.response.start":
                    trace.record("response_start_send", status=message.get("status"))
                elif final_body:
                    trace.record("body_final_send")
            except BaseException:
                pass
            try:
                await send(message)
            except BaseException as error:
                # Only observation errors are suppressed; send errors propagate.
                cancelled = isinstance(error, asyncio.CancelledError)
                trace.record("send_error", error="cancelled" if cancelled else "send_error", cancelled=cancelled)
                raise
            try:
                if kind == "http.response.start":
                    trace.record("response_start_sent", status=message.get("status"))
                elif kind == "http.response.body":
                    body_bytes = min(_MAX_COUNT, body_bytes + len(message.get("body", b"")))
                    body_messages = min(_MAX_COUNT, body_messages + 1)
                    if final_body:
                        trace.record("body_final_sent", body_bytes=body_bytes, body_messages=body_messages)
            except BaseException:
                pass

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            trace.record("app_error", error="cancelled" if cancelled else "application_error", cancelled=cancelled)
            raise
