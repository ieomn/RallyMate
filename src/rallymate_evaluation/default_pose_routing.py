from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


AUDIT_VERSION = "default-pose-routing-audit-v1.0.0"
EXPECTED_DEFAULT_PRESET = "rtmpose-m-halpe26-online"
EXPECTED_BACKEND = "rtmpose"
EXPECTED_TOPOLOGY = "halpe26"


class DefaultPoseRoutingError(ValueError):
    pass


def _indicator_counts(
    comparison: dict[str, Any], *, production_model_key: str, registry_ids: list[str]
) -> list[dict[str, Any]]:
    metrics = comparison.get("indicator_metrics", {})
    if set(metrics) != set(registry_ids):
        raise DefaultPoseRoutingError("full-video comparison indicator set differs from registry")
    models = list(comparison.get("models", {}))
    if production_model_key not in models or len(models) != 2:
        raise DefaultPoseRoutingError("full-video comparison must contain production and experimental models")
    production_is_a = models.index(production_model_key) == 0
    rows = []
    for indicator_id in registry_ids:
        metric = metrics[indicator_id]
        validity = metric.get("paired_validity", {})
        both = int(validity.get("both_measured_count", -1))
        model_a_only = int(validity.get("model_a_only_count", -1))
        model_b_only = int(validity.get("model_b_only_count", -1))
        neither = int(validity.get("neither_measured_count", -1))
        total = int(metric.get("event_pair_count", -1))
        if min(both, model_a_only, model_b_only, neither) < 0 or (
            both + model_a_only + model_b_only + neither != total
        ):
            raise DefaultPoseRoutingError("full-video paired validity accounting is inconsistent")
        measured = both + (model_a_only if production_is_a else model_b_only)
        rows.append(
            {
                "indicator_id": indicator_id,
                "instance_count": total,
                "measured_instance_count": measured,
                "unavailable_instance_count": total - measured,
                "has_real_measured_instance": measured > 0,
            }
        )
    return rows


def build_default_pose_routing_audit(
    *,
    deployment_registry: dict[str, Any],
    feasibility_registry: dict[str, Any],
    worker_smoke: dict[str, Any],
    full_video_comparison: dict[str, Any],
    full_video_scoring_summary: dict[str, Any],
    residual_computability: dict[str, Any],
    production_model_key: str,
    experimental_model_key: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    registry_ids = sorted(
        str(item["indicator_id"]) for item in feasibility_registry.get("indicators", [])
    )
    if not registry_ids or len(registry_ids) != len(set(registry_ids)):
        raise DefaultPoseRoutingError("feasibility registry indicator set is empty or duplicated")
    default_preset = str(deployment_registry.get("default_preset", ""))
    preset_by_id = {
        str(item["preset_id"]): item for item in deployment_registry.get("presets", [])
    }
    if default_preset != EXPECTED_DEFAULT_PRESET or default_preset not in preset_by_id:
        raise DefaultPoseRoutingError("RTMPose online preset is not the deployment default")
    preset = preset_by_id[default_preset]
    if preset.get("pose_backend") != EXPECTED_BACKEND or preset.get(
        "native_keypoint_format"
    ) != EXPECTED_TOPOLOGY:
        raise DefaultPoseRoutingError("deployment default does not resolve to RTMPose Halpe26")
    rollback = preset_by_id.get("yolo-baseline")
    if not rollback or rollback.get("pose_backend") != "yolo":
        raise DefaultPoseRoutingError("explicit YOLO rollback preset is missing")

    smoke_registry = worker_smoke.get("feasibility_registry", {})
    smoke_backend = worker_smoke.get("pose_backend", {})
    smoke_ids = sorted(str(value) for value in worker_smoke.get("indicator_ids", []))
    if worker_smoke.get("status") != "passed" or worker_smoke.get(
        "preset_source"
    ) != "deployment_registry_default":
        raise DefaultPoseRoutingError("worker smoke did not exercise the registry default")
    if worker_smoke.get("preset") != default_preset:
        raise DefaultPoseRoutingError("worker smoke preset differs from deployment default")
    if smoke_backend.get("backend") != EXPECTED_BACKEND or smoke_backend.get(
        "native_keypoint_format"
    ) != EXPECTED_TOPOLOGY:
        raise DefaultPoseRoutingError("worker smoke did not execute RTMPose Halpe26")
    if smoke_ids != registry_ids or sorted(smoke_registry.get("indicator_ids", [])) != registry_ids:
        raise DefaultPoseRoutingError("worker smoke indicator set differs from registry")
    if int(worker_smoke.get("non_null_grade_count", -1)) != 0 or int(
        worker_smoke.get("non_null_threshold_version_count", -1)
    ) != 0:
        raise DefaultPoseRoutingError("uncalibrated worker smoke contains a grade or threshold")
    if worker_smoke.get("bundle_validation", {}).get("status") != "passed":
        raise DefaultPoseRoutingError("worker smoke bundle validation did not pass")

    models = full_video_comparison.get("models", {})
    if production_model_key not in models or experimental_model_key not in models:
        raise DefaultPoseRoutingError("full-video comparison model key is missing")
    production = models[production_model_key]
    experimental = models[experimental_model_key]
    smoke_sha = str(smoke_backend.get("model_sha256", "")).upper()
    if smoke_sha != str(production.get("model_sha256", "")).upper() or smoke_sha != str(
        experimental.get("model_sha256", "")
    ).upper():
        raise DefaultPoseRoutingError("default smoke and full-video evidence use different weights")
    if production.get("pose_backend") != EXPECTED_BACKEND or EXPECTED_TOPOLOGY not in production.get(
        "native_keypoint_formats_observed", []
    ):
        raise DefaultPoseRoutingError("production full-video evidence is not RTMPose Halpe26")
    per_indicator = _indicator_counts(
        full_video_comparison,
        production_model_key=production_model_key,
        registry_ids=registry_ids,
    )
    production_measured = sum(item["measured_instance_count"] for item in per_indicator)
    production_total = sum(item["instance_count"] for item in per_indicator)
    if production_measured != int(production.get("measured_indicator_event_count", -1)):
        raise DefaultPoseRoutingError("production full-video measured count is inconsistent")
    if production_total != int(production.get("indicator_event_pair_count", -1)):
        raise DefaultPoseRoutingError("production full-video instance count is inconsistent")
    indicators_without_measurement = [
        item["indicator_id"] for item in per_indicator if not item["has_real_measured_instance"]
    ]

    summary_models = full_video_scoring_summary.get("model_versions", {})
    if str(summary_models.get("pose_model_sha256", "")).upper() != smoke_sha:
        raise DefaultPoseRoutingError("full scoring loop uses different Pose weights")
    if summary_models.get("pose_backend") != EXPECTED_BACKEND or summary_models.get(
        "native_keypoint_format"
    ) != EXPECTED_TOPOLOGY:
        raise DefaultPoseRoutingError("full scoring loop is not RTMPose Halpe26")
    if full_video_scoring_summary.get("grade_counts"):
        raise DefaultPoseRoutingError("uncalibrated full scoring loop contains grades")
    full_score_counts = {
        str(key): int(value)
        for key, value in full_video_scoring_summary.get("score_status_counts", {}).items()
    }
    if set(full_score_counts) - {"calibration_required", "unavailable"}:
        raise DefaultPoseRoutingError("full scoring loop contains an unsafe score status")

    residual_ids = sorted(
        str(item["indicator_id"]) for item in residual_computability.get("per_indicator", [])
    )
    if residual_ids != registry_ids or residual_computability.get("safety", {}).get(
        "accuracy_claim"
    ) is not False:
        raise DefaultPoseRoutingError("experimental computability evidence is unsafe or incomplete")
    experimental_counts = residual_computability.get("counts", {})
    if int(experimental_counts.get("indicator_with_measured_instance_count", -1)) != len(
        registry_ids
    ):
        raise DefaultPoseRoutingError("experimental computability evidence does not cover every indicator")

    report = {
        "schema_version": "1.0.0",
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "default_rtmpose_F2_measurement_route_verified_no_formal_grade",
        "sources": sources,
        "scope": {
            "feasibility_registry_version": feasibility_registry.get("registry_version"),
            "indicator_ids": registry_ids,
            "indicator_count": len(registry_ids),
            "event_codes": sorted(
                {
                    str(event).split(".", 1)[0]
                    for item in feasibility_registry["indicators"]
                    for event in item.get("required_events", [])
                }
            ),
            "maturity_levels": sorted(
                {str(item["feasibility_level"]) for item in feasibility_registry["indicators"]}
            ),
        },
        "deployment_default": {
            "registry_version": deployment_registry.get("registry_version"),
            "preset_id": default_preset,
            "role": preset.get("role"),
            "pose_backend": preset.get("pose_backend"),
            "pose_profile": preset.get("pose_profile"),
            "native_keypoint_format": preset.get("native_keypoint_format"),
            "input_size_hw": list(preset.get("input_size_hw", [])),
            "promotion_status": preset.get("promotion_status"),
            "yolo_rollback_preset_id": "yolo-baseline",
        },
        "real_worker_default_smoke": {
            "job_id": worker_smoke.get("job_id"),
            "execution_path": worker_smoke.get("execution_path"),
            "preset_id": worker_smoke.get("preset"),
            "preset_source": worker_smoke.get("preset_source"),
            "pose_backend": smoke_backend.get("backend"),
            "pose_profile": smoke_backend.get("profile"),
            "model_name": smoke_backend.get("model_name"),
            "model_sha256": smoke_sha,
            "native_keypoint_format": smoke_backend.get("native_keypoint_format"),
            "native_keypoint_count": int(smoke_backend.get("native_keypoint_count", 0)),
            "processed_frames": int(worker_smoke.get("processed_frames", 0)),
            "elapsed_seconds": float(worker_smoke.get("elapsed_seconds", 0.0)),
            "effective_processed_fps": float(worker_smoke.get("effective_processed_fps", 0.0)),
            "event_counts": {
                str(key): int(value) for key, value in worker_smoke.get("event_counts", {}).items()
            },
            "indicator_record_count": sum(
                int(value) for value in worker_smoke.get("score_status_counts", {}).values()
            ),
            "score_status_counts": {
                str(key): int(value)
                for key, value in worker_smoke.get("score_status_counts", {}).items()
            },
            "non_null_grade_count": 0,
            "non_null_threshold_version_count": 0,
            "bundle_validation_status": "passed",
        },
        "full_video_production_measurement": {
            "model_key": production_model_key,
            "model_sha256": smoke_sha,
            "native_keypoint_format": EXPECTED_TOPOLOGY,
            "candidate_event_count": sum(
                int(value) for value in full_video_scoring_summary.get("event_counts", {}).values()
            ),
            "indicator_event_instance_count": production_total,
            "measured_indicator_event_instance_count": production_measured,
            "unavailable_indicator_event_instance_count": production_total - production_measured,
            "indicator_with_measured_instance_count": len(registry_ids)
            - len(indicators_without_measurement),
            "indicators_without_measured_instances": indicators_without_measurement,
            "per_indicator": per_indicator,
            "score_status_counts": full_score_counts,
            "grade_counts": {},
            "fixed_boundaries_are_ground_truth": False,
        },
        "experimental_small_roi_measurement": {
            "model_key": experimental_model_key,
            "production_default": False,
            "indicator_event_instance_count": int(
                experimental_counts.get("indicator_event_instance_count", 0)
            ),
            "measured_indicator_event_instance_count": int(
                experimental_counts.get("measured_indicator_event_instance_count", 0)
            ),
            "unavailable_indicator_event_instance_count": int(
                experimental_counts.get("unavailable_indicator_event_instance_count", 0)
            ),
            "indicator_with_measured_instance_count": int(
                experimental_counts.get("indicator_with_measured_instance_count", 0)
            ),
        },
        "assertions": {
            "service_default_is_rtmpose_halpe26": True,
            "worker_smoke_used_registry_default_without_preset_override": True,
            "worker_smoke_matches_default_model_weights": True,
            "worker_smoke_indicator_set_matches_current_registry": True,
            "production_full_video_has_measured_instance_for_every_indicator": not indicators_without_measurement,
            "experimental_small_roi_is_not_production_default": True,
            "yolo_remains_explicit_rollback": True,
            "formal_grade_remains_blocked": True,
        },
        "interpretation": {
            "verified": "the normal service default now routes Pose inference through RTMPose-M Halpe26, and the same weights have real full-video measured feature instances for every current registry indicator",
            "not_verified": "event accuracy, feature truth accuracy, grade separation, coach calibration, independent-test performance, or F4 scoring readiness",
        },
        "safety": {
            "pose_default_changed_from_yolo_to_rtmpose": True,
            "scoring_maturity_changed": False,
            "production_min_roi_changed": False,
            "experimental_profile_promoted": False,
            "accuracy_claim": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_default_pose_routing_audit(report)
    return report


def validate_default_pose_routing_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("audit_version") != AUDIT_VERSION:
        raise DefaultPoseRoutingError("unsupported default Pose routing audit")
    if report.get("status") != "default_rtmpose_F2_measurement_route_verified_no_formal_grade":
        raise DefaultPoseRoutingError("default Pose route is not verified")
    default = report.get("deployment_default", {})
    if default.get("preset_id") != EXPECTED_DEFAULT_PRESET or default.get(
        "pose_backend"
    ) != EXPECTED_BACKEND or default.get("native_keypoint_format") != EXPECTED_TOPOLOGY:
        raise DefaultPoseRoutingError("audit does not bind the RTMPose Halpe26 default")
    sources = report.get("sources", {})
    if len(sources) < 6:
        raise DefaultPoseRoutingError("default-route source lineage is incomplete")
    for source in sources.values():
        sha = str(source.get("sha256", ""))
        if not source.get("path") or len(sha) != 64 or any(
            value not in "0123456789ABCDEF" for value in sha
        ):
            raise DefaultPoseRoutingError("default-route source lineage is malformed")
    smoke = report.get("real_worker_default_smoke", {})
    if smoke.get("preset_source") != "deployment_registry_default" or smoke.get(
        "bundle_validation_status"
    ) != "passed":
        raise DefaultPoseRoutingError("audit did not exercise the real default Worker route")
    if smoke.get("preset_id") != default.get("preset_id") or smoke.get(
        "pose_backend"
    ) != EXPECTED_BACKEND or smoke.get("native_keypoint_format") != EXPECTED_TOPOLOGY or int(
        smoke.get("native_keypoint_count", -1)
    ) != 26:
        raise DefaultPoseRoutingError("default Worker runtime differs from the declared default")
    if int(smoke.get("non_null_grade_count", -1)) != 0 or int(
        smoke.get("non_null_threshold_version_count", -1)
    ) != 0:
        raise DefaultPoseRoutingError("audit contains a grade or threshold")
    full = report.get("full_video_production_measurement", {})
    if full.get("native_keypoint_format") != EXPECTED_TOPOLOGY or str(
        full.get("model_sha256", "")
    ).upper() != str(smoke.get("model_sha256", "")).upper():
        raise DefaultPoseRoutingError("default Worker and full-video model provenance differ")
    total = int(full.get("indicator_event_instance_count", -1))
    measured = int(full.get("measured_indicator_event_instance_count", -1))
    unavailable = int(full.get("unavailable_indicator_event_instance_count", -1))
    if total <= 0 or measured + unavailable != total:
        raise DefaultPoseRoutingError("full-video measurement accounting is inconsistent")
    rows = full.get("per_indicator", [])
    indicator_count = int(report.get("scope", {}).get("indicator_count", -1))
    scope_ids = sorted(str(value) for value in report.get("scope", {}).get("indicator_ids", []))
    row_ids = sorted(str(item.get("indicator_id")) for item in rows)
    if len(scope_ids) != len(set(scope_ids)) or scope_ids != row_ids:
        raise DefaultPoseRoutingError("default-route per-indicator membership is inconsistent")
    if len(rows) != indicator_count or int(
        full.get("indicator_with_measured_instance_count", -1)
    ) != indicator_count or full.get("indicators_without_measured_instances"):
        raise DefaultPoseRoutingError("not every registry indicator has a measured instance")
    if any(
        not item.get("has_real_measured_instance")
        or int(item.get("measured_instance_count", 0)) <= 0
        for item in rows
    ):
        raise DefaultPoseRoutingError("per-indicator measured coverage is incomplete")
    if full.get("grade_counts") or set(full.get("score_status_counts", {})) - {
        "calibration_required",
        "unavailable",
    }:
        raise DefaultPoseRoutingError("full-video scoring status overstates calibration readiness")
    assertions = report.get("assertions", {})
    if not assertions or any(value is not True for value in assertions.values()):
        raise DefaultPoseRoutingError("a required default-route assertion failed")
    safety = report.get("safety", {})
    if safety.get("pose_default_changed_from_yolo_to_rtmpose") is not True:
        raise DefaultPoseRoutingError("default model replacement is not declared")
    for field in (
        "scoring_maturity_changed",
        "production_min_roi_changed",
        "experimental_profile_promoted",
        "accuracy_claim",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise DefaultPoseRoutingError(f"unsafe default-route claim: {field}")
