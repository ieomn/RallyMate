from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rallymate_service.training_evaluation import build_training_evaluation
from rallymate_scoring.technique_assessment import build_technique_assessment

USER_DEMO_RESULT_VERSION = "rallymate-user-demo-result-v1.2.0"
FORMATION_SCORE_VERSION = "recognizable-motion-information-v1.0.0"

_FORMATION_SCORE_WEIGHTS = {
    "recognizable_outline": 0.30,
    "measured_indicator_ratio": 0.30,
    "measurement_coverage": 0.20,
    "amplitude_type_coverage": 0.20,
}

_ANALYSIS_QUALITY_WEIGHTS = {
    "observed_action_family_ratio": 0.30,
    "measured_unique_indicator_ratio": 0.45,
    "mean_action_measurement_coverage": 0.25,
}


class UserDemoResultError(ValueError):
    """Raised when a completed run cannot be represented as a user demo result."""


_ACTION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "event_code": "FS01",
        "name_zh": "准备与分腿垫步",
        "indicator_ids": ("FS01-M02", "FS01-M03", "FS01-M04", "FS01-M05"),
        "feedback_zh": "观察双脚同步、落地支撑宽度和身体中心是否保持稳定。",
        "features": (
            (
                "bilateral_foot_rise_min_body",
                "双脚提起幅度",
                100.0,
                "% 身体尺度",
            ),
            (
                "bilateral_foot_rise_synchrony_ms",
                "双脚动作时差",
                1.0,
                "毫秒",
            ),
            ("stance_width_body", "落地支撑宽度", 100.0, "% 身体尺度"),
        ),
    },
    {
        "event_code": "FS02",
        "name_zh": "第一步启动",
        "indicator_ids": ("FS02-M02", "FS02-M03", "FS02-M04", "FS02-M05"),
        "feedback_zh": "观察启动脚位移、启动速度和身体移动方向是否连贯。",
        "features": (
            (
                "first_step_displacement_body",
                "第一步位移幅度",
                100.0,
                "% 身体尺度",
            ),
            (
                "launch_foot_speed_peak_body_s",
                "启动脚峰值速度",
                1.0,
                "身体尺度/秒",
            ),
            ("launch_foot_motion_duration_ms", "启动持续时间", 1.0, "毫秒"),
        ),
    },
    {
        "event_code": "FS09",
        "name_zh": "制动与重新稳定",
        "indicator_ids": (
            "FS09-M01",
            "FS09-M02",
            "FS09-M03",
            "FS09-M04",
            "FS09-M05",
        ),
        "feedback_zh": "观察减速幅度、屈膝吸收和重新建立稳定支撑所需时间。",
        "features": (
            (
                "hip_center_speed_drop_body_s",
                "身体中心减速幅度",
                1.0,
                "身体尺度/秒",
            ),
            (
                "left_knee_flexion_change_deg",
                "左膝屈曲变化",
                1.0,
                "度",
            ),
            ("stability_duration_ms", "稳定维持时间", 1.0, "毫秒"),
        ),
    },
)

_REGISTERED_DEMO_INDICATOR_IDS = frozenset(
    indicator_id
    for spec in _ACTION_SPECS
    for indicator_id in spec["indicator_ids"]
)


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UserDemoResultError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise UserDemoResultError(f"non-finite JSON number: {value}")


def load_indicator_feature_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
        # Accept a regular JSON export as well as the historical JSONL format.
        # JSONL remains the fallback because concatenated objects are not one
        # valid JSON document.
        try:
            document = json.loads(
                text,
                object_pairs_hook=_duplicate_rejecting_object,
                parse_constant=_reject_nonfinite_constant,
            )
        except json.JSONDecodeError:
            document = None
        if document is not None:
            if isinstance(document, list):
                candidates = document
            elif isinstance(document, dict):
                candidates = next(
                    (document[key] for key in ("records", "indicator_features", "predictions", "detections", "actions", "events") if isinstance(document.get(key), list)),
                    [document],
                )
            else:
                candidates = []
            for index, value in enumerate(candidates, start=1):
                if not isinstance(value, dict):
                    raise UserDemoResultError(f"indicator feature item {index} is not an object")
                records.append(value)
        else:
            for line_number, raw_line in enumerate(text.splitlines(), start=1):
                if not raw_line.strip():
                    continue
                value = json.loads(
                    raw_line,
                    object_pairs_hook=_duplicate_rejecting_object,
                    parse_constant=_reject_nonfinite_constant,
                )
                if not isinstance(value, dict):
                    raise UserDemoResultError(
                        f"indicator feature line {line_number} is not an object"
                    )
                records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UserDemoResultError(f"cannot read indicator features: {exc}") from exc
    if not records:
        raise UserDemoResultError("indicator features are empty")
    return records


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonnegative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _is_measured_record(record: Mapping[str, Any]) -> bool:
    feature_status = record.get("feature_status")
    if feature_status is None:
        feature_status = record.get("scoring_feature_status")
    if feature_status != "measured":
        return False
    quality_gate = record.get("quality_gate")
    return not (
        isinstance(quality_gate, Mapping)
        and quality_gate.get("measurement_allowed") is False
    )


def _friendly_amplitudes(
    records: Iterable[Mapping[str, Any]],
    event_code: str,
    allowed_indicator_ids: frozenset[str],
    feature_specs: Iterable[tuple[str, str, float, str]],
) -> list[dict[str, Any]]:
    values_by_event_and_name: dict[tuple[str, str], list[float]] = {}
    for record in records:
        if record.get("event_code") != event_code:
            continue
        if record.get("indicator_id") not in allowed_indicator_ids:
            continue
        if not _is_measured_record(record):
            continue
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            continue
        features = record.get("features")
        if not isinstance(features, list):
            features = record.get("scoring_features")
        if not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, Mapping) or feature.get("valid") is not True:
                continue
            feature_name = feature.get("feature_name")
            value = _finite_number(feature.get("value"))
            if isinstance(feature_name, str) and value is not None:
                values_by_event_and_name.setdefault(
                    (event_id, feature_name), []
                ).append(value)

    values_by_name: dict[str, list[float]] = {}
    for (_, feature_name), event_values in values_by_event_and_name.items():
        values_by_name.setdefault(feature_name, []).append(
            statistics.median(event_values)
        )

    result: list[dict[str, Any]] = []
    for feature_name, label_zh, scale, unit_zh in feature_specs:
        values = values_by_name.get(feature_name, [])
        if not values:
            continue
        median_value = statistics.median(values) * scale
        result.append(
            {
                "feature_name": feature_name,
                "label_zh": label_zh,
                "median_value": round(median_value, 1),
                "unit_zh": unit_zh,
                "sample_count": len(values),
                "semantics": "descriptive_motion_measurement_not_coaching_threshold",
            }
        )
    return result


def _bounded_percent(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return max(0, min(100, round(numerator / denominator * 100)))


def _formation_assessment(
    *,
    detected_segments: int,
    measured_indicator_count: int,
    expected_indicator_count: int,
    measurement_coverage_percent: int,
    amplitude_count: int,
    expected_amplitude_count: int,
) -> dict[str, Any]:
    components = {
        "recognizable_outline_percent": 100 if detected_segments > 0 else 0,
        "measured_indicator_ratio_percent": _bounded_percent(
            measured_indicator_count, expected_indicator_count
        ),
        "measurement_coverage_percent": measurement_coverage_percent,
        "amplitude_type_coverage_percent": _bounded_percent(
            amplitude_count, expected_amplitude_count
        ),
    }
    score = round(
        components["recognizable_outline_percent"]
        * _FORMATION_SCORE_WEIGHTS["recognizable_outline"]
        + components["measured_indicator_ratio_percent"]
        * _FORMATION_SCORE_WEIGHTS["measured_indicator_ratio"]
        + components["measurement_coverage_percent"]
        * _FORMATION_SCORE_WEIGHTS["measurement_coverage"]
        + components["amplitude_type_coverage_percent"]
        * _FORMATION_SCORE_WEIGHTS["amplitude_type_coverage"]
    )
    score = max(0, min(100, score))

    if detected_segments == 0:
        status = "not_observed"
        label_zh = "未形成可识别轮廓"
        explanation_zh = "当前视频里没有形成可展示的这一类动作轮廓。"
    elif score >= 85:
        status = "well_formed_information"
        label_zh = "轮廓与幅度信息较成型"
        explanation_zh = "动作轮廓、可测指标和幅度信息在当前视频中较完整。"
    elif score >= 60:
        status = "partially_formed_information"
        label_zh = "轮廓已形成，幅度信息待补充"
        explanation_zh = "已经找到动作轮廓，但仍有部分幅度或指标信息不完整。"
    else:
        status = "limited_information"
        label_zh = "仅形成少量可识别信息"
        explanation_zh = "只找到少量动作轮廓或幅度信息，暂不适合做完整展示。"

    return {
        "status": status,
        "label_zh": label_zh,
        "reference_score_0_to_100": score,
        "meaning_zh": (
            "只描述当前视频中可识别的动作轮廓与幅度信息成型程度，"
            "不评价动作好坏。"
        ),
        "explanation_zh": explanation_zh,
        "components": components,
        "component_weights": dict(_FORMATION_SCORE_WEIGHTS),
        "score_version": FORMATION_SCORE_VERSION,
    }


def build_user_demo_result(
    summary: Mapping[str, Any],
    indicator_feature_records: Iterable[Mapping[str, Any]],
    *,
    technique_registry_path: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        raise UserDemoResultError("summary must be an object")
    if summary.get("status") != "completed":
        raise UserDemoResultError("run summary is not completed")
    job_id = summary.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise UserDemoResultError("run summary has no job_id")

    records = [record for record in indicator_feature_records if isinstance(record, Mapping)]
    loop = summary.get("minimum_scoring_loop")
    loop = loop if isinstance(loop, Mapping) else {}
    event_counts = loop.get("event_counts")
    event_counts = event_counts if isinstance(event_counts, Mapping) else {}
    validity = loop.get("indicator_feature_validity")
    validity = validity if isinstance(validity, Mapping) else {}
    training_evaluation = build_training_evaluation(records)
    action_training_evaluations = training_evaluation.get("action_evaluations")
    action_training_evaluations = (
        action_training_evaluations
        if isinstance(action_training_evaluations, Mapping)
        else {}
    )
    actions: list[dict[str, Any]] = []
    measured_indicator_ids: set[str] = set()
    for record in records:
        indicator_id = record.get("indicator_id")
        if (
            isinstance(indicator_id, str)
            and indicator_id in _REGISTERED_DEMO_INDICATOR_IDS
            and _is_measured_record(record)
        ):
            measured_indicator_ids.add(indicator_id)

    for spec in _ACTION_SPECS:
        event_code = spec["event_code"]
        detected_segments = _nonnegative_int(event_counts.get(event_code))
        if detected_segments == 0:
            detected_segments = len(
                {
                    record.get("event_id")
                    for record in records
                    if record.get("event_code") == event_code
                    and isinstance(record.get("event_id"), str)
                }
            )
        expected_indicator_ids = frozenset(spec["indicator_ids"])
        indicator_ids = measured_indicator_ids & expected_indicator_ids
        expected_indicator_count = len(expected_indicator_ids)

        valid_instances = 0
        total_instances = 0
        for indicator_id, item in validity.items():
            if indicator_id not in expected_indicator_ids:
                continue
            if not isinstance(item, Mapping):
                continue
            valid_instances += _nonnegative_int(item.get("valid"))
            total_instances += _nonnegative_int(item.get("total"))
        if total_instances:
            coverage_percent = round(valid_instances / total_instances * 100)
        else:
            coverage_percent = round(
                len(indicator_ids) / max(expected_indicator_count, 1) * 100
            )
        coverage_percent = max(0, min(100, coverage_percent))

        if detected_segments == 0:
            status = "not_observed"
            summary_zh = "本次没有识别到这一类动作；这不等于动作一定不存在。"
        elif len(indicator_ids) == expected_indicator_count:
            status = "measured"
            summary_zh = "已找到动作片段，并形成可查看的动作幅度测量。"
        else:
            status = "partially_measured"
            summary_zh = "已找到部分动作证据，但仍有指标无法稳定测量。"

        amplitudes = _friendly_amplitudes(
            records,
            event_code,
            expected_indicator_ids,
            spec["features"],
        )
        formation_assessment = _formation_assessment(
            detected_segments=detected_segments,
            measured_indicator_count=len(indicator_ids),
            expected_indicator_count=expected_indicator_count,
            measurement_coverage_percent=coverage_percent,
            amplitude_count=len(amplitudes),
            expected_amplitude_count=len(spec["features"]),
        )

        performance_assessment = action_training_evaluations.get(event_code)
        performance_assessment = (
            dict(performance_assessment)
            if isinstance(performance_assessment, Mapping)
            else {
                "score_0_to_100": None,
                "level_zh": "暂无法评价",
                "summary_zh": "本类动作暂时没有形成可靠评价；这不代表动作做得差。",
                "evaluated_indicator_count": 0,
                "total_indicator_count": expected_indicator_count,
                "indicator_evaluations": [],
            }
        )
        indicator_evaluations = performance_assessment.pop(
            "indicator_evaluations", []
        )
        actions.append(
            {
                "event_code": event_code,
                "name_zh": spec["name_zh"],
                "status": status,
                "detected_segments": detected_segments,
                "registered_indicator_count": expected_indicator_count,
                "measured_indicator_count": len(indicator_ids),
                "measurement_coverage_percent": coverage_percent,
                "amplitudes": amplitudes,
                "formation_assessment": formation_assessment,
                "performance_assessment": performance_assessment,
                "indicator_evaluations": indicator_evaluations,
                "summary_zh": summary_zh,
                "feedback_zh": spec["feedback_zh"],
            }
        )

    observed_action_ratio = sum(
        action["detected_segments"] > 0 for action in actions
    ) / len(actions)
    indicator_ratio = len(measured_indicator_ids) / len(_REGISTERED_DEMO_INDICATOR_IDS)
    mean_measurement_coverage = sum(
        action["measurement_coverage_percent"] for action in actions
    ) / (100 * len(actions))
    analysis_completion_score = round(
        100
        * (
            0.30 * observed_action_ratio
            + 0.45 * indicator_ratio
            + 0.25 * mean_measurement_coverage
        )
    )
    analysis_completion_score = max(0, min(100, analysis_completion_score))

    action_reference_scores = {
        action["event_code"]: action["formation_assessment"][
            "reference_score_0_to_100"
        ]
        for action in actions
    }
    final_demo_score_value = round(
        sum(action_reference_scores.values()) / len(action_reference_scores)
    )
    final_demo_score_value = max(0, min(100, final_demo_score_value))

    if final_demo_score_value >= 85:
        formation_headline = "动作轮廓与幅度信息较成型"
    elif final_demo_score_value >= 60:
        formation_headline = "动作轮廓已形成，部分幅度信息待补充"
    else:
        formation_headline = "当前动作轮廓与幅度信息不足"

    if analysis_completion_score >= 85:
        completion_label = "本次识别材料较完整"
    elif analysis_completion_score >= 60:
        completion_label = "本次识别材料基本可用"
    else:
        completion_label = "本次识别材料不足"

    runtime = summary.get("runtime")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    artifacts = summary.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, Mapping) else {}
    cuda_available = runtime.get("cuda_available") is True
    device_used = str(runtime.get("device_used") or "")
    accelerator = (
        "GPU"
        if cuda_available and device_used.casefold() not in {"", "cpu", "none"}
        else "CPU"
    )
    training_score = training_evaluation.get("score_0_to_100")
    training_headline = training_evaluation.get("level_zh")
    technique_assessment = build_technique_assessment(
        summary,
        records,
        registry_path=technique_registry_path,
    )

    return {
        "schema_version": "1.2.0",
        "result_version": USER_DEMO_RESULT_VERSION,
        "result_kind": "real_video_training_feedback_preview",
        "job_id": job_id,
        "status": "ready",
        "headline_zh": (
            f"{training_headline} · 已生成 13 项训练评价"
            if training_score is not None
            else formation_headline
        ),
        "training_evaluation": training_evaluation,
        "technique_assessment": technique_assessment,
        "final_demo_score": {
            "label_zh": "动作信息成型参考分",
            "value_0_to_100": final_demo_score_value,
            "meaning_zh": (
                "只表示当前视频中可识别的动作轮廓与幅度信息成型程度，"
                "不是技术水平、教练评分、识别准确率或正式 A～E。"
            ),
            "semantics": (
                "recognizable_motion_outline_and_amplitude_information_formation_only"
            ),
            "score_version": FORMATION_SCORE_VERSION,
            "aggregation": "unweighted_mean_of_three_action_reference_scores",
            "action_reference_scores": action_reference_scores,
            "action_component_weights": dict(_FORMATION_SCORE_WEIGHTS),
            "is_formal_technique_score": False,
            "is_coach_score": False,
            "is_recognition_accuracy": False,
        },
        "analysis_quality": {
            "label_zh": "分析完成度",
            "value_0_to_100": analysis_completion_score,
            "headline_zh": completion_label,
            "meaning_zh": (
                "只表示本次视频中动作片段与可测特征的完整程度，"
                "不是球员技术水平、教练评分或正式 A～E。"
            ),
            "components": {
                "observed_action_family_ratio_percent": round(
                    observed_action_ratio * 100
                ),
                "measured_unique_indicator_ratio_percent": round(
                    indicator_ratio * 100
                ),
                "mean_action_measurement_coverage_percent": round(
                    mean_measurement_coverage * 100
                ),
            },
            "component_weights": dict(_ANALYSIS_QUALITY_WEIGHTS),
            "is_formal_technique_score": False,
        },
        "display_score": {
            "label_zh": "分析完成度",
            "value_0_to_100": analysis_completion_score,
            "meaning_zh": (
                "只表示本次视频中动作片段与可测特征的完整程度，"
                "不是球员技术水平、教练评分或正式 A～E。"
            ),
            "is_formal_technique_score": False,
            "compatibility_alias_for": "analysis_quality",
        },
        "formal_scoring": {
            "available": False,
            "score_0_to_100": None,
            "grade": None,
            "status": "calibration_required",
            "message_zh": (
                "正式教练标定分与 A～E 等级尚未启用；上方 Beta 动作表现分和自然语言建议"
                "可直接用于本次训练复盘。"
            ),
        },
        "actions": actions,
        "model": {
            "pose_preset": summary.get("models", {}).get("pose_deployment_preset")
            if isinstance(summary.get("models"), Mapping)
            else None,
            "pose_format": summary.get("models", {}).get("pose_format")
            if isinstance(summary.get("models"), Mapping)
                else None,
        },
        "runtime": {
            "accelerator": accelerator,
            "device_name": runtime.get("cuda_device_name")
            if accelerator == "GPU"
            else "CPU",
            "device_used": runtime.get("device_used"),
            "pose_backend": summary.get("models", {}).get("pose_backend", {}).get(
                "backend"
            )
            if isinstance(summary.get("models"), Mapping)
            and isinstance(summary.get("models", {}).get("pose_backend"), Mapping)
            else None,
            "pose_profile": summary.get("models", {}).get("pose_backend", {}).get(
                "profile"
            )
            if isinstance(summary.get("models"), Mapping)
            and isinstance(summary.get("models", {}).get("pose_backend"), Mapping)
            else None,
            "cpu_thread_limit": runtime.get("cpu_thread_limit"),
            "annotated_video_generated": bool(artifacts.get("annotated_video")),
            "annotated_video_compatibility": (
                dict(artifacts["annotated_video_compatibility"])
                if isinstance(artifacts.get("annotated_video_compatibility"), Mapping)
                else None
            ),
        },
        "safety": {
            "uses_real_uploaded_video_outputs": True,
            "synthetic_grade_generated": False,
            "unverified_A_to_E_generated": False,
            "measurement_completion_is_not_accuracy": True,
            "motion_amplitudes_have_no_coaching_threshold": True,
            "final_demo_score_is_formal_technique_score": False,
            "final_demo_score_is_coach_score": False,
            "final_demo_score_is_recognition_accuracy": False,
            "formation_assessment_is_action_quality_judgment": False,
            "training_evaluation_is_beta": True,
            "training_evaluation_is_formal_coach_score": False,
        },
    }
