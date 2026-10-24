"""Execute a declared proxy rubric and keep its score separate from source grades."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
import statistics

from .card_kinematics import DYNAMIC_SPECS, SPECS, VERSION as KINEMATICS_VERSION, finite, measure_card_window
from .card_scoring_policy import GRADE_POINTS, apply_scoring_policy
from .gs_recovery_evidence import (recovery_metadata, recovery_auxiliary, compute_gs_recovery_evidence,
    ROTATION_CARDS, STABILITY_CARDS, MANUAL_REQUIREMENTS, ROTATION_CONDITIONS, STABILITY_CONDITIONS)

VERSION = "card-evaluation-v1.0.0"
HAND_RACKET_SCORING_REVISION = "gs01-m09-hand-racket-observability-v1.0.0"
HAND_RACKET_AUXILIARY_VERSION = "gs-hand-racket-auxiliary-v1.0.0"


def hand_racket_observability_metadata(rule):
    """Only the two revised source cards opt into this non-scoring path."""
    evaluation = rule.get("evaluation", {})
    if (rule.get("indicator_id") not in {"GS01-M09-04", "GS01-M09-05"}
            or evaluation.get("scoring_revision_id") != HAND_RACKET_SCORING_REVISION):
        return {}
    return {"scoring_revision_id": HAND_RACKET_SCORING_REVISION,
            "missing_required_observables": list(evaluation["required_observables"])}


def _source_stage_bound(identifier, window):
    binding = window.get("stage_binding", {})
    event_code, part = identifier.split("-")[:2]
    stage = window.get("stage_code")
    if stage == part:
        stage = event_code + "-" + stage
    return (window.get("event_code") == event_code and stage == event_code + "-" + part
        and isinstance(window.get("event_id"), str) and bool(window["event_id"])
        and isinstance(window.get("video_id"), str) and bool(window["video_id"])
        and isinstance(binding, Mapping) and binding.get("method") in {"manual_anchor", "versioned_detector"}
        and isinstance(binding.get("version"), str) and bool(binding["version"]))


def _hand_racket_auxiliary(rule, samples, window, timeline):
    """Recompute the old wrist metric, with no condition or grade conversion."""
    output = {"version": HAND_RACKET_AUXILIARY_VERSION,
        "semantics": "bilateral_wrist_geometry_only_not_hand_racket_approach_or_control",
        "measurement_version": KINEMATICS_VERSION,
        "scoring_eligible": False, "status": "unavailable", "measurements": {}, "reason_codes": []}
    if not _source_stage_bound(rule["indicator_id"], window):
        output["reason_codes"] = ["source_stage_binding_missing_or_mismatched"]
        return output
    measured = measure_card_window(samples, window, timeline,
        required_joint_names=("left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_wrist", "right_wrist"))
    wrist = deepcopy(measured["measurements"]["wrist_separation_torso"])
    output.update(measurement_version=measured["version"], measurements={"wrist_separation_torso": wrist},
                  reason_codes=sorted(set(measured["reason_codes"] + wrist["reason_codes"])))
    if finite(wrist.get("statistics", {}).get("delta")) and wrist["geometry_gate_passed"] and not output["reason_codes"]:
        output["status"] = "measured_auxiliary"
    return output


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


def evaluate_card_window(rule, samples, window, timeline, *, recovery_reference=None):
    """Compute conditions first, then apply the current application policy."""
    samples = list(samples)
    result = _evaluate_card_window(rule, samples, window, timeline, recovery_reference=recovery_reference)
    model_count = sum((sample.get("observation_semantics") if isinstance(sample, Mapping)
                       else getattr(sample, "observation_semantics", None)) == "monocular_model_estimate"
                      for sample in samples)
    if model_count:
        result["measurement_provenance"] = {
            "observation_semantics": "monocular_model_estimate", "model_estimate_sample_count": model_count,
            "detector_sample_count": len(samples) - model_count,
            "confidence_semantics": "not_detector_confidence"}
    advisory = sorted({flag for row in timeline if isinstance(row, Mapping)
                       for flag in row.get("advisory_flags", []) if isinstance(flag, str)})
    if advisory:
        result["advisory_flags"] = advisory
    return apply_scoring_policy(result)


def _evaluate_card_window(rule, samples, window, timeline, *, recovery_reference=None):
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
    if recovery_metadata(rule):
        return _evaluate_recovery_card(rule, samples, window, timeline, result, recovery_reference)
    if evaluation.get("kind") == "manual_review":
        metadata = hand_racket_observability_metadata(rule)
        if metadata:
            result.update(**metadata, status="unavailable", reason_codes=[evaluation["score_block_reason_code"]],
                auxiliary_observations=_hand_racket_auxiliary(rule, samples, window, timeline))
            return result
        result.update(status="review_required", reason_codes=["required_observable_not_recoverable_from_pose"])
        return result
    if not _source_stage_bound(identifier, window):
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



def _evaluate_recovery_card(rule, samples, window, timeline, result, reference):
    identifier, evaluation = rule["indicator_id"], rule["evaluation"]
    result.update(**recovery_metadata(rule))
    if not _source_stage_bound(identifier, window):
        result["reason_codes"] = ["source_stage_binding_missing_or_mismatched"]
        return result
    result["auxiliary_observations"] = recovery_auxiliary(samples, window, timeline)
    if identifier in MANUAL_REQUIREMENTS:
        reason, observables = MANUAL_REQUIREMENTS[identifier]
        if (evaluation.get("kind") != "manual_review" or evaluation.get("conditions") != []
                or evaluation.get("score_block_reason_code") != reason
                or evaluation.get("required_observables") != list(observables)):
            result["reason_codes"] = ["recovery_card_contract_mismatch"]
        else:
            result["reason_codes"] = [reason]
        return result
    kind = "rotation" if identifier in ROTATION_CARDS else "stability"
    evidence = compute_gs_recovery_evidence(samples, window, timeline, kind=kind, reference=reference)
    result.update(recovery_evidence=evidence, measurement_version=evidence["version"])
    conditions = evaluation.get("conditions", [])
    expected = ROTATION_CONDITIONS if kind == "rotation" else STABILITY_CONDITIONS
    actual = [(c.get("metric"), c.get("operator"), c.get("value"), c.get("unit")) for c in conditions if isinstance(c, Mapping)]
    if (evaluation.get("kind") != "visual_proxy" or actual != list(expected)
            or any(c.get("required") is not True for c in conditions)
            or evaluation.get("grade_policy") != "condition_completion_v1"
            or evaluation.get("grade_mapping") != GRADE_POINTS):
        result["reason_codes"] = ["recovery_card_contract_mismatch"]
        return result
    result["conditions"] = [_condition(c, evidence["measurements"]) for c in conditions]
    result["reason_codes"] = list(evidence["reason_codes"])
    proof = evidence["stage"]
    result["quality"] = {"required_joint_names": proof["required_joint_names"],
        "valid_frame_count": proof["valid_frame_count"], "total_frame_count": proof["total_frame_count"],
        "geometry_gate_passed": evidence["status"] == "measured", "minimum_fraction": 1.0,
        "visibility_verified": False, "image_bounds_verified": False,
        "denominator": "all_decoder_samples_in_bound_stage"}
    if evidence["status"] != "measured" or any(c["passed"] is None for c in result["conditions"]):
        result["reason_codes"] = sorted(set(result["reason_codes"] + ["required_condition_evidence_missing"]))
        return result
    fraction = sum(c["passed"] for c in result["conditions"])/len(conditions)
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction > 0 else "E"
    result.update(status="proxy_scored", proxy_grade=grade, score=GRADE_POINTS[grade],
        condition_completion_fraction=fraction, semantics="engineering_visual_condition_completion_not_original_technical_grade")
    return result


def summarize_card_scores(results, applicable_card_ids):
    """Equal card weights; average repeated events within each card first.

    This is aggregation of internal evaluator results, not proof of recognition
    accuracy. Unobserved applicable cards stay in coverage and out of score means.
    The current application channel is averaged separately from historical
    original, proxy and manual channels; only it supplies the current total.
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
    from .card_scoring_policy import SCORING_POLICY_VERSION, FORMAL_SCORE_SEMANTICS
    has_formal_policy = any(row.get("scoring_policy_version") == SCORING_POLICY_VERSION for row in unique.values())
    channels = {}
    channel_specs = [("original", "source_scored", "original_grade"),
                     ("visual_proxy", "proxy_scored", "proxy_grade"),
                     ("manual", "manual_scored", "manual_grade")]
    if has_formal_policy:
        channel_specs.append(("formal", "scored", "grade"))
    for channel, status, grade_field in channel_specs:
        by_card = defaultdict(list)
        for key, row in unique.items():
            if key in conflicts or row.get("status") != status:
                continue
            if channel == "formal" and (row.get("scoring_policy_version") != SCORING_POLICY_VERSION
                    or row.get("semantics") != FORMAL_SCORE_SEMANTICS):
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
    result = {"version": VERSION, "grade_points": dict(GRADE_POINTS), "channels": channels,
            "status_counts": dict(Counter(row.get("status", "unavailable") for row in unique.values())),
            "conflicting_event_instances": len(conflicts),
            "overall_score": channels.get("formal", {}).get("score"),
            "missing_policy": "exclude_from_score_keep_in_applicable_coverage",
            "weight_policy": "equal_card_weight_after_event_mean",
            "uncertainty": "engineering_proxy_thresholds_uncalibrated_no_accuracy_claim"}
    if has_formal_policy:
        result.update(scoring_policy_version=SCORING_POLICY_VERSION,
                      formal_scored_cards=channels["formal"]["scored_cards"],
                      formal_scored_instances=channels["formal"]["scored_event_instances"])
    return result
