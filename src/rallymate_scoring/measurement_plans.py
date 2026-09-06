from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from rallymate_scoring.feasibility import (
    feasibility_event_codes,
    indicator_ids_at_or_above,
    load_feasibility_registry,
    validate_feasibility_registry,
)


MEASUREMENT_PLAN_VERSION = "metric-measurement-plans-v0.2.0"
DEFAULT_FEASIBILITY_REGISTRY = (
    Path(__file__).resolve().parents[2] / "metric-feasibility-pose-wave-v2.json"
)


PRIMITIVE_CATALOG: dict[str, dict[str, Any]] = {
    "pose.body_center_kinematics": {
        "dependency": "pose",
        "implementation_status": "implemented_core",
        "feature_names": [
            "hip_center_y_body",
            "body_center_speed_body_s",
            "body_center_deceleration_body_s2",
            "hip_center_relative_to_ankle_support",
        ],
        "semantics": "2D body/hip center position, displacement, speed and acceleration using timestamp_ms",
    },
    "pose.lower_limb_angles": {
        "dependency": "pose",
        "implementation_status": "implemented_core",
        "feature_names": [
            "left_knee_flexion_deg",
            "right_knee_flexion_deg",
            "left_ankle_shank_foot_angle_deg",
            "right_ankle_shank_foot_angle_deg",
        ],
        "semantics": "2D knee angles and Halpe26 shank-forefoot ankle diagnostics",
    },
    "pose.foot_support_kinematics": {
        "dependency": "pose",
        "implementation_status": "partial_feature_expansion_required",
        "feature_names": ["stance_width_body", "hip_center_relative_to_ankle_support"],
        "semantics": "foot/ankle displacement, support geometry, step and landing proxies",
    },
    "pose.shoulder_hip_rotation": {
        "dependency": "pose",
        "implementation_status": "implemented_core",
        "feature_names": ["torso_lean_deg", "shoulder_hip_angular_velocity"],
        "semantics": "2D shoulder/hip line orientation, separation and torso lean",
    },
    "pose.arm_kinematics": {
        "dependency": "pose",
        "implementation_status": "feature_implementation_required",
        "feature_names": [],
        "semantics": "wrist/elbow position, relative distance, speed and timing",
    },
    "pose.head_kinematics": {
        "dependency": "pose",
        "implementation_status": "feature_implementation_required",
        "feature_names": [],
        "semantics": "2D head center, orientation proxy and stability; never true gaze",
    },
    "pose.support_classification": {
        "dependency": "pose",
        "implementation_status": "feature_implementation_required",
        "feature_names": [],
        "semantics": "open/closed/cross-step support geometry classification in the image plane",
    },
    "pose.action_continuity": {
        "dependency": "pose",
        "implementation_status": "partial_feature_expansion_required",
        "feature_names": ["body_center_speed_body_s", "stability_duration_ms"],
        "semantics": "trajectory continuity, direction consistency and stability duration",
    },
    "event.temporal_relation": {
        "dependency": "tracking",
        "implementation_status": "partial_current_fs01_fs02_fs09_only",
        "feature_names": [],
        "semantics": "phase timing and cross-event temporal relations",
    },
    "ball.trajectory_geometry": {
        "dependency": "ball",
        "implementation_status": "dedicated_trajectory_required",
        "feature_names": [],
        "semantics": "active-ball trajectory, height, direction and ball-body spatial relation",
    },
    "racket.keypoint_geometry": {
        "dependency": "racket",
        "implementation_status": "dedicated_keypoint_model_required",
        "feature_names": [],
        "semantics": "racket head, throat, handle, axis and face projection",
    },
    "court.metric_geometry": {
        "dependency": "court",
        "implementation_status": "metric_calibration_integration_required",
        "feature_names": [],
        "semantics": "metric court coordinates, target regions and court-line relations",
    },
    "semantic.manual_review": {
        "dependency": "pose",
        "implementation_status": "manual_semantic_review_required",
        "feature_names": [],
        "semantics": "source calculation clause is preserved but cannot be reduced to a safe geometry automatically",
    },
}


def _registry() -> dict[str, Any]:
    resource = files("rallymate_scoring").joinpath("data/metric_cards.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _calculation_clauses(calculation: str) -> list[str]:
    clauses = [value.strip() for value in re.split(r"\s*\+\s*", calculation) if value.strip()]
    return clauses or ["原指标卡未提供独立计算描述"]


def classify_clause(clause: str) -> list[str]:
    """Map a source calculation clause to conservative observation primitives.

    The mapping is intentionally many-to-many. It does not invent formulas or
    thresholds; the exact source clause remains in every compiled plan.
    """

    primitive_ids: set[str] = set()
    if any(term in clause for term in ("球拍", "拍轴", "拍面", "握把", "拍头")):
        primitive_ids.add("racket.keypoint_geometry")
    if any(
        term in clause
        for term in (
            "球轨迹",
            "来球",
            "球—身体",
            "球相对",
            "击球点",
            "接触窗口",
        )
    ):
        primitive_ids.add("ball.trajectory_geometry")
    if any(
        term in clause
        for term in (
            "场地",
            "球场",
            "场地线",
            "回位区域",
            "目标区域",
            "场地方向",
            "纵向坐标",
            "路线偏差",
        )
    ):
        primitive_ids.add("court.metric_geometry")
    if any(term in clause for term in ("头部", "头部中心", "头部角")):
        primitive_ids.add("pose.head_kinematics")
    if any(term in clause for term in ("腕", "肘", "手臂", "前臂", "上臂")):
        primitive_ids.add("pose.arm_kinematics")
    if any(term in clause for term in ("肩线", "髋线", "肩髋", "躯干")):
        primitive_ids.add("pose.shoulder_hip_rotation")
    if any(
        term in clause
        for term in ("膝角", "膝踝", "膝伸展", "双膝", "外侧膝", "支撑侧膝", "下肢伸展")
    ):
        primitive_ids.add("pose.lower_limb_angles")
    if any(
        term in clause
        for term in (
            "踝",
            "双脚",
            "脚",
            "步幅",
            "单步长度",
            "步间",
            "步数",
            "支撑区",
            "支撑宽度",
            "落地",
            "离地",
            "相对位移",
        )
    ):
        primitive_ids.add("pose.foot_support_kinematics")
    if any(
        term in clause
        for term in (
            "髋中心",
            "人体中心",
            "身体中心",
            "重心",
            "加速度",
            "减速度",
            "位移方向",
            "移动方向",
            "速度变化",
            "速度连续",
            "速度低于",
            "终点速度",
            "终点与",
        )
    ):
        primitive_ids.add("pose.body_center_kinematics")
    if any(
        term in clause
        for term in (
            "连续",
            "稳定",
            "平滑",
            "方向一致",
            "方向变化",
            "保持时长",
            "持续时间",
            "轨迹交叉",
        )
    ):
        primitive_ids.add("pose.action_continuity")
    if any(
        term in clause
        for term in (
            "事件时序",
            "时刻差",
            "时间间隔",
            "早于",
            "时序",
            "后续",
            "衔接",
            "触发条件",
        )
    ):
        primitive_ids.add("event.temporal_relation")
    if any(term in clause for term in ("支撑类型", "开放式", "闭合式", "分类置信度")):
        primitive_ids.add("pose.support_classification")
    if not primitive_ids and "人体关键点" in clause:
        primitive_ids.add("pose.action_continuity")
    if not primitive_ids:
        primitive_ids.add("semantic.manual_review")
    return sorted(primitive_ids)


def _requirement_status(primitive_ids: list[str]) -> str:
    statuses = {PRIMITIVE_CATALOG[value]["implementation_status"] for value in primitive_ids}
    if statuses.issubset({"implemented_core"}):
        return "implemented_core"
    if any(
        value in statuses
        for value in (
            "dedicated_trajectory_required",
            "dedicated_keypoint_model_required",
            "metric_calibration_integration_required",
        )
    ):
        return "dependency_implementation_required"
    if "manual_semantic_review_required" in statuses:
        return "manual_semantic_review_required"
    return "feature_implementation_required"


def _event_localization(
    event_code: str,
    *,
    current_event_codes: set[str],
) -> dict[str, Any]:
    implemented = event_code in current_event_codes
    return {
        "status": "candidate_rule_baseline" if implemented else "not_implemented",
        "event_model_version": "pose-motion-bout-v0.1.0" if implemented else None,
        "ground_truth_status": "ground_truth_required",
    }


def compile_measurement_plan(
    card: dict[str, Any],
    metric_registry_version: str,
    *,
    current_f2_indicator_ids: set[str],
    current_event_codes: set[str],
    next_pose_wave_indicator_ids: set[str],
    feasibility_registry_version: str,
) -> dict[str, Any]:
    requirements = []
    primitives: set[str] = set()
    for clause in _calculation_clauses(str(card.get("calculation", ""))):
        primitive_ids = classify_clause(clause)
        primitives.update(primitive_ids)
        requirements.append(
            {
                "source_clause": clause,
                "primitive_ids": primitive_ids,
                "implementation_status": _requirement_status(primitive_ids),
            }
        )
    event_code = str(card["eventCode"])
    dependencies = sorted(set(card.get("dependencies", [])))
    required_joint_ids = sorted(set(re.findall(r"J\d{3}", str(card.get("requiredPoints", "")))))
    blockers: set[str] = {"coach_calibration_missing", "independent_test_missing"}
    if event_code not in current_event_codes:
        blockers.update({"event_localization_not_implemented", "event_ground_truth_missing"})
    else:
        blockers.add("event_ground_truth_missing")
    for primitive_id in primitives:
        status = PRIMITIVE_CATALOG[primitive_id]["implementation_status"]
        if status == "dedicated_trajectory_required":
            blockers.add("dedicated_ball_trajectory_missing")
        elif status == "dedicated_keypoint_model_required":
            blockers.add("racket_keypoints_missing")
        elif status == "metric_calibration_integration_required":
            blockers.add("metric_court_calibration_required")
        elif status == "manual_semantic_review_required":
            blockers.add("calculation_semantics_review_required")
        elif status != "implemented_core":
            blockers.add("feature_implementation_required")
    indicator_id = str(card["id"])
    if indicator_id in current_f2_indicator_ids:
        runtime_status = "implemented_f2_calibration_required"
        blockers.discard("feature_implementation_required")
        blockers.add("feature_error_not_evaluated")
    elif any(dependency in dependencies for dependency in ("ball", "racket", "court")):
        runtime_status = "dependency_implementation_required"
    elif event_code not in current_event_codes:
        runtime_status = "event_implementation_required"
    else:
        runtime_status = "feature_implementation_required"
    return {
        "indicator_id": indicator_id,
        "indicator_name": card.get("name"),
        "domain": card["domain"],
        "event_code": event_code,
        "stage_code": card["stageCode"],
        "dependencies": dependencies,
        "required_joint_ids": required_joint_ids,
        "source_contract": {
            "required_points": card.get("requiredPoints"),
            "calculation": card.get("calculation"),
            "source_status": card.get("sourceStatus"),
        },
        "event_localization": _event_localization(
            event_code,
            current_event_codes=current_event_codes,
        ),
        "measurement_requirements": requirements,
        "primitive_ids": sorted(primitives),
        "runtime_status": runtime_status,
        "next_implementation_wave": indicator_id in next_pose_wave_indicator_ids,
        "current_blockers": sorted(blockers),
        "output_policy": {
            "when_valid_features": "calibration_required",
            "when_invalid_or_missing": "unavailable",
            "grade": None,
            "threshold_version": None,
        },
        "versions": {
            "measurement_plan": MEASUREMENT_PLAN_VERSION,
            "metric_registry": metric_registry_version,
            "feasibility_registry": feasibility_registry_version,
            "feature_library": "rallymate-features-v0.1.0",
            "event_contract": "events.1.0.0",
        },
    }


def _load_build_feasibility_registry(
    source: dict[str, Any] | str | Path | None,
) -> dict[str, Any]:
    if isinstance(source, dict):
        validate_feasibility_registry(source)
        return source
    return load_feasibility_registry(source or DEFAULT_FEASIBILITY_REGISTRY)


def _next_pose_wave_indicator_ids(
    cards: list[dict[str, Any]],
    *,
    current_f2_indicator_ids: set[str],
    current_event_codes: set[str],
) -> set[str]:
    """Derive the remaining dependency-safe wave from the canonical registry."""

    return {
        str(card["id"])
        for card in cards
        if set(card.get("dependencies", [])) == {"pose"}
        and str(card["eventCode"]) in current_event_codes
        and str(card["id"]) not in current_f2_indicator_ids
    }


def build_measurement_plan_registry(
    *,
    generated_at: str | None = None,
    feasibility_registry: dict[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    metric_registry = _registry()
    metric_version = str(metric_registry["registryVersion"])
    feasibility = _load_build_feasibility_registry(feasibility_registry)
    current_f2_indicator_ids = indicator_ids_at_or_above(feasibility, "F2")
    current_event_codes = feasibility_event_codes(feasibility)
    cards = metric_registry["cards"]
    card_ids = {str(card["id"]) for card in cards}
    unknown_f2_ids = current_f2_indicator_ids - card_ids
    if unknown_f2_ids:
        raise ValueError(
            "feasibility registry contains unknown metric indicator IDs: "
            + ", ".join(sorted(unknown_f2_ids))
        )
    next_pose_wave_indicator_ids = _next_pose_wave_indicator_ids(
        cards,
        current_f2_indicator_ids=current_f2_indicator_ids,
        current_event_codes=current_event_codes,
    )
    feasibility_version = str(feasibility["registry_version"])
    plans = [
        compile_measurement_plan(
            card,
            metric_version,
            current_f2_indicator_ids=current_f2_indicator_ids,
            current_event_codes=current_event_codes,
            next_pose_wave_indicator_ids=next_pose_wave_indicator_ids,
            feasibility_registry_version=feasibility_version,
        )
        for card in cards
    ]
    runtime_counts = Counter(plan["runtime_status"] for plan in plans)
    primitive_counts = Counter(
        primitive_id for plan in plans for primitive_id in set(plan["primitive_ids"])
    )
    return {
        "schema_version": "1.0.0",
        "registry_version": MEASUREMENT_PLAN_VERSION,
        "source_metric_registry_version": metric_version,
        "source_feasibility_registry_version": feasibility_version,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "policy": {
            "valid_measurement_states": ["measured", "calibration_required", "unavailable"],
            "grade_without_calibration": "forbidden",
            "missing_value_representation": "null_not_zero",
            "frame_coverage_is_accuracy": False,
        },
        "summary": {
            "indicator_count": len(plans),
            "runtime_status_counts": dict(runtime_counts),
            "current_f2_indicator_ids": sorted(current_f2_indicator_ids),
            "current_f2_indicator_count": len(current_f2_indicator_ids),
            "current_event_codes": sorted(current_event_codes),
            "next_pose_wave_indicator_ids": sorted(next_pose_wave_indicator_ids),
            "next_pose_wave_count": len(next_pose_wave_indicator_ids),
            "primitive_usage_counts": dict(primitive_counts.most_common()),
            "decision": (
                "Every metric card now has a traceable executable-or-blocked measurement plan. "
                "Current F2 state is derived from the versioned feasibility registry; "
                "plans never imply accuracy or A-E readiness."
            ),
        },
        "primitive_catalog": [
            {"primitive_id": primitive_id, **definition}
            for primitive_id, definition in sorted(PRIMITIVE_CATALOG.items())
        ],
        "plans": plans,
    }


def write_measurement_plan_registry(
    path: str | Path,
    *,
    generated_at: str | None = None,
    feasibility_registry: dict[str, Any] | str | Path | None = None,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_measurement_plan_registry(
                generated_at=generated_at,
                feasibility_registry=feasibility_registry,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output
