from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping
from importlib.resources import files
from typing import Any

from rallymate_service.score_explanation import (
    explain_aggregate_score, explain_evidence_gaps, explain_indicator_score,
)


TRAINING_EVALUATION_VERSION = "rallymate-training-evaluation-beta-v1.3.0"
SCORE_SEMANTICS = "measurement_evidence_quality"

_SCORE_WEIGHTS = {
    "measured_instance_ratio": 0.30,
    "required_feature_coverage": 0.20,
    "median_feature_confidence": 0.15,
    "repeatability": 0.25,
    "scoring_evidence_ratio": 0.10,
}


def _technical_score_contract() -> dict[str, Any]:
    # This evaluator never consumes a coach-calibrated scoring artifact.
    return {
        "score_semantics": SCORE_SEMANTICS,
        "technical_score_0_to_100": None,
        "technical_score_status": "calibration_required",
        "technical_grade": None,
    }


def _reference_score(
    components: Mapping[str, float | None], *, available: bool,
) -> tuple[int | None, dict[str, float]]:
    """Summarize measurement evidence, never technical correctness.

    Missing repeatability/confidence cannot be imputed as a middling score.
    Expose the effective weights so a single observation's reference score is
    not mistaken for one that includes independently repeated observations.
    """
    if not available:
        return None, {}
    weights = {
        name: weight for name, weight in _SCORE_WEIGHTS.items()
        if _finite(components.get(f"{name}_percent")) is not None
    }
    total_weight = sum(weights.values())
    if not total_weight:
        return None, {}
    effective = {name: weight / total_weight for name, weight in weights.items()}
    score = round(sum(
        float(components[f"{name}_percent"]) * weight
        for name, weight in effective.items()
    ))
    return max(0, min(100, score)), effective


def _reference_level(score: int | None) -> str:
    if score is None:
        return "测量证据不足"
    if score >= 85:
        return "测量证据较完整"
    if score >= 60:
        return "测量证据部分可用"
    return "测量证据有限"

_INDICATOR_FEATURES: dict[str, tuple[str, tuple[str, ...]]] = {
    "FS01-M02": (
        "FS01",
        (
            "hip_center_y_body",
            "left_knee_flexion_deg",
            "right_knee_flexion_deg",
            "stance_width_body",
            "hip_center_relative_to_ankle_support",
        ),
    ),
    "FS01-M03": (
        "FS01",
        (
            "bilateral_foot_rise_min_body",
            "bilateral_foot_rise_synchrony_ms",
            "bilateral_foot_rise_proxy_duration_ms",
            "hip_center_vertical_velocity_body_s",
        ),
    ),
    "FS01-M04": (
        "FS01",
        (
            "bilateral_foot_vertical_slowdown_time_offset_ms",
            "post_slowdown_stance_width_body",
            "hip_center_lateral_variability_body",
        ),
    ),
    "FS01-M05": (
        "FS01",
        (
            "body_center_speed_body_s",
            "hip_center_relative_to_ankle_support",
            "torso_lean_deg",
            "shoulder_hip_angular_velocity",
        ),
    ),
    "FS02-M02": (
        "FS02",
        (
            "body_center_speed_body_s",
            "hip_center_relative_to_ankle_support",
            "torso_lean_deg",
            "launch_direction_deg",
            "target_direction_alignment_error_deg",
        ),
    ),
    "FS02-M03": (
        "FS02",
        (
            "launch_direction_deg",
            "drive_side_code",
            "support_knee_extension_velocity_deg_s",
            "hip_acceleration_along_launch_direction_body_s2",
            "support_drive_to_moving_foot_rise_proxy_ms",
        ),
    ),
    "FS02-M04": (
        "FS02",
        (
            "launch_direction_deg",
            "launch_side_code",
            "launch_foot_speed_peak_body_s",
            "launch_foot_relative_displacement_body",
            "launch_foot_motion_duration_ms",
        ),
    ),
    "FS02-M05": (
        "FS02",
        (
            "launch_side_code",
            "launch_foot_speed_drop_body_s",
            "first_step_displacement_body",
            "post_step_hip_direction_consistency",
            "launch_foot_slowdown_to_post_hip_direction_ms",
            "post_step_stance_width_body",
        ),
    ),
    "FS09-M01": (
        "FS09",
        (
            "hip_center_speed_body_s",
            "hip_center_motion_direction_deg",
            "hip_center_relative_to_ankle_midpoint_x_body",
            "hip_center_relative_to_ankle_midpoint_y_body",
            "hip_center_speed_trend_body_s2",
        ),
    ),
    "FS09-M02": (
        "FS09",
        (
            "left_ankle_speed_body_s",
            "right_ankle_speed_body_s",
            "left_ankle_speed_drop_body_s",
            "right_ankle_speed_drop_body_s",
            "braking_side_code",
            "braking_ankle_speed_drop_body_s",
            "left_knee_flexion_change_deg",
            "right_knee_flexion_change_deg",
            "hip_center_deceleration_body_s2",
            "braking_ankle_slowdown_to_hip_deceleration_ms",
        ),
    ),
    "FS09-M03": (
        "FS09",
        (
            "hip_center_deceleration_body_s2",
            "hip_center_speed_drop_body_s",
            "left_knee_flexion_change_deg",
            "right_knee_flexion_change_deg",
            "hip_height_delta_body",
            "torso_lean_variability_deg",
        ),
    ),
    "FS09-M04": (
        "FS09",
        (
            "hip_center_relative_to_ankle_support",
            "hip_center_relative_to_ankle_midpoint_x_body",
            "hip_center_relative_to_ankle_midpoint_y_body",
            "hip_center_speed_drop_body_s",
            "stance_width_body",
            "stability_duration_ms",
            "shoulder_hip_angular_velocity_change_deg_s",
            "double_support_proxy_duration_ms",
        ),
    ),
    "FS09-M05": (
        "FS09",
        (
            "stability_duration_ms",
            "hip_center_speed_drop_body_s",
            "double_support_proxy_duration_ms",
            "torso_lean_variability_deg",
            "shoulder_hip_angular_velocity_change_deg_s",
            "hip_deceleration_to_double_support_proxy_ms",
        ),
    ),
}

_REPRESENTATIVE_FEATURES: dict[str, tuple[str, ...]] = {
    "FS01-M02": ("left_knee_flexion_deg", "right_knee_flexion_deg", "stance_width_body"),
    "FS01-M03": ("bilateral_foot_rise_min_body", "bilateral_foot_rise_synchrony_ms", "bilateral_foot_rise_proxy_duration_ms"),
    "FS01-M04": ("bilateral_foot_vertical_slowdown_time_offset_ms", "post_slowdown_stance_width_body", "hip_center_lateral_variability_body"),
    "FS01-M05": ("body_center_speed_body_s", "torso_lean_deg", "shoulder_hip_angular_velocity"),
    "FS02-M02": ("body_center_speed_body_s", "launch_direction_deg", "target_direction_alignment_error_deg"),
    "FS02-M03": ("support_knee_extension_velocity_deg_s", "hip_acceleration_along_launch_direction_body_s2", "support_drive_to_moving_foot_rise_proxy_ms"),
    "FS02-M04": ("launch_foot_speed_peak_body_s", "launch_foot_relative_displacement_body", "launch_foot_motion_duration_ms"),
    "FS02-M05": ("first_step_displacement_body", "post_step_hip_direction_consistency", "launch_foot_slowdown_to_post_hip_direction_ms"),
    "FS09-M01": ("hip_center_speed_body_s", "hip_center_motion_direction_deg", "hip_center_speed_trend_body_s2"),
    "FS09-M02": ("braking_ankle_speed_drop_body_s", "hip_center_deceleration_body_s2", "braking_ankle_slowdown_to_hip_deceleration_ms"),
    "FS09-M03": ("hip_center_speed_drop_body_s", "left_knee_flexion_change_deg", "right_knee_flexion_change_deg"),
    "FS09-M04": ("stability_duration_ms", "hip_center_speed_drop_body_s", "double_support_proxy_duration_ms"),
    "FS09-M05": ("stability_duration_ms", "double_support_proxy_duration_ms", "torso_lean_variability_deg"),
}

_FEATURE_LABELS = {
    "left_knee_flexion_deg": "左膝屈曲",
    "right_knee_flexion_deg": "右膝屈曲",
    "stance_width_body": "双脚支撑宽度",
    "bilateral_foot_rise_min_body": "双脚上移幅度",
    "bilateral_foot_rise_synchrony_ms": "双脚上移时差",
    "bilateral_foot_rise_proxy_duration_ms": "双脚同步上移持续时间",
    "bilateral_foot_vertical_slowdown_time_offset_ms": "双脚减速时差",
    "post_slowdown_stance_width_body": "减速后支撑宽度",
    "hip_center_lateral_variability_body": "身体中心横向波动",
    "body_center_speed_body_s": "身体中心峰值速度",
    "torso_lean_deg": "躯干倾斜",
    "shoulder_hip_angular_velocity": "肩髋转动速度",
    "launch_direction_deg": "启动方向角",
    "target_direction_alignment_error_deg": "目标方向偏差",
    "support_knee_extension_velocity_deg_s": "支撑膝伸展速度",
    "hip_acceleration_along_launch_direction_body_s2": "身体启动加速度",
    "support_drive_to_moving_foot_rise_proxy_ms": "蹬伸到启动脚动作时差",
    "launch_foot_speed_peak_body_s": "启动脚峰值速度",
    "launch_foot_relative_displacement_body": "启动脚相对位移",
    "launch_foot_motion_duration_ms": "启动脚动作持续时间",
    "first_step_displacement_body": "第一步位移",
    "post_step_hip_direction_consistency": "第一步后方向一致性",
    "launch_foot_slowdown_to_post_hip_direction_ms": "落脚到方向稳定时差",
    "hip_center_speed_body_s": "身体中心速度",
    "hip_center_motion_direction_deg": "身体惯性方向角",
    "hip_center_speed_trend_body_s2": "身体速度变化趋势",
    "braking_ankle_speed_drop_body_s": "制动脚速度下降",
    "hip_center_deceleration_body_s2": "身体中心减速度",
    "braking_ankle_slowdown_to_hip_deceleration_ms": "制动脚到身体减速时差",
    "hip_center_speed_drop_body_s": "身体中心速度下降",
    "left_knee_flexion_change_deg": "左膝屈曲变化",
    "right_knee_flexion_change_deg": "右膝屈曲变化",
    "stability_duration_ms": "稳定维持时间",
    "double_support_proxy_duration_ms": "双脚支撑代理持续时间",
    "torso_lean_variability_deg": "躯干倾斜波动",
}

_PROXY_LIMITS = {
    "FS01-M03": "脚部上移来自普通视频运动代理，不等同真实离地或触地判定。",
    "FS01-M04": "双脚减速来自普通视频运动代理，不等同精确落地接触时刻。",
    "FS02-M03": "视觉运动只能描述蹬伸表现，不能测量真实蹬地力。",
    "FS02-M04": "脚部启动来自运动代理，不等同精确离地时刻。",
    "FS02-M05": "第一步减速来自运动代理，不等同精确落地接触时刻。",
    "FS09-M02": "制动脚与落地来自运动代理，不能测量真实冲击力或接触力。",
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _percent(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return max(0, min(100, round(numerator / denominator * 100)))


def _is_measured(record: Mapping[str, Any]) -> bool:
    status = record.get("feature_status")
    if status is None:
        status = record.get("scoring_feature_status")
    gate = record.get("quality_gate")
    return status == "measured" and not (
        isinstance(gate, Mapping) and gate.get("measurement_allowed") is False
    )


def _event_identity(record: Mapping[str, Any]) -> tuple[str, int, str, str] | None:
    video_id = record.get("video_id")
    person_id = record.get("person_track_id")
    event_id = record.get("event_id")
    indicator_id = record.get("indicator_id")
    if (
        isinstance(video_id, str) and video_id.strip()
        and isinstance(person_id, int) and not isinstance(person_id, bool)
        and isinstance(event_id, str) and event_id.strip()
        and isinstance(indicator_id, str) and indicator_id in _INDICATOR_FEATURES
    ):
        return video_id, person_id, event_id, indicator_id
    return None


def _valid_feature_map(record: Mapping[str, Any]) -> dict[str, tuple[float, float | None, str | None]]:
    raw_features = record.get("features")
    scoring_features = record.get("scoring_features")
    # FS02 direction alignment is an independently sourced scoring-context
    # feature. It is absent from the pose measurement list, even when supplied.
    # Keep measurement values authoritative and add only scoring-only names.
    features = [
        *(raw_features if isinstance(raw_features, list) else []),
        *(scoring_features if isinstance(scoring_features, list) else []),
    ]
    result: dict[str, tuple[float, float | None, str | None]] = {}
    seen: set[str] = set()
    for feature in features:
        if not isinstance(feature, Mapping):
            continue
        name = feature.get("feature_name")
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        value = _finite(feature.get("value"))
        if value is None or feature.get("valid") is not True:
            continue
        result[name] = (
            value,
            _finite(feature.get("confidence")),
            feature.get("unit") if isinstance(feature.get("unit"), str) else None,
        )
    return result


def _load_metric_cards() -> dict[str, Mapping[str, Any]]:
    resource = files("rallymate_scoring").joinpath("data/metric_cards.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    cards = payload.get("cards") if isinstance(payload, Mapping) else None
    if not isinstance(cards, list):
        return {}
    return {
        str(card["id"]): card
        for card in cards
        if isinstance(card, Mapping) and card.get("id") in _INDICATOR_FEATURES
    }


def _repeatability_for_feature(name: str, values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    if name.endswith("_code"):
        counts: dict[float, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts.values()) / len(values) * 100
    if name.endswith("_direction_deg") or name in {
        "launch_direction_deg",
        "hip_center_motion_direction_deg",
    }:
        sine = statistics.fmean(math.sin(math.radians(value)) for value in values)
        cosine = statistics.fmean(math.cos(math.radians(value)) for value in values)
        return max(0.0, min(100.0, math.hypot(sine, cosine) * 100))
    ordered = sorted(values)
    quartiles = statistics.quantiles(ordered, n=4, method="inclusive")
    q1, q3 = quartiles[0], quartiles[2]
    center = statistics.median(ordered)
    scale = max(abs(center), abs(q1), abs(q3), 0.05)
    dispersion = min(1.0, abs(q3 - q1) / (2.0 * scale))
    return (1.0 - dispersion) * 100


def _display_value(value: float, unit: str | None) -> tuple[float, str]:
    if unit == "body":
        return round(value * 100, 1), "% 身体尺度"
    unit_zh = {
        "body/s": "身体尺度/秒",
        "body/s2": "身体尺度/秒²",
        "deg": "度",
        "deg/s": "度/秒",
        "ms": "毫秒",
        "ratio": "比例",
    }.get(unit, unit or "")
    return round(value, 1), unit_zh


def _friendly_limitations(indicator_id: str, records: list[Mapping[str, Any]]) -> list[str]:
    tokens: set[str] = set()
    for record in records:
        for key in ("reason_codes",):
            values = record.get(key)
            if isinstance(values, list):
                tokens.update(str(value).casefold() for value in values if isinstance(value, str))
        gate = record.get("quality_gate")
        if isinstance(gate, Mapping):
            for key in ("hard_fail_flags", "scoring_block_flags", "advisory_flags"):
                values = gate.get(key)
                if isinstance(values, list):
                    tokens.update(str(value).casefold() for value in values if isinstance(value, str))
    messages: list[str] = []
    joined = " ".join(sorted(tokens))
    if "target_direction" in joined:
        messages.append("已测到身体移动方向，但没有可靠的目标或来球方向，因此不判断方向是否正确。")
    if "jump" in joined:
        messages.append("部分片段的骨架连续性需要复核，不能据此判断技术优劣。")
    if "swap" in joined or "side" in joined and "unverified" in joined:
        messages.append("部分片段的左右侧识别不够稳定，相关测量需要复核。")
    if "coverage" in joined or "track" in joined and "low" in joined:
        messages.append("部分片段的球员轨迹或身体关键点覆盖不足。")
    if "phase" in joined or "event_boundary" in joined:
        messages.append("部分动作阶段边界不够清楚，时序结论需谨慎理解。")
    proxy = _PROXY_LIMITS.get(indicator_id)
    if proxy:
        messages.append(proxy)
    return messages[:3]


def _representative_measurements(
    indicator_id: str,
    values_by_name: Mapping[str, list[tuple[float, str | None]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in _REPRESENTATIVE_FEATURES[indicator_id]:
        entries = values_by_name.get(name, [])
        if not entries:
            continue
        values = [value for value, _ in entries]
        unit = next((unit for _, unit in entries if unit), None)
        angular = name.endswith("_direction_deg")
        if angular:
            sine = statistics.fmean(math.sin(math.radians(value)) for value in values)
            cosine = statistics.fmean(math.cos(math.radians(value)) for value in values)
            # Opposing directions have no unique typical direction. Linear
            # medians would also turn 179/-179 degrees into the opposite way.
            if math.hypot(sine, cosine) <= 1e-9:
                continue
            center = math.degrees(math.atan2(sine, cosine))
        else:
            center = statistics.median(values)
        median_value, unit_zh = _display_value(center, unit)
        item: dict[str, Any] = {
            "feature_name": name,
            "label_zh": _FEATURE_LABELS.get(name, name),
            "median_value": median_value,
            "unit_zh": unit_zh,
            "sample_count": len(values),
            "aggregation": "circular_mean" if angular else "median",
        }
        if len(values) >= 2 and not angular:
            quartiles = statistics.quantiles(sorted(values), n=4, method="inclusive")
            low, _ = _display_value(quartiles[0], unit)
            high, _ = _display_value(quartiles[2], unit)
            item["typical_range"] = [low, high]
        result.append(item)
    return result[:3]


def _indicator_evaluation(
    indicator_id: str,
    records: list[Mapping[str, Any]],
    card: Mapping[str, Any],
) -> dict[str, Any]:
    event_code, required_features = _INDICATOR_FEATURES[indicator_id]
    total = len(records)
    measured_records = [record for record in records if _is_measured(record)]
    values_by_name: dict[str, list[tuple[float, str | None]]] = {
        name: [] for name in required_features
    }
    independent_values: dict[str, list[float]] = {name: [] for name in required_features}
    confidences: list[float] = []
    valid_slots = 0
    scoring_allowed = 0
    evidence_observations = []
    for record in records:
        gate = record.get("quality_gate")
        feature_map = _valid_feature_map(record)
        evidence_observations.append({"measured": _is_measured(record),
                                      "valid_features": tuple(feature_map), "quality_gate": gate,
                                      "event_id": record.get("event_id") if _event_identity(record) is not None else None})
        if not _is_measured(record):
            continue
        if (
            isinstance(gate, Mapping)
            and gate.get("scoring_allowed") is True
            and all(name in feature_map for name in required_features)
        ):
            scoring_allowed += 1
        for name in required_features:
            feature = feature_map.get(name)
            if feature is None:
                continue
            value, confidence, unit = feature
            values_by_name[name].append((value, unit))
            if _event_identity(record) is not None:
                independent_values[name].append(value)
            valid_slots += 1
            if confidence is not None:
                confidences.append(confidence)

    measured_ratio = _percent(len(measured_records), total)
    feature_coverage = _percent(valid_slots, total * len(required_features))
    median_confidence = (
        max(0, min(100, round(statistics.median(confidences) * 100)))
        if confidences else None
    )
    feature_repeatabilities = [
        value
        for name, entries in independent_values.items()
        if (value := _repeatability_for_feature(name, entries))
        is not None
    ]
    repeatability = (
        round(statistics.median(feature_repeatabilities))
        if feature_repeatabilities
        else None
    )
    repeatability_sample_count = max((len(values) for values in independent_values.values()), default=0)
    unidentified_count = sum(_event_identity(record) is None for record in measured_records)
    repeatability_status = (
        "available" if repeatability is not None
        else "independent_event_identity_required" if unidentified_count
        else "insufficient_samples"
    )
    evidence_ratio = _percent(scoring_allowed, total)
    components = {
        "measured_instance_ratio_percent": measured_ratio,
        "required_feature_coverage_percent": feature_coverage,
        "median_feature_confidence_percent": median_confidence,
        "repeatability_percent": repeatability,
        "scoring_evidence_ratio_percent": evidence_ratio,
    }
    available = bool(measured_records and valid_slots)
    score, effective_weights = _reference_score(components, available=available)
    score_explanation = explain_indicator_score(
        score=score, components=components, effective_weights=effective_weights,
        repeatability_status=repeatability_status,
    )
    score_explanation.update(explain_evidence_gaps(evidence_observations, required_features, _FEATURE_LABELS))
    score_explanation["counts"] = {
        "valid_feature_slots": valid_slots, "required_feature_slots": total * len(required_features),
        "feature_confidence_samples": len(confidences), "scoring_eligible_instances": scoring_allowed,
        "repeatability_samples": repeatability_sample_count,
    }

    name_zh = str(card.get("name") or indicator_id)
    representative = _representative_measurements(indicator_id, values_by_name)
    if representative:
        evidence_text = "；".join(
            f"{item['label_zh']}典型值 {item['median_value']}{item['unit_zh']}"
            for item in representative[:2]
        )
    else:
        evidence_text = "当前没有形成稳定的可读测量"
    level_zh = _reference_level(score)
    score_text = f"测量证据参考分 {score}/100；" if score is not None else ""
    summary_zh = f"{name_zh}：{score_text}{len(measured_records)}/{total} 个候选片段可测；不代表技术水平。"
    repeatability_text = (
        f"跨候选片段测量重复性为 {repeatability}%，不代表技术正确性，也不作为跨视频比较。"
        if repeatability is not None
        else "独立可测片段不足，暂不描述跨片段重复性。"
    )
    observation_zh = f"{evidence_text}。{repeatability_text}"
    suggestion_zh = (
        "结合回放复核候选动作、测量值和阶段；技术是否正确仍需教练标准或人工复核。"
        if available else "补拍完整动作过程，保持全身和双脚持续可见；缺少测量不代表动作做得差。"
    )

    return {
        "evaluation_version": TRAINING_EVALUATION_VERSION,
        **_technical_score_contract(),
        "indicator_id": indicator_id,
        "event_code": event_code,
        "name_zh": name_zh,
        "label_zh": "测量证据参考分（Beta）",
        "definition_zh": str(card.get("definition") or ""),
        "score_0_to_100": score,
        "score_explanation": score_explanation,
        "available": available,
        "measurement_status": "measured" if available else "unavailable",
        "level_zh": level_zh,
        "summary_zh": summary_zh,
        "observation_zh": observation_zh,
        "suggestion_zh": suggestion_zh,
        "measured_instance_count": len(measured_records),
        "total_instance_count": total,
        "components": components,
        "component_weights": dict(_SCORE_WEIGHTS),
        "effective_component_weights": effective_weights,
        "repeatability_status": repeatability_status,
        "repeatability_sample_count": repeatability_sample_count,
        "representative_measurements": representative,
        "limitations_zh": _friendly_limitations(indicator_id, records),
        "formal_grade": None,
        "is_formal_coach_score": False,
    }


def validated_training_records(
    indicator_feature_records: Iterable[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    """Share event validation with compatibility information assessments."""
    records = [record for record in indicator_feature_records if isinstance(record, Mapping)]
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    validation = {"duplicate_record_count": 0, "conflicting_record_count": 0, "event_code_mismatch_record_count": 0, "unidentified_record_count": 0}
    for record in records:
        indicator_id = record.get("indicator_id")
        if not isinstance(indicator_id, str) or indicator_id not in _INDICATOR_FEATURES:
            continue
        if record.get("event_code") != _INDICATOR_FEATURES[indicator_id][0]:
            validation["event_code_mismatch_record_count"] += 1
            continue
        identity = _event_identity(record)
        if identity is None:
            validation["unidentified_record_count"] += 1
        # Unidentified legacy observations can only stand as one singleton per
        # indicator. Conflicting unscoped observations cannot establish events.
        key = identity if identity is not None else ("unidentified", indicator_id)
        grouped.setdefault(key, []).append(record)
    validated = []
    for group in grouped.values():
        first = group[0]
        if any(record != first for record in group[1:]):
            validation["conflicting_record_count"] += len(group)
            continue
        validation["duplicate_record_count"] += len(group) - 1
        validated.append(first)
    return validated, validation


def build_training_evaluation(
    indicator_feature_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an evidence reference score, never a technical quality grade."""
    validated, validation = validated_training_records(indicator_feature_records)
    by_indicator: dict[str, list[Mapping[str, Any]]] = {
        indicator_id: [] for indicator_id in _INDICATOR_FEATURES
    }
    for record in validated:
        by_indicator[str(record["indicator_id"])].append(record)

    cards = _load_metric_cards()
    evaluations = [
        _indicator_evaluation(indicator_id, by_indicator[indicator_id], cards.get(indicator_id, {}))
        for indicator_id in _INDICATOR_FEATURES
    ]
    measured = [item for item in evaluations if item["available"]]
    scores = [item["score_0_to_100"] for item in measured if item["score_0_to_100"] is not None]
    overall_score = round(statistics.fmean(scores)) if scores else None
    action_evaluations: dict[str, dict[str, Any]] = {}
    for event_code in ("FS01", "FS02", "FS09"):
        items = [item for item in evaluations if item["event_code"] == event_code]
        measured_count = sum(item["available"] for item in items)
        item_scores = [item["score_0_to_100"] for item in items if item["score_0_to_100"] is not None]
        action_score = round(statistics.fmean(item_scores)) if item_scores else None
        score_text = f"测量证据参考分 {action_score}/100；" if action_score is not None else ""
        action_evaluations[event_code] = {
            **_technical_score_contract(),
            "label_zh": "测量证据参考分（Beta）",
            "score_0_to_100": action_score,
            "available": measured_count > 0,
            "level_zh": _reference_level(action_score),
            "summary_zh": f"{score_text}{measured_count}/{len(items)} 项有测量；不代表技术水平。",
            "aggregation": "unweighted_mean_of_available_indicator_reference_scores",
            "evaluated_indicator_count": measured_count,
            "total_indicator_count": len(items),
            "indicator_evaluations": items,
        }
    return {
        "evaluation_version": TRAINING_EVALUATION_VERSION,
        **_technical_score_contract(),
        "input_validation": validation,
        "available": bool(measured),
        "label_zh": "测量证据参考分（Beta）",
        "score_0_to_100": overall_score,
        "score_explanation": explain_aggregate_score(evaluations, overall_score),
        "level_zh": _reference_level(overall_score),
        "summary_zh": (
            f"测量证据参考分 {overall_score}/100；当前 {len(evaluations)} 项中有 {len(measured)} 项有测量，不代表技术水平。"
            if overall_score is not None else "当前视频缺少可用测量，暂不提供参考分；缺少证据不代表动作做得差。"
        ),
        "meaning_zh": "按可测比例、特征覆盖、置信度、跨片段重复性和评分证据比例生成参考分；缺失分量不补分，其余权重归一化。总分只平均有测量的指标，不代表技术正确性，不用于跨视频技术水平比较。技术分和 A～E 等级仍待教练标定。",
        "aggregation": "unweighted_mean_of_available_indicator_reference_scores",
        "evaluated_indicator_count": len(measured),
        "total_indicator_count": len(evaluations),
        "strengths_zh": [],
        "priorities_zh": [],
        "action_evaluations": action_evaluations,
        "indicator_evaluations": evaluations,
        "component_weights": dict(_SCORE_WEIGHTS),
        "formal_grade": None,
        "is_formal_coach_score": False,
    }
