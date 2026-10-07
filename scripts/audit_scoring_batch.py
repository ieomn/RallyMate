"""Read-only verification of completed batch snapshots; never submits inference.

The reference-score implementation below deliberately does not import the
production evaluator. Its pinned feature/weight contract verifies arithmetic
and record provenance, not the validity of coach standards. Pending entries
remain pending. A non-null formal grade requires a separate trusted authority
audit; self-reported authorization fields alone never make that check pass.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any


AUDIT_VERSION = "scoring-batch-audit-v1.0.0"
WEIGHTS = {"measured_instance_ratio": .30, "required_feature_coverage": .20,
           "median_feature_confidence": .15, "repeatability": .25, "scoring_evidence_ratio": .10}
# Pinned v1.3.0 evidence-score inputs, NOT Word-derived technical thresholds.
FEATURES = {
    "FS01-M02": "hip_center_y_body left_knee_flexion_deg right_knee_flexion_deg stance_width_body hip_center_relative_to_ankle_support",
    "FS01-M03": "bilateral_foot_rise_min_body bilateral_foot_rise_synchrony_ms bilateral_foot_rise_proxy_duration_ms hip_center_vertical_velocity_body_s",
    "FS01-M04": "bilateral_foot_vertical_slowdown_time_offset_ms post_slowdown_stance_width_body hip_center_lateral_variability_body",
    "FS01-M05": "body_center_speed_body_s hip_center_relative_to_ankle_support torso_lean_deg shoulder_hip_angular_velocity",
    "FS02-M02": "body_center_speed_body_s hip_center_relative_to_ankle_support torso_lean_deg launch_direction_deg target_direction_alignment_error_deg",
    "FS02-M03": "launch_direction_deg drive_side_code support_knee_extension_velocity_deg_s hip_acceleration_along_launch_direction_body_s2 support_drive_to_moving_foot_rise_proxy_ms",
    "FS02-M04": "launch_direction_deg launch_side_code launch_foot_speed_peak_body_s launch_foot_relative_displacement_body launch_foot_motion_duration_ms",
    "FS02-M05": "launch_side_code launch_foot_speed_drop_body_s first_step_displacement_body post_step_hip_direction_consistency launch_foot_slowdown_to_post_hip_direction_ms post_step_stance_width_body",
    "FS09-M01": "hip_center_speed_body_s hip_center_motion_direction_deg hip_center_relative_to_ankle_midpoint_x_body hip_center_relative_to_ankle_midpoint_y_body hip_center_speed_trend_body_s2",
    "FS09-M02": "left_ankle_speed_body_s right_ankle_speed_body_s left_ankle_speed_drop_body_s right_ankle_speed_drop_body_s braking_side_code braking_ankle_speed_drop_body_s left_knee_flexion_change_deg right_knee_flexion_change_deg hip_center_deceleration_body_s2 braking_ankle_slowdown_to_hip_deceleration_ms",
    "FS09-M03": "hip_center_deceleration_body_s2 hip_center_speed_drop_body_s left_knee_flexion_change_deg right_knee_flexion_change_deg hip_height_delta_body torso_lean_variability_deg",
    "FS09-M04": "hip_center_relative_to_ankle_support hip_center_relative_to_ankle_midpoint_x_body hip_center_relative_to_ankle_midpoint_y_body hip_center_speed_drop_body_s stance_width_body stability_duration_ms shoulder_hip_angular_velocity_change_deg_s double_support_proxy_duration_ms",
    "FS09-M05": "stability_duration_ms hip_center_speed_drop_body_s double_support_proxy_duration_ms torso_lean_variability_deg shoulder_hip_angular_velocity_change_deg_s hip_deceleration_to_double_support_proxy_ms",
}
FEATURES = {key: tuple(value.split()) for key, value in FEATURES.items()}
REQUIRED_ARTIFACTS = ("summary", "demo_result", "scores.jsonl", "indicator-features.jsonl",
                      "events.jsonl", "scoring-loop-summary.json")


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except OverflowError:
        return None


def equivalent(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            equivalent(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            equivalent(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    if isinstance(expected, float):
        return finite(actual) is not None and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-9)
    return actual == expected


class Checks:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def expect(self, code: str, actual: Any, expected: Any) -> None:
        passed = equivalent(actual, expected)
        item = {"code": code, "status": "passed" if passed else "failed"}
        if not passed:
            item.update(actual=safe_detail(actual), expected=safe_detail(expected))
        self.items.append(item)

    def fail(self, code: str, reason: str) -> None:
        self.items.append({"code": code, "status": "failed", "reason": reason})


def safe_detail(value: Any, depth: int = 0) -> Any:
    """Keep invalid input diagnosable and the report finite/JSON serializable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and finite(value) is None:
        return "nonfinite_or_oversized_number"
    if depth > 5:
        return "nested_detail_truncated"
    if isinstance(value, dict):
        items = list(value.items())[:30]
        result = {str(k): safe_detail(v, depth + 1) for k, v in items}
        if len(value) > len(items):
            result["_omitted_keys"] = len(value) - len(items)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_detail(v, depth + 1) for v in value[:30]] + ([{"omitted_items": len(value) - 30}] if len(value) > 30 else [])
    return value[:1000] if isinstance(value, str) else value


def identity(record: dict) -> tuple | None:
    if (isinstance(record.get("video_id"), str) and record["video_id"].strip()
            and isinstance(record.get("person_track_id"), int) and not isinstance(record["person_track_id"], bool)
            and isinstance(record.get("event_id"), str) and record["event_id"].strip()
            and record.get("indicator_id") in FEATURES):
        return tuple(record[k] for k in ("video_id", "person_track_id", "event_id", "indicator_id"))
    return None


def normalized_records(records: list[dict]) -> tuple[dict, dict]:
    groups: dict[tuple, list] = defaultdict(list)
    validation = dict.fromkeys(("duplicate_record_count", "conflicting_record_count",
                               "event_code_mismatch_record_count", "unidentified_record_count"), 0)
    for record in records:
        indicator = record.get("indicator_id")
        if indicator not in FEATURES:
            continue
        if record.get("event_code") != indicator[:4]:
            validation["event_code_mismatch_record_count"] += 1
            continue
        key = identity(record)
        if key is None:
            validation["unidentified_record_count"] += 1
        groups[key if key is not None else ("unidentified", indicator)].append(record)
    result: dict[str, list] = {key: [] for key in FEATURES}
    for group in groups.values():
        if any(record != group[0] for record in group[1:]):
            validation["conflicting_record_count"] += len(group)
        else:
            validation["duplicate_record_count"] += len(group) - 1
            result[group[0]["indicator_id"]].append(group[0])
    return result, validation


def valid_features(record: dict) -> dict:
    result, seen = {}, set()
    for field in ("features", "scoring_features"):
        for feature in record.get(field, []) if isinstance(record.get(field), list) else []:
            if not isinstance(feature, dict):
                continue
            name = feature.get("feature_name")
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            if feature.get("valid") is True and finite(feature.get("value")) is not None:
                result[name] = (finite(feature["value"]), finite(feature.get("confidence")))
    return result


def repeatability(name: str, values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    if name.endswith("_code"):
        return max(Counter(values).values()) / len(values) * 100
    if name.endswith("_direction_deg"):
        vector = (statistics.fmean(math.sin(math.radians(v)) for v in values),
                  statistics.fmean(math.cos(math.radians(v)) for v in values))
        return max(0., min(100., math.hypot(*vector) * 100))
    q1, _, q3 = statistics.quantiles(sorted(values), n=4, method="inclusive")
    scale = max(abs(statistics.median(values)), abs(q1), abs(q3), .05)
    return (1 - min(1., abs(q3 - q1) / (2 * scale))) * 100


def expected_indicator(indicator: str, records: list[dict]) -> dict:
    required = FEATURES[indicator]
    measured = slots = eligible = 0
    confidences: list[float] = []
    independent: dict[str, list] = defaultdict(list)
    for record in records:
        gate = record.get("quality_gate") if isinstance(record.get("quality_gate"), dict) else {}
        status = record.get("feature_status")
        status = record.get("scoring_feature_status") if status is None else status
        if status != "measured" or gate.get("measurement_allowed") is False:
            continue
        measured += 1
        features = valid_features(record)
        eligible += gate.get("scoring_allowed") is True and all(name in features for name in required)
        for name in required:
            if name in features:
                value, confidence = features[name]
                slots += 1
                if confidence is not None:
                    confidences.append(confidence)
                if identity(record) is not None:
                    independent[name].append(value)
    repeated = [r for name, values in independent.items() if (r := repeatability(name, values)) is not None]
    percentage = lambda n, d: max(0, min(100, round(n / d * 100))) if d else 0
    components = {
        "measured_instance_ratio_percent": percentage(measured, len(records)),
        "required_feature_coverage_percent": percentage(slots, len(records) * len(required)),
        "median_feature_confidence_percent": max(0, min(100, round(statistics.median(confidences) * 100))) if confidences else None,
        "repeatability_percent": round(statistics.median(repeated)) if repeated else None,
        "scoring_evidence_ratio_percent": percentage(eligible, len(records)),
    }
    available = bool(measured and slots)
    weights = {k: v for k, v in WEIGHTS.items() if components[k + "_percent"] is not None} if available else {}
    weights = {k: v / sum(weights.values()) for k, v in weights.items()}
    raw = sum(components[k + "_percent"] * weight for k, weight in weights.items()) if weights else None
    return {"available": available, "measured_instance_count": measured, "total_instance_count": len(records),
            "components": components, "component_weights": WEIGHTS, "effective_component_weights": weights,
            "score_0_to_100": max(0, min(100, round(raw))) if raw is not None else None,
            "raw_score": raw}


def audit_explanation(checks: Checks, prefix: str, explanation: dict, expected: dict) -> None:
    checks.expect(prefix + ".semantics", explanation.get("score_semantics"), "measurement_evidence_quality")
    checks.expect(prefix + ".status", explanation.get("status"), "available" if expected["available"] else "unavailable")
    checks.expect(prefix + ".score", explanation.get("score_0_to_100"), expected["score_0_to_100"])
    checks.expect(prefix + ".raw", explanation.get("raw_score"), expected["raw_score"])
    adjustment = expected["score_0_to_100"] - expected["raw_score"] if expected["raw_score"] is not None else None
    checks.expect(prefix + ".rounding", explanation.get("rounding_adjustment_points"), adjustment)
    components = explanation.get("components", [])
    checks.expect(prefix + ".component_keys", [c.get("key") for c in components], list(WEIGHTS))
    for component in components:
        key = component.get("key")
        weight = expected["effective_component_weights"].get(key)
        value = float(expected["components"][str(key) + "_percent"]) if weight is not None else None
        checks.expect(prefix + f".{key}.included", component.get("included"), weight is not None)
        for field, answer in {"value_percent": value, "effective_weight": weight,
                "max_points": 100 * weight if weight is not None else None,
                "earned_points": value * weight if weight is not None else None,
                "deduction_points": (100 - value) * weight if weight is not None else None}.items():
            checks.expect(prefix + f".{key}.{field}", component.get(field), answer)


def audit_training(training: dict, records: list[dict], checks: Checks) -> dict:
    grouped, validation = normalized_records(records)
    checks.expect("training.input_validation", training.get("input_validation"), validation)
    checks.expect("training.component_weights", training.get("component_weights"), WEIGHTS)
    values = training.get("indicator_evaluations", [])
    checks.expect("training.indicator_ids", [v.get("indicator_id") for v in values], list(FEATURES))
    by_id = {v.get("indicator_id"): v for v in values}
    expected = {indicator: expected_indicator(indicator, grouped[indicator]) for indicator in FEATURES}
    for indicator, answer in expected.items():
        value = by_id.get(indicator, {})
        for key, item in answer.items():
            if key != "raw_score":
                checks.expect(f"{indicator}.{key}", value.get(key), item)
        if "score_explanation" in value:
            audit_explanation(checks, indicator + ".explanation", value["score_explanation"], answer)
    scores = [v["score_0_to_100"] for v in expected.values() if v["score_0_to_100"] is not None]
    rounded_mean = statistics.fmean(scores) if scores else None
    displayed = round(rounded_mean) if scores else None
    checks.expect("training.two_stage_rounding", training.get("score_0_to_100"), displayed)
    checks.expect("training.available", training.get("available"), bool(scores))
    checks.expect("training.evaluated_count", training.get("evaluated_indicator_count"), len(scores))
    checks.expect("training.total_count", training.get("total_indicator_count"), 13)
    checks.expect("training.aggregation", training.get("aggregation"), "unweighted_mean_of_available_indicator_reference_scores")
    action_values = training.get("action_evaluations", {})
    for event in ("FS01", "FS02", "FS09"):
        available = [v["score_0_to_100"] for k, v in expected.items() if k.startswith(event) and v["available"]]
        action = action_values.get(event, {})
        checks.expect(event + ".action_score", action.get("score_0_to_100"), round(statistics.fmean(available)) if available else None)
        checks.expect(event + ".nested_indicators", action.get("indicator_evaluations"), [v for v in values if v.get("indicator_id", "").startswith(event)])
    for prefix, item in [("training", training), *[(str(v.get("indicator_id")), v) for v in values], *action_values.items()]:
        checks.expect(prefix + ".score_semantics", item.get("score_semantics"), "measurement_evidence_quality")
        checks.expect(prefix + ".technical_score", item.get("technical_score_0_to_100"), None)
        checks.expect(prefix + ".technical_grade", item.get("technical_grade"), None)
        checks.expect(prefix + ".formal_grade", item.get("formal_grade"), None)
    overall = training.get("score_explanation")
    if isinstance(overall, dict):
        included = [v for v in expected.values() if v["available"]]
        raw = statistics.fmean(v["raw_score"] for v in included) if included else None
        checks.expect("overall_explanation.raw", overall.get("raw_score"), raw)
        checks.expect("overall_explanation.score", overall.get("score_0_to_100"), displayed)
        checks.expect("overall_explanation.rounded_mean", overall.get("rounded_indicator_mean"), rounded_mean)
        adjustment = displayed - raw if raw is not None else None
        checks.expect("overall_explanation.total_rounding", overall.get("rounding_adjustment_points"), adjustment)
        checks.expect("overall_explanation.component_keys", [c.get("key") for c in overall.get("components", [])], list(WEIGHTS))
        if included:
            stages = {"mean_indicator_rounding_points": rounded_mean - raw,
                      "aggregate_rounding_points": displayed - rounded_mean}
            checks.expect("overall_explanation.rounding_stages", overall.get("rounding_stages"), stages)
            deductions = []
            for component in overall.get("components", []):
                key = component.get("key")
                max_points = statistics.fmean(v["effective_component_weights"].get(key, 0) * 100 for v in included)
                earned = statistics.fmean((v["components"].get(str(key) + "_percent") or 0) * v["effective_component_weights"].get(key, 0) for v in included)
                checks.expect(f"overall_explanation.{key}.included", component.get("included"), bool(max_points))
                checks.expect(f"overall_explanation.{key}.effective_weight", component.get("effective_weight"), max_points / 100 if max_points else None)
                checks.expect(f"overall_explanation.{key}.value_percent", component.get("value_percent"), earned / max_points * 100 if max_points else None)
                checks.expect(f"overall_explanation.{key}.max_points", component.get("max_points"), max_points if max_points else None)
                checks.expect(f"overall_explanation.{key}.earned", component.get("earned_points"), earned if max_points else None)
                checks.expect(f"overall_explanation.{key}.deduction", component.get("deduction_points"), max_points - earned if max_points else None)
                deductions.append(max_points - earned)
            checks.expect("overall_explanation.closure", 100 - sum(deductions) + adjustment, float(displayed))
        else:
            for component in overall.get("components", []):
                key = component.get("key")
                checks.expect(f"overall_explanation.{key}.included", component.get("included"), False)
                for name in ("value_percent", "effective_weight", "max_points", "earned_points", "deduction_points"):
                    checks.expect(f"overall_explanation.{key}.{name}", component.get(name), None)
    return {"score_0_to_100": displayed, "available_indicator_count": len(scores),
            "indicator_scores": {k: v["score_0_to_100"] for k, v in expected.items()},
            "rounded_indicator_mean": rounded_mean}


def sha256(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            hashed.update(block)
    return hashed.hexdigest()


def resolve_path(value: str, workspace: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at line {line_number}")
            yield row


def audit_formal(scores: list[dict], records: list[dict], events: list[dict], loop: dict,
                 checks: Checks, job_id: str) -> dict:
    event_keys = {(r.get("video_id"), r.get("person_track_id"), r.get("event_id"), r.get("event_code")) for r in events}
    record_keys = Counter(identity(r) for r in records)
    score_keys = Counter(identity(r) for r in scores)
    checks.expect("formal.one_score_per_indicator_record", dict(score_keys), dict(record_keys))
    emitted = 0
    anomalies = []
    for index, score in enumerate(scores):
        key = (score.get("video_id"), score.get("person_track_id"), score.get("event_id"), score.get("event_code"))
        if key not in event_keys or key[0] != job_id or identity(score) is None:
            anomalies.append({"record": index, "reason": "score_event_identity_mismatch"})
        grade, status = score.get("grade"), score.get("status")
        if grade is None:
            if status not in {"unavailable", "calibration_required"}:
                anomalies.append({"record": index, "reason": "missing_grade_for_status"})
            continue
        emitted += 1
        if not isinstance(grade, str) or grade not in {"A", "B", "C", "D", "E"} or status != "scored":
            anomalies.append({"record": index, "reason": "invalid_grade_or_status"})
        if score.get("feasibility_level") != "F4" or score.get("quality_gate", {}).get("scoring_allowed") is not True:
            anomalies.append({"record": index, "reason": "grade_missing_F4_or_evidence_gate"})
        if not score.get("threshold_version") or not score.get("evidence"):
            anomalies.append({"record": index, "reason": "grade_missing_threshold_or_evidence"})
    checks.expect("formal.record_anomalies", anomalies, [])
    scores_by_key = {identity(row): row for row in scores}
    record_disagreements = sum(
        record.get("grade") != scores_by_key.get(identity(record), {}).get("grade")
        or record.get("scoring_status") != scores_by_key.get(identity(record), {}).get("status")
        for record in records)
    checks.expect("formal.indicator_record_agreement", record_disagreements, 0)
    checks.expect("formal.aggregate_grade", loop.get("result_state", {}).get("grade"), None)
    checks.expect("formal.grade_count", loop.get("grade_counts", {}), dict(Counter(r["grade"] for r in scores if r.get("grade") is not None)))
    if emitted:
        provenance = loop.get("provenance", {})
        checks.expect("formal.calibration_assets_present", bool(provenance.get("calibration_assets")), True)
        checks.expect("formal.runtime_authorization_present", bool(provenance.get("runtime_authorization_files")), True)
        checks.fail("formal.trusted_authorization", "Non-null grades require independent verification of the operator-trusted promotion ledger, F4 registry, artifact hashes, runtime/view binding, and test evidence. Snapshot claims alone are insufficient.")
    return {"record_count": len(scores), "emitted_grade_count": emitted,
            "authorization_status": "requires_trusted_authority_audit" if emitted else "not_required_no_grades_emitted",
            "status_counts": dict(Counter(s.get("status") for s in scores))}


def audit_entry(entry: dict, workspace: Path) -> dict:
    result = {"job_id": entry.get("job_id"), "source_path": entry.get("source_path"),
              "source_sha256": entry.get("source_sha256"), "snapshot_state": entry.get("state")}
    if entry.get("state") not in {"verified", "completed", "succeeded"}:
        failed = entry.get("state") in {"failed", "error", "rejected", "cancelled"}
        return {**result, "status": "failed" if failed else "pending", "checks": [],
                "reason": entry.get("error") or entry.get("failure_reason") or "Entry is not a completed artifact snapshot; no checks counted as passed."}
    checks = Checks()
    result["checks"] = checks.items
    try:
        paths = {}
        artifacts = entry.get("artifacts", {})
        for name in REQUIRED_ARTIFACTS:
            if name not in artifacts:
                checks.fail("artifact." + name, "Completed snapshot is missing required artifact")
        for name, metadata in artifacts.items():
            path = resolve_path(metadata["path"], workspace)
            paths[name] = path
            if not path.is_file():
                checks.fail("artifact." + name, "Artifact file is missing")
                continue
            checks.expect("artifact." + name + ".sha256", sha256(path), str(metadata.get("sha256", "")).lower())
            checks.expect("artifact." + name + ".size", path.stat().st_size, metadata.get("size_bytes"))
        if any(name not in paths or not paths[name].is_file() for name in REQUIRED_ARTIFACTS):
            raise ValueError("Cannot audit an incomplete artifact snapshot")
        summary, demo, loop = [read_json(paths[key]) for key in ("summary", "demo_result", "scoring-loop-summary.json")]
        response = read_json(resolve_path(entry["job_response_path"], workspace))
        checks.expect("job.status", response.get("status"), "succeeded")
        checks.expect("summary.status", summary.get("status"), "completed")
        job_id = entry["job_id"]
        for name, value in {"job.id": response.get("id"), "summary.id": summary.get("job_id"),
                            "demo.id": demo.get("job_id"), "loop.id": loop.get("video_id"),
                            "loop.pipeline_id": loop.get("provenance", {}).get("pipeline_job_id")}.items():
            checks.expect(name, value, job_id)
        source = resolve_path(entry["source_path"], workspace)
        source_hash = sha256(source)
        checks.expect("source.sha256", source_hash, str(entry.get("source_sha256", "")).lower())
        checks.expect("loop.source_sha256", str(loop.get("provenance", {}).get("video_sha256", "")).lower(), source_hash)
        upload = resolve_path(summary["input"]["video_path"], workspace)
        checks.expect("job.upload_sha256", sha256(upload), source_hash)
        checks.expect("summary.loop_snapshot", summary.get("minimum_scoring_loop"), loop)
        if "summary" in response:
            checks.expect("job.summary_snapshot", response["summary"], summary)
        for name, digest in loop.get("artifact_sha256", {}).items():
            if name in paths:
                checks.expect("loop.artifact_hash." + name, sha256(paths[name]), str(digest).lower())
        processing = summary["processing"]
        expected_frames = entry["metadata"].get("frame_count")
        if finite(expected_frames) is None or expected_frames <= 0 or expected_frames != int(expected_frames):
            checks.fail("video.expected_frame_count", "Inventory has no verified positive integral source frame count")
        else:
            expected_frames = int(expected_frames)
            for name, value in {"processed_frames": processing.get("processed_frames"),
                                "source_end_frame_exclusive": processing.get("source_end_frame_exclusive"),
                                "source_frame_count": summary["input"]["video"].get("frame_count")}.items():
                checks.expect("video." + name, value, expected_frames)
        checks.expect("video.frame_stride", processing.get("frame_stride"), 1)
        checks.expect("video.source_start_frame", processing.get("source_start_frame"), 0)
        checks.expect("video.timing_verified", processing.get("source_timing", {}).get("timing_verified"), True)
        frames_path = resolve_path(entry["local_pipeline_artifacts"]["frames.jsonl"], workspace)
        checks.expect("video.frames_sha256", sha256(frames_path), str(loop.get("provenance", {}).get("frames_sha256", "")).lower())
        frame_count = 0
        previous_time = None
        frame_errors = Counter()
        camera_counts = Counter()
        camera_verified = 0
        ankle_counts = {name: Counter() for name in ("left_ankle", "right_ankle")}
        for row in iter_jsonl(frames_path):
            frame = row.get("frame", {})
            if row.get("job_id") != job_id:
                frame_errors["job_id_mismatch"] += 1
            if frame.get("index") != frame_count or frame.get("processed_index") != frame_count:
                frame_errors["missing_duplicate_or_reordered_frame"] += 1
            time = finite(frame.get("timestamp_ms"))
            if time is None or previous_time is not None and time <= previous_time:
                frame_errors["nonincreasing_or_invalid_source_time"] += 1
            if frame.get("timestamp_source") != "decoder_pts":
                frame_errors["unverified_source_timing"] += 1
            previous_time = time
            frame_count += 1
            camera = row.get("camera_motion", {})
            camera_counts[camera.get("status", "unknown")] += 1
            camera_verified += camera.get("compensation_valid") is True
            # One selected subject per source frame; count observed joints, not
            # predictions as anatomical truth or all people as the main player.
            primary_id = summary.get("primary_player", {}).get("primary_player_id")
            pose = next((p for p in row.get("poses", []) if p.get("person_track_id") == primary_id), {})
            for joint in pose.get("keypoints", []):
                if joint.get("name") in ankle_counts:
                    counter = ankle_counts[joint["name"]]
                    counter["observed_keypoint_rows"] += 1
                    counter["explicit_out_of_frame_rows"] += joint.get("in_frame") is False
                    confidence = finite(joint.get("confidence"))
                    counter["confidence_at_least_0_35_and_not_out_of_frame_rows"] += confidence is not None and confidence >= .35 and joint.get("in_frame") is not False
        checks.expect("video.frame_rows", frame_count, processing.get("processed_frames"))
        checks.expect("video.frame_errors", dict(frame_errors), {})
        checks.expect("video.last_timestamp", previous_time, float(processing.get("last_processed_timestamp_ms")))
        records, scores, events = [list(iter_jsonl(paths[key])) for key in ("indicator-features.jsonl", "scores.jsonl", "events.jsonl")]
        checks.expect("records.source_ids", sorted({r.get("video_id") for r in records}), [job_id] if records else [])
        event_errors = sum(e.get("video_id") != job_id or finite(e.get("start_ms")) is None or finite(e.get("end_ms")) is None
                           or e["start_ms"] < 0 or e["end_ms"] < e["start_ms"] or e["end_ms"] > (previous_time or 0) for e in events)
        checks.expect("events.identity_and_interval_errors", event_errors, 0)
        result["training"] = audit_training(demo.get("training_evaluation", {}), records, checks)
        result["formal"] = audit_formal(scores, records, events, loop, checks, job_id)
        checks.expect("formal.demo_grade", demo.get("formal_scoring", {}).get("grade"), None)
        checks.expect("formal.demo_technical_score", demo.get("formal_scoring", {}).get("score_0_to_100"), None)
        checks.expect("formal.demo_available", demo.get("formal_scoring", {}).get("available"), False)
        result["video"] = {"processed_frames": frame_count, "source_frames": expected_frames,
                           "event_count": len(events), "indicator_record_count": len(records),
                           "camera_status_counts": dict(camera_counts), "camera_compensation_declared_valid_frames": camera_verified,
                           "action_recognition_diagnostics": summary.get("action_recognition", {}).get("diagnostics", {}),
                           "ankle_observations_for_primary_track_id": summary.get("primary_player", {}).get("primary_player_id"),
                           "ankle_observation_counts": {key: dict(value) for key, value in ankle_counts.items()},
                           "diagnostic_semantics": "Per-frame evidence counts, not continuous duration or technique quality; low confidence does not prove a joint is outside the image."}
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        checks.fail("snapshot_read_or_contract", f"{type(error).__name__}: {str(error)[:500]}")
    result["status"] = "failed" if any(c["status"] == "failed" for c in checks.items) else "passed"
    result["failure_count"] = sum(c["status"] == "failed" for c in checks.items)
    return result


def audit_source_contract(path: Path, workspace: Path, source_roots: list[Path] | None = None) -> dict:
    """Bind the source audit by hash; do not promote qualitative rules to scores."""
    checks = Checks()
    result: dict = {"contract_path": str(path.resolve()), "checks": checks.items}
    try:
        contract = read_json(path)
        result["contract_sha256"] = sha256(path)
        checks.expect("word.artifact_scope", contract.get("artifact_scope"), "source_reference_audit")
        checks.expect("word.validation", contract.get("validation", {}).get("status"), "passed")
        rules = contract.get("explicit_rules", {})
        for name in ("numeric_score_cutpoints_provided_by_sources", "numeric_weights_provided_by_sources", "deduction_formula_provided_by_sources"):
            checks.expect("word." + name, rules.get(name), False)
        documents = contract.get("source_documents", [])
        checks.expect("word.document_count", len(documents), 7)
        for document in documents:
            source = resolve_path(document["path"], workspace)
            candidates = [source, *[root / Path(document["path"]).name for root in source_roots or []]]
            existing = [candidate for candidate in candidates if candidate.is_file()]
            code = "word.source_hash." + document["source_id"]
            if not existing:
                checks.fail(code, "Source file unavailable; supply its directory with --source-root. No source hash check passed.")
            else:
                expected_hash = str(document["sha256"]).lower()
                checks.expect(code, any(sha256(candidate) == expected_hash for candidate in existing), True)
        indicators = contract.get("indicators", [])
        checks.expect("word.indicator_count", len(indicators), 298)
        by_id = {item.get("indicator_id"): item for item in indicators}
        checks.expect("word.unique_indicator_count", len(by_id), 298)
        result["indicator_alignment"] = []
        for indicator, feature_names in FEATURES.items():
            item = by_id.get(indicator, {})
            implementation = item.get("implementation", {})
            scoring = item.get("scoring_source_summary", {})
            required = implementation.get("scoring_requirements", {}).get("required_feature_names")
            checks.expect(indicator + ".pinned_required_features", required, list(feature_names))
            checks.expect(indicator + ".source_formal_enabled", item.get("runtime_summary", {}).get("formal_scoring_enabled"), False)
            for name in ("numeric_score_range", "numeric_grade_cutpoints", "indicator_weight", "aggregation_formula", "deduction_rule"):
                checks.expect(indicator + ".word." + name, scoring.get(name), None)
            result["indicator_alignment"].append({"indicator_id": indicator,
                "source_id": item.get("source_id"), "source_stable_id": item.get("source_stable_id"),
                "feasibility_level": item.get("runtime_summary", {}).get("current_feasibility_level"),
                "comparison_status": implementation.get("comparison_status"), "issue_ids": item.get("issue_ids", []),
                "measurement_semantics": "partial_engineering_proxy_not_complete_Word_rule_equivalence"})
        result["source_numeric_score_formula"] = "not_provided"
        result["technical_validity"] = "not_established"
    except (OSError, ValueError, TypeError, KeyError) as error:
        checks.fail("word.contract_read", f"{type(error).__name__}: {str(error)[:500]}")
    result["status"] = "failed" if any(c["status"] == "failed" for c in checks.items) else "passed_source_binding_only"
    return result


def audit_manifest(manifest_path: Path, workspace: Path, source_contract: Path | None = None,
                   source_roots: list[Path] | None = None) -> dict:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    entries = [audit_entry(entry, workspace) for entry in manifest["entries"]]
    manifest_checks = Checks()
    if "source_count" in manifest:
        manifest_checks.expect("manifest.source_count", manifest["source_count"], len(entries))
    job_ids = [e.get("job_id") for e in manifest["entries"]]
    manifest_checks.expect("manifest.unique_job_ids", len(set(job_ids)), len(job_ids))
    known_hashes = [e.get("source_sha256") for e in manifest["entries"] if e.get("source_sha256")]
    manifest_checks.expect("manifest.unique_source_hashes", len(set(known_hashes)), len(known_hashes))
    if "source_sha256_to_job_id" in manifest:
        index = manifest["source_sha256_to_job_id"]
        for entry in manifest["entries"]:
            for digest in entry.get("equivalent_source_sha256s", [entry.get("source_sha256")]):
                manifest_checks.expect("manifest.source_job_binding." + str(digest), index.get(digest), entry.get("job_id"))
    counts = dict(Counter(entry["status"] for entry in entries))
    status = "failed" if counts.get("failed") else "pending" if counts.get("pending") or not entries else "passed"
    source_alignment = audit_source_contract(source_contract, workspace, source_roots) if source_contract else {
        "status": "not_checked", "reason": "No source-truth Word contract supplied."}
    if source_alignment["status"] == "failed" or any(c["status"] == "failed" for c in manifest_checks.items):
        status = "failed"
    completed = [entry for entry in entries if entry["status"] != "pending"]
    formal_proven = bool(completed) and all(e["status"] == "passed" and "formal" in e and e["formal"]["emitted_grade_count"] == 0
        and not any(c["status"] == "failed" and c["code"].startswith("formal.") for c in e["checks"]) for e in completed)
    formula_proven = bool(completed) and all(e["status"] == "passed" and "training" in e and not any(c["status"] == "failed"
        and c["code"].startswith(("training.", "FS", "overall_explanation.")) for c in e["checks"]) for e in completed)
    return {"audit_version": AUDIT_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "auditor_python_version": sys.version.split()[0],
            "manifest_path": str(manifest_path.resolve()), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "status": status, "counts": counts, "manifest_checks": manifest_checks.items, "entries": entries,
            "reference_contract": "evidence-score-v1.3.0 arithmetic; 13 pinned indicator feature sets; Python nearest-even rounding",
            "assertions": {"scope": "completed_snapshot_entries_only", "pending_entry_count": counts.get("pending", 0),
                "no_false_formal_grade": "verified" if formal_proven else "not_verified",
                "reference_formula_proven": "verified" if formula_proven else "not_verified",
                "technical_validity": "not_established"},
            "word_source_alignment": source_alignment,
            "limits": ["A passed audit verifies snapshot integrity and evidence-score arithmetic, not recognition accuracy or tennis technique.",
                       "Emitted formal grades fail closed pending independent trusted-authorization verification.",
                       "Pending jobs have not been checked and never contribute a pass."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New report in an ignored/local output directory")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-contract", type=Path, help="Optional Word source-reference contract; hashes original files read-only")
    parser.add_argument("--source-root", type=Path, action="append", default=[],
                        help="Directory containing original Word files; repeat for multiple directories. Never searched recursively.")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.manifest.resolve():
        parser.error("Output must not overwrite the source manifest")
    if args.output.exists():
        parser.error("Output already exists; choose a new report path to preserve the audit trail")
    report = audit_manifest(args.manifest, args.workspace, args.source_contract, args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"status": report["status"], "counts": report["counts"], "output": str(args.output)}, ensure_ascii=False))
    return {"passed": 0, "failed": 1, "pending": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
