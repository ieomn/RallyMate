"""Read saved native SAM timing without loading models, including active JSONL.

A trailing incomplete line is reported and ignored; completed records are never
rewritten. Comparison requires the same source pixels/time/bbox/identity envelope.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics

from scripts.audit_local_sam_native import native_geometry_errors


def stats(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "sum": sum(values), "mean": statistics.fmean(values),
            "median": statistics.median(values), "p95_nearest_rank": values[math.ceil(.95 * len(values)) - 1],
            "min": values[0], "max": values[-1]}


def read_records(path):
    rows, incomplete = [], False
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if line.endswith("\n"):
                    raise
                incomplete = True
    return rows, incomplete


def summarize(path):
    rows, incomplete = read_records(path)
    measurements = defaultdict(list)
    seen_ipc_requests = set()
    for row in rows:
        for key in ("inference_ms", "peak_memory_bytes", "peak_reserved_memory_bytes",
                    "current_allocated_memory_bytes", "current_reserved_memory_bytes"):
            if isinstance(row.get(key), (int, float)):
                measurements[key].append(row[key])
                if not row.get("first_model_call"):
                    measurements["steady_" + key].append(row[key])
        for family in ("stage_timing_ms", "ipc_request_timing_ms"):
            if family == "ipc_request_timing_ms" and row.get(family):
                request_key = (row.get("video_sha256"), row.get("frame_index"), row.get("processed_index"))
                if request_key in seen_ipc_requests:
                    continue
                seen_ipc_requests.add(request_key)
            for key, value in row.get(family, {}).items():
                measurements[f"{family}.{key}"].append(value)
        for family in ("wall_ms", "cuda_stream_span_ms"):
            for key, value in row.get("estimator_stage_timing", {}).get(family, {}).items():
                measurements[f"estimator.{family}.{key}"].append(value)
                if not row.get("first_model_call"):
                    measurements[f"steady_estimator.{family}.{key}"].append(value)
    distinct_frames = {row.get("frame_index") for row in rows}
    ok_frames = {row.get("frame_index") for row in rows if row.get("status") == "ok"}
    return {"path": str(Path(path).resolve()), "complete_records": len(rows),
            "first_model_call_records": sum(row.get("first_model_call") is True for row in rows),
            "first_model_call_inference_ms": [row.get("inference_ms") for row in rows if row.get("first_model_call") is True],
            "distinct_source_frames": len(distinct_frames), "frames_with_ok_subject": len(ok_frames),
            "trailing_incomplete_line_ignored": incomplete,
            "status_counts": dict(Counter(row.get("status") for row in rows)),
            "geometry_invalid_ok_records": sum(bool(native_geometry_errors(row)) for row in rows if row.get("status") == "ok"),
            "frame_range": [rows[0].get("frame_index"), rows[-1].get("frame_index")] if rows else None,
            "statistics": {key: stats(values) for key, values in measurements.items()},
            "rolling_500_record_latency_ms": [stats([row["inference_ms"] for row in rows[start:start+500] if "inference_ms" in row])
                                             for start in range(0, len(rows), 500)],
            "semantics": "Nested stages and request waits overlap; do not sum. CUDA spans include stream idle; not GPU utilization. This is observed completion/geometry, not accuracy truth."}


def compare(first_path, second_path):
    first, first_incomplete = read_records(first_path)
    second, second_incomplete = read_records(second_path)
    fields = ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
              "timestamp_ms", "image_size", "bbox_px", "person_track_id", "selection_epoch", "camera_reference_epoch")
    mismatches, differences, compared, status_equal = [], defaultdict(list), 0, 0
    for index, (a, b) in enumerate(zip(first, second)):
        bad = [name for name in fields if a.get(name) != b.get(name)]
        bad.extend(name for name in fields if name not in a or name not in b or a[name] is None or b[name] is None)
        for row in (a, b):
            sha = row.get("video_sha256")
            if not isinstance(sha, str) or len(sha) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha):
                bad.append("video_sha256_missing_or_invalid")
        if str(a.get("video_sha256", "")).lower() != str(b.get("video_sha256", "")).lower():
            bad.append("video_sha256")
        if bad:
            mismatches.append({"record": index, "fields": sorted(set(bad))})
            continue
        status_equal += a.get("status") == b.get("status")
        if a.get("status") != "ok" or b.get("status") != "ok":
            continue
        if native_geometry_errors(a) or native_geometry_errors(b):
            mismatches.append({"record": index, "fields": ["native_geometry_invalid"]})
            continue
        compared += 1
        for field in ("keypoints_2d", "keypoints_3d"):
            differences[field].extend(abs(x-y) for name in a[field] for x, y in zip(a[field][name], b[field][name]))
    complete = not first_incomplete and not second_incomplete
    return {"first_records": len(first), "second_records": len(second), "compared_ok_records": compared,
            "both_files_complete": complete,
            "same_binding_status_records": status_equal, "binding_mismatches": mismatches,
            "all_records_bound_and_status_equal": complete and len(first) == len(second) == status_equal and not mismatches,
            "absolute_coordinate_differences": {key: stats(values) for key, values in differences.items()},
            "geometry_exact_numeric_equal": (complete and bool(compared) and len(first) == len(second) == status_equal and not mismatches
                                       and all(max(values) == 0 for values in differences.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--compare-native", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"first": summarize(args.native)}
    if args.compare_native:
        report.update(second=summarize(args.compare_native), comparison=compare(args.native, args.compare_native))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"output": str(args.output), "records": report["first"]["complete_records"]}))


if __name__ == "__main__":
    main()
