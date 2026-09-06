from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping
from importlib.resources import files
from typing import Any


TRAINING_EVALUATION_VERSION = "rallymate-training-evaluation-beta-v1.0.0"

_SCORE_WEIGHTS = {
    "measured_instance_ratio": 0.30,
    "required_feature_coverage": 0.20,
    "median_feature_confidence": 0.15,
    "repeatability": 0.25,
    "scoring_evidence_ratio": 0.10,
}

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
    number = float(value)
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


def _valid_feature_map(record: Mapping[str, Any]) -> dict[str, tuple[float, float | None, str | None]]:
    raw_features = record.get("features")
    if not isinstance(raw_features, list):
        raw_features = record.get("scoring_features")
    result: dict[str, tuple[float, float | None, str | None]] = {}
    if not isinstance(raw_features, list):
        return result
    for feature in raw_features:
        if not isinstance(feature, Mapping) or feature.get("valid") is not True:
            continue
        name = feature.get("feature_name")
        value = _finite(feature.get("value"))
        if not isinstance(name, str) or value is None or name in result:
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
    if not values:
        return None
    if len(values) == 1:
        return 50.0
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


def _level(score: int | None) -> str:
    if score is None:
        return "暂无法评价"
    if score >= 85:
        return "表现较稳定"
    if score >= 70:
        return "表现基本稳定"
    if score >= 55:
        return "已有动作基础，稳定性待提高"
    return "建议优先改善"


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
        messages.append("部分片段的骨架连续性需要复核，已降低本项参考分。")
    if "swap" in joined or "side" in joined and "unverified" in joined:
        messages.append("部分片段的左右侧识别不够稳定，已降低本项参考分。")
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
        median_value, unit_zh = _display_value(statistics.median(values), unit)
        item: dict[str, Any] = {
            "feature_name": name,
            "label_zh": _FEATURE_LABELS.get(name, name),
            "median_value": median_value,
            "unit_zh": unit_zh,
            "sample_count": len(values),
        }
        if len(values) >= 2:
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
    confidences: list[float] = []
    valid_slots = 0
    scoring_allowed = 0
    for record in records:
        gate = record.get("quality_gate")
        if isinstance(gate, Mapping) and gate.get("scoring_allowed") is True:
            scoring_allowed += 1
        if not _is_measured(record):
            continue
        feature_map = _valid_feature_map(record)
        for name in required_features:
            feature = feature_map.get(name)
            if feature is None:
                continue
            value, confidence, unit = feature
            values_by_name[name].append((value, unit))
            valid_slots += 1
            if confidence is not None:
                confidences.append(confidence)

    measured_ratio = _percent(len(measured_records), total)
    feature_coverage = _percent(valid_slots, total * len(required_features))
    median_confidence = round(statistics.median(confidences) * 100) if confidences else 0
    median_confidence = max(0, min(100, median_confidence))
    feature_repeatabilities = [
        value
        for name, entries in values_by_name.items()
        if (value := _repeatability_for_feature(name, [item[0] for item in entries]))
        is not None
    ]
    repeatability = (
        round(statistics.median(feature_repeatabilities))
        if feature_repeatabilities
        else 0
    )
    evidence_ratio = _percent(scoring_allowed, total)
    components = {
        "measured_instance_ratio_percent": measured_ratio,
        "required_feature_coverage_percent": feature_coverage,
        "median_feature_confidence_percent": median_confidence,
        "repeatability_percent": max(0, min(100, repeatability)),
        "scoring_evidence_ratio_percent": evidence_ratio,
    }
    if not measured_records or valid_slots == 0:
        score: int | None = None
    else:
        score = round(
            components["measured_instance_ratio_percent"]
            * _SCORE_WEIGHTS["measured_instance_ratio"]
            + components["required_feature_coverage_percent"]
            * _SCORE_WEIGHTS["required_feature_coverage"]
            + components["median_feature_confidence_percent"]
            * _SCORE_WEIGHTS["median_feature_confidence"]
            + components["repeatability_percent"]
            * _SCORE_WEIGHTS["repeatability"]
            + components["scoring_evidence_ratio_percent"]
            * _SCORE_WEIGHTS["scoring_evidence_ratio"]
        )
        score = max(0, min(100, score))

    name_zh = str(card.get("name") or indicator_id)
    representative = _representative_measurements(indicator_id, values_by_name)
    if representative:
        evidence_text = "；".join(
            f"{item['label_zh']}典型值 {item['median_value']}{item['unit_zh']}"
            for item in representative[:2]
        )
    else:
        evidence_text = "当前没有形成稳定的可读测量"
    level_zh = _level(score)
    if score is None:
        summary_zh = f"{name_zh}暂时无法形成可靠评价。"
        observation_zh = (
            "这不代表动作做得差，而是当前片段的关键身体点或动作阶段不足以支撑评价。"
        )
        training_focus = str(
            card.get("improvementFeedback") or "保持动作连贯，并让全身和双脚持续可见。"
        )
        suggestion_zh = (
            "建议补拍完整动作过程；训练时可重点检查："
            f"{training_focus}"
        )
    else:
        summary_zh = f"{name_zh}：{score}/100，{level_zh}。"
        observation_zh = (
            f"本次共找到 {total} 个相关片段，其中 {len(measured_records)} 个可测；"
            f"{evidence_text}。跨片段重复性为 {components['repeatability_percent']}%。"
        )
        if score >= 80:
            training_target = str(
                card.get("positiveFeedback") or "动作节奏连贯且重复稳定。"
            )
            suggestion_zh = (
                "本次可测动作的重复性较好，可继续保持；训练目标可对照："
                f"{training_target}"
            )
        else:
            training_focus = str(
                card.get("improvementFeedback") or "优先练习动作节奏与重复稳定性。"
            )
            suggestion_zh = (
                "本项在当前视频中的稳定性或证据质量仍可提高；训练时重点检查："
                f"{training_focus}"
            )

    return {
        "evaluation_version": TRAINING_EVALUATION_VERSION,
        "indicator_id": indicator_id,
        "event_code": event_code,
        "name_zh": name_zh,
        "definition_zh": str(card.get("definition") or ""),
        "score_0_to_100": score,
        "level_zh": level_zh,
        "summary_zh": summary_zh,
        "observation_zh": observation_zh,
        "suggestion_zh": suggestion_zh,
        "measured_instance_count": len(measured_records),
        "total_instance_count": total,
        "components": components,
        "component_weights": dict(_SCORE_WEIGHTS),
        "representative_measurements": representative,
        "limitations_zh": _friendly_limitations(indicator_id, records),
        "formal_grade": None,
        "is_formal_coach_score": False,
    }


def _aggregate_level(score: int | None) -> str:
    return _level(score)


def build_training_evaluation(
    indicator_feature_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, user-readable Beta training evaluation.

    The score combines measurement coverage, feature confidence, repeated-action
    stability and scoring-evidence quality.  It intentionally remains separate
    from the formal calibration/grade pipeline.
    """

    records = [record for record in indicator_feature_records if isinstance(record, Mapping)]
    by_indicator: dict[str, list[Mapping[str, Any]]] = {
        indicator_id: [] for indicator_id in _INDICATOR_FEATURES
    }
    for record in records:
        indicator_id = record.get("indicator_id")
        if isinstance(indicator_id, str) and indicator_id in by_indicator:
            by_indicator[indicator_id].append(record)

    cards = _load_metric_cards()
    evaluations = [
        _indicator_evaluation(indicator_id, by_indicator[indicator_id], cards.get(indicator_id, {}))
        for indicator_id in _INDICATOR_FEATURES
    ]
    scored = [item for item in evaluations if item["score_0_to_100"] is not None]
    overall_score = (
        round(statistics.fmean(item["score_0_to_100"] for item in scored))
        if scored
        else None
    )

    action_evaluations: dict[str, dict[str, Any]] = {}
    for event_code in ("FS01", "FS02", "FS09"):
        items = [item for item in evaluations if item["event_code"] == event_code]
        item_scores = [item["score_0_to_100"] for item in items if item["score_0_to_100"] is not None]
        action_score = round(statistics.fmean(item_scores)) if item_scores else None
        action_evaluations[event_code] = {
            "score_0_to_100": action_score,
            "level_zh": _aggregate_level(action_score),
            "summary_zh": (
                f"{len(item_scores)}/{len(items)} 项形成评价，动作表现参考分为 {action_score}/100。"
                if action_score is not None
                else "本类动作暂时没有形成可靠评价；这不代表动作做得差。"
            ),
            "evaluated_indicator_count": len(item_scores),
            "total_indicator_count": len(items),
            "indicator_evaluations": items,
        }

    ranked = sorted(
        scored,
        key=lambda item: (-int(item["score_0_to_100"]), str(item["indicator_id"])),
    )
    strengths = [
        f"{item['name_zh']}：{item['score_0_to_100']}/100，{item['level_zh']}。"
        for item in ranked[:3]
    ]
    priorities = [
        f"{item['name_zh']}：{item['suggestion_zh']}"
        for item in sorted(
            scored,
            key=lambda item: (int(item["score_0_to_100"]), str(item["indicator_id"])),
        )[:3]
    ]

    return {
        "evaluation_version": TRAINING_EVALUATION_VERSION,
        "available": overall_score is not None,
        "label_zh": "动作表现参考分（Beta）",
        "score_0_to_100": overall_score,
        "level_zh": _aggregate_level(overall_score),
        "summary_zh": (
            f"当前 13 项中有 {len(scored)} 项形成评价，整体参考分为 {overall_score}/100。"
            if overall_score is not None
            else "当前视频没有形成足够动作证据，暂时无法生成动作表现评价。"
        ),
        "meaning_zh": (
            "根据本视频中重复动作的可测比例、特征覆盖、骨架置信信息、跨片段重复性和评分证据质量生成，"
            "用于训练复盘；它不是经过教练标定的正式技术分或 A～E 等级。"
        ),
        "evaluated_indicator_count": len(scored),
        "total_indicator_count": len(evaluations),
        "strengths_zh": strengths,
        "priorities_zh": priorities,
        "action_evaluations": action_evaluations,
        "indicator_evaluations": evaluations,
        "component_weights": dict(_SCORE_WEIGHTS),
        "formal_grade": None,
        "is_formal_coach_score": False,
    }
