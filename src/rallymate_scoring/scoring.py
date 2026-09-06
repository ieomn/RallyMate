from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from rallymate_scoring.calibration import (
    GRADES,
    calibration_scoring_readiness,
    validate_ordinal_model,
    validate_threshold_calibration,
)
from rallymate_scoring.blocker_taxonomy import scoring_block_details
from rallymate_scoring.quality_policy import (
    evaluate_indicator_event_quality,
)
from rallymate_scoring.calibration_promotion import (
    TrustedProductionCalibration,
    is_trusted_production_calibration,
)
from rallymate_scoring.runtime_profile_binding import (
    RuntimeProfileBoundProductionCalibration,
    is_runtime_profile_bound_calibration,
    runtime_profile_versions,
)


def _base_result(
    *,
    status: str,
    grade: str | None,
    confidence: float,
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
        "confidence": round(max(0.0, min(1.0, confidence)), 6),
        "feature": feature,
        "threshold_version": threshold_version,
        "model_versions": model_versions,
        "evidence": evidence,
        "reason_codes": reason_codes,
        "feedback": feedback,
        "quality_gate": quality_gate,
    }


def _trusted_registry_versions(
    calibration: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(
        calibration,
        (TrustedProductionCalibration, RuntimeProfileBoundProductionCalibration),
    ):
        return {}
    authorization = calibration.authorization
    if authorization.get("runtime_registry_binding_verified") is not True:
        return {}
    return {
        "promoted_feasibility_registry": str(
            authorization["promoted_registry_version"]
        ),
        "promoted_feasibility_registry_sha256": str(
            authorization["promoted_registry_content_sha256"]
        ),
        "runtime_feasibility_registry": str(
            authorization["runtime_registry_version"]
        ),
        "runtime_feasibility_registry_sha256": str(
            authorization["runtime_registry_content_sha256"]
        ),
        "maturity_evidence_bundle": str(
            authorization["maturity_evidence_bundle_id"]
        ),
        "maturity_evidence_sha256": str(
            authorization["maturity_evidence_content_sha256"]
        ),
        "maturity_evidence_final_transition_sha256": str(
            authorization["maturity_evidence_final_transition_sha256"]
        ),
    }


def _production_authorization(
    calibration: Mapping[str, Any],
) -> dict[str, Any] | None:
    if isinstance(
        calibration,
        (TrustedProductionCalibration, RuntimeProfileBoundProductionCalibration),
    ):
        return calibration.authorization
    return None


def _runtime_profile_mismatch_reasons(
    calibration: Mapping[str, Any],
    indicator_id: str,
    model_versions: dict[str, str | None],
    evidence: list[dict[str, Any]],
) -> list[str]:
    if not isinstance(calibration, RuntimeProfileBoundProductionCalibration):
        return ["runtime_profile_binding_required"]
    authorization = calibration.authorization
    if authorization.get("runtime_profile_binding_verified") is not True:
        return ["runtime_profile_binding_required"]
    pose = authorization.get("runtime_profile_pose")
    pipeline = authorization.get("runtime_profile_pipeline")
    if not isinstance(pose, dict) or not isinstance(pipeline, dict):
        return ["runtime_profile_authorization_invalid"]
    pose_fields = {
        "backend": "pose_backend",
        "runtime": "pose_runtime",
        "profile": "pose_profile",
        "model_sha256": "pose_model_sha256",
        "native_keypoint_format": "native_keypoint_format",
        "native_keypoint_count": "native_keypoint_count",
        "keypoint_schema_version": "keypoint_schema_version",
    }
    for profile_field, runtime_field in pose_fields.items():
        actual = model_versions.get(runtime_field)
        if actual != pose.get(profile_field):
            return [f"runtime_pose_profile_mismatch:{runtime_field}"]
    pipeline_fields = {
        "indicator_definition": "indicator_definition",
        "event_contract": "event_contract",
        "event_detector": "event",
        "phase_contract": "phase_contract",
        "primary_player": "primary_player",
        "quality_policy": "quality_policy",
        "feature_contract": "feature_contract",
    }
    for profile_field, runtime_field in pipeline_fields.items():
        actual = model_versions.get(runtime_field)
        if isinstance(actual, Mapping):
            actual = actual.get(indicator_id)
        if actual != pipeline.get(profile_field):
            return [f"runtime_pipeline_profile_mismatch:{runtime_field}"]

    expected_video_id = authorization.get("runtime_view_video_id")
    expected_video_sha = authorization.get("runtime_view_video_sha256")
    if not evidence:
        return ["runtime_view_evidence_trace_missing"]
    for item in evidence:
        if not isinstance(item, dict) or item.get("video_id") != expected_video_id:
            return ["runtime_view_video_mismatch"]
        supplied_sha = item.get("video_sha256")
        if not isinstance(supplied_sha, str) or not supplied_sha:
            return ["runtime_view_video_sha256_missing"]
        if supplied_sha.lower() != expected_video_sha:
            return ["runtime_view_video_sha256_mismatch"]
    return []


def _scoring_block_explanation(flags: list[str]) -> tuple[list[str], str]:
    """Return typed reasons plus feedback for a score-only quality block."""

    reasons, messages = scoring_block_details(flags)
    return reasons, (
        "；".join(messages)
        + "；特征可保留用于 F2 审计，但当前事件禁止输出 A～E。"
    )


def _append_concurrent_scoring_blocks(
    *,
    reason_codes: list[str],
    feedback: str,
    quality_gate: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Preserve score-only blocks when an earlier unavailable cause wins."""

    block_reasons, messages = scoring_block_details(
        list(quality_gate.get("scoring_block_flags", []))
    )
    if not block_reasons:
        return reason_codes, feedback
    merged = list(dict.fromkeys([*reason_codes, *block_reasons]))
    suffix = "；另外，" + "；".join(messages) + "。"
    return merged, feedback.rstrip("。") + suffix


def score_indicator(
    *,
    indicator_id: str,
    features: list[dict[str, Any]],
    calibration: Mapping[str, Any] | None,
    model_versions: dict[str, str | None],
    evidence: list[dict[str, Any]],
    allow_test_only: bool = False,
    feasibility_level: str | None = None,
    event_quality_flags: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    quality_gate = evaluate_indicator_event_quality(
        indicator_id, event_quality_flags
    )
    if quality_gate["hard_fail"]:
        reason_codes, feedback = _append_concurrent_scoring_blocks(
            reason_codes=[
                "event_quality_hard_fail",
                *quality_gate["hard_fail_flags"],
            ],
            feedback="事件或主球员时序质量未通过门禁，当前指标不可用。",
            quality_gate=quality_gate,
        )
        return _base_result(
            status="unavailable",
            grade=None,
            confidence=0.0,
            feature={"items": features},
            threshold_version=None,
            model_versions=model_versions,
            evidence=evidence,
            reason_codes=reason_codes,
            feedback=feedback,
            quality_gate=quality_gate,
        )
    feature_names = [item.get("feature_name") for item in features]
    if any(not isinstance(name, str) or not name for name in feature_names):
        raise ValueError("scoring features require non-empty feature_name")
    if len(feature_names) != len(set(feature_names)):
        raise ValueError("scoring feature_name values must be unique")
    feature_by_name = {item["feature_name"]: item for item in features}
    invalid = [item for item in features if not item.get("valid")]
    if invalid:
        reason_codes, feedback = _append_concurrent_scoring_blocks(
            reason_codes=["required_feature_unavailable"],
            feedback="证据质量不足，当前指标不可用。",
            quality_gate=quality_gate,
        )
        return _base_result(
            status="unavailable",
            grade=None,
            confidence=0.0,
            feature={"items": features},
            threshold_version=None,
            model_versions=model_versions,
            evidence=evidence,
            reason_codes=reason_codes,
            feedback=feedback,
            quality_gate=quality_gate,
        )
    if not quality_gate["scoring_allowed"]:
        block_reasons, block_feedback = _scoring_block_explanation(
            list(quality_gate.get("scoring_block_flags", []))
        )
        return _base_result(
            status="unavailable",
            grade=None,
            confidence=min(
                (float(item.get("confidence", 0.0)) for item in features),
                default=0.0,
            ),
            feature={"items": features},
            threshold_version=None,
            model_versions=model_versions,
            evidence=evidence,
            reason_codes=block_reasons,
            feedback=block_feedback,
            quality_gate=quality_gate,
        )
    if calibration is None:
        return _base_result(
            status="calibration_required",
            grade=None,
            confidence=min((float(item.get("confidence", 0.0)) for item in features), default=0.0),
            feature={"items": features},
            threshold_version=None,
            model_versions=model_versions,
            evidence=evidence,
            reason_codes=["coach_calibration_missing", "independent_test_missing"],
            feedback="特征已测量，但缺少教练真值标定，不能输出 A～E。",
            quality_gate=quality_gate,
        )
    if calibration.get("indicator_id") != indicator_id:
        raise ValueError("calibration indicator_id does not match scoring request")
    if calibration.get("artifact_scope") == "production":
        authorization = _production_authorization(calibration)
    else:
        authorization = None
    if authorization is not None:
        registry_binding_valid = (
            authorization.get("runtime_registry_binding_verified") is True
            and authorization.get("runtime_registry_indicator_id") == indicator_id
            and authorization.get("runtime_registry_indicator_level") == "F4"
            and authorization.get("runtime_registry_version")
            == authorization.get("promoted_registry_version")
            and authorization.get("runtime_registry_content_sha256")
            == authorization.get("promoted_registry_content_sha256")
        )
        if not registry_binding_valid:
            candidate_version = calibration.get("threshold_version") or calibration.get(
                "model_version"
            )
            return _base_result(
                status="calibration_required",
                grade=None,
                confidence=min(
                    (float(item.get("confidence", 0.0)) for item in features),
                    default=0.0,
                ),
                feature={"items": features},
                threshold_version=None,
                model_versions={
                    **model_versions,
                    "calibration_candidate": candidate_version,
                    "indicator_feasibility_level": feasibility_level,
                },
                evidence=evidence,
                reason_codes=[
                    "runtime_feasibility_registry_binding_required",
                    "production_calibration_not_runtime_registry_authorized",
                ],
                feedback=(
                    "生产标定资产未与当前 F4 可行性注册表快照精确绑定，"
                    "不能输出 A～E。"
                ),
                quality_gate=quality_gate,
            )
        required_features = authorization.get("runtime_registry_required_features")
        if feature_names != required_features:
            return _base_result(
                status="unavailable",
                grade=None,
                confidence=0.0,
                feature={"items": features},
                threshold_version=None,
                model_versions={
                    **model_versions,
                    "indicator_feasibility_level": feasibility_level,
                    **_trusted_registry_versions(calibration),
                },
                evidence=evidence,
                reason_codes=[
                    "required_feature_contract_mismatch",
                    "promoted_registry_feature_set_required",
                ],
                feedback=(
                    "本次事件特征集合与该生产标定资产晋级时绑定的指标契约"
                    "不一致，禁止输出 A～E。"
                ),
                quality_gate=quality_gate,
            )
        runtime_mismatches = _runtime_profile_mismatch_reasons(
            calibration,
            indicator_id,
            model_versions,
            evidence,
        )
        if runtime_mismatches:
            candidate_version = calibration.get("threshold_version") or calibration.get(
                "model_version"
            )
            return _base_result(
                status="calibration_required",
                grade=None,
                confidence=min(
                    (float(item.get("confidence", 0.0)) for item in features),
                    default=0.0,
                ),
                feature={"items": features},
                threshold_version=None,
                model_versions={
                    **model_versions,
                    "calibration_candidate": candidate_version,
                    "indicator_feasibility_level": feasibility_level,
                    **_trusted_registry_versions(calibration),
                    **runtime_profile_versions(calibration),
                },
                evidence=evidence,
                reason_codes=[
                    *runtime_mismatches,
                    "production_calibration_not_runtime_profile_authorized",
                ],
                feedback=(
                    "生产标定资产未与当前姿态模型、事件/Track/质量版本及"
                    "已核验视角精确绑定，不能输出 A～E。"
                ),
                quality_gate=quality_gate,
            )
    if calibration["artifact_scope"] == "production" and feasibility_level != "F4":
        candidate_version = calibration.get("threshold_version") or calibration.get(
            "model_version"
        )
        return _base_result(
            status="calibration_required",
            grade=None,
            confidence=min(
                (float(item.get("confidence", 0.0)) for item in features),
                default=0.0,
            ),
            feature={"items": features},
            threshold_version=None,
            model_versions={
                **model_versions,
                "calibration_candidate": candidate_version,
                "indicator_feasibility_level": feasibility_level,
            },
            evidence=evidence,
            reason_codes=["feasibility_F4_required", "calibration_candidate_not_promoted"],
            feedback="指标尚未逐级晋级到 F4，不能输出正式 A～E。",
            quality_gate=quality_gate,
        )
    scoring_ready, promotion_reason = calibration_scoring_readiness(
        calibration, allow_test_only=allow_test_only
    )
    if not scoring_ready:
        candidate_version = calibration.get("threshold_version") or calibration.get(
            "model_version"
        )
        independent_test = calibration["independent_test"]
        return _base_result(
            status="calibration_required",
            grade=None,
            confidence=min(
                (float(item.get("confidence", 0.0)) for item in features),
                default=0.0,
            ),
            feature={"items": features},
            threshold_version=None,
            model_versions={
                **model_versions,
                "calibration_candidate": candidate_version,
                "independent_test_report": independent_test.get("report_version"),
            },
            evidence=evidence,
            reason_codes=[promotion_reason, "calibration_candidate_not_promoted"],
            feedback="标定资产尚未通过独立测试与正式晋级门禁，不能输出 A～E。",
            quality_gate=quality_gate,
        )
    if (
        calibration.get("artifact_scope") == "production"
        and not (
            is_trusted_production_calibration(calibration)
            or is_runtime_profile_bound_calibration(calibration)
        )
    ):
        candidate_version = calibration.get("threshold_version") or calibration.get(
            "model_version"
        )
        return _base_result(
            status="calibration_required",
            grade=None,
            confidence=min(
                (float(item.get("confidence", 0.0)) for item in features),
                default=0.0,
            ),
            feature={"items": features},
            threshold_version=None,
            model_versions={
                **model_versions,
                "calibration_candidate": candidate_version,
                "indicator_feasibility_level": feasibility_level,
            },
            evidence=evidence,
            reason_codes=[
                "trusted_promotion_ledger_verification_required",
                "production_calibration_not_runtime_authorized",
            ],
            feedback=(
                "生产标定资产尚未由受信晋级账本加载验证，不能输出 A～E。"
            ),
            quality_gate=quality_gate,
        )
    backend = calibration.get("backend")
    if backend == "threshold_rule":
        validate_threshold_calibration(calibration)
    elif backend == "ordinal_regression":
        validate_ordinal_model(calibration)
    else:
        raise ValueError(f"unsupported calibration backend: {backend!r}")
    if backend == "threshold_rule":
        feature_name = calibration["primary_feature"]
        if feature_name not in feature_by_name:
            raise ValueError(f"calibration requires missing feature {feature_name}")
        item = feature_by_name[feature_name]
        if item.get("unit") != calibration["unit"]:
            raise ValueError("calibration unit does not match feature unit")
        if item.get("feature_version") != calibration["primary_feature_version"]:
            raise ValueError(
                "calibration primary_feature_version does not match feature version"
            )
        value = float(item["value"])
        thresholds = [float(value) for value in calibration["thresholds"]]
        ascending_index = sum(value >= threshold for threshold in thresholds)
        if calibration["direction"] == "higher_is_better":
            grade = GRADES[ascending_index]
        else:
            grade = GRADES[4 - ascending_index]
        return _base_result(
            status="scored",
            grade=grade,
            confidence=float(item.get("confidence", 0.0)),
            feature=item,
            threshold_version=calibration["threshold_version"],
            model_versions={
                **model_versions,
                "calibration": calibration["threshold_version"],
                "independent_test_report": calibration["independent_test"].get(
                    "report_version"
                ),
                "indicator_feasibility_level": feasibility_level,
                **_trusted_registry_versions(calibration),
                **runtime_profile_versions(calibration),
                **(
                    {
                        "trusted_promotion_ledger": calibration.authorization[
                            "ledger_version"
                        ],
                        "trusted_promotion_entry": calibration.authorization[
                            "entry_id"
                        ],
                    }
                    if isinstance(
                        calibration,
                        (
                            TrustedProductionCalibration,
                            RuntimeProfileBoundProductionCalibration,
                        ),
                    )
                    else {}
                ),
            },
            evidence=evidence,
            reason_codes=["coach_calibrated_threshold_rule", promotion_reason],
            feedback=str(calibration.get("feedback_by_grade", {}).get(grade, "")),
            quality_gate=quality_gate,
        )
    if backend == "ordinal_regression":
        values = []
        confidences = []
        selected_features = []
        for name in calibration["feature_order"]:
            if name not in feature_by_name:
                raise ValueError(f"ordinal model requires missing feature {name}")
            item = feature_by_name[name]
            if item.get("unit") != calibration["unit_by_feature"][name]:
                raise ValueError(
                    f"ordinal model unit does not match feature unit for {name}"
                )
            if (
                item.get("feature_version")
                != calibration["feature_version_by_feature"][name]
            ):
                raise ValueError(
                    f"ordinal model feature version does not match {name}"
                )
            values.append(float(item["value"]))
            confidences.append(float(item.get("confidence", 0.0)))
            selected_features.append(item)
        eta = float(calibration.get("intercept", 0.0)) + float(
            np.dot(np.asarray(values), np.asarray(calibration["coefficients"]))
        )
        cumulative = [
            1.0 / (1.0 + math.exp(-(float(cutpoint) - eta)))
            for cutpoint in calibration["cutpoints"]
        ]
        probabilities = [
            cumulative[0],
            cumulative[1] - cumulative[0],
            cumulative[2] - cumulative[1],
            cumulative[3] - cumulative[2],
            1.0 - cumulative[3],
        ]
        grade_index = int(np.argmax(probabilities))
        grade = GRADES[grade_index]
        return _base_result(
            status="scored",
            grade=grade,
            confidence=float(max(probabilities)) * min(confidences, default=0.0),
            feature={
                "items": selected_features,
                "grade_probabilities": {
                    name: round(float(value), 8)
                    for name, value in zip(GRADES, probabilities)
                },
            },
            threshold_version=None,
            model_versions={
                **model_versions,
                "calibration": calibration["model_version"],
                "independent_test_report": calibration["independent_test"].get(
                    "report_version"
                ),
                "indicator_feasibility_level": feasibility_level,
                **_trusted_registry_versions(calibration),
                **runtime_profile_versions(calibration),
                **(
                    {
                        "trusted_promotion_ledger": calibration.authorization[
                            "ledger_version"
                        ],
                        "trusted_promotion_entry": calibration.authorization[
                            "entry_id"
                        ],
                    }
                    if isinstance(
                        calibration,
                        (
                            TrustedProductionCalibration,
                            RuntimeProfileBoundProductionCalibration,
                        ),
                    )
                    else {}
                ),
            },
            evidence=evidence,
            reason_codes=["coach_calibrated_ordinal_regression", promotion_reason],
            feedback=str(calibration.get("feedback_by_grade", {}).get(grade, "")),
            quality_gate=quality_gate,
        )
    raise AssertionError("validated calibration backend was not handled")
