"""Compare full and incremental previews on an isolated prefix of a completed artifact.

The source is opened read-only. Appends occur only in a bounded temporary copy.
Run in an idle CPU window; this script never submits jobs or invokes a model.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_idle import IdleJobsGuard
from rallymate_service.live_trajectory import LiveTrajectoryCache
from rallymate_vision.trajectory import MAX_FRAMES_LINE_BYTES, build_trajectory_preview, parse_trajectory_line


def comparable_preview(payload):
    result = deepcopy(payload)
    result.pop("job_id", None)
    result["source"].pop("read_strategy", None)
    result["source"]["frames_path"] = "frames.jsonl"
    return result


def measure_preview(call, *, incremental):
    counters = {"parsed_lines": 0, "parsed_bytes": 0}
    def parse(raw, number):
        counters["parsed_lines"] += 1
        counters["parsed_bytes"] += len(raw)
        return parse_trajectory_line(raw, number)
    module = "rallymate_service.live_trajectory" if incremental else "rallymate_vision.trajectory"
    with patch(module + ".parse_trajectory_line", new=parse):
        started = time.perf_counter()
        payload = call()
        counters["wall_seconds"] = time.perf_counter() - started
    return payload, counters


def _file_identity(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def read_complete_lines(stream, count, remaining_bytes):
    selected = []
    for _ in range(count):
        line = stream.readline(MAX_FRAMES_LINE_BYTES + 1)
        if not line:
            raise ValueError("Completed source has fewer lines than requested; reduce the benchmark prefix")
        if len(line) > MAX_FRAMES_LINE_BYTES or not line.endswith(b"\n"):
            raise ValueError("Source must contain complete JSONL rows within the ordinary line limit")
        remaining_bytes -= len(line)
        if remaining_bytes < 0:
            raise ValueError("Benchmark prefix exceeds its explicit input byte budget")
        selected.append(line)
    return b"".join(selected), remaining_bytes


def run_benchmark(*, source: Path, output: Path, initial_lines: int, append_lines: int,
                  polls: int, max_input_bytes: int, sample_limit: int, guard) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise ValueError("Completed source artifact does not exist")
    if output.exists():
        raise ValueError("Use a new output directory; earlier reports must not be overwritten")
    guard.check()
    source_identity = _file_identity(source)
    output.mkdir(parents=True)
    report = {"schema_version": "live-trajectory-benchmark-v1", "status": "running", "phases": [],
              "source": str(source), "source_identity_before": list(source_identity),
              "python": {"executable": sys.executable, "version": sys.version},
              "scope": "read_only_completed_source_prefix; scratch_appends_only; exact_complete_history_preview_comparison",
              "timing_scope": "preview_construction_including_parse_counters; excludes_source_copy_idle_checks_comparison_and_report_IO",
              "input_limits": {"initial_lines": initial_lines, "append_lines": append_lines, "polls": polls,
                               "max_input_bytes": max_input_bytes}, "idle_guard": guard.receipt}
    def save():
        temporary = output / "benchmark.tmp"
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(output / "benchmark.json")
    try:
        with tempfile.TemporaryDirectory(prefix="trajectory-prefix-", dir=output) as scratch, source.open("rb") as stream:
            path = Path(scratch) / "frames.jsonl"
            cache = LiveTrajectoryCache()
            job = {"id": "isolated-trajectory-benchmark", "started_at": "benchmark", "attempts": 1}
            digest, remaining = hashlib.sha256(), max_input_bytes
            for phase in range(polls + 1):
                guard.check()
                added, remaining = read_complete_lines(stream, initial_lines if phase == 0 else append_lines, remaining)
                digest.update(added)
                with path.open("ab") as destination:
                    destination.write(added)
                results = {}
                order = ("full", "incremental") if phase % 2 == 0 else ("incremental", "full")
                for mode in order:
                    guard.check()
                    call = (lambda: cache.preview(job, path, sample_limit=sample_limit, prediction_horizon_ms=0)) if mode == "incremental" else (
                        lambda: build_trajectory_preview(path, sample_limit=sample_limit, prediction_horizon_ms=0, allow_partial=True))
                    results[mode] = measure_preview(call, incremental=mode == "incremental")
                    guard.check()
                full, incremental = results["full"][0], results["incremental"][0]
                if comparable_preview(full) != comparable_preview(incremental):
                    raise ValueError("Incremental preview differs from the full complete-history result")
                diagnostic = incremental["source"]["read_strategy"]
                if diagnostic["mode"] != "incremental_complete_history":
                    raise ValueError("Benchmark exceeded the incremental cache budget; reduce its source prefix")
                item = {"phase": phase, "kind": "initial_full_scan" if phase == 0 else "append",
                        "order": list(order), "added_bytes": len(added), "snapshot_bytes": path.stat().st_size,
                        "source_frames": full["source"]["frame_count"], "outputs_exact_equal": True,
                        "full": results["full"][1], "incremental": results["incremental"][1],
                        "incremental_read_strategy": diagnostic}
                report["phases"].append(item)
                report["selected_source_prefix_sha256"] = digest.hexdigest()
                save()
                print(json.dumps(item, ensure_ascii=False), flush=True)
            steady = report["phases"][1:]
            full_bytes = sum(item["full"]["parsed_bytes"] for item in steady)
            incremental_bytes = sum(item["incremental"]["parsed_bytes"] for item in steady)
            report["steady_state"] = {"all_outputs_exact_equal": True,
                "full_parsed_lines": sum(item["full"]["parsed_lines"] for item in steady),
                "incremental_parsed_lines": sum(item["incremental"]["parsed_lines"] for item in steady),
                "full_parsed_bytes": full_bytes, "incremental_parsed_bytes": incremental_bytes,
                "parsed_byte_reduction_fraction": 1 - incremental_bytes / full_bytes if full_bytes else None,
                "median_full_wall_seconds": statistics.median(item["full"]["wall_seconds"] for item in steady),
                "median_incremental_wall_seconds": statistics.median(item["incremental"]["wall_seconds"] for item in steady)}
        if _file_identity(source) != source_identity:
            raise ValueError("Source changed during benchmarking; use a completed immutable artifact")
        report["source_unchanged"] = True
        report["status"] = "completed_exact_comparison"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        report["source_identity_after"] = list(_file_identity(source)) if source.exists() else None
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Completed, immutable frames.jsonl; opened read-only")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--idle-jobs-db", type=Path, required=True)
    parser.add_argument("--idle-jobs-python", type=Path)
    parser.add_argument("--initial-lines", type=int, default=128)
    parser.add_argument("--append-lines", type=int, default=32)
    parser.add_argument("--polls", type=int, default=4)
    parser.add_argument("--max-input-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--sample-limit", type=int, default=128)
    args = parser.parse_args(argv)
    if (not 1 <= args.initial_lines <= 5000 or not 1 <= args.append_lines <= 1000
            or not 1 <= args.polls <= 8 or not 1 <= args.max_input_bytes <= 256 * 1024 * 1024
            or not 1 <= args.sample_limit <= 1000):
        parser.error("initial-lines 1..5000; append-lines 1..1000; polls 1..8; input at most 256 MiB; sample-limit 1..1000")
    service_root, output = args.idle_jobs_db.resolve().parent, args.output_root.resolve()
    if output == service_root or service_root in output.parents:
        parser.error("Benchmark output must be outside the production service data directory")
    with IdleJobsGuard(args.idle_jobs_db, python=args.idle_jobs_python) as guard:
        report = run_benchmark(source=args.source, output=output, initial_lines=args.initial_lines,
            append_lines=args.append_lines, polls=args.polls, max_input_bytes=args.max_input_bytes,
            sample_limit=args.sample_limit, guard=guard)
    print(json.dumps(report["steady_state"], indent=2))


if __name__ == "__main__":
    main()
