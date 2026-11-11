"""Offline FIFO capacity estimates from measured isolated SAM pipeline probes.

No GPU, worker, network, deployment or purchase is performed. Each probe and
batch configuration stays a separate case; extrapolation is never qualification.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

MAX_PROBE_BYTES = 128 * 1024 * 1024
SCHEMA = "measured-pipeline-capacity-simulation-v1"


def _positive(value, name, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            or integer and type(value) is not int):
        raise ValueError(f"{name} must be a finite positive {'integer' if integer else 'number'}")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _observed_source(probe):
    binding = probe.get("source_binding") or {}
    groups = binding.get("frames") or []
    histogram, native_histogram, statuses = Counter(), Counter(), Counter()
    first = last = None
    for group in groups:
        if not isinstance(group, list):
            raise ValueError("source_binding.frames must contain frame arrays")
        if any(not isinstance(row, dict) for row in group):
            raise ValueError("source bindings must be objects")
        for row in group:
            status = row.get("status", "ok")
            if status not in ("ok", "skipped", "failed", "error"):
                raise ValueError("unrecognized native source status")
            statuses[status] += 1
        histogram[sum(row.get("status", "ok") == "ok" for row in group)] += 1
        native_histogram[len(group)] += 1
        if group:
            row = group[0]
            for field in ("frame_index", "pts", "time_base_num", "time_base_den"):
                value = row.get(field)
                if type(value) is not int or value < 0 or field.startswith("time_base") and value == 0:
                    raise ValueError("source PTS binding is invalid")
            point = (row["frame_index"], row["pts"] * row["time_base_num"] / row["time_base_den"])
            if first is None:
                first = point
            last = point
    fps = None
    if first and last and last[0] > first[0] and last[1] > first[1]:
        fps = (last[0] - first[0]) / (last[1] - first[1])
    return {"video_sha256": binding.get("video_sha256"),
        "observed_pts_interval_mean_fps": fps,
        "observed_frame_count": len(groups), "observed_subject_count_histogram": dict(sorted(histogram.items())),
        "native_record_histogram": dict(sorted(native_histogram.items())),
        "native_record_status_counts": dict(sorted(statuses.items())),
        "skipped_records": statuses["skipped"],
        "failed_or_error_records": statuses["failed"] + statuses["error"],
        "observed_target_count": sum(count * frames for count, frames in histogram.items()),
        "semantics": "successful native subjects only (legacy missing status means ok); skipped placeholders are not people; not submitted user count or verified player identity"}


def load_cases(path, *, source_fps=None, allow_window_extrapolation=False):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROBE_BYTES + 1)
    if len(raw) > MAX_PROBE_BYTES:
        raise ValueError("probe exceeds the bounded 128 MiB input limit")
    probe = json.loads(raw, object_pairs_hook=_object, parse_constant=_reject_constant)
    if not isinstance(probe, dict) or probe.get("schema_version") != "sam3d-isolated-pipeline-probe-v2":
        raise ValueError("expected a sam3d-isolated-pipeline-probe-v2 receipt")
    if probe.get("status") not in ("completed_pending_artifact_comparison", "completed_comparison_not_scoring_acceptance"):
        raise ValueError("probe has not completed its measured pipeline trials")
    frames = _positive(probe.get("frames_requested"), "frames_requested", integer=True)
    start_frame = probe.get("start_frame", 0)
    if type(start_frame) is not int or start_frame < 0:
        raise ValueError("start_frame must be a nonnegative integer")
    if start_frame and not allow_window_extrapolation:
        raise ValueError("nonzero start_frame includes prefix decoding; use --allow-window-extrapolation to label this bias explicitly")
    trials = probe.get("trials")
    order = probe.get("trial_order")
    if not isinstance(trials, list) or not trials or not isinstance(order, list) or len(trials) != len(order):
        raise ValueError("all declared measured trials must be present")
    grouped = {}
    for trial, declared in zip(trials, order):
        batch = _positive(trial.get("batch_frames"), "batch_frames", integer=True)
        if batch != declared:
            raise ValueError("trial order and measured batch configuration disagree")
        wall = _positive(trial.get("caller_wall_seconds"), "caller_wall_seconds")
        processing = trial.get("processing") or {}
        if processing.get("processed_frames") != frames or processing.get("frame_stride") != 1:
            raise ValueError("each trial must process every requested frame with stride one")
        grouped.setdefault(batch, []).append(float(wall))
    observed = _observed_source(probe)
    if source_fps is not None:
        fps = _positive(source_fps, "source_fps")
        fps_basis = "explicit_operator_supplied_source_metadata"
    else:
        fps = _positive(observed["observed_pts_interval_mean_fps"], "source PTS interval mean FPS (or supply --source-fps)")
        fps_basis = "derived_from_first_last_retained_decoder_PTS_not_container_nominal_fps"
    provenance = {"probe_path": str(path), "probe_sha256": hashlib.sha256(raw).hexdigest(),
        "probe_status": probe["status"], "video_path_as_recorded": probe.get("video"),
        "source": observed, "source_fps": fps, "source_fps_basis": fps_basis,
        "source_frames_requested": frames, "source_start_frame": start_frame,
        "approximate_source_interval_seconds": frames / fps,
        "max_players_as_recorded": probe.get("max_players"),
        "probe_scope": probe.get("scope"), "timing_scope": probe.get("timing_scope"),
        "sampling_design": probe.get("sampling_design", "see_trial_order"),
        "trial_order": order, "experiment_configuration": probe.get("experiment_configuration"),
        "comparison_reference": probe.get("comparison"),
        "scoring_accuracy_validated_as_recorded": probe.get("scoring_accuracy_validated", False)}
    cases = []
    for batch, times in sorted(grouped.items()):
        median = statistics.median(times)
        cases.append({"case_id": f"{provenance['probe_sha256'][:12]}-batch{batch}",
            "measurement_kind": "single_gpu_pipeline_measurement", "batch_frames": batch,
            "batch_policy": "production_default_batch_size" if batch == 1 else "candidate_requires_accuracy_and_evidence_gate",
            "deployment_configuration_authorized": False,
            "policy_note": "batch1 is the default batch size; the measured graph/archive experiment may differ from production. Candidate timing never enables deployment.",
            "provenance": provenance,
            "samples": {"count": len(times), "caller_wall_seconds": times,
                "median_seconds": median, "minimum_seconds": min(times), "maximum_seconds": max(times),
                "variability_scope": "observed trials only; not a confidence interval or production tail estimate"},
            "measured_pipeline_frames_per_second": frames / median,
            "measured_seconds_per_frame": median / frames,
            "linear_extrapolation_warnings": [
                "Short clips include fixed postprocessing overhead; extrapolation is not a full-video benchmark.",
                "Person density, motion, event count, resolution, codec and pose success rate must resemble this measured source.",
                "Scenario 60 fps is a frame-count extrapolation; it is not an observed 60 fps benchmark.",
                *(["Nonzero start_frame includes source-prefix decoding; this biased per-frame estimate is explicitly opted in."] if start_frame else [])]})
    return cases


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def fifo_burst(submitters, service_seconds, gpu_workers):
    _positive(submitters, "submitters", integer=True)
    _positive(gpu_workers, "gpu_workers", integer=True)
    _positive(service_seconds, "service_seconds")
    if submitters > 10000 or gpu_workers > 10000:
        raise ValueError("simulation is bounded to 10000 submitters/workers")
    starts = [(index // gpu_workers) * service_seconds for index in range(submitters)]
    finishes = [start + service_seconds for start in starts]
    def distribution(values):
        return {"min": min(values), "p50": nearest_rank(values, .5),
                "p95": nearest_rank(values, .95), "max": max(values)}
    return {"queue_wait_seconds": distribution(starts), "completion_seconds": distribution(finishes),
        "waves": math.ceil(submitters / gpu_workers), "burst_completed_seconds": max(finishes),
        "ideal_steady_capacity_jobs_per_hour": gpu_workers * 3600 / service_seconds}


def workers_for_wait_slo(submitters, service_seconds, maximum_wait_seconds):
    _positive(submitters, "submitters", integer=True)
    _positive(service_seconds, "service_seconds")
    if (type(maximum_wait_seconds) not in (int, float) or not math.isfinite(maximum_wait_seconds)
            or maximum_wait_seconds < 0):
        raise ValueError("maximum_wait_seconds must be finite and nonnegative")
    # Search avoids a fragile division/floor boundary when a float is just
    # above an exact multiple. This is a small bounded hypothetical burst.
    if submitters > 10000:
        raise ValueError("SLO simulation is bounded to 10000 submitters")
    return next(workers for workers in range(1, submitters + 1)
                if ((submitters - 1) // workers) * service_seconds <= maximum_wait_seconds)


def build_plan(cases, *, submitters=(20, 24), minutes=(5, 10), fps_values=(30, 60),
               cards=(1, 2, 4, 8), wait_slo_minutes=10):
    if not cases:
        raise ValueError("at least one measured case is required")
    scenarios, requirements = [], []
    for case in cases:
        for count in submitters:
            for duration in minutes:
                for fps in fps_values:
                    _positive(duration, "video minutes")
                    _positive(fps, "scenario FPS")
                    target_frames = math.ceil(duration * 60 * fps)
                    service = target_frames * case["measured_seconds_per_frame"]
                    identity = {"case_id": case["case_id"], "classification": "simulated_linear_extrapolation_not_multi_gpu_measurement",
                        "submitters": count, "video_minutes": duration, "video_fps": fps,
                        "target_frames": target_frames, "estimated_single_job_service_seconds": service,
                        "frame_scale_from_measured_case": target_frames / case["provenance"]["source_frames_requested"]}
                    for workers in cards:
                        scenarios.append({**identity, "gpu_workers": workers, "concurrency_per_card": 1,
                            **fifo_burst(count, service, workers)})
                    required = workers_for_wait_slo(count, service, wait_slo_minutes * 60)
                    requirements.append({**identity, "maximum_queue_wait_slo_minutes": wait_slo_minutes,
                        "minimum_gpu_workers_under_ideal_assumptions": required,
                        "estimated_max_wait_seconds": ((count - 1) // required) * service,
                        "not_a_hardware_purchase_or_deployment_recommendation": True})
    return {"schema_version": SCHEMA, "status": "offline_simulation_only", "measured_cases": cases,
        "assumptions": [
            "All submissions arrive at time zero; FIFO queue starts empty; jobs in each scenario have identical frame counts and service times.",
            "One active inference job per physical GPU; identical GPUs and warm persistent models.",
            "Pipeline service time scales linearly by processed frame count from caller_wall_seconds / frames_requested.",
            "No shared CPU, RAM, NVMe, PCIe or upload-network contention; ideal scaling must be verified on real multiple GPUs.",
            "Upload time, model load, warmup, webpage delivery and independent mesh video rendering are excluded, following the input benchmark scope.",
            "p50/p95 use nearest-rank positions within this deterministic simultaneous burst; they are not measured production latency percentiles.",
            "Required GPU counts satisfy the selected queue-wait SLO under these assumptions only; inference itself still takes one service interval.",
            "No cloud prices, procurement, public deployment or inference settings are changed."],
        "scenarios": scenarios, "wait_slo_requirements": requirements}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, action="append", required=True, help="Repeat to compare source probes; cases are never pooled")
    parser.add_argument("--source-fps", type=float, help="Optional documented source FPS for all supplied probes; otherwise derives retained PTS mean")
    parser.add_argument("--allow-window-extrapolation", action="store_true")
    parser.add_argument("--wait-slo-minutes", type=float, default=10, help="Hypothetical maximum queue wait; default 10 minutes, not an agreed customer SLO")
    parser.add_argument("--output", type=Path, required=True, help="New JSON file; refuses overwrite")
    args = parser.parse_args(argv)
    try:
        cases = [case for path in args.probe for case in load_cases(path, source_fps=args.source_fps,
                  allow_window_extrapolation=args.allow_window_extrapolation)]
        if len({case["case_id"] for case in cases}) != len(cases):
            raise ValueError("duplicate source probe supplied")
        result = build_plan(cases, wait_slo_minutes=args.wait_slo_minutes)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output.resolve()), "cases": len(cases),
        "scenarios": len(result["scenarios"]), "status": result["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
