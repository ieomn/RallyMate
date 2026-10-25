"""Create a new, CPU-only finalization job from one failed inference bundle.

The resident worker must be paused by the operator before using this command.
The API can remain online. This command never edits the failed job or its files.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from rallymate_service.config import load_settings
from rallymate_service.database import JobDatabase, utc_now
from rallymate_vision.contracts import load_request


REQUIRED_RUNTIME_FILES = (
    "scripts/recover_failed_finalization.py",
    "src/rallymate_vision/pipeline.py",
    "src/rallymate_vision/validation.py",
    "src/rallymate_service/database.py",
    "src/rallymate_service/worker.py",
)
TERMINAL = {"succeeded", "failed", "cancelled"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_hash(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-fA-F0-9]{64}", value) is None:
        raise ValueError("expected SHA256 must contain exactly 64 hexadecimal characters")
    return value.lower()


def _verify_hash(path: Path, expected: str) -> str:
    actual = sha256(path)
    if actual != _expected_hash(expected):
        raise ValueError(f"SHA256 mismatch: {path}")
    return actual


def _write_json(path: Path, payload: dict) -> None:
    # Every audit file is new and must never overwrite existing evidence.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _manifest(path: Path, expected: str, *, workspace: Path | None = None) -> dict:
    digest = _verify_hash(path, expected)
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"manifest requires a nonempty files list: {path}")
    seen = set()
    for entry in entries:
        name = entry.get("path")
        relative = Path(name) if isinstance(name, str) else Path(".")
        if relative.is_absolute() or ".." in relative.parts or not name or name in seen:
            raise ValueError(f"unsafe or duplicate manifest path: {name}")
        seen.add(name)
        _expected_hash(entry.get("sha256"))
        if workspace is not None:
            file = (workspace / relative).resolve()
            if not file.is_relative_to(workspace):
                raise ValueError(f"manifest path escapes workspace: {name}")
            _verify_hash(file, entry["sha256"])
    if workspace is not None and not set(REQUIRED_RUNTIME_FILES).issubset(seen):
        raise ValueError("recovery source manifest omits a required runtime file")
    return {"path": str(path.resolve()), "sha256": digest, "payload": payload}


@contextmanager
def _operator_lock(path: Path, payload: dict):
    # A stale lock is evidence for an operator to inspect, never auto-delete it.
    _write_json(path, payload)
    try:
        yield
    finally:
        path.unlink()


def _assert_idle(database: JobDatabase) -> None:
    if database.count_by_status().get("running", 0):
        raise RuntimeError("a job is running; pause the resident worker and wait for idle")


def _source_files(directory: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"source bundle contains a symlink: {path}")
        if path.is_file():
            hashes[path.relative_to(directory).as_posix()] = sha256(path)
    if not {"frames.jsonl", "summary.json", "scoring-loop-summary.json"}.issubset(hashes):
        raise ValueError("failed bundle is missing inference/finalization artifacts")
    return hashes


def _clone(source: Path, destination: Path, hashes: dict[str, str]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name, digest in hashes.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Independent copies include summary/readiness/report; none alias source.
        shutil.copy2(source / name, target)
        _verify_hash(target, digest)


def _request_payload(original: dict, request, recovery_id: str, output: Path, metadata: dict) -> dict:
    payload = copy.deepcopy(original)
    payload["job_id"] = recovery_id
    payload.setdefault("source", {})["video_path"] = str(request.video_path)
    payload.setdefault("output", {})["directory"] = str(output)
    for field in ("detect", "pose", "pose_config"):
        value = getattr(request.models, field)
        if value is not None:
            payload.setdefault("models", {})[field] = str(value)
    scoring = payload.setdefault("scoring", {})
    for field in ("feasibility_registry", "reference_context"):
        value = getattr(request.scoring, field)
        if value is not None:
            scoring[field] = str(value)
    scoring["calibration_assets"] = [str(path) for path in request.scoring.calibration_assets]
    payload.setdefault("upstream_metadata", {})["finalization_recovery"] = {
        "reused_inference": True,
        "source_failed_job_id": metadata["source_failed_job_id"],
        "frames_sha256": metadata["frames_sha256"],
        "video_sha256": metadata["video_sha256"],
    }
    return payload


def _finalize_artifacts(*args, **kwargs):
    from rallymate_vision.pipeline import finalize_pipeline_artifacts
    return finalize_pipeline_artifacts(*args, **kwargs)


class CpuFinalizationRunner:
    # The worker also requires an exact atomic job claim and a valid persisted
    # recovery marker. This capability alone never authorizes a queued job.
    cpu_finalization_only = True

    def __init__(self, recovery_id: str, source_summary: dict, metadata: dict):
        self.recovery_id = recovery_id
        self.source_summary = source_summary
        self.metadata = metadata

    def __call__(self, request, progress_callback=None, cancellation_callback=None,
                 registry_lifecycle_manifest_path=None):
        if request.job_id != self.recovery_id:
            raise RuntimeError("CPU recovery runner received a different job")

        def progress(phase):
            if cancellation_callback:
                cancellation_callback()
            if progress_callback:
                progress_callback({"phase": phase, "percent": 98,
                    "processed_frames": self.source_summary["processing"]["processed_frames"],
                    "total_frames": self.source_summary["processing"]["processed_frames"],
                    "message": "正在复核已有推理产物并完成报告"})

        progress("finalizing")
        def verify_runtime():
            _manifest(request.output_dir / "_recovery" / "recovery-manifest.json",
                self.metadata["recovery_merged_manifest_sha256"],
                workspace=Path(self.metadata["runtime_workspace_root"]))

        verify_runtime()
        result = _finalize_artifacts(request, self.source_summary,
            registry_lifecycle_manifest_path=registry_lifecycle_manifest_path,
            report_postprocessing=progress, cancellation_callback=cancellation_callback,
            recovery_metadata=self.metadata)
        verify_runtime()
        return result


def _ensure_terminal(database: JobDatabase, recovery_id: str, worker_id: str) -> dict:
    state = database.get_job(recovery_id)
    if state is None:
        raise RuntimeError("recovery job disappeared")
    if state["status"] == "queued":
        database.request_cancel(recovery_id)
    elif state["status"] == "running":
        if state["worker_id"] == worker_id:
            database.mark_failed(recovery_id, "CPU recovery exited before terminal completion",
                worker_id=worker_id, attempts=state["attempts"])
        else:
            # Do not complete another worker's attempt or let it be retried.
            database.request_cancel(recovery_id)
    state = database.get_job(recovery_id)
    if state["status"] not in TERMINAL:
        raise RuntimeError("recovery ownership lost; cancellation requested, operator must stop its owner")
    return state


def recover_failed_finalization(settings, *, failed_job_id: str, worker_paused: bool,
        operator_note: str, expected_video_sha256: str, expected_frames_sha256: str,
        expected_detect_model_sha256: str, expected_pose_model_sha256: str,
        source_manifest: Path, source_manifest_sha256: str,
        recovery_source_manifest: Path, recovery_source_manifest_sha256: str,
        frontend_build_manifest: Path, frontend_build_manifest_sha256: str,
        workspace_root: Path) -> dict:
    """Prepare and synchronously consume one recovery job, never the general queue."""
    from rallymate_service.worker import process_one

    if not worker_paused or not operator_note.strip():
        raise ValueError("explicit worker-paused confirmation and operator note are required")
    if "expected_job_id" not in inspect.signature(process_one).parameters:
        raise RuntimeError("worker lacks atomic expected_job_id support")
    if not settings.database_path.is_file():
        raise FileNotFoundError("existing service database is required")
    database = JobDatabase(settings.database_path)
    workspace = workspace_root.resolve()
    evidence = {
        "source": _manifest(source_manifest, source_manifest_sha256),
        "recovery": _manifest(recovery_source_manifest, recovery_source_manifest_sha256, workspace=workspace),
        "frontend": _manifest(frontend_build_manifest, frontend_build_manifest_sha256),
    }
    if evidence["recovery"]["payload"].get("frontend_build_manifest_sha256") not in (None, evidence["frontend"]["sha256"]):
        raise ValueError("recovery source manifest declares a different frontend build")
    recovery_id = str(uuid.uuid4())
    worker_id = f"cpu-finalization-{socket.gethostname()}-{recovery_id}"
    lock_data = {"recovery_job_id": recovery_id, "source_failed_job_id": failed_job_id,
        "pid": os.getpid(), "worker_id": worker_id, "created_at": utc_now(),
        "worker_paused_confirmed": True, "operator_note": operator_note}
    with _operator_lock(settings.data_root / "finalization-recovery.lock", lock_data):
        _assert_idle(database)
        source_job = database.get_job(failed_job_id)
        if source_job is None or source_job["status"] != "failed":
            raise ValueError("source job must exist and remain failed")
        if not (source_job.get("error") or "").startswith("OutputValidationError:"):
            raise ValueError("only failed final artifact validation can be recovered")
        request = load_request(source_job["request_path"])
        source_dir = Path(source_job["output_dir"]).resolve()
        if (request.job_id != failed_job_id or request.output_dir != source_dir
                or request.video_path != Path(source_job["video_path"]).resolve()):
            raise ValueError("source job/request identity differs")
        summary = json.loads((source_dir / "summary.json").read_text(encoding="utf-8"))
        if summary.get("job_id") != failed_job_id or summary.get("processing", {}).get("processed_frames", 0) <= 0:
            raise ValueError("source provisional summary is missing completed inference")
        video_hash = _verify_hash(request.video_path, expected_video_sha256)
        hashes = _source_files(source_dir)
        if hashes["frames.jsonl"] != _expected_hash(expected_frames_sha256):
            raise ValueError("source frames SHA256 differs from operator evidence")
        frame_count = 0
        with (source_dir / "frames.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                frame = json.loads(line)
                if (frame.get("job_id") != failed_job_id
                        or frame.get("frame", {}).get("video_sha256", "").lower() != video_hash):
                    raise ValueError("frame source job/video identity differs")
                frame_count += 1
        if frame_count != summary["processing"]["processed_frames"]:
            raise ValueError("frame count differs from source provisional summary")
        model_hashes = {"detect": _verify_hash(request.models.detect, expected_detect_model_sha256),
                        "pose": _verify_hash(request.models.pose, expected_pose_model_sha256)}
        expected_pose = summary.get("models", {}).get("pose_backend", {}).get("model_sha256")
        if not isinstance(expected_pose, str) or model_hashes["pose"] != expected_pose.lower():
            raise ValueError("pose model differs from source inference metadata")
        if request.models.pose_config:
            model_hashes["pose_config"] = sha256(request.models.pose_config)
        output = (settings.runs_dir / recovery_id).resolve()
        if output == source_dir or output.is_relative_to(source_dir):
            raise ValueError("recovery output cannot alias or nest in the source bundle")
        _clone(source_dir, output, hashes)
        audit = output / "_recovery"
        audit.mkdir(exist_ok=False)
        _write_json(audit / "source-failed-job.json", source_job)
        shutil.copy2(source_dir / "summary.json", audit / "source-summary.json")
        shutil.copy2(request.request_path, audit / "source-request.json")
        for label, item in evidence.items():
            destination = audit / f"{label}-manifest.json"
            shutil.copy2(item["path"], destination)
            _verify_hash(destination, item["sha256"])
        metadata = {"version": "cpu-finalization-recovery-v1", "reused_inference": True,
            "source_failed_job_id": failed_job_id, "recovery_job_id": recovery_id,
            "source_output_dir": str(source_dir), "source_artifact_sha256": hashes,
            "source_failed_job_snapshot_sha256": sha256(audit / "source-failed-job.json"),
            "source_request_sha256": sha256(audit / "source-request.json"),
            "video_sha256": video_hash, "frames_sha256": hashes["frames.jsonl"],
            "frames_bytes": (source_dir / "frames.jsonl").stat().st_size,
            "model_sha256": model_hashes,
            "source_merged_manifest_sha256": evidence["source"]["sha256"],
            "recovery_source_manifest_sha256": evidence["recovery"]["sha256"],
            "recovery_merged_manifest_sha256": evidence["recovery"]["sha256"],
            "frontend_build_manifest_sha256": evidence["frontend"]["sha256"],
            "operator": lock_data, "inference_identity_preserved": True,
            "runtime_workspace_root": str(workspace),
            "artifact_copy_mode": "independent_copies"}
        _write_json(audit / "provenance.json", metadata)
        _write_json(audit / "operator-lock.json", lock_data)
        original_payload = json.loads(request.request_path.read_text(encoding="utf-8"))
        settings.requests_dir.mkdir(parents=True, exist_ok=True)
        request_path = settings.requests_dir / f"{recovery_id}.json"
        _write_json(request_path, _request_payload(original_payload, request, recovery_id, output, metadata))
        _assert_idle(database)
        if database.get_job(failed_job_id) != source_job:
            raise RuntimeError("failed source job changed during recovery preparation")
        created = False
        started = time.perf_counter()
        try:
            database.create_job(recovery_id, source_job["original_filename"], request.video_path, request_path, output)
            created = True
            _assert_idle(database)
            process_one(settings, database, worker_id,
                runner=CpuFinalizationRunner(recovery_id, summary, metadata), expected_job_id=recovery_id)
        finally:
            # Covers failures before claim, worker setup, cancellation and Ctrl-C.
            if created or database.get_job(recovery_id) is not None:
                terminal = _ensure_terminal(database, recovery_id, worker_id)
                _write_json(audit / "outcome.json", {"job_id": recovery_id,
                    "status": terminal["status"], "error": terminal.get("error"),
                    "finished_at": utc_now(), "cpu_recovery_elapsed_seconds": time.perf_counter() - started})
        if database.get_job(failed_job_id) != source_job:
            raise RuntimeError("source failed job unexpectedly changed")
        receipt = {"version": "cpu-finalization-recovery-receipt-v1",
            "recovery_job_id": recovery_id, "source_failed_job_id": failed_job_id,
            "status": terminal["status"], "error": terminal.get("error"),
            "database_terminal_status": terminal["status"], "reused_inference": True,
            **{key: metadata[key] for key in ("video_sha256", "frames_sha256", "frames_bytes",
                "model_sha256", "source_merged_manifest_sha256", "recovery_merged_manifest_sha256",
                "frontend_build_manifest_sha256")},
            "output_dir": str(output), "request_path": str(request_path),
            "provenance_path": str(audit / "provenance.json")}
        _write_json(audit / "receipt.json", receipt)
        return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-job-id", required=True)
    parser.add_argument("--worker-paused", action="store_true", required=True)
    parser.add_argument("--operator-note", required=True)
    parser.add_argument("--expected-video-sha256", required=True)
    parser.add_argument("--expected-frames-sha256", required=True)
    parser.add_argument("--expected-detect-model-sha256", required=True)
    parser.add_argument("--expected-pose-model-sha256", required=True)
    for name in ("source-manifest", "recovery-source-manifest", "frontend-build-manifest"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"CPU recovery interrupted by signal {signum}")
    previous_handlers = {number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = recover_failed_finalization(load_settings(), **vars(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "error": "CPU recovery stopped; owned job cleanup completed"}))
        return 130
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
