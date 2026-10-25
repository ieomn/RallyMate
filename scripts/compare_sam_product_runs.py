"""Offline same-input comparison of two completed product_probe directories.

No models or services are loaded. Timings never override failed input or output
equivalence checks. A treatment may vary only its declared configuration.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from scripts.summarize_sam_profile import compare, read_records, summarize


TIMING_FIELDS = {
    "first_model_call", "inference_ms", "peak_memory_bytes", "memory_scope",
    "peak_reserved_memory_bytes", "current_allocated_memory_bytes",
    "current_reserved_memory_bytes", "stage_timing_ms", "estimator_stage_timing",
    "ipc_request_timing_ms", "ipc_timing_scope",
}
TREATMENTS = ("cache", "threads", "prefetch", "prompt", "prompt_rotation")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prediction(row):
    return {key: value for key, value in row.items() if key not in TIMING_FIELDS}


def normalized_models(summary, treatment):
    models = deepcopy(summary["models"])
    pose = models["pose_backend"]
    pose.pop("load_seconds", None)
    if treatment == "cache":
        pose.pop("cuda_cache_policy", None)
    elif treatment == "threads":
        pose.pop("cpu_threads", None)
    elif treatment in ("prompt", "prompt_rotation"):
        # One explicitly named treatment only. Source, precision, cache policy,
        # threads and every other known or future metadata field stay compared.
        pose.pop("prompt_vectorization", None)
        if treatment == "prompt_rotation":
            pose.pop("rotation_vectorization", None)
    return models


def normalized_runtime(summary, treatment):
    runtime = deepcopy(summary["runtime"])
    if treatment == "threads":
        for key in ("cpu_thread_limit", "torch_interop_threads", "opencv_threads"):
            runtime.pop(key, None)
    return runtime


def load_run(probe):
    probe = Path(probe)
    intent, result = read_json(probe / "intent.json"), read_json(probe / "result.json")
    job = result["job"]
    run_dir = Path(job["output_dir"])
    request_path = Path(job["request_path"])
    request, summary = read_json(request_path), read_json(run_dir / "summary.json")
    native_path = run_dir / "sam3d_frames.jsonl"
    rows, incomplete = read_records(native_path)
    upload = Path(request["source"]["video_path"])
    source_sha = digest(upload)
    expected_sha = intent["sha256"].lower()
    return {"probe": str(probe.resolve()), "intent": intent, "result": result,
            "request": request, "summary": summary, "native_path": native_path,
            "native": rows, "incomplete": incomplete,
            "upload_sha256": source_sha,
            "source_sha_consistent": source_sha == expected_sha == result["source_sha256"].lower()
            and all(row.get("video_sha256", "").lower() == source_sha for row in rows),
            "identity_consistent": job["id"] == request["job_id"] == summary["job_id"],
            "artifact_sha256": {"intent": digest(probe / "intent.json"),
                                "result": digest(probe / "result.json"),
                                "request": digest(request_path),
                                "summary": digest(run_dir / "summary.json"),
                                "native": digest(native_path)}}


def coordinate_tolerance_check(geometry, tolerance_2d, tolerance_3d):
    if not all(math.isfinite(value) and value >= 0 for value in (tolerance_2d, tolerance_3d)):
        raise ValueError("Diagnostic tolerances must be finite and nonnegative")
    differences = geometry["absolute_coordinate_differences"]
    return {"declared_by": "caller", "absolute_tolerance_2d_pixels": tolerance_2d,
            "absolute_tolerance_3d_model_units": tolerance_3d,
            "all_bound_coordinates_within_declared_tolerances": bool(
                geometry["all_records_bound_and_status_equal"] and geometry["compared_ok_records"]
                and differences.get("keypoints_2d", {}).get("max", math.inf) <= tolerance_2d
                and differences.get("keypoints_3d", {}).get("max", math.inf) <= tolerance_3d),
            "scope": "Diagnostic coordinates only; does not change exact-equality flags, certify pose accuracy, or cover camera/derived measurements."}


def evaluate(a, b, *, treatment="cache", first_calls=1, coordinate_tolerances=None):
    if treatment not in TREATMENTS:
        raise ValueError("Unknown comparison treatment")
    checks = {}
    checks["both_succeeded_without_cancellation"] = all(
        item["result"]["job"]["status"] == "succeeded"
        and item["summary"]["status"] == "completed"
        and item["result"].get("cancel_requested") is False for item in (a, b))
    checks["job_identity_consistent"] = all(item["identity_consistent"] for item in (a, b))
    checks["uploaded_bytes_identical_and_bound"] = (
        a["upload_sha256"] == b["upload_sha256"]
        and all(item["source_sha_consistent"] for item in (a, b)))
    checks["request_configuration_identical"] = all(
        a["request"].get(key) == b["request"].get(key)
        for key in ("schema_version", "models", "processing", "court", "scoring"))
    checks["technique_registry_identical"] = (
        a["request"].get("upstream_metadata", {}).get("technique_registry")
        == b["request"].get("upstream_metadata", {}).get("technique_registry"))
    checks["model_metadata_identical_except_declared_treatment"] = (
        normalized_models(a["summary"], treatment) == normalized_models(b["summary"], treatment))
    checks["runtime_identical_except_declared_treatment"] = (
        normalized_runtime(a["summary"], treatment) == normalized_runtime(b["summary"], treatment))
    checks["required_model_identity_present"] = all(
        all(item["summary"]["models"]["pose_backend"].get(key)
            for key in ("model_sha256", "config_sha256", "mhr_sha256", "source_commit", "inference_type"))
        for item in (a, b))
    processing_fields = ("processed_frames", "source_start_frame", "source_end_frame_exclusive",
                         "last_processed_timestamp_ms", "source_timing", "frame_stride", "camera_motion")
    checks["actual_processing_identical"] = all(
        a["summary"]["processing"].get(key) == b["summary"]["processing"].get(key)
        for key in processing_fields)
    if treatment != "prefetch":
        checks["prefetch_policy_identical"] = (
            a["summary"]["processing"].get("source_prefetch")
            == b["summary"]["processing"].get("source_prefetch"))
    checks["all_requested_frames_processed"] = all(
        item["summary"]["processing"]["processed_frames"] == item["intent"]["frames"]
        for item in (a, b))
    checks["first_model_call_count_matches_policy"] = all(
        sum(row.get("first_model_call") is True for row in item["native"]) == first_calls
        for item in (a, b))
    checks["counts_identical"] = a["summary"]["counts"] == b["summary"]["counts"]
    geometry = compare(a["native_path"], b["native_path"])
    checks["native_binding_and_status_identical"] = geometry["all_records_bound_and_status_equal"]
    full_exact = ([prediction(row) for row in a["native"]]
                  == [prediction(row) for row in b["native"]])
    changed_fields = Counter()
    for first, second in zip(a["native"], b["native"]):
        first, second = prediction(first), prediction(second)
        for key in first.keys() | second.keys():
            if first.get(key) != second.get(key):
                changed_fields[key] += 1
    numerical_equal = geometry["geometry_exact_numeric_equal"] and full_exact
    timings = {}
    for label, item in (("baseline", a), ("candidate", b)):
        timings[label] = {
            "post_to_observed_terminal_seconds": item["result"]["wall_seconds"],
            "pipeline_seconds": item["summary"]["processing"]["elapsed_seconds"],
            "pipeline_stage_seconds": item["summary"]["processing"]["stage_seconds"],
            "model_load_seconds_excluded_from_request": item["summary"]["models"]["pose_backend"].get("load_seconds"),
            "native": summarize(item["native_path"]),
        }
    return {"schema": "sam-product-same-input-comparison-v1", "treatment": treatment,
            "declared_treatment_metadata": ({
                "fields": {name: {
                    "path": "models.pose_backend." + name,
                    "baseline_present": name in a["summary"]["models"]["pose_backend"],
                    "candidate_present": name in b["summary"]["models"]["pose_backend"],
                    "baseline": a["summary"]["models"]["pose_backend"].get(name),
                    "candidate": b["summary"]["models"]["pose_backend"].get(name),
                } for name in (("prompt_vectorization", "rotation_vectorization")
                               if treatment == "prompt_rotation" else ("prompt_vectorization",))},
                "scope": "Only these explicitly named metadata fields may differ; all other model and runtime metadata remain compared. Missing baseline fields remain reported as absent."
            } if treatment in ("prompt", "prompt_rotation") else None),
            "checks": checks, "same_workload_verified": all(checks.values()),
            "numerical_equivalence_verified": numerical_equal,
            "numerical_equivalence_policy": "exact_numeric_equality_of_all_non_timing_native_predictions; false_is_not_an_accuracy_failure",
            "coordinate_tolerance_diagnostic": (coordinate_tolerance_check(geometry, *coordinate_tolerances)
                                                if coordinate_tolerances is not None else None),
            "changed_native_field_record_counts": dict(changed_fields),
            "full_native_predictions_exact_equal_excluding_timing": full_exact,
            "geometry": geometry, "timing": timings,
            "source_sha256": a["upload_sha256"],
            "probes": [item["probe"] for item in (a, b)],
            "artifact_sha256": [item["artifact_sha256"] for item in (a, b)],
            "semantics": "Only the declared treatment may vary. API wall includes upload/queue/first-call warmup/postprocessing and <=0.5s observation lag, excludes eager model loading. Native stages are nested, not additive. Exact equality establishes reproducibility, not human pose accuracy. GPU total usage must be read with fixed idle processes from probe samples."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--treatment", choices=TREATMENTS, default="cache")
    parser.add_argument("--expected-first-model-calls", type=int, choices=(0, 1), default=1)
    parser.add_argument("--diagnostic-2d-atol", type=float)
    parser.add_argument("--diagnostic-3d-atol", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tolerance = (args.diagnostic_2d_atol, args.diagnostic_3d_atol)
    if any(value is not None for value in tolerance) and any(value is None for value in tolerance):
        parser.error("Both diagnostic tolerances must be explicitly supplied together")
    report = evaluate(load_run(args.baseline), load_run(args.candidate),
                      treatment=args.treatment, first_calls=args.expected_first_model_calls,
                      coordinate_tolerances=tolerance if tolerance[0] is not None else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({key: report[key] for key in ("same_workload_verified", "numerical_equivalence_verified", "checks")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
