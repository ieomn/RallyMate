"""One isolated legacy report build with exact proof-budget counters.

Run only after GPU experiments/background mesh work finish. Source DB and
artifacts are read-only; an audit hook confines all writes to a new output root.
No payload snapshot is saved and no transport/time/memory budget is raised.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", default="afd950a6-0967-49dc-8e74-fd1b4c76a9e6")
    parser.add_argument("--output-root", type=Path, default=ROOT / ".codex_tmp/legacy-report-budget-20261107")
    args = parser.parse_args(argv)
    output = args.output_root.resolve()
    source_root = (ROOT / "service_data").resolve()
    if output == source_root or output.is_relative_to(source_root):
        parser.error("diagnostic output must be outside original service data")
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "temp"
    temporary.mkdir()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    os.environ.update(CUDA_VISIBLE_DEVICES="", RALLYMATE_DATA_ROOT=str(output / "data"),
        RALLYMATE_DATABASE_PATH=str(output / "data/import-only.sqlite3"),
        OMP_WAIT_POLICY="PASSIVE", KMP_BLOCKTIME="0")
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "2"
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = str(temporary)

    def allowed_write(value):
        if isinstance(value, int) or value is None:
            return
        path = Path(os.fsdecode(value)).resolve()
        if not path.is_relative_to(output):
            raise PermissionError("Diagnostic attempted a write outside its isolated output directory")

    def audit(event, values):
        if event == "open":
            name, mode, flags = values
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                allowed_write(name)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime"):
            allowed_write(values[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            allowed_write(values[0])
            allowed_write(values[1])
        elif event == "sqlite3.connect":
            name = os.fsdecode(values[0])
            if name != ":memory:" and not (name.startswith("file:") and "mode=ro" in name):
                allowed_write(name)
    sys.addaudithook(audit)

    from fastapi.testclient import TestClient
    import rallymate_service.api as api
    import rallymate_service.user_demo as user_demo
    from rallymate_service.config import load_settings
    from rallymate_service.database import JobDatabase
    from rallymate_service.report_evidence_transport import ReportEvidenceLimitError
    from rallymate_vision.postprocess_resources import process_memory_bytes

    started = time.perf_counter()
    events, budget_failures = [], []
    event_lock = threading.Lock()

    def emit(event, **values):
        entry = {"at_seconds": round(time.perf_counter() - started, 6),
                 "utc": datetime.now(timezone.utc).isoformat(), "event": event, **values}
        with event_lock:
            events.append(entry)
            with (output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False, allow_nan=False), flush=True)

    def exception_chain(exc):
        chain, seen = [], set()
        while exc is not None and id(exc) not in seen and len(chain) < 16:
            seen.add(id(exc))
            entry = {"exception_type": type(exc).__name__}
            if isinstance(exc, ReportEvidenceLimitError):
                diagnostic = exc.diagnostics or {"budget": exc.budget, "actual": exc.actual, "maximum": exc.maximum}
                # The codec diagnostic is counters/top-level field names only.
                # No exception locals, arguments or report values are captured.
                entry["proof_budget"] = diagnostic
                with event_lock:
                    if diagnostic not in budget_failures:
                        budget_failures.append(diagnostic)
            chain.append(entry)
            exc = exc.__cause__ or exc.__context__
        return chain

    def measured(name, function):
        @functools.wraps(function)
        def wrapper(*values, **kwargs):
            then = time.perf_counter()
            emit("start", name=name)
            try:
                result = function(*values, **kwargs)
            except BaseException as exc:
                emit("end", name=name, seconds=round(time.perf_counter() - then, 6),
                     exception_chain=exception_chain(exc), memory_bytes=process_memory_bytes())
                raise
            emit("end", name=name, seconds=round(time.perf_counter() - then, 6), memory_bytes=process_memory_bytes())
            return result
        return wrapper

    class ReadOnlyJobs(JobDatabase):
        def __init__(self, path):
            # Do not call JobDatabase.__init__: its mkdir would violate the
            # write audit even when the original directory already exists.
            self.path = Path(path).resolve()

        @contextlib.contextmanager
        def connect(self):
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                yield connection
            finally:
                connection.close()

        def initialize(self):
            with self.connect() as connection:
                assert connection.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone()

    database = ReadOnlyJobs(source_root / "jobs.sqlite3")
    active = database.count_by_status()
    if active.get("queued", 0) or active.get("running", 0):
        raise RuntimeError("Original analysis queue must be idle before this CPU diagnostic")
    settings = replace(load_settings(), data_root=output / "data", database_path=database.path,
                       api_key=None, cpu_threads=2)
    source_settings = replace(settings, data_root=source_root)
    original_video_descriptor = api.original_video_descriptor
    api.original_video_descriptor = measured("original_video_descriptor",
        lambda job, _: original_video_descriptor(job, source_settings))

    original_pack = api.pack_report_evidence
    def counted_pack(*values, **kwargs):
        kwargs["diagnostics_callback"] = lambda diagnostic: emit("proof_budget_counters", counters=diagnostic)
        return original_pack(*values, **kwargs)
    api.pack_report_evidence = counted_pack
    for name in ("verify_evidence_manifest", "build_user_demo_result", "load_footwork_review",
                 "load_source_aligned_assessment", "build_process_evaluation", "build_swing_kinematics",
                 "build_tennis_assessment", "build_indicator_assessment", "build_card_execution_report",
                 "build_analysis_report", "project_report", "pack_report_evidence", "encode_bounded_json"):
        setattr(api, name, measured(name, getattr(api, name)))
    for name in ("build_training_evaluation_with_records", "_friendly_amplitudes", "build_technique_assessment"):
        setattr(user_demo, name, measured("user_demo." + name, getattr(user_demo, name)))

    original_records = api.open_indicator_feature_records
    @contextlib.contextmanager
    def measured_records(*values, **kwargs):
        manager = original_records(*values, **kwargs)
        records = measured("open_indicator_feature_records.enter", manager.__enter__)()
        try:
            yield records
        finally:
            manager.__exit__(*sys.exc_info())
    api.open_indicator_feature_records = measured_records
    emit("diagnostic", job_id=args.job_id, original_database_mode="read-only", artifact_writes="audit_denied",
        payload_snapshots=False, budgets_changed=False, cuda_visible_devices="",
        concurrency="operator must run after GPU and background mesh experiments finish",
        source_hashes={name: hashlib.sha256((ROOT / "src/rallymate_service" / name).read_bytes()).hexdigest()
                       for name in ("api.py", "report_evidence_transport.py", "user_demo.py", "indicator_records.py")})
    app = api.create_app(settings, database)
    assert app.state.report_delivery_cache.preparation_timeout_seconds == 300
    request_started = time.perf_counter()
    status, details = None, None
    try:
        with TestClient(app) as client:
            while True:
                response = client.get(f"/v1/jobs/{args.job_id}/demo-result", params={
                    "report_contract": "current", "report_transport": "model-proof-pool-v1", "report_delivery": "deferred-v1"})
                status = response.status_code
                if status != 202:
                    details = response.json() if status != 200 else {"response_bytes": len(response.content)}
                    emit("http_terminal", status=status, seconds=round(time.perf_counter() - request_started, 6), details=details)
                    break
                if time.perf_counter() - request_started > 360:
                    raise RuntimeError("Diagnostic HTTP polling exceeded its outer deadline")
                time.sleep(1)
    except BaseException as exc:
        emit("diagnostic_error", exception_chain=exception_chain(exc))
        raise
    finally:
        result = {"job_id": args.job_id, "http_status": status, "http_details": details,
            "total_seconds": round(time.perf_counter() - started, 6), "proof_budget_failures": budget_failures,
            "final_json_encoding_called": any(e.get("event") == "start" and e.get("name") == "encode_bounded_json" for e in events),
            "payload_saved": False, "events": events}
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        emit("finished", http_status=status, total_seconds=result["total_seconds"],
             proof_budget_failure_count=len(budget_failures), final_json_encoding_called=result["final_json_encoding_called"])


if __name__ == "__main__":
    main()
