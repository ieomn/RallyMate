"""Execute a declared proxy rubric and keep its score separate from source grades."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import statistics

from .card_kinematics import DYNAMIC_SPECS, SPECS, finite, measure_card_window

VERSION = "card-evaluation-v1.0.0"
GRADE_POINTS = {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20}


def _condition(condition, measurements):
    metric = condition.get("metric", "")
    name, separator, reducer = metric.rpartition(".")
    if metric in measurements:
        name, reducer = metric, "value"
    measurement = measurements.get(name, {})
    value = measurement.get("statistics", {}).get(reducer)
    result = {"key": condition.get("key"), "metric": metric, "value": value,
              "operator": condition.get("operator"), "passed": None,
              "threshold": condition.get("bounds", condition.get("value")),
              "unit": condition.get("unit"), "evidence": measurement}
    if not finite(value) or not measurement.get("geometry_gate_passed"):
        result["reason"] = "metric_not_computed_or_incomplete"
        return result
    if measurement.get("unit") != condition.get("unit"):
        result["reason"] = "metric_unit_mismatch"
        return result
    operator, threshold = condition.get("operator"), condition.get("value")
    if operator in {"gte", "lte", "eq"} and finite(threshold):
        result["passed"] = {"gte": value >= threshold, "lte": value <= threshold, "eq": value == threshold}[operator]
    elif operator == "between":
        bounds = condition.get("bounds")
        if isinstance(bounds, list) and len(bounds) == 2 and all(finite(v) for v in bounds) and bounds[0] <= bounds[1]:
            result["passed"] = bounds[0] <= value <= bounds[1]
    if result["passed"] is None:
        result["reason"] = "invalid_condition_math"
    return result


def evaluate_card_window(rule, samples, window, timeline):
    """Evaluate only registered frame measurements, never caller-provided pass bits.

    A source stage must have a documented manual anchor or a versioned detector.
    An estimated three-phase swing does not supply ten source-stage boundaries.
    """
    identifier, evaluation = rule["indicator_id"], rule["evaluation"]
    result = {"version": VERSION, "indicator_id": identifier, "rule_version": rule.get("rule_version"),
              "event_id": window.get("event_id"), "video_id": window.get("video_id"),
              "person_track_id": window.get("person_track_id"), "window": dict(window),
              "status": "unavailable", "original_grade": None, "proxy_grade": None, "score": None,
              "reason_codes": [], "conditions": [], "source_grade_supported": False,
              "threshold_basis": evaluation.get("numeric_basis", "engineering_v1_uncalibrated")}
    if evaluation.get("kind") == "manual_review":
        result.update(status="review_required", reason_codes=["required_observable_not_recoverable_from_pose"])
        return result
    binding = window.get("stage_binding", {})
    event_code, part = identifier.split("-")[:2]
    canonical_stage = event_code + "-" + part
    supplied_stage = window.get("stage_code")
    if supplied_stage == part:
        supplied_stage = event_code + "-" + supplied_stage
    if (window.get("event_code") != identifier.split("-")[0]
            or supplied_stage != canonical_stage
            or not isinstance(window.get("event_id"), str) or not window["event_id"]
            or not isinstance(window.get("video_id"), str) or not window["video_id"]
            or not isinstance(binding, Mapping) or binding.get("method") not in {"manual_anchor", "versioned_detector"}
            or not isinstance(binding.get("version"), str) or not binding["version"]):
        result["reason_codes"] = ["source_stage_binding_missing_or_mismatched"]
        return result
    conditions = evaluation.get("conditions", [])
    required = set(rule.get("required_joint_names", []))
    for condition in conditions if isinstance(conditions, list) else []:
        if not isinstance(condition, Mapping):
            continue
        required.update(condition.get("required_joint_names", []))
        metric = condition.get("metric", "")
        spec = DYNAMIC_SPECS.get(metric) or SPECS.get(metric.rpartition(".")[0])
        if spec:
            required.update(spec[0])
    measured = measure_card_window(samples, window, timeline, required_joint_names=required)
    result["measurement_version"] = measured["version"]
    result["reason_codes"].extend(measured["reason_codes"])
    if (evaluation.get("kind") != "visual_proxy" or not isinstance(conditions, list) or not conditions
            or any(not isinstance(c, Mapping) or c.get("required") is not True for c in conditions)):
        result["reason_codes"].append("executable_proxy_contract_missing")
        return result
    result["conditions"] = [_condition(c, measured["measurements"]) for c in conditions]
    for c in conditions:
        required.update(c.get("required_joint_names", []))
        metric = c.get("metric", "")
        metric_name = metric if metric in measured["measurements"] else metric.rpartition(".")[0]
        required.update(measured["measurements"].get(metric_name, {}).get("required_joint_names", []))
    jointly_valid = set(r["frame_index"] for r in timeline)
    for joint in required:
        jointly_valid.intersection_update(measured["joint_valid_frames"].get(joint, []))
    passes = bool(required) and len(timeline) >= 3 and len(jointly_valid)*10 >= len(timeline)*7
    result["quality"] = {"required_joint_names": sorted(required), "valid_frame_count": len(jointly_valid),
                         "total_frame_count": len(timeline), "geometry_gate_passed": passes,
                         "visibility_verified": False, "minimum_fraction": .7,
                         "image_bounds_verified": False,
                         "denominator": "all_decoder_samples_in_bound_stage"}
    if not passes:
        result["reason_codes"].append("joint_union_geometry_below_70_percent")
    if any(c["passed"] is None for c in result["conditions"]) or result["reason_codes"]:
        result["reason_codes"].append("required_condition_evidence_missing")
        return result
    if (evaluation.get("grade_policy") != "condition_completion_v1"
            or evaluation.get("grade_mapping") != GRADE_POINTS):
        result["reason_codes"].append("unsupported_grade_mapping")
        return result
    fraction = sum(c["passed"] for c in result["conditions"])/len(conditions)
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    result.update(status="proxy_scored", proxy_grade=grade, score=GRADE_POINTS[grade],
                  condition_completion_fraction=fraction,
                  semantics="engineering_visual_condition_completion_not_original_technical_grade")
    return result


def summarize_card_scores(results, applicable_card_ids):
    """Equal card weights; average repeated events within each card first.

    This is aggregation of internal evaluator results, not proof of recognition
    accuracy. Unobserved applicable cards stay in coverage and out of score means.
    No combined number merges original, proxy and manual judgments.
    """
    applicable = set(applicable_card_ids)
    if len(applicable) != len(applicable_card_ids):
        raise ValueError("applicable card identities must be unique")
    unique, conflicts = {}, set()
    for row in results:
        if row.get("indicator_id") not in applicable:
            raise ValueError("result outside applicable card scope")
        key = (row["indicator_id"], row.get("video_id"), row.get("event_id"), row.get("person_track_id"))
        if key in unique and unique[key] != row:
            conflicts.add(key)
        unique[key] = row
    channels = {}
    for channel, status, grade_field in (("original", "source_scored", "original_grade"),
                                         ("visual_proxy", "proxy_scored", "proxy_grade"),
                                         ("manual", "manual_scored", "manual_grade")):
        by_card = defaultdict(list)
        for key, row in unique.items():
            if key in conflicts or row.get("status") != status:
                continue
            grade, score = row.get(grade_field), row.get("score")
            if grade not in GRADE_POINTS or not finite(score) or GRADE_POINTS[grade] != score:
                continue
            if not isinstance(row.get("event_id"), str) or not row["event_id"]:
                continue
            by_card[row["indicator_id"]].append(score)
        scores = {card: statistics.fmean(values) for card, values in sorted(by_card.items())}
        channels[channel] = {"score": statistics.fmean(scores.values()) if scores else None,
                             "scored_cards": len(scores), "applicable_cards": len(applicable),
                             "coverage": len(scores)/len(applicable) if applicable else None,
                             "scored_event_instances": sum(map(len, by_card.values())), "per_card": scores}
    return {"version": VERSION, "grade_points": dict(GRADE_POINTS), "channels": channels,
            "status_counts": dict(Counter(row.get("status", "unavailable") for row in unique.values())),
            "conflicting_event_instances": len(conflicts), "overall_score": None,
            "missing_policy": "exclude_from_score_keep_in_applicable_coverage",
            "weight_policy": "equal_card_weight_after_event_mean",
            "uncertainty": "engineering_proxy_thresholds_uncalibrated_no_accuracy_claim"}
