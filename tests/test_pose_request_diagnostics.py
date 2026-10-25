from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from rallymate_service import pose_request_diagnostics as diagnostics
from rallymate_vision.pose_playback import PosePlaybackError
import test_trajectory_api as fixture


JOB = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "f67b39de-cf4d-4298-b175-fbc5a0c094f1"
OTHER_ID = "f67b39de-cf4d-4298-b175-fbc5a0c094f2"
SECRET = "PRIVATE_BODY_HEADER_QUERY_EXCEPTION"
URL = f"/v1/jobs/{JOB}/pose-preview"


def scope(path=URL, method="GET"):
    return {"type": "http", "method": method, "path": path, "state": {},
            "query_string": SECRET.encode(), "headers": [(b"x-secret", SECRET.encode())]}


def bind(state, request_id=REQUEST_ID):
    request = Request(state)
    request.state.request_id = request_id
    diagnostics.bind_pose_request_diagnostics(request)
    return request


class PoseDiagnosticsASGITests(unittest.TestCase):
    def test_flag_requires_exact_one(self):
        for value in (None, "", "0", "true", " 1", "1"):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ["RALLYMATE_POSE_REQUEST_DIAGNOSTICS"] = value
                self.assertEqual(diagnostics.pose_request_diagnostics_enabled(), value == "1")

    def test_original_messages_and_receive_calls_are_unchanged(self):
        rows, sent = [], []
        start = {"type": "http.response.start", "status": 200, "headers": [(b"x-secret", SECRET.encode())]}
        first = {"type": "http.response.body", "body": SECRET.encode(), "more_body": True}
        final = {"type": "http.response.body", "body": b"tail", "more_body": False}
        received = [{"type": "http.request", "body": SECRET.encode()}, {"type": "http.disconnect"}]
        calls = []

        async def receive():
            calls.append(1)
            return received[len(calls) - 1]

        async def send(message):
            sent.append(message)

        async def app(state, read, write):
            request = bind(state)
            self.assertIs(await read(), received[0])
            diagnostics.record_pose_phase(request, "route_return")
            for message in (start, first, final):
                await write(message)
            self.assertIs(await read(), received[1])

        with patch.object(diagnostics, "_write_record", side_effect=rows.append):
            asyncio.run(diagnostics.PoseRequestDiagnosticsMiddleware(app)(scope(), receive, send))
        self.assertEqual(len(calls), 2)
        for actual, expected in zip(sent, (start, first, final)):
            self.assertIs(actual, expected)
        self.assertEqual([row["phase"] for row in rows], ["api_entry", "route_return", "response_start_send",
                         "response_start_sent", "body_final_send", "body_final_sent", "disconnect"])
        completed = next(row for row in rows if row["phase"] == "body_final_sent")
        self.assertEqual(completed["body_bytes"], len(SECRET.encode()) + 4)
        self.assertEqual(completed["body_messages"], 2)
        self.assertFalse(rows[-1]["cancelled"])
        self.assertNotIn(SECRET, json.dumps(rows))
        self.assertTrue(all(row["request_id"] == REQUEST_ID for row in rows))
        self.assertTrue(all(row["utc"].endswith("Z") and row["elapsed_ms"] >= 0 for row in rows))
        self.assertIn("span_ms", completed)

    def test_only_matching_get_and_safe_existing_ids_emit(self):
        cases = [(URL, "GET", REQUEST_ID, True), (URL, "GET", "a" * 32, True),
                 (URL, "GET", "web-abc123-xyz", True), (URL, "GET", SECRET, False),
                 (URL, "GET", REQUEST_ID + "\n", False), (URL, "POST", REQUEST_ID, False),
                 ("/health/live", "GET", REQUEST_ID, False),
                 ("/v1/jobs/private-user-string/pose-preview", "GET", REQUEST_ID, False)]
        for path, method, request_id, expected in cases:
            rows = []

            async def app(state, receive, send):
                request = bind(state, request_id)
                diagnostics.record_pose_phase(request, "route_return")

            with self.subTest(path=path, method=method, request_id=request_id), patch.object(diagnostics, "_write_record", side_effect=rows.append):
                asyncio.run(diagnostics.PoseRequestDiagnosticsMiddleware(app)(scope(path, method), None, None))
                self.assertEqual(bool(rows), expected)
                self.assertTrue(all(row["request_id"] == request_id for row in rows))

    def test_sink_and_clock_failures_cannot_change_business(self):
        for fault in (RuntimeError(SECRET), asyncio.CancelledError(SECRET)):
            sent = []

            async def send(message):
                sent.append(message)

            async def app(state, receive, write):
                request = bind(state)
                with diagnostics.pose_diagnostic_stage(request, "build"):
                    pass
                await write({"type": "http.response.body", "body": b"ok"})

            with patch.object(diagnostics.LOGGER, "info", side_effect=fault):
                asyncio.run(diagnostics.PoseRequestDiagnosticsMiddleware(app)(scope(), None, send))
            self.assertEqual(sent[0]["body"], b"ok")
        with patch.object(diagnostics.time, "perf_counter", side_effect=RuntimeError(SECRET)):
            asyncio.run(diagnostics.PoseRequestDiagnosticsMiddleware(app)(scope(), None, send))
        self.assertEqual(len(sent), 2)

    def test_business_and_send_cancellation_propagate_unchanged(self):
        for failure in (RuntimeError(SECRET), asyncio.CancelledError(SECRET)):
            rows = []

            async def send(message):
                raise failure

            async def app(state, receive, write):
                bind(state)
                await write({"type": "http.response.body", "body": b"ok"})

            async def exercise():
                # Verify the middleware boundary directly. Python 3.10's
                # asyncio.run may reconstruct CancelledError at its Task boundary.
                with self.assertRaises(type(failure)) as raised:
                    await diagnostics.PoseRequestDiagnosticsMiddleware(app)(scope(), None, send)
                self.assertIs(raised.exception, failure)

            with patch.object(diagnostics, "_write_record", side_effect=rows.append):
                asyncio.run(exercise())
            self.assertNotIn("body_final_sent", [row["phase"] for row in rows])
            self.assertEqual(rows[-1]["cancelled"], isinstance(failure, asyncio.CancelledError))
            self.assertNotIn(SECRET, json.dumps(rows))
            with self.assertRaises(type(failure)) as raised:
                with diagnostics.pose_diagnostic_stage(Request(scope()), "db_get_job"):
                    raise failure
            self.assertIs(raised.exception, failure)

    def test_overlapping_requests_keep_their_own_id(self):
        rows = []

        async def exercise():
            arrived = 0
            ready = asyncio.Event()

            async def app(state, receive, send):
                nonlocal arrived
                request = bind(state, state["test_id"])
                with diagnostics.pose_diagnostic_stage(request, "build"):
                    arrived += 1
                    if arrived == 2:
                        ready.set()
                    await ready.wait()
                diagnostics.record_pose_phase(request, "route_return")

            middleware = diagnostics.PoseRequestDiagnosticsMiddleware(app)
            await asyncio.gather(*(middleware(dict(scope(), test_id=value), None, None) for value in (REQUEST_ID, OTHER_ID)))

        with patch.object(diagnostics, "_write_record", side_effect=rows.append):
            asyncio.run(exercise())
        for value in (REQUEST_ID, OTHER_ID):
            self.assertEqual([row["phase"] for row in rows if row["request_id"] == value],
                             ["api_entry", "build_start", "build_end", "route_return"])

    def test_fixed_whitelist_ignores_unknown_observations(self):
        rows = []
        state = scope()
        state["state"][diagnostics._STATE_KEY] = diagnostics._Trace()
        with patch.object(diagnostics, "_write_record", side_effect=rows.append):
            request = bind(state)
            diagnostics.record_pose_phase(request, SECRET)
            diagnostics.record_pose_phase(request, "build_end", error=SECRET)
            diagnostics.record_pose_phase(request, "route_return", body_bytes=float("inf"), status=SECRET)
        allowed = {"event", "route", "request_id", "phase", "utc", "elapsed_ms", "phase_delta_ms",
                   "span_ms", "cancelled", "error", "status", "cache_hit", "body_bytes", "body_messages"}
        self.assertTrue(all(set(row) <= allowed for row in rows))
        self.assertEqual(len(rows), 2)
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_repeated_observations_are_bounded(self):
        rows = []
        state = scope()
        state["state"][diagnostics._STATE_KEY] = diagnostics._Trace()
        with patch.object(diagnostics, "_write_record", side_effect=rows.append):
            request = bind(state)
            for _ in range(100):
                diagnostics.record_pose_phase(request, "disconnect")
        self.assertEqual(len(rows), 32)


class PoseDiagnosticsAPITests(unittest.TestCase):
    def app(self, root, *, enabled="1", api_key=None):
        helper = fixture.TrajectoryApiTests()
        with patch.dict(os.environ, {"RALLYMATE_POSE_REQUEST_DIAGNOSTICS": enabled}):
            app, settings, database = helper._app(root, api_key=api_key)
        helper._succeeded_job(settings, database)
        return app, settings, database

    def test_default_off_and_on_keep_payload_cache_and_request_id(self):
        results = []
        for enabled in ("0", "1"):
            with tempfile.TemporaryDirectory() as directory:
                app, settings, db = self.app(Path(directory), enabled=enabled)
                rows = []
                with patch.object(diagnostics, "_write_record", side_effect=rows.append), patch(
                    "rallymate_service.api.build_pose_playback", return_value={"frames": [], "private": SECRET}
                ) as builder, TestClient(app) as client:
                    responses = [client.get(URL, headers={"X-Request-ID": value}) for value in (REQUEST_ID, OTHER_ID)]
                self.assertEqual(builder.call_count, 1)
                self.assertEqual([response.headers["x-request-id"] for response in responses], [REQUEST_ID, OTHER_ID])
                self.assertTrue(all(response.status_code == 200 for response in responses))
                results.append([response.json() for response in responses])
                if enabled == "0":
                    self.assertEqual(rows, [])
                else:
                    phases = [row["phase"] for row in rows if row["request_id"] == REQUEST_ID]
                    self.assertEqual(phases[:8], ["api_entry", "db_get_job_start", "db_get_job_end", "pose_lock_wait",
                                                  "pose_lock_acquired", "build_start", "build_end", "route_return"])
                    self.assertEqual([row["cache_hit"] for row in rows if row["phase"] == "pose_lock_acquired"], [False, True])
                    self.assertFalse(any(row["phase"] == "build_start" and row["request_id"] == OTHER_ID for row in rows))
                    self.assertIn("body_final_sent", phases)
                    self.assertNotIn(SECRET, json.dumps(rows))
        self.assertEqual(results[0], results[1])

    def test_auth_missing_job_and_query_rejections_have_status_without_build(self):
        with tempfile.TemporaryDirectory() as directory:
            app, _, _ = self.app(Path(directory), api_key="secret")
            cases = [(URL, {}, 401), (URL.replace(JOB, OTHER_ID), {"Authorization": "Bearer secret"}, 404),
                     (URL + "?sample_limit=601", {"Authorization": "Bearer secret"}, 422)]
            with TestClient(app) as client:
                for url, headers, status in cases:
                    rows = []
                    with patch.object(diagnostics, "_write_record", side_effect=rows.append):
                        response = client.get(url, headers={"X-Request-ID": REQUEST_ID, **headers})
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(rows[0]["phase"], "api_entry")
                    self.assertTrue(any(row["phase"] == "response_start_sent" and row["status"] == status for row in rows))
                    self.assertFalse(any(row["phase"] in {"build_start", "route_return"} for row in rows))

    def test_build_failure_and_outer_500_are_distinguished(self):
        for failure, status in ((PosePlaybackError(SECRET), 422), (RuntimeError(SECRET), 500)):
            with tempfile.TemporaryDirectory() as directory:
                app, _, _ = self.app(Path(directory))
                rows = []
                with patch.object(diagnostics, "_write_record", side_effect=rows.append), patch(
                    "rallymate_service.api.build_pose_playback", side_effect=failure
                ), TestClient(app, raise_server_exceptions=False) as client:
                    response = client.get(URL, headers={"X-Request-ID": REQUEST_ID})
                self.assertEqual(response.status_code, status)
                self.assertTrue(any(row["phase"] == "build_end" and row["error"] == "build_error" for row in rows))
                self.assertNotIn("route_return", [row["phase"] for row in rows])
                self.assertNotIn(SECRET, json.dumps(rows))
                if status == 500:
                    # Starlette's outer ServerErrorMiddleware owns this response.
                    self.assertIn("app_error", [row["phase"] for row in rows])
                    self.assertFalse(any(row.get("status") == 500 for row in rows))

    def test_database_error_stays_an_error_and_logging_failure_is_inert(self):
        with tempfile.TemporaryDirectory() as directory:
            app, _, db = self.app(Path(directory))
            rows = []
            with TestClient(app, raise_server_exceptions=False) as client:
                with patch.object(diagnostics, "_write_record", side_effect=rows.append), patch.object(db, "get_job", side_effect=RuntimeError(SECRET)):
                    response = client.get(URL, headers={"X-Request-ID": REQUEST_ID})
                self.assertEqual(response.status_code, 500)
                self.assertTrue(any(row["phase"] == "db_get_job_end" and row["error"] == "database_error" for row in rows))
                with patch.object(diagnostics.LOGGER, "info", side_effect=RuntimeError(SECRET)), patch(
                    "rallymate_service.api.build_pose_playback", return_value={"frames": []}
                ):
                    self.assertEqual(client.get(URL, headers={"X-Request-ID": REQUEST_ID}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
