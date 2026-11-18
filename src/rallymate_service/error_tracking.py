"""Optional error reporting with explicit account and request-data boundaries."""
from __future__ import annotations

from contextlib import nullcontext
import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

_enabled = False
_configuration = None
_SECRET = re.compile(r"(?i)(\b(?:password|passwd|temporary_password|authorization|cookie|csrf_token|jwt_secret|api_key|token|secret)\b[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+")
_URL = re.compile(r"(?:https?|postgres(?:ql)?(?:\+psycopg)?)://[^\s<>\"']+")
_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def clean_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def safe_message(value: str) -> str:
    value = _TOKEN.sub("[session removed]", value)
    value = _URL.sub(lambda match: clean_url(match.group()), value)
    return _SECRET.sub(r"\1[removed]", value)


def scrub_event(event: dict, hint: dict | None = None) -> dict:
    """Retain error stacks and safe provenance, never credentials or request bodies."""
    if hint is not None:
        hint.pop("attachments", None)
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {key: clean_url(value) if key == "url" else value
                            for key, value in request.items() if key in {"method", "url"} and isinstance(value, str)}
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    event.pop("spans", None)
    event.pop("_meta", None)
    if "transaction" in event and (event.get("transaction_info") or {}).get("source") != "route":
        event["transaction"] = "RallyMate operation"
    if "logentry" in event:
        event["logentry"] = {"message": "RallyMate application error"}
    if "message" in event:
        event["message"] = "RallyMate application error"
    record = (hint or {}).get("log_record")
    provenance = getattr(record, "rallymate_provenance", None)
    contexts = event.get("contexts", {})
    event["contexts"] = {key: value for key, value in contexts.items() if key in {"trace", "runtime", "os"}}
    if isinstance(event["contexts"].get("trace"), dict):
        # Sampling baggage and span data can carry user input outside the request.
        event["contexts"]["trace"] = {
            key: value for key, value in event["contexts"]["trace"].items()
            if key in {"trace_id", "span_id", "parent_span_id", "op", "status", "origin"}
        }
    safe = {key: value for key, value in (provenance or contexts.get("rallymate", {})).items()
            if key in {"job_id", "attempt", "pose_backend", "device", "request_id", "route"}
            and isinstance(value, (str, int, float, bool))}
    if safe:
        event["contexts"]["rallymate"] = safe
    user_id = (provenance or {}).get("user_id") or (event.get("user") or {}).get("id")
    event["user"] = {"id": str(user_id)} if user_id else {}
    event["tags"] = {key: value for key, value in event.get("tags", {}).items()
                     if key in {"service", "environment", "pose_backend"}}
    for exception in (event.get("exception") or {}).get("values", []):
        # Arbitrary exception text can include complete SQL, user input or third-party
        # response bodies. The exception type and original frame locations remain.
        exception["value"] = "Application error; inspect stack trace and safe job context"
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
            frame.pop("pre_context", None)
            frame.pop("post_context", None)
            frame.pop("context_line", None)
    return event


def _drop_telemetry(event, hint):
    # Structured logs and SDK metrics use separate envelopes, not before_send.
    return None


def initialize_error_tracking(settings, service: str) -> None:
    global _enabled, _configuration
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    if not 0 <= sample_rate <= 1:
        raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1")
    environment = os.getenv("SENTRY_ENVIRONMENT", "").strip() or settings.environment
    configuration = (dsn, environment, os.getenv("SENTRY_RELEASE", ""), sample_rate, service)
    if configuration == _configuration:
        return
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration
    sentry_sdk.init(dsn=dsn, environment=environment, release=configuration[2] or None,
        send_default_pii=False, include_local_variables=False, include_source_context=False,
        max_request_body_size="never", max_breadcrumbs=0, enable_logs=False,
        before_send_log=_drop_telemetry, before_send_metric=_drop_telemetry,
        trace_lifecycle="static", stream_gen_ai_spans=False, auto_session_tracking=False,
        profiles_sample_rate=0, profile_session_sample_rate=0, spotlight=False,
        traces_sample_rate=sample_rate, before_send=scrub_event, before_send_transaction=scrub_event,
        integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)])
    sentry_sdk.set_tag("service", service)
    _enabled, _configuration = True, configuration


def request_scope():
    if not _enabled:
        return nullcontext()
    import sentry_sdk
    return sentry_sdk.isolation_scope()


def set_account_context(user: dict) -> None:
    if _enabled:
        import sentry_sdk
        sentry_sdk.set_user({"id": user["id"]})


def capture_request_error(error: Exception) -> None:
    if _enabled:
        import sentry_sdk
        sentry_sdk.capture_exception(error)
