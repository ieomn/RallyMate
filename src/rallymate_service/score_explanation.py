"""Reconcile existing evidence scores without introducing scoring decisions."""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


EXPLANATION_VERSION = "evidence-score-explanation-v1.0.0"
SCORE_SEMANTICS = "measurement_evidence_quality"
COMPONENT_LABELS = {
    "measured_instance_ratio": "可测片段比例",
    "required_feature_coverage": "必需特征覆盖",
    "median_feature_confidence": "特征置信度",
    "repeatability": "跨片段重复性",
    "scoring_evidence_ratio": "评分证据覆盖",
}
_COMPONENT_REASONS = {
    "measured_instance_ratio": "可测片段占全部候选片段的比例；未通过测量条件的片段会降低此值。",
    "required_feature_coverage": "有效必需特征占全部候选片段应有特征的比例；缺测或未通过整项测量条件的特征不计入。",
    "median_feature_confidence": "已纳入测量的必需特征置信度中位数，反映测量可信程度，不是动作正确率。",
    "repeatability": "比较独立片段之间各特征的重复性，再取平均；方向、侧别和动作条件的差异也会降低此值，不代表技术错误。",
    "scoring_evidence_ratio": "评分证据门槛通过且必需特征完整的片段，占全部候选片段的比例；通过也不等于已有正式技术等级。",
}
_REASONS = {
    "measurement_unavailable": "该片段未满足整项测量条件，不能据此判断动作错误。",
    "required_feature_unavailable": "部分必需特征没有有效测量。",
    "keypoint_continuity": "部分关键点的连续性需要复核。",
    "left_right_assignment": "部分左右关键点的对应关系需要复核。",
    "subject_continuity": "部分片段的主体身份连续性尚未确认。",
    "target_direction": "独立目标方向未确认，不能判断移动方向是否正确。",
    "phase_evidence": "部分动作阶段或边界证据尚未确认。",
    "observation_coverage": "部分关键点或人物轨迹覆盖不足。",
    "side_assignment": "启动脚侧别尚未确认。",
    "other_evidence_condition": "部分评分证据条件尚未满足。",
}


def _number(value: Any, lower: float = 0.0, upper: float = 100.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and lower <= number <= upper else None


def _excluded(key: str, reason: str) -> dict[str, Any]:
    return {"key": key, "label_zh": COMPONENT_LABELS[key], "value_percent": None,
            "effective_weight": None, "max_points": None, "earned_points": None,
            "deduction_points": None, "included": False, "reason_zh": reason}


def _base(aggregation: str) -> dict[str, Any]:
    return {"version": EXPLANATION_VERSION, "score_semantics": SCORE_SEMANTICS,
            "status": "unavailable", "score_0_to_100": None, "raw_score": None,
            "rounding_adjustment_points": None, "aggregation": aggregation,
            "rounding_method": "nearest_integer_ties_to_even",
            "deduction_semantics": "evidence_reference_points_not_technical_fault_penalties",
            "components": [_excluded(key, "未形成可用参考分，不计算得分或失分。") for key in COMPONENT_LABELS]}


def explain_indicator_score(*, score: int | None, components: Mapping[str, Any],
                            effective_weights: Mapping[str, Any], repeatability_status: str) -> dict[str, Any]:
    result = _base("single_indicator_weighted_components")
    displayed = _number(score)
    if (displayed is None or displayed != int(displayed) or not isinstance(components, Mapping)
            or not isinstance(effective_weights, Mapping) or not effective_weights):
        return result
    # This adapter only explains a valid upstream result. Never normalize,
    # clamp or reinterpret malformed inputs as a different score.
    if any(key not in COMPONENT_LABELS for key in effective_weights):
        return result
    weights = {key: _number(value, 0, 1) for key, value in effective_weights.items()}
    if any(value is None or value <= 0 for value in weights.values()):
        return result
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-12, rel_tol=0):
        return result
    rows = []
    for key in COMPONENT_LABELS:
        if key not in weights:
            reason = ("独立片段身份未确认，本分量未计入，其余分量按实际权重归一化。"
                      if key == "repeatability" and repeatability_status == "independent_event_identity_required"
                      else "独立可测片段不足，本分量未计入，其余分量按实际权重归一化。"
                      if key == "repeatability" else "本分量证据未提供，不记零分，其余分量按实际权重归一化。")
            rows.append(_excluded(key, reason))
            continue
        value = _number(components.get(f"{key}_percent"))
        if value is None:
            return result
        weight = weights[key]
        maximum, earned = weight * 100, weight * value
        rows.append({"key": key, "label_zh": COMPONENT_LABELS[key], "value_percent": value,
                     "effective_weight": weight, "max_points": maximum, "earned_points": earned,
                     "deduction_points": maximum - earned, "included": True,
                     "reason_zh": _COMPONENT_REASONS[key]})
    # Use the scoring function's operation order and Python rounding policy.
    raw = sum(float(components[f"{key}_percent"]) * float(weight)
              for key, weight in effective_weights.items())
    if round(raw) != displayed:
        return result
    result.update(status="available", score_0_to_100=score, raw_score=raw,
                  rounding_adjustment_points=displayed - raw, components=rows)
    return result


def explain_aggregate_score(evaluations: Sequence[Mapping[str, Any]], score: int | None) -> dict[str, Any]:
    result = _base("unweighted_mean_of_available_indicator_reference_scores")
    if any(not isinstance(item, Mapping) or not isinstance(item.get("indicator_id"), str) for item in evaluations):
        return result
    included = [item for item in evaluations if item.get("available") is True and _number(item.get("score_0_to_100")) is not None]
    ids = [str(item["indicator_id"]) for item in included]
    result.update(included_indicator_ids=ids,
                  excluded_indicator_ids=[str(item["indicator_id"]) for item in evaluations if str(item["indicator_id"]) not in ids],
                  included_indicator_count=len(included), total_indicator_count=len(evaluations),
                  indicator_scores=[{"indicator_id": item["indicator_id"], "score_0_to_100": item["score_0_to_100"]} for item in included],
                  rounding_stages={"mean_indicator_rounding_points": None, "aggregate_rounding_points": None})
    displayed = _number(score)
    if not included or displayed is None or displayed != int(displayed):
        return result
    explanations = [item.get("score_explanation") for item in included]
    if any(not isinstance(item, Mapping) or item.get("status") != "available"
           or item.get("version") != EXPLANATION_VERSION or item.get("score_semantics") != SCORE_SEMANTICS
           or item.get("score_0_to_100") != source["score_0_to_100"]
           for item, source in zip(explanations, included)):
        return result
    # Re-derive from upstream components, rather than trusting caller-supplied
    # explanation numbers. The displayed scores and their gates stay upstream.
    checked = [explain_indicator_score(score=item["score_0_to_100"], components=item.get("components", {}),
                                     effective_weights=item.get("effective_component_weights", {}),
                                     repeatability_status=str(item.get("repeatability_status", ""))) for item in included]
    if any(item["status"] != "available" for item in checked):
        return result
    rounded_mean = statistics.fmean(item["score_0_to_100"] for item in included)
    if round(rounded_mean) != displayed:
        return result
    rows = []
    for index, key in enumerate(COMPONENT_LABELS):
        component_rows = [item["components"][index] for item in checked]
        count = sum(item["included"] for item in component_rows)
        if count == 0:
            row = _excluded(key, "纳入总分的指标均未提供本分量，不记零分。")
        else:
            maximum = statistics.fmean(item["max_points"] if item["included"] else 0.0 for item in component_rows)
            earned = statistics.fmean(item["earned_points"] if item["included"] else 0.0 for item in component_rows)
            row = {"key": key, "label_zh": COMPONENT_LABELS[key], "value_percent": earned / maximum * 100,
                   "effective_weight": maximum / 100, "max_points": maximum, "earned_points": earned,
                   "deduction_points": maximum - earned, "included": True,
                   "reason_zh": "平均各已评分指标的实际分量贡献；未提供的分量没有分配额度或失分。"}
        row.update(included_indicator_count=count, total_indicator_count=len(included),
                   value_percent_aggregation="earned_points_divided_by_allocated_max_points")
        rows.append(row)
    raw = sum(item["earned_points"] for item in rows if item["included"])
    result.update(status="available", score_0_to_100=score, raw_score=raw, rounded_indicator_mean=rounded_mean,
                  rounding_adjustment_points=displayed - raw, components=rows,
                  rounding_stages={"mean_indicator_rounding_points": rounded_mean - raw,
                                   "aggregate_rounding_points": displayed - rounded_mean})
    return result


def _blocker_group(flag: str) -> str:
    if "jump" in flag:
        return "keypoint_continuity"
    if "swap" in flag or "left_right" in flag:
        return "left_right_assignment"
    if "direction" in flag or "target" in flag:
        return "target_direction"
    if "phase" in flag or "boundary" in flag:
        return "phase_evidence"
    if "coverage" in flag:
        return "observation_coverage"
    if "track" in flag or "identity" in flag:
        return "subject_continuity"
    if "side" in flag:
        return "side_assignment"
    return "other_evidence_condition"


def explain_evidence_gaps(observations: Sequence[Mapping[str, Any]], required_features: Sequence[str],
                          feature_labels: Mapping[str, str]) -> dict[str, Any]:
    total = len(observations)
    missing, excluded, blockers = Counter(), Counter(), Counter()
    feature_events, blocker_events = defaultdict(set), defaultdict(set)

    def event_refs(values: set[str]) -> dict[str, Any]:
        return {"event_ids": sorted(values)[:3], "event_id_count": len(values),
                "event_ids_truncated": len(values) > 3}

    for item in observations:
        measured = item.get("measured") is True
        valid = set(item.get("valid_features", ()))
        absent = set(required_features) - valid
        missing.update(absent)
        if not measured:
            excluded.update(set(required_features) & valid)
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id.strip() and len(event_id) <= 256:
            for name in absent | (set(required_features) & valid if not measured else set()):
                feature_events[name].add(event_id)
        reasons = set()
        if not measured:
            reasons.add("measurement_unavailable")
        if absent:
            reasons.add("required_feature_unavailable")
        gate = item.get("quality_gate")
        gate = gate if isinstance(gate, Mapping) else {}
        if not measured or gate.get("scoring_allowed") is not True or absent:
            flags = [flag for key in ("hard_fail_flags", "scoring_block_flags")
                     for flag in (gate.get(key) if isinstance(gate.get(key), list) else [])
                     if isinstance(flag, str) and 0 < len(flag) <= 256]
            reasons.update(_blocker_group(flag) for flag in flags)
            if not reasons:
                reasons.add("other_evidence_condition")
        blockers.update(reasons)
        if isinstance(event_id, str) and event_id.strip() and len(event_id) <= 256:
            for key in reasons:
                blocker_events[key].add(event_id)
    return {
        "measured_instance_count": sum(item.get("measured") is True for item in observations),
        "total_instance_count": total,
        "feature_gaps": [{"feature_name": name, "label_zh": feature_labels.get(name, name),
                          "required_instance_count": total, "missing_instance_count": missing[name],
                          "excluded_by_measurement_gate_count": excluded[name],
                          "unavailable_instance_count": missing[name] + excluded[name],
                          "available_instance_count": total - missing[name] - excluded[name],
                          **event_refs(feature_events[name])}
                         for name in required_features if missing[name] or excluded[name]],
        "blocker_reasons": [{"key": key, "reason_zh": _REASONS[key], "affected_instance_count": count,
                             **event_refs(blocker_events[key])}
                            for key, count in sorted(blockers.items(), key=lambda pair: (-pair[1], pair[0]))],
        "blocker_count_semantics": "overlapping_validated_record_counts_not_additive_point_deductions",
    }
