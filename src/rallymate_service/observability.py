"""HTTP/queue metrics and worker log observation outside inference implementations."""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import logging
import os
from pathlib import Path
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, start_http_server
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from sqlalchemy import func, select
from starlette.routing import Match

from .error_tracking import capture_request_error, initialize_error_tracking, request_scope
from .models import jobs


class QueueMetrics:
    def __init__(self, runtime):
        self.runtime = runtime

    def collect(self):
        available = GaugeMetricFamily("rallymate_database_available", "Account/queue database responds to a read")
        count = GaugeMetricFamily("rallymate_jobs", "Retained jobs by current state, including logical deletion", labels=["status"])
        attempts = CounterMetricFamily("rallymate_job_attempts", "Retained cumulative execution attempts")
        try:
            if not self.runtime.ready:
                raise RuntimeError("not ready")
            with self.runtime.engine.connect() as connection:
                rows = connection.execute(select(jobs.c.status, func.count(), func.coalesce(func.sum(jobs.c.attempts), 0)).group_by(jobs.c.status)).all()
            values = {row[0]: row[1] for row in rows}
            for status in ("queued", "running", "succeeded", "failed", "cancelled"):
                count.add_metric([status], values.get(status, 0))
            attempts.add_metric([], sum(row[2] for row in rows))
            available.add_metric([], 1)
        except Exception:
            available.add_metric([], 0)
        yield available
        yield count
        yield attempts


class HttpMetricsMiddleware:
    def __init__(self, app, *, application, registry):
        self.app, self.application = app, application
        self.requests = Counter("rallymate_http_requests_total", "HTTP responses including rejected requests",
                                ["method", "route", "status"], registry=registry)
        buckets = (.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900, 3600)
        self.duration = Histogram("rallymate_http_request_duration_seconds", "HTTP time through completion of the response body",
                                  ["method", "route"], buckets=buckets, registry=registry)
        self.headers = Histogram("rallymate_http_time_to_headers_seconds", "HTTP time to response headers",
                                 ["method", "route"], buckets=buckets, registry=registry)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].rstrip("/").endswith("/metrics"):
            return await self.app(scope, receive, send)
        route = "unmatched"
        for candidate in self.application.routes:
            match, _ = candidate.matches(scope)
            if match == Match.FULL:
                route = getattr(candidate, "path", "unmatched")
                break
        method = scope["method"] if scope["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        started, status, finished = time.perf_counter(), 500, False

        async def measured_send(message):
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
                self.headers.labels(method, route).observe(time.perf_counter() - started)
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True

        try:
            with request_scope():
                try:
                    await self.app(scope, receive, measured_send)
                except Exception as error:
                    # Capture before leaving the authenticated request's isolated scope.
                    capture_request_error(error)
                    raise
        finally:
            if not finished and status < 400:
                status = 499
            self.requests.labels(method, route, str(status)).inc()
            self.duration.labels(method, route).observe(time.perf_counter() - started)


def install_api_observability(app, settings):
    initialize_error_tracking(settings, "api")
    token_file = os.getenv("METRICS_TOKEN_FILE")
    token = Path(token_file).read_text(encoding="utf-8").strip() if token_file else os.getenv("METRICS_TOKEN", "").strip()
    if token and (len(token) < 32 or len(set(token)) < 12):
        raise ValueError("METRICS_TOKEN must contain at least 32 random characters")
    registry = CollectorRegistry()
    registry.register(QueueMetrics(app.state.account_runtime))
    app.state.metrics_registry = registry

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        if not token:
            raise HTTPException(404, "Not found")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Metrics authentication required", headers={"WWW-Authenticate": "Bearer"})
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST,
                        headers={"Cache-Control": "no-store"})

    app.add_middleware(HttpMetricsMiddleware, application=app, registry=registry)


class WorkerLogObserver(logging.Handler):
    """Observe existing job lifecycle messages; never wrap or replace the inference runner."""
    terminal = {
        "job %s failed": "failed", "job %s cancellation acknowledged after runner exited": "cancelled",
        "job %s worker attempt lost ownership; stopped": "ownership_lost",
    }

    def __init__(self, database, settings, registry, clock=time.perf_counter):
        super().__init__(logging.INFO)
        self.database, self.settings, self.clock = database, settings, clock
        self.active = {}
        self.duration = Histogram("rallymate_inference_attempt_duration_seconds", "Worker attempt wall time including CPU/postprocessing",
            ["status"], buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400), registry=registry)
        self.attempts = Counter("rallymate_inference_attempts_total", "Observed worker attempts completed since this process started",
                                ["status"], registry=registry)

    def emit(self, record):
        try:
            if not isinstance(record.args, tuple) or not record.args or not isinstance(record.args[0], str):
                return
            identifier = record.args[0]
            if record.msg == "processing job %s":
                provenance = {"job_id": identifier, "pose_backend": self.settings.pose_backend,
                              "device": str(self.settings.device)}
                try:
                    with self.database.connect() as connection:
                        row = connection.execute("SELECT user_id, attempts FROM jobs WHERE id = ?", (identifier,)).fetchone()
                    if row:
                        provenance.update(user_id=row["user_id"], attempt=row["attempts"])
                except Exception:
                    pass
                self.active[identifier] = (self.clock(), provenance)
            elif identifier in self.active:
                started, provenance = self.active[identifier]
                record.rallymate_provenance = provenance
                status = self.terminal.get(record.msg)
                if record.msg == "job %s finished with state %s" and len(record.args) > 1:
                    status = record.args[1] if record.args[1] in {"succeeded", "failed", "cancelled", "ownership_lost"} else "other"
                if status:
                    self.active.pop(identifier)
                    self.attempts.labels(status).inc()
                    self.duration.labels(status).observe(max(0, self.clock() - started))
        except Exception:
            # Telemetry must not change job completion, cancellation, or exception handling.
            return


@contextmanager
def observe_worker(settings, database):
    initialize_error_tracking(settings, "worker")
    registry = CollectorRegistry()
    up = Gauge("rallymate_worker_up", "Observed worker process is running", registry=registry)
    up.set(1)
    observer = WorkerLogObserver(database, settings, registry)
    logger = logging.getLogger("rallymate.worker")
    logger.addHandler(observer)
    server = thread = None
    try:
        port = int(os.getenv("RALLYMATE_WORKER_METRICS_PORT", "0"))
        if not 0 <= port <= 65535:
            raise ValueError("RALLYMATE_WORKER_METRICS_PORT must be between 0 and 65535")
        if port:
            server, thread = start_http_server(port, addr=os.getenv("RALLYMATE_WORKER_METRICS_HOST", "127.0.0.1"), registry=registry)
        yield
    finally:
        up.set(0)
        logger.removeHandler(observer)
        observer.close()
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
