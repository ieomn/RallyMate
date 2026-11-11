from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from rallymate_scoring.calibration import GRADES
from rallymate_scoring.blocker_taxonomy import scoring_block_details
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality


def _model_trace_binding_error(trace, identity, frames):
    """Check supplied trace fields against measured geometry, not only peers.

    Older traces may omit model-only fields. Once supplied, a field must agree
    with the validated model windows, including the original frame clock.
    """
    for field, expected in identity.items():
        if field not in trace:
            continue
        value = trace[field]
        if field == "video_sha256":
            valid = isinstance(value, str) and value.lower() == expected.lower()
        else:
            valid = type(value) is int and value == expected
        if not valid:
            return f"model_source_{field}_mismatch"
    source_frames = trace.get("source_frames")
    if "source_frames" in trace and (not isinstance(source_frames, list) or not source_frames
            or any(type(frame) is not int or frame not in frames for frame in source_frames)
            or len(source_frames) != len(set(source_frames))):
        return "model_source_frames_mismatch"
    for field, binding_field in (("source_timestamps_ms", "source_timestamp_ms"),
                                  ("video_timestamps_ms", "video_timestamp_ms")):
        if field in trace:
            values = trace[field]
            if (not isinstance(source_frames, list) or not isinstance(values, list)
                    or len(values) != len(source_frames)
                    or any(type(value) not in (int, float) or value != frames[frame].get(binding_field)
                           for frame, value in zip(source_frames, values))):
                return "model_source_clock_mismatch"
    frame_index = trace.get("frame_index")
    if "frame_index" in trace and (type(frame_index) is not int or frame_index not in frames):
        return "model_source_frames_mismatch"
    clock_fields = ("processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms", "video_timestamp_ms")
    if any(field in trace for field in clock_fields):
        if type(frame_index) is not int or frame_index not in frames:
            return "model_source_clock_mismatch"
        for field in clock_fields:
            allowed_types = (int, float) if field in {"timestamp_ms", "video_timestamp_ms"} else (int,)
            if field in trace and (type(trace[field]) not in allowed_types
                    or trace[field] != frames[frame_index].get(field)):
                return "model_source_clock_mismatch"
    if "source_bindings" in trace:
        bindings = trace["source_bindings"]
        if not isinstance(bindings, list) or not bindings:
            return "model_source_frame_bindings_invalid"
        seen = set()
        for binding in bindings:
            if not isinstance(binding, Mapping) or type(binding.get("frame_index")) is not int:
                return "model_source_frame_bindings_invalid"
            frame = binding["frame_index"]
            if frame in seen or source_frames is not None and frame not in source_frames:
                return "model_source_frame_bindings_invalid"
            seen.add(frame)
            reason = _model_trace_binding_error(binding, identity, frames)
            if reason:
                return reason
    if "source_window" in trace:
        window = trace["source_window"]
        if not isinstance(window, Mapping):
            return "model_source_window_invalid"
        reason = _model_trace_binding_error(window, identity, frames)
        if reason:
            return reason
        window_frames = window.get("source_frames")
        if not isinstance(window_frames, list) or not window_frames:
            return "model_source_window_invalid"
        for field, frame in (("start_ms", window_frames[0]), ("end_ms", window_frames[-1])):
            if field in window and (type(window[field]) not in (int, float)
                    or window[field] != frames[frame].get("video_timestamp_ms")):
                return "model_source_clock_mismatch"
    return None


def _base_result(
    *,
    status: str,
    grade: str | None,
    confidence: float | None,
    feature: dict[str, Any],
    threshold_version: str | None,
    model_versions: dict[str, str | None],
    evidence: list[dict[str, Any]],
    reason_codes: list[str],
    feedback: str,
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "status": status,
        "grade": grade,
        "confidence": None if confidence is None else round(max(0.0, min(1.0, confidence)), 6),
        "feature": feature,
        "threshold_version": threshold_version,
        "model_versions": model_versions,
        "evidence": evidence,
        "reason_codes": reason_codes,
        "feedback": feedback,
        "quality_gate": quality_gate,
    }


def _scoring_block_explanation(flags: list[str]) -> tuple[list[str], str]:
    """Return typed reasons plus feedback for a score-only quality block."""

    reasons, messages = scoring_block_details(flags)
    return reasons, (
        "；".join(messages)
        + "；已有特征保留；缺少所需的身份或阶段证据，当前规则无法计算。"
    )


def score_indicator(
    *, indicator_id: str, features: list[dict[str, Any]],
    calibration: Mapping[str, Any] | None, model_versions: dict[str, str | None],
    evidence: list[dict[str, Any]], allow_test_only: bool = False,
    feasibility_level: str | None = None,
    event_quality_flags: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Evaluate available mathematics; maturity and approval do not grant scores.

    Missing rules remain measured without a grade. Unknown SAM confidence stays
    unknown; source, clock, identity, geometry and required-feature checks remain.
    """
    from rallymate_scoring.scoring_rules import finite_number, validate_scoring_rule
    from rallymate_scoring.model_measurement_contract import (
        has_model_measurement_context, validated_model_window, validated_geometry_mask,
        MODEL_MEASUREMENT_PROVENANCE,
    )
    quality_gate = evaluate_indicator_event_quality(indicator_id, event_quality_flags)
    model_estimate = has_model_measurement_context(*features)
    confidence_values = [item.get("confidence") for item in features]
    confidence = (None if model_estimate or not confidence_values
                  or any(not finite_number(value) or not 0 <= value <= 1 for value in confidence_values)
                  else min(float(value) for value in confidence_values))
    names = [item.get("feature_name") for item in features]
    if any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        raise ValueError("scoring features require unique non-empty feature_name values")
    by_name = {item["feature_name"]: item for item in features}

    def result(status, reasons, feedback, *, grade=None, selected=None, rule_version=None):
        if status == "unavailable" and quality_gate["scoring_block_flags"]:
            block_reasons, block_feedback = _scoring_block_explanation(list(quality_gate["scoring_block_flags"]))
            reasons = list(dict.fromkeys([*reasons, *block_reasons]))
            if block_feedback not in feedback:
                feedback += block_feedback
        payload = _base_result(status=status, grade=grade, confidence=confidence,
            feature=selected if selected is not None else {"items": features},
            threshold_version=rule_version, model_versions={**model_versions,
                "indicator_feasibility_level": feasibility_level,
                **({"calibration": rule_version} if rule_version else {})},
            evidence=evidence, reason_codes=reasons, feedback=feedback, quality_gate=quality_gate)
        payload["assessment_status"] = "graded" if grade else "measurement_only" if status == "calibration_required" else "unavailable"
        payload["grade_rule"] = dict(calibration) if grade is not None else None
        payload["grading_basis"] = "supplied_mathematical_rule" if grade else "no_numeric_grade_generated"
        payload["accuracy_validated"] = False
        if model_estimate:
            payload["measurement_provenance"] = {**MODEL_MEASUREMENT_PROVENANCE,
                "assessment_status": payload["assessment_status"], "scoring_allowed": quality_gate["scoring_allowed"]}
        return payload

    if quality_gate["hard_fail"]:
        return result("unavailable", ["event_quality_hard_fail", *quality_gate["hard_fail_flags"]],
            "所需事件阶段、身份或时序证据缺失；保留已有测量，不生成等级。")
    invalid = [item for item in features if item.get("valid") is not True or not finite_number(item.get("value"))]
    if not features or invalid:
        return result("unavailable", ["required_feature_unavailable"], "所需特征缺失或不是有限数值，不生成等级。")
    # Multiple evidence records must describe the same input. This is input
    # integrity, independent of optional legacy approval or release metadata.
    for field in ("video_id", "video_sha256", "event_id", "person_track_id"):
        values = [trace.get(field) for trace in evidence]
        if not any(value is not None for value in values):
            continue
        if field in {"video_id", "video_sha256"} and any(value is None for value in values):
            return result("unavailable", [f"source_{field}_missing"], "评分证据缺少一致的原视频或事件身份。")
        normalized = [str(value).lower() if field == "video_sha256" else value for value in values if value is not None]
        if any(value != normalized[0] for value in normalized[1:]):
            return result("unavailable", [f"source_{field}_mismatch"], "评分证据混用了不同的原视频、事件或人物。")
    # Legacy wrappers may carry an explicit input-video binding. Its source
    # identity still matters; no authorization flag is read or required.
    binding = getattr(calibration, "authorization", {})
    if isinstance(binding, Mapping):
        for field in ("video_id", "video_sha256"):
            expected = binding.get(f"runtime_view_{field}")
            if expected is None:
                continue
            if not evidence or any(trace.get(field) is None for trace in evidence):
                return result("unavailable", [f"source_{field}_missing"], "已指定输入视频，但评分证据缺少对应来源。")
            expected = str(expected).lower() if field == "video_sha256" else expected
            if any((str(trace[field]).lower() if field == "video_sha256" else trace[field]) != expected for trace in evidence):
                return result("unavailable", [f"source_{field}_mismatch"], "评分证据与显式绑定的输入视频不一致。")
    if model_estimate:
        identity = None
        bound_frames = {}
        for item in features:
            window = validated_model_window(item)
            masks = validated_geometry_mask(item.get("geometry_mask"), window) if window else None
            source_frames = item.get("source_frames")
            if (window is None or masks is None or item.get("confidence") is not None
                    or not any(any(mask) for mask in masks.values())
                    or not isinstance(source_frames, list) or not source_frames
                    or any(type(frame) is not int or frame not in window["source_frames"] for frame in source_frames)):
                return result("unavailable", ["model_source_geometry_binding_invalid"],
                    "模型测量缺少一致的原视频、时间、身份或坐标证据。")
            current_identity = {key: window[key] for key in ("video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch")}
            current_identity["video_sha256"] = current_identity["video_sha256"].lower()
            if identity is not None and identity != current_identity:
                return result("unavailable", ["model_source_identity_mismatch"], "同一指标的特征不属于同一视频、人物或坐标时段。")
            identity = current_identity
            for frame, local_time, source_time, binding in zip(window["source_frames"], window["video_timestamps_ms"],
                                                              window["source_timestamps_ms"], window["source_bindings"]):
                # The exact PTS clock and its rounded display timestamp have
                # different contracts, especially at fractional frame rates.
                bound = {**binding, "video_sha256": binding["video_sha256"].lower(),
                         "video_timestamp_ms": local_time, "source_timestamp_ms": source_time}
                if frame in bound_frames and bound_frames[frame] != bound:
                    return result("unavailable", ["model_source_clock_mismatch"], "同一原视频帧的测量使用了不同的时钟或身份绑定。")
                bound_frames[frame] = bound
        for trace in evidence:
            reason = _model_trace_binding_error(trace, identity, bound_frames)
            if reason:
                return result("unavailable", [reason], "评分证据与测量绑定的原视频、人物、帧或时钟不一致。")
    if not quality_gate["scoring_allowed"]:
        reasons, feedback = _scoring_block_explanation(list(quality_gate["scoring_block_flags"]))
        return result("unavailable", reasons, feedback)
    if calibration is None:
        return result("calibration_required", ["grade_rule_not_defined"],
            "特征已计算；未提供把这些数值映射为 A～E 的数学规则。原文定性描述不自动产生数值切点，不要求先取得人工审批。")
    if calibration.get("indicator_id") != indicator_id:
        raise ValueError("calibration indicator_id does not match scoring request")
    validate_scoring_rule(calibration)
    backend = calibration["backend"]
    if backend == "threshold_rule":
        name = calibration["primary_feature"]
        if name not in by_name:
            raise ValueError(f"calibration requires missing feature {name}")
        item = by_name[name]
        if item.get("unit") != calibration["unit"] or item.get("feature_version") != calibration["primary_feature_version"]:
            raise ValueError("calibration primary_feature_version or unit does not match feature version/unit")
        index = sum(float(item["value"]) >= threshold for threshold in calibration["thresholds"])
        grade = GRADES[index if calibration["direction"] == "higher_is_better" else 4-index]
        return result("scored", ["explicit_threshold_rule"],
            str(calibration.get("feedback_by_grade", {}).get(grade, "按已提供的阈值规则计算；不代表已验证识别准确率。")),
            grade=grade, selected=item, rule_version=calibration["threshold_version"])
    selected = []
    for name in calibration["feature_order"]:
        if name not in by_name:
            raise ValueError(f"ordinal model requires missing feature {name}")
        item = by_name[name]
        if item.get("unit") != calibration["unit_by_feature"][name]:
            raise ValueError(f"ordinal feature unit does not match {name}")
        if item.get("feature_version") != calibration["feature_version_by_feature"][name]:
            raise ValueError(f"ordinal feature version does not match {name}")
        selected.append(item)
    eta = float(calibration.get("intercept", 0.0)) + sum(float(item["value"])*weight
        for item, weight in zip(selected, calibration["coefficients"]))
    if not math.isfinite(eta):
        return result("unavailable", ["ordinal_linear_predictor_nonfinite"], "规则线性组合溢出，无法计算等级。")
    def sigmoid(value):
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exp_value = math.exp(value)
        return exp_value / (1.0 + exp_value)
    cumulative = [sigmoid(cut-eta) for cut in calibration["cutpoints"]]
    probabilities = [cumulative[0], *[b-a for a,b in zip(cumulative, cumulative[1:])], 1-cumulative[-1]]
    grade = GRADES[max(range(5), key=probabilities.__getitem__)]
    return result("scored", ["explicit_ordinal_rule"],
        str(calibration.get("feedback_by_grade", {}).get(grade, "按已提供的有序模型计算；不代表已验证识别准确率。")),
        grade=grade, selected={"items": selected, "grade_probabilities": dict(zip(GRADES, probabilities))},
        rule_version=calibration["model_version"])
