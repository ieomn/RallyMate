from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SCORING_BLOCKER_TAXONOMY_VERSION = "scoring-blocker-taxonomy-v1.0.0"
PHASE_RIGHT_CENSORED_PEAK_PREFIX = "phase_proxy_right_censored_peak:"
PHASE_LOW_SAMPLE_PEAK_PREFIX = "phase_proxy_low_sample_peak:"
REQUIRED_PHASE_MISSING_PREFIX = "required_phase_missing:"
LEAD_FOOT_SIDE_AMBIGUOUS_FLAG = "lead_foot_side_proxy_ambiguous"


EXACT_BLOCKER_SPECS: tuple[dict[str, str], ...] = (
    {
        "flag": "source_track_switch_candidates_present",
        "reason_code": "event_identity_continuity_unverified",
        "truth_requirement": "manual_primary_identity_continuity_truth",
        "blocker_group": "identity_continuity",
        "feedback": "主球员身份连续性尚未确认",
    },
    {
        "flag": "primary_identity_ambiguous",
        "reason_code": "event_identity_continuity_unverified",
        "truth_requirement": "manual_primary_identity_continuity_truth",
        "blocker_group": "identity_continuity",
        "feedback": "主球员身份连续性尚未确认",
    },
    {
        "flag": "keypoint_jump_candidates_present",
        "reason_code": "keypoint_jump_diagnostic_unverified",
        "truth_requirement": "full_timeline_manual_keypoint_jump_truth",
        "blocker_group": "pose_diagnostic",
        "feedback": "关键点跳变诊断尚未完成独立验证",
    },
    {
        "flag": "left_right_swap_candidates_present",
        "reason_code": "left_right_assignment_unverified",
        "truth_requirement": "full_timeline_manual_left_right_swap_truth",
        "blocker_group": "pose_diagnostic",
        "feedback": "左右关键点分配尚未确认",
    },
    {
        "flag": "tactical_target_direction_not_observed",
        "reason_code": "tactical_target_direction_required",
        "truth_requirement": "manual_target_direction_semantics",
        "blocker_group": "tactical_context",
        "feedback": "目标方向语义尚未提供",
    },
    {
        "flag": "pose_kinematic_coverage_low",
        "reason_code": "event_boundary_evidence_low",
        "truth_requirement": "manual_event_boundary_truth",
        "blocker_group": "event_boundary",
        "feedback": "自动事件边界的 Pose 运动学覆盖不足",
    },
    {
        "flag": "primary_pose_coverage_low",
        "reason_code": "primary_pose_observation_coverage_low",
        "truth_requirement": "manual_pose_observation_and_event_boundary_review",
        "blocker_group": "pose_observation",
        "feedback": "事件内主球员 Pose 观测覆盖不足",
    },
    {
        "flag": LEAD_FOOT_SIDE_AMBIGUOUS_FLAG,
        "reason_code": "lead_foot_side_assignment_unverified",
        "truth_requirement": "manual_launch_side_semantics",
        "blocker_group": "side_semantics",
        "feedback": "左右脚活动峰接近，启动脚侧别代理尚未确认",
    },
)

PREFIX_BLOCKER_SPECS: tuple[dict[str, str], ...] = (
    {
        "flag_prefix": PHASE_RIGHT_CENSORED_PEAK_PREFIX,
        "reason_code": "event_phase_proxy_right_censored_unverified",
        "truth_requirement_template": "manual_phase_boundary:{phase_key}",
        "blocker_group": "event_phase",
        "feedback_template": "阶段 {phase_keys} 仅由事件右边界前的实测峰代理，尚未通过人工边界验证",
    },
    {
        "flag_prefix": PHASE_LOW_SAMPLE_PEAK_PREFIX,
        "reason_code": "event_phase_proxy_low_sample_unverified",
        "truth_requirement_template": "manual_phase_boundary:{phase_key}",
        "blocker_group": "event_phase",
        "feedback_template": "阶段 {phase_keys} 仅由两样本实测峰代理，尚未通过人工边界验证",
    },
)

_EXACT_BY_FLAG = {item["flag"]: item for item in EXACT_BLOCKER_SPECS}

# These requirements are consumed by truth worklists but are not score-only
# flags. Keeping them here prevents the annotation layer from inventing a
# second, drifting vocabulary.
_AUXILIARY_TRUTH_REQUIREMENTS = {
    "restabilization_not_observed": "manual_restabilization_phase_and_keypoint_truth",
}


def blocker_spec(flag: str) -> dict[str, str] | None:
    exact = _EXACT_BY_FLAG.get(flag)
    if exact is not None:
        return dict(exact)
    for item in PREFIX_BLOCKER_SPECS:
        prefix = item["flag_prefix"]
        if flag.startswith(prefix):
            phase_key = flag.removeprefix(prefix)
            return {
                "flag": flag,
                "flag_prefix": prefix,
                "reason_code": item["reason_code"],
                "truth_requirement": item["truth_requirement_template"].format(
                    phase_key=phase_key
                ),
                "blocker_group": item["blocker_group"],
                "feedback": item["feedback_template"].format(phase_keys=phase_key),
            }
    return None


def typed_reason_for_flag(flag: str) -> str | None:
    spec = blocker_spec(flag)
    return None if spec is None else spec["reason_code"]


def truth_requirement_for_flag(flag: str) -> str:
    spec = blocker_spec(flag)
    if spec is not None:
        # Preserve the existing, more specific public names for the three
        # currently registered phase proxies.
        phase_overrides = {
            "phase_proxy_right_censored_peak:landing_proxy_ms": "manual_landing_phase_boundary",
            "phase_proxy_right_censored_peak:first_step_slowdown_proxy_ms": "manual_first_step_slowdown_phase_boundary",
            "phase_proxy_low_sample_peak:first_step_slowdown_proxy_ms": "manual_first_step_slowdown_phase_boundary",
        }
        return phase_overrides.get(flag, spec["truth_requirement"])
    if flag.startswith(REQUIRED_PHASE_MISSING_PREFIX):
        return f"manual_phase_boundary:{flag.removeprefix(REQUIRED_PHASE_MISSING_PREFIX)}"
    return _AUXILIARY_TRUTH_REQUIREMENTS.get(
        flag, "external_manual_review_required"
    )


def blocker_group_for_flag(flag: str) -> str:
    spec = blocker_spec(flag)
    if spec is not None:
        return spec["blocker_group"]
    if flag.startswith(REQUIRED_PHASE_MISSING_PREFIX) or flag in _AUXILIARY_TRUTH_REQUIREMENTS:
        return "event_phase"
    return "external_review"


def typed_reason_supports_flag(reason_code: str, flag: str) -> bool:
    return typed_reason_for_flag(flag) == reason_code


def known_typed_reason_codes() -> frozenset[str]:
    return frozenset(
        [item["reason_code"] for item in EXACT_BLOCKER_SPECS]
        + [item["reason_code"] for item in PREFIX_BLOCKER_SPECS]
    )


def exact_typed_reason_support_flags() -> dict[str, frozenset[str]]:
    output: dict[str, set[str]] = {}
    for item in EXACT_BLOCKER_SPECS:
        output.setdefault(item["reason_code"], set()).add(item["flag"])
    return {reason: frozenset(flags) for reason, flags in output.items()}


def scoring_block_details(flags: Iterable[str]) -> tuple[list[str], list[str]]:
    ordered_flags = [str(flag) for flag in flags]
    if not ordered_flags:
        return [], []
    reasons = ["event_scoring_quality_blocked"]
    messages: list[str] = []

    # Preserve the public ordering used before the taxonomy was centralized:
    # identity/pose/context reasons, then phase-proxy reasons, and finally the
    # lead-foot side proxy.  Ordering is part of the explainability contract
    # because feedback is rendered as a sentence sequence in reports.
    lead_foot_spec = _EXACT_BY_FLAG[LEAD_FOOT_SIDE_AMBIGUOUS_FLAG]
    for item in EXACT_BLOCKER_SPECS:
        if item["flag"] == LEAD_FOOT_SIDE_AMBIGUOUS_FLAG:
            continue
        if item["flag"] in ordered_flags:
            if item["reason_code"] not in reasons:
                reasons.append(item["reason_code"])
            if item["feedback"] not in messages:
                messages.append(item["feedback"])

    for prefix_item in PREFIX_BLOCKER_SPECS:
        prefix = prefix_item["flag_prefix"]
        phase_keys = [flag.removeprefix(prefix) for flag in ordered_flags if flag.startswith(prefix)]
        if phase_keys:
            reasons.append(prefix_item["reason_code"])
            messages.append(
                prefix_item["feedback_template"].format(
                    phase_keys="、".join(phase_keys)
                )
            )

    if LEAD_FOOT_SIDE_AMBIGUOUS_FLAG in ordered_flags:
        if lead_foot_spec["reason_code"] not in reasons:
            reasons.append(lead_foot_spec["reason_code"])
        if lead_foot_spec["feedback"] not in messages:
            messages.append(lead_foot_spec["feedback"])

    if not messages:
        reasons.append("event_scoring_context_unverified")
        messages.append("正式评分所需上下文尚未确认")
    return list(dict.fromkeys([*reasons, *ordered_flags])), messages


def taxonomy_document() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "taxonomy_version": SCORING_BLOCKER_TAXONOMY_VERSION,
        "exact_entries": [dict(item) for item in EXACT_BLOCKER_SPECS],
        "prefix_entries": [dict(item) for item in PREFIX_BLOCKER_SPECS],
        "auxiliary_truth_requirements": dict(_AUXILIARY_TRUTH_REQUIREMENTS),
        "unknown_policy": {
            "reason_code": "event_scoring_context_unverified",
            "truth_requirement": "external_manual_review_required",
            "blocker_group": "external_review",
        },
        "safety": {
            "quality_gate_modified": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
            "accuracy_claim": False,
        },
    }
