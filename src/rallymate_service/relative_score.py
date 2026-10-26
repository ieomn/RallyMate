"""Versioned references from observed rule values, separate from formal grades.

Only independent target alignment contributes to the current score. Selected
wrist-speed trends remain in a separate legacy diagnostic, not a technical
grade. Missing dimensions remain null; clip length never shrinks scores to 50.
"""
from __future__ import annotations

import math
import statistics
import json
from importlib.resources import files
from collections.abc import Mapping
from typing import Any

_POLICY = json.loads(files("rallymate_scoring").joinpath("data/relative_reference_policy.json").read_text(encoding="utf-8"))
VERSION = _POLICY["version"]
LEGACY_VERSION = "relative-reference-v1.0.0"
_HISTORY = _POLICY["legacy_policy"]
PRIOR_WEIGHT = _HISTORY["prior_sample_weight"]
NEUTRAL_SCORE = _HISTORY["neutral_score"]
_DIMENSIONS = tuple((row["id"], row["name_zh"]) for row in _POLICY["dimensions"])
_RULES = tuple(_POLICY["rules"])
_LEGACY_RULES = tuple(_POLICY["excluded_legacy_rules"])
SCORING_RULE_IDS = frozenset(row["id"] for row in _RULES)
EXCLUDED_LEGACY_RULE_IDS = frozenset(row["id"] for row in _LEGACY_RULES)


def _finite(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _round(value: float) -> float:
    # Match JavaScript rounding for nonnegative scores, including exact ties.
    return math.floor(value * 10 + .5) / 10


def _reference_dimensions(dimensions: Any, definitions: tuple, *, legacy: bool = False) -> list[dict[str, Any]]:
    """Recompute exact rule mathematics; ignore cached or supplied scores."""
    rows = dimensions if isinstance(dimensions, (list, tuple)) else []
    output = []
    for identifier, label in _DIMENSIONS:
        matches = [row for row in rows if isinstance(row, Mapping) and row.get("id") == identifier]
        dimension = matches[0] if len(matches) == 1 else {}
        criteria = dimension.get("criteria", []) if dimension.get("status") != "unavailable" else []
        criteria = criteria if isinstance(criteria, list) else []
        rules = []
        for definition in definitions:
            if definition["dimension"] != identifier:
                continue
            rule_id, unit, source = (definition[key] for key in ("id", "unit", "source"))
            samples: dict[str, tuple[float, bool, float, float]] = {}
            conflicts: set[str] = set()
            for row in criteria:
                if not isinstance(row, Mapping) or row.get("id") != rule_id:
                    continue
                value, start, end = row.get("value"), row.get("start_ms"), row.get("end_ms")
                episode = row.get("episode_id")
                if (row.get("status") != "measured" or row.get("unit") != unit or row.get("source") != source
                        or not _finite(value) or not definition["min"] <= value <= definition["max"]
                        or not _finite(start) or not _finite(end) or not 0 <= start < end
                        or not isinstance(episode, str) or not episode.strip()):
                    continue
                if definition["minimum_samples"]:
                    count = row.get("sample_count")
                    if not _finite(count) or int(count) != count or count < definition["minimum_samples"]:
                        continue
                provenance = row.get("measurement_provenance")
                model = isinstance(provenance, Mapping) and provenance.get("observation_semantics") == "monocular_model_estimate"
                raw = (100 * (1 - (value - definition["min"]) / (definition["max"] - definition["min"]))
                       if definition["mapping"] == "inverse_fraction" else float(value))
                sample = (raw, model, start, end)
                if episode in samples and samples[episode] != sample:
                    conflicts.add(episode)
                else:
                    samples[episode] = sample
            for episode in conflicts:
                samples.pop(episode, None)
            count = len(samples)
            raw = statistics.fmean(value[0] for value in samples.values()) if count else None
            weight = count / (count + PRIOR_WEIGHT) if legacy else float(bool(count))
            score = (NEUTRAL_SCORE + (raw - NEUTRAL_SCORE) * weight if raw is not None else float(NEUTRAL_SCORE)) if legacy else raw
            rules.append({"id": rule_id, "name_zh": definition["name_zh"], "raw_mean": _round(raw) if raw is not None else None,
                          "score": _round(score) if score is not None else None, "sample_count": count,
                          "model_estimate_sample_count": sum(value[1] for value in samples.values()),
                          "measurement_weight": round(weight, 6), "formula_zh": definition["formula_zh"],
                          "source": source, "excluded_conflicting_episode_count": len(conflicts)})
        active = [row for row in rules if row["sample_count"]]
        output.append({"id": identifier, "name_zh": label,
                        "score": _round(statistics.fmean(row["score"] for row in active)) if active else float(NEUTRAL_SCORE) if legacy else None,
                        "basis": "observed_reference" if active else "neutral_prior" if legacy else "unavailable",
                       "observed_rule_count": len(active), "implemented_rule_count": len(rules),
                       "sample_count": sum(row["sample_count"] for row in active), "rules": rules})
    return output


def dimension_scoring_audit(identifier: str, criteria: Any, *, active_rules: list[dict] | None = None) -> dict[str, Any]:
    """Audit returned detail; full-input counts remain separately disclosed.

    Caller-provided counters never establish scoring eligibility or numerical
    contributions. Both backend and browser can reproduce this returned scope.
    """
    rows = criteria if isinstance(criteria, list) else []
    measured = set()
    episodes = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value, start, end, episode = (row.get(key) for key in ("value", "start_ms", "end_ms", "episode_id"))
        criterion_id = row.get("id")
        if (row.get("status") == "measured" and _finite(value) and _finite(start) and _finite(end)
                and 0 <= start < end and isinstance(episode, str) and episode.strip()
                and isinstance(criterion_id, str) and criterion_id.strip()):
            measured.add((criterion_id, episode, start, end, value))
            episodes.add(episode)
    if active_rules is None:
        computed = _reference_dimensions([{"id": identifier, "status": "partial", "criteria": rows}], _RULES)
        active_rules = next((row["rules"] for row in computed if row["id"] == identifier), [])
    scored = [row for row in active_rules if row["sample_count"] > 0]
    conflicts = sum(row["excluded_conflicting_episode_count"] for row in active_rules)
    excluded = [row["id"] for row in _LEGACY_RULES if row["dimension"] == identifier]
    if scored:
        kind, reason = "none", "已按独立目标方向规则计分；只覆盖该子项，不代表整维技术质量。"
    elif conflicts:
        kind = "source_conflict"
        reason = "同一动作的独立目标评分输入互相冲突，已排除冲突动作；这不是缺少目标，也不代表动作不合格。"
    elif identifier == "movement":
        kind = "independent_reference_missing"
        reason = "尚无通过绑定检查的独立目标方向与启动测量组合；已有步伐测量不因此消失，其他移动技术规则尚未实现。"
    elif excluded:
        kind = "selection_biased_proxy_excluded"
        reason = "腕速加减速趋势与阶段选择使用同一曲线，已退出评分；曲线测量和旧版诊断保留，完整技术规则尚未实现。"
    else:
        kind = "rule_not_implemented"
        reason = ("触球姿态与拍面控制的量化规则尚未实现，且未绑定经验证触球时段。" if identifier == "contact"
                  else "已支持过程测量，但尚未实现该维度的技术评分规则；不将几何量或时间长短任意映射为优劣。")
    return {"gap": {"type": kind, "reason_zh": reason, "rule_version": VERSION,
                    "measurement_status": "measured" if measured else "unavailable"},
            "scoring_audit": {"measured_criterion_count": len(measured), "measured_episode_count": len(episodes),
                "scored_rule_count": len(scored), "scored_episode_count": sum(row["sample_count"] for row in scored),
                "implemented_rule_count": sum(row["dimension"] == identifier for row in _RULES),
                "excluded_conflicting_episode_count": conflicts,
                "excluded_legacy_rule_ids": excluded,
                "count_scope": "validated_returned_criteria"}}


def build_relative_reference(dimensions: Any = ()) -> dict[str, Any]:
    """Use available independent rules; preserve both prior policies as history."""
    rows = dimensions if isinstance(dimensions, (list, tuple)) else []
    output = _reference_dimensions(rows, _RULES)
    legacy = _reference_dimensions(rows, _RULES + _LEGACY_RULES, legacy=True)
    legacy_relative = _reference_dimensions(rows, _RULES, legacy=True)
    for dimension in output:
        matches = [row for row in rows if isinstance(row, Mapping) and row.get("id") == dimension["id"]]
        original = matches[0] if len(matches) == 1 else {}
        criteria = original.get("criteria", []) if original.get("status") != "unavailable" else []
        dimension.update(dimension_scoring_audit(dimension["id"], criteria, active_rules=dimension["rules"]))
    observed = sum(row["basis"] == "observed_reference" for row in output)
    scores = [row["score"] for row in output if row["score"] is not None]
    return {"version": VERSION, "semantics": "observed_measurement_reference",
            "overall_score": _round(statistics.fmean(scores)) if scores else None,
            "is_calibrated": False,
            "aggregation": "independent_episode_mean_then_rule_mean_then_observed_dimension_mean",
            "observed_dimension_count": observed, "total_dimension_count": 5, "dimensions": output,
            "summary_zh": f"{observed}/5 维有当前有效计分规则与输入；缺测维度保持为空，不进入成绩分母。已有测量与计分缺口分别显示，不代表完整五维技术能力。",
            "formula_zh": _POLICY["formula_zh"], "limitations_zh": list(_POLICY["limitations_zh"]),
            "legacy_relative_reference": {"version": _HISTORY["version"], "semantics": "neutral_prior_relative_reference",
                "is_technical_quality": False, "contributes_to_current_score": False,
                "overall_score": _round(statistics.fmean(row["score"] for row in legacy_relative)),
                "dimensions": legacy_relative, "observed_dimension_count": observed,
                "neutral_score": NEUTRAL_SCORE, "prior_sample_weight": PRIOR_WEIGHT,
                "aggregation": "independent_episode_mean_then_rule_mean_then_five_equal_dimensions",
                "formula_zh": _HISTORY["formula_zh"],
                "reason_zh": "按旧 v2 规则从测量重算的历史参考；缺项填 50 与样本数先验均已退出当前成绩。"},
            "legacy_diagnostic": {"version": LEGACY_VERSION, "semantics": "selection_biased_motion_curve_diagnostic",
                "is_technical_quality": False, "contributes_to_current_score": False,
                "overall_score": _round(statistics.fmean(row["score"] for row in legacy)),
                "dimensions": legacy, "observed_dimension_count": sum(row["basis"] == "observed_reference" for row in legacy),
                "reason_zh": "按旧版公式重算的历史曲线诊断；其腕速分段存在选择偏倚，不再贡献当前总分。"},
            "recalculation": {"from_version": _HISTORY["version"], "to_version": VERSION,
                "source": "validated_process_measurements", "cached_scores_used": False,
                "reason_zh": "历史报告按当前规则从原始过程测量重算；旧 v1 曲线诊断和旧 v2 参考独立保留，不参与当前成绩，不改写原始测量。"}}
