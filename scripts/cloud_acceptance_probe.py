"""Bounded acceptance probes against the operator's deployed RallyMate only.

No language-model endpoints are called. A workload requires a deployment receipt,
an explicit idle GPU handoff in that receipt, and a real local video. Concurrency
is capped at two, total jobs at four, and escalation requires a passing serial
report for the same URL, revision and input. These are functional samples, not a
capacity benchmark or an estimate of model/scoring accuracy.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx

# The probe is also a direct repository script, without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rallymate_service.report_evidence_transport import unpack_report_evidence


TERMINAL = {"succeeded", "completed", "failed", "cancelled"}
SUCCESS = {"succeeded", "completed"}
JOB_ID = re.compile(r"^[a-zA-Z0-9_-]{8,80}$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
SAFE_JOB_ERRORS = {"upload_create_failed", "invalid_chunk_size", "chunk_not_acknowledged", "upload_resume_failed",
                   "upload_complete_failed", "job_deadline_exceeded", "job_poll_failed", "job_response_mismatch",
                   "invalid_job_state", "job_did_not_succeed", "deployment_identity_failed"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def percentile(values, quantile):
    """Observed nearest-rank percentile; never extrapolate a small sample."""
    values = sorted(value for value in values if isinstance(value, (int, float)) and math.isfinite(value))
    return values[max(0, math.ceil(quantile * len(values)) - 1)] if values else None


def seconds_between(start, end):
    try:
        first, last = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (start, end))
        seconds = (last - first).total_seconds()
        return seconds if first.tzinfo and last.tzinfo and seconds >= 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def target_from_receipt(receipt: dict, workload: bool):
    base = str(receipt.get("service_url") or "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Deployment receipt needs a clean service_url origin")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Public acceptance requires HTTPS")
    versions = receipt.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError("Deployment receipt needs an exact versions/manifest identity")
    if workload and receipt.get("qa_workload_ready") is not True:
        raise ValueError("Deployment has not handed the GPU slot to QA (qa_workload_ready=true)")
    revision = versions.get("source_current_manifest_sha256")
    if workload and (not isinstance(revision, str) or not SHA256.fullmatch(revision)):
        raise ValueError("Workload requires a 64-hex source_current_manifest_sha256")
    if not isinstance(revision, str) or not SHA256.fullmatch(revision):
        revision = digest(json.dumps(versions, sort_keys=True, ensure_ascii=False).encode())
    return base, revision


def require_serial(report, base, revision, video_sha256, max_frames):
    if (report.get("base_url") != base or report.get("revision_sha256") != revision
            or report.get("video_sha256") != video_sha256 or report.get("concurrency") != 1
            or report.get("max_frames") != max_frames or report.get("mode") != "workload" or not report.get("successful")
            or not report.get("jobs") or any(not job.get("passed") for job in report["jobs"])):
        raise ValueError("Escalation requires a successful serial run of this exact target and input")


class Probe:
    def __init__(self, base, timeout=30, deadline=None):
        self.base = base
        self.timeout = timeout
        self.deadline = deadline
        self.requests = []
        self.client = httpx.Client(base_url=base, timeout=timeout, follow_redirects=False,
                                   headers={"X-Request-ID": "qa-" + uuid.uuid4().hex, "Origin": base})

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        # Do not follow returned artifact URLs to third-party services.
        if not path.startswith(("/v1/", "/health/")) or "://" in path:
            raise ValueError("Only same-origin analysis API paths are allowed")
        started = time.monotonic()
        item = {"method": method, "path": path}
        try:
            remaining = self.deadline - started if self.deadline is not None else self.timeout
            if remaining <= 0:
                raise ValueError("job_deadline_exceeded")
            kwargs.setdefault("timeout", min(self.timeout, remaining))
            response = self.client.request(method, path, **kwargs)
            item.update(status=response.status_code, response_sha256=digest(response.content),
                        decoded_response_bytes=len(response.content),
                        content_length=response.headers.get("content-length"),
                        content_encoding=response.headers.get("content-encoding"))
            try:
                payload = response.json()
            except ValueError:
                payload = None
            return response.status_code, payload
        except httpx.HTTPError as exc:
            item.update(status=None, error_type=type(exc).__name__)
            return None, None
        finally:
            item["elapsed_seconds"] = round(time.monotonic() - started, 6)
            self.requests.append(item)


def smoke(probe):
    checks = []
    for path in ("/health/live", "/health/ready", "/v1/meta", "/v1/techniques"):
        status, payload = probe.request("GET", path)
        checks.append({"name": path, "passed": status == 200 and isinstance(payload, dict), "http_status": status})
    status, _ = probe.request("GET", "/v1/jobs/" + str(uuid.uuid4()))
    checks.append({"name": "missing_job_is_404", "passed": status == 404, "http_status": status})
    return checks


def invalid_inputs(probe):
    checks = []
    for name, filename, size, options, expected in (
        ("unsupported_extension", "qa.txt", 10, {}, 415),
        ("empty_upload", "qa.mp4", 0, {}, 413),
        ("negative_start", "qa.mp4", 10, {"start_ms": -1}, 422),
        ("invalid_frame_limit", "qa.mp4", 10, {"max_frames": 0}, 422),
        ("unknown_option", "qa.mp4", 10, {"unknown_qa_option": True}, 422),
    ):
        status, _ = probe.request("POST", "/v1/uploads", json={"upload_id": str(uuid.uuid4()), "filename": filename,
                                                                "size": size, "options": options})
        checks.append({"name": name, "passed": status == expected, "http_status": status, "expected_status": expected})
    return checks


def deployment_check(probe, expected_revision, phase):
    """Bind this request to the operator's source manifest, not just receipt text."""
    valid_revision = isinstance(expected_revision, str) and SHA256.fullmatch(expected_revision) is not None
    status, payload = probe.request("GET", "/v1/meta") if valid_revision else (None, None)
    deployment = payload.get("deployment") if isinstance(payload, dict) else None
    return {"name": "deployment_identity", "phase": phase, "http_status": status,
            "passed": bool(valid_revision and status == 200 and isinstance(deployment, dict)
                           and deployment.get("source_manifest_sha256") == expected_revision
                           and deployment.get("source_files_verified") is True
                           and deployment.get("manifest_unchanged_since_startup") is True)}


def read_prepared_report(probe, job_id):
    """Only a complete 200 report passes; a 202 receipt is not the result."""
    deadline = min(time.monotonic() + 120, getattr(probe, "deadline", None) or float("inf"))
    path = (f"/v1/jobs/{job_id}/demo-result?report_contract=current"
            "&report_transport=model-proof-pool-v1&report_delivery=deferred-v1")
    while time.monotonic() < deadline:
        status, payload = probe.request("GET", path)
        if status == 200:
            if not isinstance(payload, dict) or payload.get("job_id") != job_id or payload.get("status") != "ready":
                return 502, None
            try:
                return 200, unpack_report_evidence(payload)
            except ValueError:
                return 502, None
        if (status != 202 or not isinstance(payload, dict) or payload.get("job_id") != job_id
                or payload.get("status") != "preparing" or payload.get("report_delivery") != "deferred-v1"):
            return status, None
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return 408, None


def result_contract_valid(suffix, payload, job_id):
    """Minimum endpoint shape/identity only; never a model or business-quality verdict."""
    if not isinstance(payload, dict):
        return False
    if not suffix:
        return (payload.get("id") == job_id and isinstance(payload.get("status"), str)
                and payload["status"] in SUCCESS and isinstance(payload.get("summary"), dict)
                and payload["summary"].get("job_id") == job_id)
    if payload.get("job_id") != job_id or not isinstance(payload.get("schema_version"), str):
        return False
    if suffix == "/demo-result":
        return (payload.get("schema_version") == "1.2.0" and payload.get("status") == "ready"
                and payload.get("result_kind") == "real_video_training_feedback_preview"
                and isinstance(payload.get("training_evaluation"), dict)
                and isinstance(payload.get("analysis_report"), dict))
    if suffix == "/trajectory":
        return (payload.get("schema_version") == "1.0.0" and payload.get("status") in ("ready", "not_observed")
                and all(isinstance(payload.get(key), dict) for key in ("source", "ball", "racket"))
                and payload["source"].get("frames_path") == "frames.jsonl")
    if suffix == "/pose-preview":
        return (payload.get("schema_version") == "1.0.0" and payload.get("result_kind") == "observed_pose_playback"
                and payload.get("coordinate_space") == "normalized_frame_0_1" and isinstance(payload.get("frames"), list)
                and isinstance(payload.get("source"), dict) and payload["source"].get("frames_path") == "frames.jsonl")
    if suffix in {"/technical-review", "/technical-review/export"}:
        return (payload.get("schema_version") == "technical-review-v1.0.0"
                and type(payload.get("revision")) is int and payload["revision"] >= 0
                and all(isinstance(payload.get(key), list) for key in ("entries", "rules"))
                and (suffix != "/technical-review/export" or isinstance(payload.get("history"), list)))
    return False


def upload(probe, video, max_frames):
    upload_id = str(uuid.uuid4())
    status, session = probe.request("POST", "/v1/uploads", json={"upload_id": upload_id, "filename": "qa-video" + video.suffix.lower(),
        "size": video.stat().st_size, "options": {"max_frames": max_frames, "write_annotated_video": True, "court_mode": "disabled"}})
    if status != 201 or not isinstance(session, dict):
        raise ValueError("upload_create_failed")
    chunk_size = session.get("chunk_size")
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= 4 * 1024 * 1024:
        raise ValueError("invalid_chunk_size")
    with video.open("rb") as handle:
        index = 0
        while content := handle.read(chunk_size):
            checksum = digest(content)
            status, acknowledgement = probe.request("PUT", f"/v1/uploads/{upload_id}/chunks/{index}", content=content,
                headers={"Content-Type": "application/octet-stream", "X-Chunk-SHA256": checksum})
            if status != 200 or not isinstance(acknowledgement, dict) or acknowledgement.get("chunk_sha256", {}).get(str(index)) != checksum:
                raise ValueError("chunk_not_acknowledged")
            if index == 0:
                # Separate request simulates reconnecting to the persisted session.
                status, resumed = probe.request("GET", f"/v1/uploads/{upload_id}")
                if status != 200 or not isinstance(resumed, dict) or resumed.get("chunk_sha256", {}).get("0") != checksum:
                    raise ValueError("upload_resume_failed")
            index += 1
    status, job = probe.request("POST", f"/v1/uploads/{upload_id}/complete")
    if status != 202 or not isinstance(job, dict) or not JOB_ID.fullmatch(str(job.get("id", ""))):
        raise ValueError("upload_complete_failed")
    return job


def run_job(base, video, max_frames, deadline_seconds, poll_seconds=2, *, expected_revision=None):
    started = time.monotonic()
    probe = Probe(base, deadline=started + deadline_seconds)
    result = {"passed": False, "job_id": None, "states": [], "queue_wait_seconds": None, "processing_seconds": None,
              "acceptance_scope": "job_identity_and_minimum_response_contracts", "business_quality_verified": False,
              "deployment_checks": []}
    try:
        result["deployment_checks"].append(deployment_check(probe, expected_revision, "before_upload"))
        if not result["deployment_checks"][-1]["passed"]:
            raise ValueError("deployment_identity_failed")
        job = upload(probe, video, max_frames)
        result["upload_seconds"] = round(time.monotonic() - started, 6)
        result["job_id"] = job["id"]
        while time.monotonic() - started < deadline_seconds:
            status, job = probe.request("GET", f"/v1/jobs/{result['job_id']}?report_contract=current")
            if status != 200 or not isinstance(job, dict):
                raise ValueError("job_poll_failed")
            if job.get("id") != result["job_id"]:
                raise ValueError("job_response_mismatch")
            state = job.get("status")
            if not isinstance(state, str) or state not in TERMINAL | {"queued", "running", "processing"}:
                raise ValueError("invalid_job_state")
            result["states"].append({"elapsed_seconds": round(time.monotonic() - started, 6), "status": state,
                                     "progress": job.get("progress")})
            if state in TERMINAL:
                result["terminal_status"] = state
                result["queue_wait_seconds"] = seconds_between(job.get("created_at"), job.get("started_at"))
                result["processing_seconds"] = seconds_between(job.get("started_at"), job.get("completed_at"))
                if state not in SUCCESS:
                    raise ValueError("job_did_not_succeed")
                break
            time.sleep(min(poll_seconds, max(0, deadline_seconds - (time.monotonic() - started))))
        else:
            result["server_job_may_still_be_active"] = True
            raise ValueError("job_deadline_exceeded")
        result["end_to_end_seconds"] = round(time.monotonic() - started, 6)
        result["result_checks"] = []
        for suffix in ("", "/demo-result", "/trajectory", "/pose-preview", "/technical-review", "/technical-review/export"):
            if suffix == "/demo-result":
                status, payload = read_prepared_report(probe, result["job_id"])
            else:
                status, payload = probe.request("GET", f"/v1/jobs/{result['job_id']}{suffix}?report_contract=current")
            result["result_checks"].append({"path": suffix or "refresh_job", "http_status": status,
                                            "passed": status == 200 and result_contract_valid(suffix, payload, result["job_id"])})
        result["passed"] = all(check["passed"] for check in result["result_checks"])
    except Exception as exc:
        result["error_code"] = str(exc) if isinstance(exc, ValueError) and str(exc) in SAFE_JOB_ERRORS else "job_probe_failed"
        result["error_type"] = type(exc).__name__
        # A lost completion response can leave a running job whose ID is unknown.
        completion_attempted = any(item.get("method") == "POST" and item.get("path", "").endswith("/complete")
                                   for item in probe.requests)
        if (result["job_id"] or completion_attempted) and result.get("terminal_status") not in TERMINAL:
            result["server_job_may_still_be_active"] = True
    finally:
        if result.get("terminal_status") in TERMINAL:
            try:
                check = deployment_check(probe, expected_revision, "after_terminal_and_results")
            except Exception as exc:
                check = {"name": "deployment_identity", "phase": "after_terminal_and_results", "passed": False,
                         "error_type": type(exc).__name__}
            result["deployment_checks"].append(check)
            result["passed"] = result["passed"] and check["passed"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        result["requests"] = probe.requests
        probe.close()
    return result


def aggregate(jobs, elapsed):
    successful = [job for job in jobs if job.get("passed")]
    return {"attempted_jobs": len(jobs), "passed_jobs": len(successful),
            "error_rate": (len(jobs) - len(successful)) / len(jobs) if jobs else None,
            "successful_jobs_per_minute": 60 * len(successful) / elapsed if elapsed > 0 else None,
            "end_to_end_seconds": {"sample_count": len(successful), "p50": percentile([j.get("end_to_end_seconds") for j in successful], .5),
                                   "p95": percentile([j.get("end_to_end_seconds") for j in successful], .95)},
            "queue_wait_seconds": {"p50": percentile([j.get("queue_wait_seconds") for j in jobs], .5),
                                   "p95": percentile([j.get("queue_wait_seconds") for j in jobs], .95)},
            "percentile_method": "nearest_rank; bounded small sample, not a service-level estimate"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-status", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--invalid-inputs", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=150)
    parser.add_argument("--deadline-seconds", type=int, default=600)
    parser.add_argument("--serial-report", type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Use a new evidence path; prior results are never overwritten")
    if not (1 <= args.jobs <= 4 and 1 <= args.concurrency <= 2 and 1 <= args.max_frames <= 600 and 30 <= args.deadline_seconds <= 1200):
        parser.error("Bounds: 1..4 jobs, 1..2 concurrency, 1..600 frames, 30..1200 seconds per job")
    receipt = read_json(args.deployment_status)
    base, revision = target_from_receipt(receipt, bool(args.video))
    if args.video and (not args.video.is_file() or not 0 < args.video.stat().st_size <= 64 * 1024 * 1024):
        parser.error("Use a real video file of at most 64 MiB for bounded acceptance")
    video_hash = digest(args.video.read_bytes()) if args.video else None
    if args.concurrency > 1:
        if not args.video or not args.serial_report:
            parser.error("Concurrent workload needs --video and --serial-report")
        require_serial(read_json(args.serial_report), base, revision, video_hash, args.max_frames)
    report = {"schema_version": "cloud-acceptance-probe-v1", "started_at": datetime.now(timezone.utc).isoformat(),
              "mode": "workload" if args.video else "smoke", "base_url": base, "revision_sha256": revision,
              "video_sha256": video_hash, "concurrency": args.concurrency, "max_frames": args.max_frames,
              "acceptance_scope": "job_identity_and_minimum_response_contracts", "business_quality_verified": False,
              "ground_truth_used": False, "jobs": [], "limitations": [
                  "HTTP availability does not verify visual behavior, SAM provenance, score correctness, cancellation or LLM success.",
                  "GPU/RAM/disk/resource recovery require separately timestamped server observations.",
                  "Job timeouts stop further submissions; they do not cancel server work."]}
    probe = Probe(base)
    started = time.monotonic()
    try:
        report["checks"] = smoke(probe)
        if args.invalid_inputs:
            report["checks"].extend(invalid_inputs(probe))
        if args.video:
            report["checks"].append(deployment_check(probe, revision, "before_workload"))
        if args.video and all(check["passed"] for check in report["checks"]):
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                for offset in range(0, args.jobs, args.concurrency):
                    futures = [pool.submit(run_job, base, args.video, args.max_frames, args.deadline_seconds, expected_revision=revision)
                               for _ in range(min(args.concurrency, args.jobs - offset))]
                    batch = []
                    for future in futures:
                        try:
                            job = future.result()
                            if not isinstance(job, dict) or type(job.get("passed")) is not bool:
                                raise TypeError("invalid job probe result")
                        except Exception as exc:
                            # Another future may already own server work. Preserve every slot and stop escalation.
                            job = {"passed": False, "job_id": None, "error_code": "job_probe_future_failed",
                                   "error_type": type(exc).__name__, "server_job_may_still_be_active": True}
                        report["jobs"].append(job)
                        batch.append(job)
                    if any(not job["passed"] for job in batch):
                        report["stopped_after_failure"] = True
                        break
        report["elapsed_seconds"] = round(time.monotonic() - started, 6)
        report["metrics"] = aggregate(report["jobs"], report["elapsed_seconds"])
        report["requests"] = probe.requests
        report["successful"] = all(check["passed"] for check in report["checks"]) and (
            not args.video or len(report["jobs"]) == args.jobs and all(job["passed"] for job in report["jobs"]))
    finally:
        probe.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    print(json.dumps({"successful": report["successful"], "metrics": report["metrics"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
