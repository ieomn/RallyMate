"""One-job, bounded cancellation acceptance against a pinned deployment receipt.

Requires qa_workload_ready=true and a decodable local video of at most 64 MiB.
This runner never starts a second job to chase a missed queued/running phase.
API cancellation does not establish GPU release; collect independent telemetry.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time

if __package__:
    from .cloud_acceptance_probe import Probe, SUCCESS, TERMINAL, deployment_check, digest, read_json, seconds_between, target_from_receipt, upload
else:
    from cloud_acceptance_probe import Probe, SUCCESS, TERMINAL, deployment_check, digest, read_json, seconds_between, target_from_receipt, upload


ACTIVE = {"queued", "running", "processing"}
IDENTITY_FIELDS = ("id", "created_at", "original_filename")
STABLE_FIELDS = (*IDENTITY_FIELDS, "status", "started_at", "attempts", "cancel_requested", "cancel_requested_at", "cancelled_at")
REPORT_FIELDS = ("summary", "result", "artifact_urls", "demo_result_url", "trajectory_url", "technique_assessment_url")
PRESERVED_OUTPUT_FIELDS = ("summary", "result", "artifact_urls", "demo_result_url", "technique_assessment_url")
SAFE_ERRORS = {"upload_create_failed", "invalid_chunk_size", "chunk_not_acknowledged", "upload_resume_failed",
               "upload_complete_failed", "job_deadline_exceeded", "job_poll_failed", "job_response_mismatch",
               "invalid_job_state", "cancel_validation_failed", "deployment_identity_failed"}


def cancelled_evidence_valid(job):
    return (job.get("status") == "cancelled" and job.get("cancel_requested") is True
            and seconds_between(job.get("cancel_requested_at"), job.get("cancelled_at")) is not None)


def never_claimed(job):
    return "started_at" in job and job["started_at"] is None and type(job.get("attempts")) is int and job["attempts"] == 0


def contains_preserved(previous, current):
    if isinstance(previous, dict):
        return isinstance(current, dict) and all(key in current and contains_preserved(value, current[key]) for key, value in previous.items())
    return previous == current


def baseline_preserved(before, after):
    return (all(key in after and after[key] == before[key] for key in IDENTITY_FIELDS if key in before)
            and all(contains_preserved(before[key], after.get(key)) for key in PRESERVED_OUTPUT_FIELDS
                    if before.get(key) not in (None, {}, [], "")))


def validate_video(video: Path):
    if (not video.is_file() or video.suffix.lower() not in {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
            or not 0 < video.stat().st_size <= 64 * 1024 * 1024):
        raise ValueError("Use a real local video of at most 64 MiB")
    import cv2

    capture = cv2.VideoCapture(str(video.resolve()))
    try:
        if not capture.isOpened():
            raise ValueError("Local video cannot be decoded")
        ok, frame = capture.read()
        if not ok or frame is None or frame.ndim < 2 or min(frame.shape[:2]) <= 0:
            raise ValueError("Local video has no decodable frame")
        return {"first_frame_decoded": True, "width": int(frame.shape[1]), "height": int(frame.shape[0])}
    finally:
        capture.release()


def run_cancellation(receipt: dict, video: Path, *, when="running", max_frames=150, deadline_seconds=180, poll_seconds=2):
    # Validate the immutable target and all bounds before constructing any HTTP client.
    base, revision = target_from_receipt(receipt, workload=True)
    if when not in {"queued", "running"} or not (1 <= max_frames <= 600 and 30 <= deadline_seconds <= 1200 and 1 <= poll_seconds <= 10):
        raise ValueError("Bounds: queued|running, 1..600 frames, 30..1200 total seconds, 1..10 poll seconds")
    video_info = validate_video(video)
    video_hash = digest(video.read_bytes())
    started = time.monotonic()
    deadline = started + deadline_seconds
    probe = Probe(base, deadline=deadline)
    report = {"schema_version": "cloud-cancellation-probe-v1", "started_at": datetime.now(timezone.utc).isoformat(),
              "base_url": base, "revision_sha256": revision, "video_sha256": video_hash, "video_validation": video_info,
              "when": when, "max_frames": max_frames, "deadline_seconds": deadline_seconds, "job_limit": 1,
              "job_id": None, "passed": False, "outcome": "failed", "target_phase_observed": False, "cancel_phase_exercised": False,
              "cancellation_confirmed": False, "stop_latency_seconds": None, "states": [], "checks": [],
              "deployment_checks": [],
              "gpu_release_verified": False, "limitations": [
                  "A decoded input and mock tests do not establish recognition or scoring accuracy.",
                  "stop_latency_seconds is client-observed request-to-cancelled time, including polling delay.",
                  "GPU/VRAM/RAM/resource release requires independent timestamped server telemetry.",
                  "Timeout never submits another job; inspect the recorded job/upload ID before further workload."]}
    terminal_job = None
    cancel_started = None
    before_cancel = []
    phase_job = None

    def check_preserved(payload, source):
        report["checks"].append({"name": "pre_cancel_identity_and_outputs_preserved", "source": source,
                                 "passed": all(baseline_preserved(before, payload) for before in before_cancel)})

    def ensure_time():
        if time.monotonic() >= deadline:
            raise ValueError("job_deadline_exceeded")

    def wait_poll():
        ensure_time()
        time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))

    def record_job(payload, source):
        nonlocal terminal_job
        if not isinstance(payload, dict) or payload.get("id") != report["job_id"]:
            raise ValueError("job_response_mismatch")
        state = payload.get("status")
        if not isinstance(state, str) or state not in ACTIVE | TERMINAL:
            raise ValueError("invalid_job_state")
        if source == "before_cancel":
            before_cancel.append(deepcopy(payload))
        else:
            check_preserved(payload, source)
        report["states"].append({"elapsed_seconds": round(time.monotonic() - started, 6), "source": source,
                                 "status": state, "cancel_requested": payload.get("cancel_requested") is True})
        if state in TERMINAL:
            if terminal_job is None:
                terminal_job = payload
                report["terminal_status"] = state
                if state == "cancelled":
                    evidence_valid = cancelled_evidence_valid(payload)
                    report["checks"].append({"name": "cancelled_evidence_fields_valid", "passed": evidence_valid})
                    if cancel_started is not None and evidence_valid:
                        report["cancellation_confirmed"] = True
                        report["stop_latency_seconds"] = round(time.monotonic() - cancel_started, 6)
        return payload

    def poll(source):
        ensure_time()
        status, payload = probe.request("GET", f"/v1/jobs/{report['job_id']}?report_contract=current")
        if status is None or status in {408, 429} or isinstance(status, int) and status >= 500:
            return None
        if status != 200:
            raise ValueError("job_poll_failed")
        return record_job(payload, source)

    try:
        report["deployment_checks"].append(deployment_check(probe, revision, "before_upload"))
        if not report["deployment_checks"][-1]["passed"]:
            raise ValueError("deployment_identity_failed")
        job = upload(probe, video, max_frames)
        report["job_id"] = job["id"]
        before_cancel.append(deepcopy(job))
        report["upload_seconds"] = round(time.monotonic() - started, 6)
        while True:
            job = poll("before_cancel")
            if job is not None:
                state = job["status"]
                if state in TERMINAL:
                    report["outcome"] = "not_exercised" if state in SUCCESS | {"cancelled"} else "failed"
                    report["reason"] = "terminal_before_requested_phase"
                    break
                if state == when:
                    report["target_phase_observed"] = True
                    phase_job = job
                    break
                if when == "queued" and state in {"running", "processing"}:
                    # Stop our one job safely, but never count this as a queued cancellation test.
                    report["reason"] = "queued_phase_missed_cleanup_only"
                    report["cleanup_cancel"] = True
                    break
            wait_poll()

        if terminal_job is None:
            ensure_time()
            cancel_started = time.monotonic()
            status, payload = probe.request("POST", f"/v1/jobs/{report['job_id']}/cancel?report_contract=current")
            acknowledged = False
            if status == 200:
                job = record_job(payload, "cancel_response")
                acknowledged = (job["status"] in SUCCESS | {"failed"} or cancelled_evidence_valid(job)
                                or job["status"] in ACTIVE and job.get("cancel_requested") is True)
                report["cancel_phase_exercised"] = report["target_phase_observed"] and (when == "running" or (
                    job["status"] == "cancelled" and never_claimed(phase_job) and never_claimed(job)))
                if when == "queued" and not report["cancel_phase_exercised"] and job["status"] not in SUCCESS:
                    report["reason"] = "queued_transitioned_before_cancel"
            report["checks"].append({"name": "cancel_request_acknowledged", "passed": acknowledged, "http_status": status})
            while terminal_job is None:
                wait_poll()
                poll("after_cancel")
            if terminal_job["status"] in SUCCESS:
                report["outcome"] = "not_exercised"
                report["reason"] = "completed_in_cancel_race"
            elif terminal_job["status"] == "cancelled":
                if not cancelled_evidence_valid(terminal_job):
                    report["outcome"] = "failed"
                    report["reason"] = "cancelled_evidence_invalid"
                else:
                    report["outcome"] = "cancelled" if report["cancel_phase_exercised"] else "not_exercised"
            else:
                report["reason"] = "job_failed_instead_of_cancelled"

        if cancel_started is not None:
            ensure_time()
            status, repeated = probe.request("POST", f"/v1/jobs/{report['job_id']}/cancel?report_contract=current")
            if isinstance(repeated, dict):
                check_preserved(repeated, "repeat_cancel")
            stable = status == 200 and isinstance(repeated, dict) and all(repeated.get(key) == terminal_job.get(key) for key in STABLE_FIELDS)
            retained = stable and all(repeated.get(key) == terminal_job.get(key) for key in REPORT_FIELDS)
            report["checks"].append({"name": "repeat_cancel_is_idempotent", "passed": bool(stable and retained), "http_status": status})
            if status == 200 and isinstance(repeated, dict) and repeated.get("id") == report["job_id"] and isinstance(repeated.get("status"), str) and repeated["status"] in ACTIVE:
                report["terminal_state_regressed_to_active"] = True
        ensure_time()
        status, history = probe.request("GET", f"/v1/jobs/{report['job_id']}?report_contract=current")
        if isinstance(history, dict):
            check_preserved(history, "history")
        readable = status == 200 and isinstance(history, dict) and all(history.get(key) == terminal_job.get(key) for key in STABLE_FIELDS)
        retained = readable and all(history.get(key) == terminal_job.get(key) for key in REPORT_FIELDS)
        report["checks"].append({"name": "terminal_job_history_preserved", "passed": bool(readable and retained), "http_status": status})
        if status == 200 and isinstance(history, dict) and history.get("id") == report["job_id"] and isinstance(history.get("status"), str) and history["status"] in ACTIVE:
            report["terminal_state_regressed_to_active"] = True
        report["passed"] = report["outcome"] == "cancelled" and report["cancellation_confirmed"] and all(check["passed"] for check in report["checks"])
    except Exception as exc:
        report["error_code"] = str(exc) if str(exc) in SAFE_ERRORS else "cancel_validation_failed"
        report["error_type"] = type(exc).__name__
    finally:
        if report["job_id"]:
            try:
                check = deployment_check(probe, revision, "after_cancellation")
            except Exception as exc:
                check = {"name": "deployment_identity", "phase": "after_cancellation", "passed": False,
                         "error_type": type(exc).__name__}
            report["deployment_checks"].append(check)
            report["passed"] = report["passed"] and check["passed"]
        # An upload completion with a lost response may have created a server job with an unknown ID.
        completion_attempted = any(item.get("method") == "POST" and item.get("path", "").endswith("/complete") for item in probe.requests)
        invalid_stop = terminal_job is not None and terminal_job["status"] == "cancelled" and not cancelled_evidence_valid(terminal_job)
        report["server_job_may_still_be_active"] = (bool(report.get("terminal_state_regressed_to_active")) or invalid_stop
                                                   or terminal_job is None and bool(report["job_id"] or completion_attempted))
        report["elapsed_seconds"] = round(time.monotonic() - started, 6)
        report["requests"] = probe.requests
        probe.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-status", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--when", choices=("queued", "running"), required=True)
    parser.add_argument("--max-frames", type=int, default=150)
    parser.add_argument("--deadline-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Use a new evidence path; previous results are never overwritten")
    try:
        report = run_cancellation(read_json(args.deployment_status), args.video, when=args.when, max_frames=args.max_frames,
                                  deadline_seconds=args.deadline_seconds, poll_seconds=args.poll_seconds)
    except (ValueError, OSError) as exc:
        # Preflight errors have no server workload and do not include file contents or credentials.
        parser.error(str(exc) if isinstance(exc, ValueError) else "Cannot read local receipt/video")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"passed": report["passed"], "outcome": report["outcome"], "job_id": report["job_id"],
                      "server_job_may_still_be_active": report["server_job_may_still_be_active"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
