"""Read-only CPU/product acceptance for completed 300-frame probe directories.

No GPU calls, model loading, process inspection, API requests, or source writes.
Exact comparisons remain visible separately from caller-declared tolerances.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import numpy as np

from scripts.compare_sam_product_runs import digest, evaluate, load_run, read_json
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_vision.pose.metadata import MHR70_NAMES


EXPECTED_SOURCE_SHA = "4dec5a1031ab956e28c53dc424da2178416f8b5df4fdee66b9da8ebddcf31589"
BINDING_FIELDS = ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                  "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch")


def jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def cpu_accounting(samples):
    """Account CPU time by OS process identity, not sampled CPU percentages."""
    if len(samples) < 2:
        raise ValueError("at least two CPU samples are required")
    observations = defaultdict(list)
    issues = []
    for index, sample in enumerate(samples):
        seen = set()
        for process in sample.get("processes", []):
            identity = (process["pid"], process.get("create_time"))
            if identity in seen:
                issues.append(f"duplicate process identity at sample {index}: {identity}")
                continue
            seen.add(identity)
            observations[identity].append((index, float(sample["elapsed_seconds"]), process))
    processes, totals = [], defaultdict(float)
    for (pid, created), rows in observations.items():
        roles = {row[2].get("role", "unclassified") for row in rows}
        role = next(iter(roles)) if len(roles) == 1 else "conflicting_roles"
        first, last = rows[0], rows[-1]
        cpu = [float(row[2]["cpu_seconds"]) for row in rows]
        elapsed = last[1] - first[1]
        valid = (created is not None and len(rows) >= 2 and elapsed > 0
                 and all(math.isfinite(value) and value >= 0 for value in cpu)
                 and all(b >= a for a, b in zip(cpu, cpu[1:]))
                 and len(roles) == 1 and role != "unclassified")
        delta = cpu[-1] - cpu[0] if valid else None
        if not valid:
            issues.append(f"incomplete or unverified CPU identity/interval for pid {pid}")
        if delta is not None:
            totals[role] += delta
        processes.append({
            "pid": pid, "create_time": created, "role": role,
            "parent_pid": first[2].get("parent_pid"), "sample_count": len(rows),
            "first_sample_index": first[0], "last_sample_index": last[0],
            "first_elapsed_seconds": first[1], "last_elapsed_seconds": last[1],
            "observed_interval_seconds": elapsed, "first_cpu_seconds": cpu[0],
            "last_cpu_seconds": cpu[-1], "cpu_core_seconds": delta,
            "average_cores_during_own_interval": delta / elapsed if delta is not None else None,
            "present_at_every_global_sample": len(rows) == len(samples), "valid": valid,
        })
    start, end = float(samples[0]["elapsed_seconds"]), float(samples[-1]["elapsed_seconds"])
    interval = end - start
    tracked = [row for row in processes if row["role"] in {"api", "sam"}]
    complete = (interval > 0 and {row["role"] for row in tracked} == {"api", "sam"}
                and all(row["valid"] and row["present_at_every_global_sample"] for row in tracked)
                and not any(row["role"] in {"unclassified", "conflicting_roles"} for row in processes))
    return {
        "samples": len(samples), "sample_start_elapsed_seconds": start,
        "sample_end_elapsed_seconds": end, "sampled_wall_seconds": interval,
        "first_job_status": samples[0]["job"]["status"], "last_job_status": samples[-1]["job"]["status"],
        "per_process": processes, "cpu_core_seconds_by_role": dict(totals),
        "api_plus_sam_cpu_core_seconds": totals["api"] + totals["sam"] if complete else None,
        "api_plus_sam_average_cores": (totals["api"] + totals["sam"]) / interval if complete else None,
        "same_full_sample_interval_for_api_and_sam": complete,
        "issues": issues,
        "scope": "Sum of last-minus-first user+system CPU seconds for each (PID, create_time), including any zero-CPU SAM launcher. Excludes pre-first-sample load/upload CPU and post-last-sample work; includes queue idle and terminal observation lag. One core-second is one CPU core busy for one second; no host-logical-core normalization. Missing/changed identities are not extrapolated.",
    }


def array_difference(first, second, atol):
    a, b = np.asarray(first), np.asarray(second)
    valid = (a.shape == b.shape and a.size > 0 and a.dtype.kind in "fiu" and b.dtype.kind in "fiu"
             and np.isfinite(a).all() and np.isfinite(b).all())
    delta = np.abs(a.astype(np.float64) - b.astype(np.float64)) if valid else None
    return {"first_shape": list(a.shape), "second_shape": list(b.shape),
            "first_dtype": str(a.dtype), "second_dtype": str(b.dtype),
            "shape_and_finite_valid": bool(valid), "same_dtype": a.dtype == b.dtype,
            "numeric_exact": bool(valid and np.array_equal(a, b)),
            "max_abs_difference": float(delta.max()) if valid else None,
            "changed_elements": int(np.count_nonzero(delta)) if valid else None,
            "elements_above_atol": int(np.count_nonzero(delta > atol)) if valid else None,
            "within_atol": bool(valid and (delta <= atol).all()), "atol": atol, "rtol": 0}


def aggregate_differences(rows):
    return {"compared_records": len(rows),
            "all_exact": bool(rows) and all(row["numeric_exact"] for row in rows),
            "all_within_atol": bool(rows) and all(row["within_atol"] for row in rows),
            "all_same_dtype": bool(rows) and all(row["same_dtype"] for row in rows),
            "max_abs_difference": max((row["max_abs_difference"] for row in rows if row["max_abs_difference"] is not None), default=None),
            "changed_elements": sum(row["changed_elements"] or 0 for row in rows),
            "elements_above_atol": sum(row["elements_above_atol"] or 0 for row in rows),
            "invalid_record_indexes": [i for i, row in enumerate(rows) if not row["shape_and_finite_valid"]],
            "first_changed_records": [{"index": i, **row} for i, row in enumerate(rows) if not row["numeric_exact"]][:10]}


def owned_mesh_path(folder, value):
    path = (folder / value).resolve()
    if path.parent != (folder / "sam3d-mesh-data").resolve() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("mesh array is outside its owned directory or size bound")
    return path


def mesh_arrays(folder, row, topology_cache):
    artifact = row["mesh_artifact"]
    path = owned_mesh_path(folder, artifact["path"])
    faces_path = owned_mesh_path(folder, artifact["faces_path"])
    if digest(path) != artifact["sha256"].lower():
        raise ValueError("captured mesh hash mismatch")
    if faces_path not in topology_cache:
        if digest(faces_path) != artifact["faces_sha256"].lower():
            raise ValueError("captured mesh topology hash mismatch")
        topology_cache[faces_path] = np.load(faces_path, allow_pickle=False)
    with np.load(path, allow_pickle=False) as payload:
        arrays = {key: payload[key].copy() for key in ("vertices", "camera", "focal")}
    arrays["faces"] = topology_cache[faces_path]
    if (arrays["vertices"].shape != (artifact["vertex_count"], 3)
            or arrays["faces"].shape != (artifact["face_count"], 3)
            or arrays["camera"].shape != (3,) or arrays["focal"].size != 1):
        raise ValueError("captured mesh dimensions do not match native descriptor")
    if (not np.array_equal(arrays["camera"], np.asarray(row["pred_cam_t"]))
            or float(arrays["focal"].reshape(-1)[0]) != float(np.asarray(row["focal_length"]).reshape(-1)[0])):
        raise ValueError("captured mesh camera does not match native estimator record")
    return arrays


def geometric_comparison(a, b):
    differences = defaultdict(list)
    shapes_valid = True
    mesh_presence_equal = True
    caches = [{}, {}]
    folders = [Path(item["result"]["job"]["output_dir"]) for item in (a, b)]
    for first, second in zip(a["native"], b["native"]):
        if first.get("status") != "ok" or second.get("status") != "ok":
            continue
        for field, size, atol in (("keypoints_2d", 2, .01), ("keypoints_3d", 3, .0001)):
            if set(first.get(field, {})) != set(MHR70_NAMES) or set(second.get(field, {})) != set(MHR70_NAMES):
                shapes_valid = False
                continue
            x, y = [[row[field][name] for name in MHR70_NAMES] for row in (first, second)]
            shapes_valid &= np.shape(x) == np.shape(y) == (70, size)
            differences[field].append(array_difference(x, y, atol))
        for field, atol in (("pred_cam_t", .0001), ("focal_length", .01)):
            differences[field].append(array_difference(first[field], second[field], atol))
        mesh_presence_equal &= bool(first.get("mesh_artifact")) == bool(second.get("mesh_artifact"))
        if first.get("mesh_artifact") and second.get("mesh_artifact"):
            x, y = [mesh_arrays(folder, row, cache)
                    for folder, row, cache in zip(folders, (first, second), caches)]
            for field, atol in (("vertices", .0001), ("camera", .0001), ("focal", .01), ("faces", 0)):
                differences["mesh_" + field].append(array_difference(x[field], y[field], atol))
    return {"every_ok_record_has_exactly_70_named_2d_and_3d_points": bool(shapes_valid),
            "mesh_presence_matches_each_record": bool(mesh_presence_equal),
            "captured_mesh_hashes_and_native_camera_verified": True,
            "fields": {key: aggregate_differences(values) for key, values in differences.items()},
            "tolerance_scope": "2D coordinates and focal length: absolute 1e-2 pixels. 3D keypoints, vertices and camera translation: absolute 1e-4 model units. Faces require exact equality. rtol=0. No tolerance is applied to identities, timing windows, score decisions, or exact flags."}


def score_projection(item):
    folder = Path(item["result"]["job"]["output_dir"])
    features = list(jsonl(folder / "indicator-features.jsonl"))
    process = build_process_evaluation(item["summary"], features)
    windows, values = [], []
    for dimension in process["dimensions"]:
        for row in dimension["criteria"]:
            windows.append([dimension["id"], *[row.get(key) for key in
                            ("id", "start_ms", "end_ms", "status", "unit", "source", "assessment_status")]])
            values.append({key: row.get(key) for key in ("value", "score")})
    events = list(jsonl(folder / "events.jsonl"))
    event_binding = {row["event_id"]: [row.get(key) for key in
                                     ("event_code", "person_track_id", "start_ms", "end_ms")] for row in events}
    decisions = []
    unresolved = []
    for row in jsonl(folder / "scores.jsonl"):
        if row.get("event_id") not in event_binding:
            unresolved.append(row.get("event_id"))
        decisions.append({"event_window": event_binding.get(row.get("event_id")),
                          **{key: row.get(key) for key in ("indicator_id", "status", "grade", "reason_codes",
                              "quality_flags", "feature_version", "source_frames", "threshold_version", "grade_rule")}})
    decisions.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return {"process_version": process["version"], "relative_reference": process["relative_reference"],
            "formal_overall_score": process["overall_score"], "windows": windows, "values": values,
            "indicator_decisions": decisions, "unresolved_score_event_ids": unresolved,
            "criterion_counts": dict(Counter(window[0] for window in windows)),
            "scope": "Rebuilt by current process evaluator from each saved summary and all indicator-feature records. Run-specific event/episode/job IDs are not compared as identity; windows and native tracked subject bindings are compared explicitly."}


def compare_products(baseline, candidate):
    a, b = load_run(baseline), load_run(candidate)
    strict = evaluate(a, b, treatment="threads", first_calls=0, coordinate_tolerances=(.01, .0001))
    geometry = geometric_comparison(a, b)
    cpu = {label: cpu_accounting(list(jsonl(Path(item["probe"]) / "samples.jsonl")))
           for label, item in (("baseline", a), ("candidate", b))}
    score = [score_projection(item) for item in (a, b)]
    first_calls = [sum(row.get("first_model_call") is True for row in item["native"]) for item in (a, b)]
    call_state_known = [bool(item["native"]) and all(
        isinstance(row.get("first_model_call"), bool) for row in item["native"] if row.get("status") == "ok")
        and any(row.get("status") == "ok" for row in item["native"]) for item in (a, b)]
    frame_envelopes = []
    for item in (a, b):
        folder = Path(item["result"]["job"]["output_dir"])
        frame_envelopes.append([row["frame"] for row in jsonl(folder / "frames.jsonl")])
    checks = {
        "api_and_sam_cpu_intervals_verified": all(row["same_full_sample_interval_for_api_and_sam"] for row in cpu.values()),
        "cold_or_warm_state_known": all(call_state_known) and all(count in (0, 1) for count in first_calls),
        "expected_source_and_300_frame_request": all(
            item["upload_sha256"] == EXPECTED_SOURCE_SHA
            and all(item["intent"].get(key) == value for key, value in
                    (("frames", 300), ("start_ms", 18000), ("players", 2), ("court_mode", "disabled"))) for item in (a, b)),
        "300_actual_frame_envelopes_identical": len(frame_envelopes[0]) == len(frame_envelopes[1]) == 300 and frame_envelopes[0] == frame_envelopes[1],
        "native_binding_and_status_identical": strict["checks"]["native_binding_and_status_identical"],
        "all_70_point_shapes_valid": geometry["every_ok_record_has_exactly_70_named_2d_and_3d_points"],
        "mesh_every_record_and_all_fields_present": geometry["mesh_presence_matches_each_record"] and
            set(geometry["fields"]) == {"keypoints_2d", "keypoints_3d", "pred_cam_t", "focal_length", "mesh_vertices", "mesh_camera", "mesh_focal", "mesh_faces"},
        "all_geometry_within_declared_tolerances": bool(geometry["fields"]) and all(row["all_within_atol"] for row in geometry["fields"].values()),
        "score_windows_status_units_sources_exact": score[0]["windows"] == score[1]["windows"],
        "score_rule_values_exact": score[0]["values"] == score[1]["values"],
        "relative_reference_exact": score[0]["relative_reference"] == score[1]["relative_reference"],
        "formal_indicator_decisions_exact": score[0]["indicator_decisions"] == score[1]["indicator_decisions"] and not any(row["unresolved_score_event_ids"] for row in score),
    }
    content_workload = {key: value for key, value in strict["checks"].items() if key != "first_model_call_count_matches_policy"}
    changed_score_values = [{"index": index, "baseline_window": x, "candidate_window": y,
                             "baseline_value": xv, "candidate_value": yv}
                            for index, (x, y, xv, yv) in enumerate(zip(score[0]["windows"], score[1]["windows"], score[0]["values"], score[1]["values"])) if x != y or xv != yv]
    return {"version": "cpu-product-acceptance-v1", "timestamp": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "auditor_sha256": digest(__file__),
            "jobs": [item["result"]["job"]["id"] for item in (a, b)],
            "checks": checks, "all_acceptance_checks_pass": all(checks.values()) and all(content_workload.values()),
            "same_content_workload_except_first_call_state": all(content_workload.values()),
            "strict_legacy_comparison_unmodified": strict,
            "cold_warm": {"baseline_first_model_calls": first_calls[0], "candidate_first_model_calls": first_calls[1],
                          "baseline_state": "unknown" if not call_state_known[0] else "warm" if first_calls[0] == 0 else "cold" if first_calls[0] == 1 else "invalid_multiple_first_calls",
                          "candidate_state": "unknown" if not call_state_known[1] else "warm" if first_calls[1] == 0 else "cold" if first_calls[1] == 1 else "invalid_multiple_first_calls",
                          "matched_state": first_calls[0] == first_calls[1],
                          "scope": "Cold means first estimator inference after model load, not model-loading time. Probe elapsed and CPU sampling exclude eager model loading. Cross-state latency ratios are descriptive only."},
            "declared_changed_metadata": {
                "parent_actual_pools": [item["summary"]["runtime"] for item in (a, b)],
                "sam_cpu_threads": [item["summary"]["models"]["pose_backend"].get("cpu_threads") for item in (a, b)],
                "scope": "Only models.pose_backend.cpu_threads and parent runtime cpu_thread_limit/torch_interop_threads/opencv_threads are declared CPU policy differences. Unknown model/runtime changes remain strict failures."},
            "cpu": cpu, "geometry": geometry,
            "scores": {"baseline": score[0], "candidate": score[1], "changed_values": changed_score_values},
            "limits": ["Single 300-frame same-video observation is not a population accuracy or speed guarantee.",
                       "Native exact results retain path/hash/graph-runtime differences; numeric mesh comparisons independently validate captured arrays."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve existing acceptance receipt")
    deadline = time.monotonic() + args.wait_seconds
    while not all((folder / "result.json").exists() for folder in (args.baseline, args.candidate)):
        if time.monotonic() >= deadline:
            raise FileNotFoundError("both completed probe results are required")
        time.sleep(min(5, max(.1, deadline - time.monotonic())))
    report = compare_products(args.baseline, args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({key: report[key] for key in ("jobs", "checks", "cold_warm", "all_acceptance_checks_pass")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
