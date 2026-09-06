from __future__ import annotations

import html
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from rallymate_evaluation.default_pose_routing import (
    validate_default_pose_routing_audit,
)
from rallymate_evaluation.multivideo_measurement_recovery import (
    validate_multivideo_measurement_recovery_report,
)
from rallymate_evaluation.f2_error_budget_readiness import (
    validate_f2_error_budget_readiness_sources,
)
from rallymate_evaluation.smoothing_coverage import (
    validate_smoothing_counterfactual_coverage_sources,
)
from rallymate_evaluation.pose_diagnostic_review import (
    validate_pose_diagnostic_review_queue_sources,
)
from rallymate_evaluation.pose_diagnostic_truth import (
    validate_pose_diagnostic_evaluation,
    validate_pose_diagnostic_truth_pack_sources,
)
from rallymate_evaluation.event_bounded_pose_gaps import (
    validate_event_bounded_pose_gap_report_sources,
)
from rallymate_evaluation.event_gap_keypoint_truth import (
    evaluate_event_gap_keypoints,
    validate_event_gap_truth_manifest,
)
from rallymate_evaluation.event_gap_feature_truth import (
    validate_event_gap_feature_truth_report_sources,
)
from rallymate_evaluation.fs09_phase_truth import (
    validate_fs09_phase_truth_manifest,
    validate_fs09_phase_truth_report_sources,
)
from rallymate_evaluation.pose_diagnostic_clip_truth import (
    validate_clip_truth_manifest_sources,
    validate_empty_clip_truth_report,
)
from rallymate_scoring.calculation_readiness import (
    validate_indicator_calculation_readiness,
)
from rallymate_scoring.measurement_portfolio import (
    validate_indicator_measurement_portfolio,
)
from rallymate_scoring.cycle_measurement import validate_scoring_cycle_measurement
from rallymate_scoring.scoring_context import validate_scoring_reference_context
from rallymate_annotation.scoring_action_readiness import (
    validate_scoring_truth_action_readiness,
)
from rallymate_annotation.scoring_evidence_plan import (
    validate_scoring_truth_evidence_plan_sources,
)
from rallymate_annotation.scoring_truth_refresh import (
    load_latest_scoring_truth_refresh,
)
from rallymate_scoring.calibration_handoff import (
    load_latest_scoring_truth_calibration_handoff,
)
from rallymate_scoring.calibration_portfolio import (
    load_latest_scoring_truth_calibration_portfolio,
)
from rallymate_scoring.multivideo_coverage import (
    load_latest_multivideo_indicator_calculation_coverage,
)
from rallymate_scoring.multivideo_readiness import (
    load_latest_multivideo_scoring_readiness_decomposition,
)
from rallymate_scoring.blocker_taxonomy_audit import (
    validate_blocker_taxonomy_replay_audit,
)
from rallymate_scoring.diagnostic_policy_review import (
    validate_diagnostic_policy_review,
)


LABELS = {
    "yolo": "YOLO COCO17",
    "rtmpose-s-halpe26-256x192": "RTMPose-S Halpe26 256×192",
    "rtmpose-m-halpe26-256x192": "RTMPose-M Halpe26 256×192",
    "rtmpose-m-halpe26-384x288": "RTMPose-M Halpe26 384×288",
    "rtmpose-m-wholebody133-256x192": "RTMPose-M WholeBody133 256×192",
}

SCORE_BLOCKER_REASON_LABELS = (
    ("keypoint_jump_diagnostic_unverified", "关键点跳变待复核"),
    ("left_right_assignment_unverified", "左右点分配待复核"),
    ("event_identity_continuity_unverified", "主球员身份连续性待复核"),
    ("event_boundary_evidence_low", "自动事件边界证据不足"),
    ("tactical_target_direction_required", "目标方向语义缺失"),
    ("event_phase_proxy_right_censored_unverified", "右边界截断阶段代理"),
    ("event_phase_proxy_low_sample_unverified", "两样本阶段代理"),
    ("lead_foot_side_assignment_unverified", "启动脚侧别歧义"),
)

SCORE_BLOCK_FLAG_LABELS = {
    "keypoint_jump_candidates_present": "关键点跳变候选",
    "left_right_swap_candidates_present": "左右点交换候选",
    "tactical_target_direction_not_observed": "目标方向语义缺失",
    "pose_kinematic_coverage_low": "自动事件运动学覆盖不足",
    "primary_pose_coverage_low": "主球员 Pose 覆盖不足",
    "source_track_switch_candidates_present": "source Track 切换候选",
    "primary_identity_ambiguous": "主球员选择歧义",
    "lead_foot_side_proxy_ambiguous": "启动脚侧别代理歧义",
    "phase_proxy_right_censored_peak:landing_proxy_ms": "落地阶段右边界截断代理",
    "phase_proxy_right_censored_peak:first_step_slowdown_proxy_ms": "第一步减速右边界截断代理",
    "phase_proxy_low_sample_peak:first_step_slowdown_proxy_ms": "第一步减速两样本代理",
}


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _production_calibration_asset_paths(directory: Path) -> list[Path]:
    """Return loadable production assets, excluding ledgers and templates."""

    assets: list[Path] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("artifact_scope") != "production":
            continue
        if payload.get("backend") not in {"threshold_rule", "ordinal_regression"}:
            continue
        if not isinstance(payload.get("indicator_id"), str):
            continue
        assets.append(path)
    return assets


def _shared_model_indicator_ids(scoring: dict) -> list[str]:
    model_sets = {
        str(model["backend"]): set(model.get("indicator_feature_validity", {}))
        for model in scoring.get("models", [])
    }
    if not model_sets or any(not values for values in model_sets.values()):
        raise ValueError("pose scoring report has an empty model indicator set")
    if len({frozenset(values) for values in model_sets.values()}) != 1:
        raise ValueError("pose scoring models do not share the same indicator set")
    return sorted(next(iter(model_sets.values())))


def _pose_wave_summary(report: dict) -> dict:
    results = report.get("indicator_results", {})
    if not isinstance(results, dict) or not results:
        raise ValueError("pose-wave report has no indicator results")
    indicator_ids = sorted(results)
    scoring_counts: Counter[str] = Counter()
    feature_measured = 0
    instance_count = 0
    for item in results.values():
        instances = int(item["instances"])
        instance_count += instances
        feature_measured += int(item["feature_status_counts"].get("measured", 0))
        scoring_counts.update(
            {
                str(status): int(count)
                for status, count in item["scoring_status_counts"].items()
            }
        )
    declared_count = report.get("indicator_scope", {}).get("indicator_count")
    if declared_count is None:
        declared_count = report.get("assertions", {}).get("target_indicator_count")
    if declared_count is not None and int(declared_count) != len(indicator_ids):
        raise ValueError("pose-wave declared indicator count does not match results")
    return {
        "indicator_ids": indicator_ids,
        "indicator_count": len(indicator_ids),
        "instance_count": instance_count,
        "feature_measured": feature_measured,
        "scoring_status_counts": dict(scoring_counts),
        "event_counts": {
            str(code): int(count)
            for code, count in report.get("summary", {}).get("event_counts", {}).items()
        },
        "grade_count": int(report.get("assertions", {}).get("grade_count", 0)),
    }


def _same_window_scope(report: dict) -> dict:
    indicators = report.get("indicators", {})
    models = report.get("models", {})
    if not isinstance(indicators, dict) or not indicators:
        raise ValueError("same-window report has no indicators")
    model_ids = set(models)
    if not model_ids or any(set(row) != model_ids for row in indicators.values()):
        raise ValueError("same-window indicator rows do not cover every model")
    event_codes = sorted(
        {
            str(code)
            for model in models.values()
            for code in model.get("event_counts", {})
        }
    )
    return {
        "indicator_ids": sorted(indicators),
        "indicator_count": len(indicators),
        "event_codes": event_codes,
    }


def _event_counts_text(event_counts: dict[str, int]) -> str:
    if not event_counts:
        return "未记录候选事件数"
    return "、".join(
        f"{html.escape(code)} {int(count)} 个候选区间"
        for code, count in sorted(event_counts.items())
    )


def _report_href(path_value: str, *, root: Path, page_directory: Path) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = root / path
    return Path(os.path.relpath(path, page_directory)).as_posix()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _pose_wave_intro(summary: dict) -> str:
    statuses = summary["scoring_status_counts"]
    return (
        f"共 {summary['indicator_count']} 项指标、{summary['instance_count']} 条指标事件记录："
        f"特征层 {summary['feature_measured']} 条 <code>measured</code>、"
        f"{summary['instance_count'] - summary['feature_measured']} 条 <code>unavailable</code>；"
        "评分层 "
        f"{statuses.get('calibration_required', 0)} 条 <code>calibration_required</code>，"
        f"{statuses.get('unavailable', 0)} 条 <code>unavailable</code>，"
        f"A～E 数量为 {summary['grade_count']}。"
    )


def _event_disagreement_summary(report: dict, preferred_iou: float = 0.3) -> dict:
    semantics = report.get("semantics", {})
    if semantics.get("accuracy_claim") is not False:
        raise ValueError("event disagreement report must not claim accuracy")
    rows = report.get("threshold_results", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("event disagreement report has no threshold results")
    selected = min(
        rows,
        key=lambda row: abs(float(row["minimum_segment_iou"]) - preferred_iou),
    )
    return {
        "left_label": report["inputs"]["left"]["label"],
        "right_label": report["inputs"]["right"]["label"],
        "left_event_count": int(selected["left_event_count"]),
        "right_event_count": int(selected["right_event_count"]),
        "threshold_results": rows,
        "selected": selected,
    }


def _fixed_boundary_summary(report: dict) -> dict:
    semantics = report.get("comparison_semantics", {})
    if semantics.get("accuracy_claim") is not False:
        raise ValueError("fixed-boundary report must not claim accuracy")
    if semantics.get("grade_generated") is not False:
        raise ValueError("fixed-boundary report must not generate grades")
    pairs = report.get("feature_pairs", [])
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("fixed-boundary report has no feature pairs")
    states = Counter(
        str(item.get("comparison", {}).get("validity_state")) for item in pairs
    )
    model_order = report.get("model_order", [])
    if len(model_order) != 2:
        raise ValueError("fixed-boundary report must compare exactly two models")
    return {
        "event_count": int(report["boundary_source"]["event_count"]),
        "truth_status": report["boundary_source"]["truth_status"],
        "indicator_count": int(report["registry_source"]["indicator_count"]),
        "feature_count": len(report.get("feature_metrics", {})),
        "feature_pair_count": len(pairs),
        "validity_states": dict(states),
        "model_order": model_order,
        "models": report["models"],
    }


def _pose_profile_routing_summary(report: dict) -> dict:
    safety = report.get("safety", {})
    if any(
        safety.get(field) is not False
        for field in (
            "accuracy_claim",
            "ground_truth_provided",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
            "fixed_boundary_coverage_is_accuracy",
            "self_segmented_counts_used_for_routing",
        )
    ):
        raise ValueError("pose profile routing audit contains an unsafe claim")
    decision = report.get("routing_decision", {})
    scope = report.get("scope", {})
    if decision.get("automatic_model_promotion") is not False:
        raise ValueError("pose profile routing audit must not promote a model")
    if decision.get("per_feature_or_per_event_model_cherry_picking_allowed") is not False:
        raise ValueError("pose profile routing audit must prohibit cherry-picking")
    if decision.get("selected_scoring_primary") != scope.get("current_primary"):
        raise ValueError("pose profile routing decision does not match current primary")
    profiles = report.get("profiles", {})
    comparisons = report.get("comparisons", {})
    if set(comparisons) != set(profiles) - {scope.get("current_primary")}:
        raise ValueError("pose profile routing comparison set is incomplete")
    return {
        "current_primary": str(scope["current_primary"]),
        "indicator_count": int(scope["indicator_count"]),
        "profiles": profiles,
        "comparisons": comparisons,
        "decision": decision,
    }


def _scoring_blocker_audit_summary(report: dict) -> dict:
    assertions = report.get("assertions", {})
    if assertions.get("typed_reason_mapping_consistent") is not True:
        raise ValueError("scoring blocker typed reason mapping is not verified")
    if assertions.get("recoverable_means_calibration_required_not_scored") is not True:
        raise ValueError("scoring blocker recovery semantics are unsafe")
    if any(
        assertions.get(field) is not False
        for field in (
            "quality_gate_modified",
            "grade_generated",
            "threshold_generated",
            "maturity_promoted",
            "counts_are_accuracy_metrics",
        )
    ):
        raise ValueError("scoring blocker audit contains an unsafe assertion")
    counts = report.get("counts", {})
    statuses = counts.get("status_counts", {})
    decomposition = counts.get("mutually_exclusive_decomposition", {})
    total = int(counts.get("indicator_record_count", -1))
    if total != sum(int(value) for value in statuses.values()):
        raise ValueError("scoring blocker status counts do not cover all records")
    if total != sum(int(value) for value in decomposition.values()):
        raise ValueError("scoring blocker decomposition does not cover all records")
    return {
        "counts": counts,
        "priority": report.get("human_review_priority", []),
        "reason_code_counts": report.get("reason_code_counts", {}),
        "typed_reason_metrics": report.get("typed_reason_metrics", []),
        "source": report.get("source", {}),
    }


def _truth_priority_worklist_summary(report: dict) -> dict:
    safety = report.get("safety", {})
    if any(
        safety.get(field) is not False
        for field in (
            "model_candidates_are_truth",
            "quality_gate_modified",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
            "priority_is_accuracy",
            "clearing_one_item_automatically_changes_score",
        )
    ):
        raise ValueError("truth priority worklist contains an unsafe claim")
    if safety.get("maximum_post_review_status_without_calibration") != "calibration_required":
        raise ValueError("truth priority worklist implies uncalibrated scoring")
    counts = report.get("counts", {})
    if int(counts.get("accepted_annotations", -1)) != 0:
        raise ValueError("generated truth priority worklist must remain blank")
    return counts


def _feature_observation_gap_summary(report: dict) -> dict:
    assertions = report.get("assertions", {})
    for field in (
        "required_feature_contract_matches_registry",
        "compact_features_match_full_features",
        "profile_comparisons_use_identical_candidate_boundaries",
        "human_ground_truth_still_required",
    ):
        if assertions.get(field) is not True:
            raise ValueError(f"feature observation gap audit lacks {field}")
    for field in (
        "measurement_gate_modified",
        "scoring_gate_modified",
        "automatic_profile_fallback_enabled",
        "cross_model_counts_are_accuracy_metrics",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if assertions.get(field) is not False:
            raise ValueError(f"feature observation gap audit has unsafe {field}")
    counts = report.get("counts", {})
    total = int(counts.get("indicator_record_count", -1))
    if total != int(counts.get("feature_measured_indicator_count", -1)) + int(
        counts.get("feature_unavailable_indicator_count", -1)
    ):
        raise ValueError("feature observation gap counts do not cover all records")
    return {
        "counts": counts,
        "reasons": report.get("recovery_priority", []),
        "profiles": report.get("profile_alternative_metrics", []),
    }


def _truth_action_worklist_summary(report: dict) -> dict:
    if report.get("worklist_version") != "scoring-truth-action-worklist-v2.0.0":
        raise ValueError("unsupported current truth action worklist")
    if report.get("status") != "annotation_required":
        raise ValueError("current truth action worklist must require annotation")
    safety = report.get("safety", {})
    if any(
        safety.get(field) is not False
        for field in (
            "model_candidates_are_truth",
            "quality_gate_modified",
            "scoring_state_modified",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
            "coverage_is_accuracy",
            "clearing_one_action_automatically_changes_score",
        )
    ):
        raise ValueError("current truth action worklist contains an unsafe claim")
    counts = report.get("counts", {})
    unavailable = int(counts.get("unavailable_indicator_instances", -1))
    actionable = int(counts.get("actionable_unavailable_indicator_instances", -2))
    if unavailable != actionable:
        raise ValueError("current truth action worklist is not exhaustive")
    if int(counts.get("work_items", -1)) != len(report.get("items", [])):
        raise ValueError("current truth action worklist item count mismatch")
    return counts


def _truth_action_readiness_summary(report: dict) -> dict:
    validate_scoring_truth_action_readiness(report)
    safety = report["safety"]
    if safety["scoring_state_modified"] or safety["grades_generated"]:
        raise ValueError("truth action readiness changed scoring state")
    return report["counts"]


def _truth_evidence_plan_summary(report: dict) -> dict:
    validate_scoring_truth_evidence_plan_sources(report)
    safety = report["safety"]
    if safety["scoring_state_modified"] or safety["grades_generated"]:
        raise ValueError("truth evidence plan changed scoring state")
    return report["counts"]


def _truth_refresh_summary(manifest: dict) -> dict:
    safety = manifest["safety"]
    if safety["scoring_state_modified"] or safety["grades_generated"]:
        raise ValueError("truth refresh changed scoring state")
    return {
        "refresh_id": manifest["refresh_id"],
        "states": manifest["states"],
        "counts": manifest["counts"],
        "artifacts": manifest["artifacts"],
    }


def _truth_calibration_handoff_summary(manifest: dict) -> dict:
    safety = manifest["safety"]
    if (
        safety["fit_executed"]
        or safety["candidate_written"]
        or safety["thresholds_generated"]
        or safety["model_trained"]
        or safety["grades_generated"]
        or safety["automatic_F3_or_F4_promotion"]
    ):
        raise ValueError("truth calibration handoff contains an unsafe scoring claim")
    return {
        "handoff_id": manifest["handoff_id"],
        "status": manifest["status"],
        "states": manifest["states"],
        "counts": manifest["counts"],
        "artifacts": manifest["artifacts"],
    }


def _truth_calibration_portfolio_summary(manifest: dict) -> dict:
    safety = manifest["safety"]
    if (
        safety["gpu_inference_executed"]
        or safety["fit_executed"]
        or safety["candidate_written"]
        or safety["thresholds_generated"]
        or safety["model_trained"]
        or safety["grades_generated"]
        or safety["automatic_F3_or_F4_promotion"]
    ):
        raise ValueError("truth calibration portfolio contains an unsafe scoring claim")
    return {
        "portfolio_id": manifest["portfolio_id"],
        "status": manifest["status"],
        "states": manifest["states"],
        "counts": manifest["counts"],
        "artifacts": manifest["artifacts"],
    }


def _multivideo_calculation_coverage_summary(manifest: dict) -> dict:
    safety = manifest["safety"]
    if (
        safety["gpu_inference_executed"]
        or safety["candidate_events_are_ground_truth"]
        or safety["event_accuracy_claim"]
        or safety["feature_accuracy_claim"]
        or safety["formal_score_claim"]
        or safety["grades_generated"]
        or safety["thresholds_generated"]
        or safety["automatic_F3_or_F4_promotion"]
    ):
        raise ValueError("multivideo calculation coverage has an unsafe claim")
    return {
        "coverage_id": manifest["coverage_id"],
        "status": manifest["status"],
        "counts": manifest["counts"],
        "videos": manifest["videos"],
        "indicators": manifest["indicators"],
        "artifacts": manifest["artifacts"],
    }


def _multivideo_readiness_summary(manifest: dict) -> dict:
    safety = manifest["safety"]
    if any(safety.values()):
        raise ValueError("multivideo readiness contains an unsafe claim")
    assertions = manifest.get("assertions", {})
    if (
        assertions.get("typed_reason_mapping_consistent") is not True
        or assertions.get("recovery_means_calibration_required_not_scored")
        is not True
        or assertions.get("quality_gate_modified") is not False
        or assertions.get("recovery_counterfactual_changes_scores") is not False
        or assertions.get("priority_is_diagnostic_accuracy") is not False
    ):
        raise ValueError("multivideo readiness contains unsafe blocker assertions")
    return {
        "audit_id": manifest["audit_id"],
        "status": manifest["status"],
        "counts": manifest["counts"],
        "videos": manifest["videos"],
        "indicators": manifest["indicators"],
        "measurement_failure_reasons": manifest["measurement_failure_reasons"],
        "scoring_block_flags": manifest["scoring_block_flags"],
        "scoring_block_recovery_priority": manifest[
            "scoring_block_recovery_priority"
        ],
        "typed_reason_metrics": manifest["typed_reason_metrics"],
        "artifacts": manifest["artifacts"],
    }


def _small_roi_recovery_summary(report: dict, video_validation: dict) -> dict:
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"small ROI experiment has unsafe {field}")
    if safety.get("current_production_default_unchanged") is not True:
        raise ValueError("small ROI experiment changed the production default")
    if safety.get("recovered_means_observable_under_experimental_small_roi_not_accurate") is not True:
        raise ValueError("small ROI experiment overstates recovered observations")
    settings = report.get("settings", {})
    if int(settings.get("baseline_min_roi_size_px", -1)) != 32 or int(
        settings.get("experimental_min_roi_size_px", -1)
    ) != 8:
        raise ValueError("small ROI experiment does not compare 32px against 8px")
    counts = report.get("inference", {}).get("counts", {})
    attempted = int(counts.get("inference_attempted", -1))
    recovered_poses = int(counts.get("pose_output_recovered", -1))
    if attempted <= 0 or recovered_poses != attempted:
        raise ValueError("small ROI experiment inference accounting is incomplete")
    impact = report.get("measurement_impact", {})
    transitions = impact.get("status_transitions", {})
    total = sum(int(value) for value in transitions.values())
    recovered_indicators = int(impact.get("recovered_indicator_instance_count", -1))
    regressions = int(impact.get("regressed_indicator_instance_count", -1))
    if recovered_indicators != int(transitions.get("unavailable_to_measured", -1)):
        raise ValueError("small ROI recovery transition count is inconsistent")
    if regressions != 0 or int(transitions.get("measured_to_unavailable", 0)) != 0:
        raise ValueError("published small ROI experiment contains regressions")
    video_safety = video_validation.get("safety", {})
    if video_validation.get("status") != "passed" or any(
        video_safety.get(field) is not False
        for field in (
            "accuracy_claim",
            "ground_truth_provided",
            "production_enabled",
            "grade_generated",
            "threshold_generated",
        )
    ):
        raise ValueError("small ROI comparison video is not safely validated")
    video = video_validation.get("video", {})
    if str(video.get("codec", "")).lower() not in {"h264", "avc1"}:
        raise ValueError("small ROI comparison video is not H.264")
    return {
        "settings": settings,
        "inference_counts": counts,
        "transitions": transitions,
        "indicator_instance_count": total,
        "recovered_indicator_instance_count": recovered_indicators,
        "regressed_indicator_instance_count": regressions,
        "video": video,
    }


def _multivideo_measurement_recovery_summary(
    report: dict, video_validations: dict[str, dict]
) -> dict:
    validate_multivideo_measurement_recovery_report(report)
    for collection in ("gap_audits", "small_roi_experiments"):
        for item in report["sources"][collection]:
            binding = item["report"]
            path = Path(binding["path"])
            if not path.is_file() or _file_sha256(path) != str(
                binding["sha256"]
            ).upper():
                raise ValueError(f"M66 {collection} source binding failed")
    experiment_bindings = {
        str(item["video_id"]): item["report"]
        for item in report["sources"]["small_roi_experiments"]
    }
    if set(video_validations) != set(experiment_bindings):
        raise ValueError("M66 dynamic-video validation scope is incomplete")
    for video_id, validation in video_validations.items():
        binding = experiment_bindings[video_id]
        video = validation.get("video", {})
        video_path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not video_path.is_file()
            or _file_sha256(video_path) != str(video.get("sha256", "")).upper()
            or str(
                validation.get("source", {}).get("experiment_report_sha256", "")
            ).upper()
            != str(binding["sha256"]).upper()
        ):
            raise ValueError("M66 dynamic-video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M66 dynamic video contains an unsafe claim")
    return report


def _m67_measurement_recovery_summary(
    report: dict, video_validations: dict[str, dict]
) -> dict:
    if report.get("report_version") != "multivideo-pose-observability-recovery-v1.0.0":
        raise ValueError("unsupported M67 recovery report")
    if report.get("status") != "experimental_observability_gain_not_production_or_accuracy":
        raise ValueError("M67 recovery status is unsafe")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M67 recovery contains unsafe {field}")
    if report.get("decision", {}).get("production_default_changed") is not False:
        raise ValueError("M67 recovery changed the production default")
    projection = report.get("composed_experimental_projection", {})
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    if (
        int(projection.get("operational_measured", -1))
        + int(projection.get("operational_unavailable", -1))
        != total
        or int(projection.get("feature_vector_complete", -1))
        + int(projection.get("feature_vector_incomplete", -1))
        != total
    ):
        raise ValueError("M67 recovery projection does not balance")
    if projection.get("composition_was_recomputed_from_combined_frames") is not True:
        raise ValueError("M67 recovery counts were arithmetically added")
    for binding in (
        [report["sources"]["m66"]]
        + report["sources"]["margin_reports"]
        + report["sources"]["clipped_reports"]
        + report["sources"]["combined_reports"]
        + report["sources"]["dynamic_video_validations"]
    ):
        path = Path(binding["path"])
        if not path.is_file() or _file_sha256(path) != str(binding["sha256"]).upper():
            raise ValueError("M67 source binding failed")
    if set(video_validations) != set(report["scope"]["video_ids"]):
        raise ValueError("M67 dynamic video scope is incomplete")
    for validation in video_validations.values():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
        ):
            raise ValueError("M67 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M67 dynamic video contains an unsafe claim")
    return report


def _m68_pose_router_summary(
    report: dict, video_validations: dict[str, dict]
) -> dict:
    if report.get("report_version") != (
        "multivideo-required-joint-superset-router-v1.0.0"
    ):
        raise ValueError("unsupported M68 Pose router report")
    if report.get("status") != (
        "regression_free_observability_router_candidate_requires_truth"
    ):
        raise ValueError("M68 Pose router status is unsafe")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M68 Pose router contains unsafe {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M68 Pose router omits the independent-test disclaimer")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("M68 Pose router changed production")
    router = report.get("router", {})
    if (
        int(router.get("selected_margin_frames", -1))
        + int(router.get("rejected_margin_frames", -1))
        != int(router.get("candidate_margin_target_frames", -1))
        or router.get("selection_uses_feature_values") is not False
        or router.get("selection_uses_event_outcomes") is not False
        or router.get("selection_uses_grades_or_thresholds") is not False
    ):
        raise ValueError("M68 Pose router selection audit is inconsistent")
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    baseline = report.get("baseline", {})
    routed = report.get("routed_projection", {})
    for state in (baseline, routed):
        if int(state.get("feature_vector_complete", -1)) + int(
            state.get("feature_vector_incomplete", -1)
        ) != total:
            raise ValueError("M68 feature projection does not balance")
        if int(state.get("operational_measured", -1)) + int(
            state.get("operational_unavailable", -1)
        ) != total:
            raise ValueError("M68 operational projection does not balance")
    if int(routed.get("feature_vector_regressed", -1)) != 0 or int(
        routed.get("operational_regressed", -1)
    ) != 0:
        raise ValueError("M68 published router is not regression-free")

    report_bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("router_reports", []):
        path = Path(binding["path"])
        if not path.is_file() or _file_sha256(path) != str(binding["sha256"]).upper():
            raise ValueError("M68 router report source binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        report_bindings[str(source_report["video_id"])] = binding
    m67_binding = report.get("sources", {}).get("m67_summary", {})
    m67_path = Path(str(m67_binding.get("path", "")))
    if not m67_path.is_file() or _file_sha256(m67_path) != str(
        m67_binding.get("sha256", "")
    ).upper():
        raise ValueError("M68 M67 source binding failed")
    if set(video_validations) != set(report.get("scope", {}).get("video_ids", [])):
        raise ValueError("M68 dynamic video scope is incomplete")
    if set(report_bindings) != set(video_validations):
        raise ValueError("M68 report/video source scope differs")
    for video_id, validation in video_validations.items():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
            or str(
                validation.get("source", {}).get("experiment_report_sha256", "")
            ).upper()
            != str(report_bindings[video_id]["sha256"]).upper()
        ):
            raise ValueError("M68 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M68 dynamic video contains an unsafe claim")
    return report


def _m69_high_resolution_summary(
    report: dict,
    residual_audit: dict,
    video_validations: dict[str, dict],
) -> dict:
    if report.get("report_version") != (
        "multivideo-residual-high-resolution-pose-v1.0.0"
    ):
        raise ValueError("unsupported M69 high-resolution report")
    if report.get("status") != (
        "regression_free_high_resolution_observability_candidate_requires_truth"
    ):
        raise ValueError("M69 high-resolution status is unsafe")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M69 high-resolution report contains unsafe {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M69 omits the independent-test disclaimer")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("M69 changed production")

    inference = report.get("inference", {})
    if (
        int(inference.get("pose_output_produced", -1))
        + int(inference.get("pose_output_missing", -1))
        != int(inference.get("target_frame_count", -1))
        or int(inference.get("selected_frame_count", -1))
        + int(inference.get("rejected_frame_count", -1))
        != int(inference.get("target_frame_count", -1))
    ):
        raise ValueError("M69 inference accounting is inconsistent")
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    baseline = report.get("baseline", {})
    projected = report.get("experimental_projection", {})
    for state in (baseline, projected):
        if int(state.get("feature_vector_complete", -1)) + int(
            state.get("feature_vector_incomplete", -1)
        ) != total:
            raise ValueError("M69 feature projection does not balance")
        if int(state.get("operational_measured", -1)) + int(
            state.get("operational_unavailable", -1)
        ) != total:
            raise ValueError("M69 operational projection does not balance")
    if int(projected.get("feature_vector_regressed", -1)) != 0 or int(
        projected.get("operational_regressed", -1)
    ) != 0:
        raise ValueError("M69 published candidate is not regression-free")

    residual_binding = report.get("sources", {}).get("residual_audit", {})
    residual_path = Path(str(residual_binding.get("path", "")))
    if (
        residual_audit.get("report_version")
        != "pose-observability-residual-audit-v1.0.0"
        or not residual_path.is_file()
        or _file_sha256(residual_path)
        != str(residual_binding.get("sha256", "")).upper()
        or json.loads(residual_path.read_text(encoding="utf-8")) != residual_audit
    ):
        raise ValueError("M69 residual audit source binding failed")

    experiment_bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("experiment_reports", []):
        path = Path(str(binding.get("path", "")))
        if not path.is_file() or _file_sha256(path) != str(
            binding.get("sha256", "")
        ).upper():
            raise ValueError("M69 experiment report source binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        experiment_bindings[str(source_report["video_id"])] = binding
    if set(video_validations) != set(report.get("scope", {}).get("video_ids", [])):
        raise ValueError("M69 dynamic video scope is incomplete")
    if set(experiment_bindings) != set(video_validations):
        raise ValueError("M69 report/video source scope differs")
    for video_id, validation in video_validations.items():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or validation.get("experiment_kind") != "high_resolution_router"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
            or str(
                validation.get("source", {}).get("experiment_report_sha256", "")
            ).upper()
            != str(experiment_bindings[video_id]["sha256"]).upper()
        ):
            raise ValueError("M69 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M69 dynamic video contains an unsafe claim")
    return report


def _m70_pose_profile_ablation_summary(
    report: dict,
    video_validations: dict[str, dict],
) -> dict:
    if report.get("report_version") != (
        "multivideo-pose-profile-context-ablation-v1.0.0"
    ):
        raise ValueError("unsupported M70 Pose-profile ablation report")
    if report.get("status") != (
        "m69_anchored_extension_regression_free_requires_truth"
    ):
        raise ValueError("M70 Pose-profile ablation status is unsafe")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M70 Pose-profile report contains unsafe {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M70 omits the independent-test disclaimer")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("naive_multicandidate_rejected") is not True
    ):
        raise ValueError("M70 changed production or accepted the unsafe route")

    total = int(report.get("scope", {}).get("indicator_instances", -1))
    baseline = report.get("baseline", {})
    anchored = report.get("strategies", {}).get("m69_anchored_extension", {})
    for state in (baseline, anchored):
        if int(state.get("feature_vector_complete", -1)) + int(
            state.get("feature_vector_incomplete", -1)
        ) != total:
            raise ValueError("M70 feature projection does not balance")
        if int(state.get("operational_measured", -1)) + int(
            state.get("operational_unavailable", -1)
        ) != total:
            raise ValueError("M70 operational projection does not balance")
    if any(
        int(anchored.get(field, -1)) != 0
        for field in (
            "feature_regressed",
            "operational_regressed",
            "lost_m69_feature_recovery_count",
            "lost_m69_operational_recovery_count",
        )
    ):
        raise ValueError("M70 anchored extension is not regression-free")
    naive = report.get("strategies", {}).get("naive_multicandidate", {})
    if naive.get("preserves_uniform_recovered_sets") is not False:
        raise ValueError("M70 hides the naive multicandidate loss")

    report_bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("anchored_reports", []):
        path = Path(str(binding.get("path", "")))
        if not path.is_file() or _file_sha256(path) != str(
            binding.get("sha256", "")
        ).upper():
            raise ValueError("M70 anchored report source binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        report_bindings[str(source_report["video_id"])] = binding
    expected_video_ids = {
        str(item["video_id"])
        for item in report.get("by_video", [])
        if int(item.get("anchored_extension_selected_frames", 0)) > 0
    }
    if set(video_validations) != expected_video_ids:
        raise ValueError("M70 dynamic video scope is incomplete")
    for video_id, validation in video_validations.items():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or validation.get("experiment_kind") != "anchored_profile_extension"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
            or str(
                validation.get("source", {}).get("experiment_report_sha256", "")
            ).upper()
            != str(report_bindings[video_id]["sha256"]).upper()
        ):
            raise ValueError("M70 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M70 dynamic video contains an unsafe claim")
    return report


def _m71_larger_pose_model_summary(
    report: dict,
    video_validations: dict[str, dict],
) -> dict:
    if report.get("report_version") != (
        "multivideo-larger-pose-model-extension-v1.0.0"
    ):
        raise ValueError("unsupported M71 larger Pose model report")
    if report.get("status") != (
        "experimental_larger_pose_model_extension_regression_free_requires_truth"
    ):
        raise ValueError("M71 larger Pose model status is unsafe")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M71 larger Pose model report contains unsafe {field}")
    if report.get("safety", {}).get(
        "current_three_video_regression_free_is_not_independent_validation"
    ) is not True:
        raise ValueError("M71 omits the independent-test disclaimer")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m70_recovered_sets_preserved") is not True
        or decision.get("current_three_video_extension_regression_free") is not True
    ):
        raise ValueError("M71 changed production or hides a regression")
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    projection = report.get("m71_projection", {})
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise ValueError("M71 feature projection does not balance")
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M71 operational projection does not balance")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68",
            "operational_regressed_from_m68",
            "lost_m70_feature_recovery_count",
            "lost_m70_operational_recovery_count",
        )
    ):
        raise ValueError("M71 projection is not regression-free")

    report_bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("per_video_reports", []):
        path = Path(str(binding.get("path", "")))
        if not path.is_file() or _file_sha256(path) != str(
            binding.get("sha256", "")
        ).upper():
            raise ValueError("M71 per-video report source binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        report_bindings[str(source_report["video_id"])] = binding
    expected_video_ids = {
        str(item["video_id"])
        for item in report.get("by_video", [])
        if int(item.get("additional_operational_recovery_over_m70", 0)) > 0
    }
    if set(video_validations) != expected_video_ids:
        raise ValueError("M71 dynamic video scope is incomplete")
    for video_id, validation in video_validations.items():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or validation.get("experiment_kind") != "larger_pose_model_extension"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
            or str(
                validation.get("source", {}).get("experiment_report_sha256", "")
            ).upper()
            != str(report_bindings[video_id]["sha256"]).upper()
        ):
            raise ValueError("M71 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim",
                "ground_truth_provided",
                "production_enabled",
                "grade_generated",
                "threshold_generated",
            )
        ):
            raise ValueError("M71 dynamic video contains an unsafe claim")
    return report


def _m72_additive_keypoint_fusion_summary(
    report: dict,
    video_validation: dict,
) -> dict:
    if report.get("report_version") != "multivideo-additive-keypoint-fusion-v1.0.0":
        raise ValueError("unsupported M72 additive fusion report")
    if report.get("status") != "experimental_additive_keypoint_fusion_regression_free_requires_truth":
        raise ValueError("M72 additive fusion status is unsafe")
    for field in (
        "accuracy_claim", "ground_truth_provided", "production_enabled",
        "automatic_fallback_enabled", "feature_gate_modified",
        "measurement_gate_modified", "event_boundaries_modified",
        "grades_generated", "thresholds_generated", "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M72 additive fusion contains unsafe {field}")
    contract = report.get("fusion_contract", {})
    for field, expected in {
        "baseline_valid_coordinates_overwritten": 0,
        "same_topology_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if contract.get(field) != expected:
            raise ValueError(f"unsafe M72 fusion contract: {field}")
    projection = report.get("m72_projection", {})
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total or int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M72 projection does not balance")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68", "operational_regressed_from_m68",
            "lost_m71_feature_recovery_count", "lost_m71_operational_recovery_count",
        )
    ):
        raise ValueError("M72 projection is not regression-free")
    residual = report.get("remaining_residual", {})
    if int(residual.get("indicator_instance_count", -1)) != len(residual.get("items", [])):
        raise ValueError("M72 residual accounting is inconsistent")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m71_recovered_sets_preserved") is not True
        or decision.get("fusion_is_experimental_truth_required") is not True
    ):
        raise ValueError("M72 decision is unsafe")

    bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("per_video_reports", []):
        path = Path(str(binding.get("path", "")))
        if not path.is_file() or _file_sha256(path) != str(binding.get("sha256", "")).upper():
            raise ValueError("M72 per-video report binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        bindings[str(source_report["video_id"])] = binding
    video_id = "3ae77ee3271d67de171585a5c39ddd69"
    video = video_validation.get("video", {})
    path = Path(str(video.get("path", "")))
    if (
        video_validation.get("status") != "passed"
        or video_validation.get("experiment_kind") != "additive_keypoint_fusion"
        or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
        or not path.is_file()
        or _file_sha256(path) != str(video.get("sha256", "")).upper()
        or str(video_validation.get("source", {}).get("experiment_report_sha256", "")).upper()
        != str(bindings[video_id]["sha256"]).upper()
    ):
        raise ValueError("M72 dynamic video validation failed")
    if any(
        video_validation.get("safety", {}).get(field) is not False
        for field in (
            "accuracy_claim", "ground_truth_provided", "production_enabled",
            "grade_generated", "threshold_generated",
        )
    ):
        raise ValueError("M72 video contains an unsafe claim")
    return report


def _m73_wholebody_mapped_fusion_summary(
    report: dict,
    video_validations: dict[str, dict],
) -> dict:
    if report.get("report_version") != "multivideo-wholebody133-mapped-fusion-v1.0.0":
        raise ValueError("unsupported M73 WholeBody mapped fusion report")
    if report.get("status") != "experimental_wholebody_mapped_fusion_regression_free_no_additional_indicator_recovery":
        raise ValueError("M73 WholeBody mapped fusion status is unsafe")
    for field in (
        "accuracy_claim", "ground_truth_provided", "production_enabled",
        "automatic_fallback_enabled", "feature_gate_modified",
        "measurement_gate_modified", "event_boundaries_modified",
        "grades_generated", "thresholds_generated", "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"M73 WholeBody mapped fusion contains unsafe {field}")
    if report.get("safety", {}).get(
        "point_count_is_not_accuracy_or_indicator_recovery"
    ) is not True:
        raise ValueError("M73 report does not disclaim point-count accuracy")
    contract = report.get("fusion_contract", {})
    for field, expected in {
        "baseline_keypoint_format": "halpe26",
        "candidate_keypoint_format": "coco_wholebody133",
        "mapping_is_one_to_one": True,
        "baseline_valid_coordinates_overwritten": 0,
        "baseline_topology_preserved": True,
        "explicit_topology_map_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if contract.get(field) != expected:
            raise ValueError(f"unsafe M73 fusion contract: {field}")
    mapping = contract.get("target_to_candidate_joint_names", {})
    if not mapping or any(target != source for target, source in mapping.items()):
        raise ValueError("M73 report contains a non-semantic topology map")
    projection = report.get("m73_projection", {})
    total = int(report.get("scope", {}).get("indicator_instances", -1))
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total or int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise ValueError("M73 projection does not balance")
    if any(
        int(projection.get(field, -1)) != 0
        for field in (
            "feature_regressed_from_m68", "operational_regressed_from_m68",
            "additional_feature_recovery_over_m72",
            "additional_operational_recovery_over_m72",
            "lost_m72_feature_recovery_count",
            "lost_m72_operational_recovery_count",
        )
    ):
        raise ValueError("M73 projection must preserve M72 and report zero gain")
    decision = report.get("decision", {})
    if (
        decision.get("production_default_changed") is not False
        or decision.get("candidate_promoted") is not False
        or decision.get("m72_recovered_sets_preserved") is not True
        or decision.get("additional_indicator_recovery_observed") is not False
        or decision.get(
            "denser_topology_alone_did_not_resolve_remaining_indicator_contracts"
        ) is not True
    ):
        raise ValueError("M73 decision is unsafe")
    bindings: dict[str, dict] = {}
    for binding in report.get("sources", {}).get("per_video_reports", []):
        path = Path(str(binding.get("path", "")))
        if not path.is_file() or _file_sha256(path) != str(binding.get("sha256", "")).upper():
            raise ValueError("M73 per-video report binding failed")
        source_report = json.loads(path.read_text(encoding="utf-8"))
        bindings[str(source_report["video_id"])] = binding
    expected_video_ids = {
        str(item["video_id"])
        for item in report.get("by_video", [])
        if int(item.get("changed_frames", 0)) > 0
    }
    if set(video_validations) != expected_video_ids:
        raise ValueError("M73 dynamic video scope differs from changed videos")
    for video_id, validation in video_validations.items():
        video = validation.get("video", {})
        path = Path(str(video.get("path", "")))
        if (
            validation.get("status") != "passed"
            or validation.get("experiment_kind") != "wholebody_mapped_fusion"
            or str(video.get("codec", "")).lower() not in {"h264", "avc1"}
            or not path.is_file()
            or _file_sha256(path) != str(video.get("sha256", "")).upper()
            or str(validation.get("source", {}).get("experiment_report_sha256", "")).upper()
            != str(bindings[video_id]["sha256"]).upper()
        ):
            raise ValueError("M73 dynamic video validation failed")
        if any(
            validation.get("safety", {}).get(field) is not False
            for field in (
                "accuracy_claim", "ground_truth_provided", "production_enabled",
                "grade_generated", "threshold_generated",
            )
        ):
            raise ValueError("M73 video contains an unsafe claim")
    return report


def _m74_event_bounded_gap_summary(report: dict) -> dict:
    validate_event_bounded_pose_gap_report_sources(report)
    summary = report["counterfactual_summary"]
    if (
        int(summary["counterfactual_recovered_indicator_instance_count"]) != 7
        or int(summary["counterfactual_remaining_indicator_instance_count"]) != 6
        or int(summary["production_recovered_indicator_instance_count"]) != 0
        or int(summary["unique_event_joint_observation_count"]) != 142
    ):
        raise ValueError("M74 event-bounded gap accounting drifted")
    if report["decision"]["materialize_production_pose_artifact"] is not False:
        raise ValueError("M74 cannot be presented as a production Pose artifact")
    return report


def _m75_event_gap_truth_summary(
    pack_directory: Path,
    manifest: dict,
    validation: dict,
    error_report: dict,
) -> dict:
    validate_event_gap_truth_manifest(manifest)
    recomputed = evaluate_event_gap_keypoints(pack_directory)
    scope = manifest["scope"]
    counts = validation.get("counts", {})
    if (
        int(scope.get("video_count", -1)) != 2
        or int(scope.get("frame_count", -1)) != 54
        or int(scope.get("joint_task_count", -1)) != 142
        or int(counts.get("joint_tasks", -1)) != 142
    ):
        raise ValueError("M75 blind truth task scope drifted")
    if validation.get("status") != "annotation_required" or any(
        int(counts.get(name, -1)) != 0
        for name in (
            "raw_annotations",
            "tasks_with_two_independent_annotators",
            "accepted_adjudications",
            "compiled_keypoint_records",
            "compiled_joint_values",
        )
    ):
        raise ValueError("published M75 truth pack must remain empty")
    if (
        error_report.get("status") != "annotation_required"
        or error_report.get("metrics") is not None
        or error_report.get("pack") != recomputed.get("pack")
        or error_report.get("counts") != recomputed.get("counts")
        or error_report.get("safety") != recomputed.get("safety")
    ):
        raise ValueError("M75 empty error report is stale or exposes unsupported metrics")
    if error_report.get("acceptance", {}).get("production_interpolation_allowed") is not False:
        raise ValueError("M75 enabled interpolation without external truth acceptance")
    return {
        "status": validation["status"],
        "video_count": int(scope["video_count"]),
        "frame_count": int(scope["frame_count"]),
        "joint_task_count": int(scope["joint_task_count"]),
        "joint_count": len(scope["joint_names"]),
        "raw_annotations": int(counts["raw_annotations"]),
        "accepted_adjudications": int(counts["accepted_adjudications"]),
        "metrics": error_report["metrics"],
    }


def _m76_event_gap_feature_truth_summary(report: dict) -> dict:
    validate_event_gap_feature_truth_report_sources(report)
    scope = report["scope"]
    counts = report["counts"]
    if (
        report.get("status") != "annotation_required"
        or int(scope.get("interpolation_candidate_joint_tasks", -1)) != 142
        or int(scope.get("indicator_instances_with_interpolation_points", -1)) != 11
        or int(scope.get("required_feature_records", -1)) != 47
        or int(scope.get("unique_required_features", -1)) != 25
        or int(scope.get("candidate_complete_indicator_instances", -1)) != 7
        or int(scope.get("phase_only_residual_indicator_instances_excluded", -1)) != 2
        or report.get("metrics") is not None
        or report.get("per_feature") is not None
        or report.get("per_indicator") is not None
        or report.get("details") != []
        or any(int(value) != 0 for value in counts.values())
    ):
        raise ValueError("published M76 feature-truth report must remain empty and fail closed")
    if (
        report.get("acceptance", {}).get("production_interpolation_allowed") is not False
        or report.get("acceptance", {}).get("f2_to_f3_allowed") is not False
        or report.get("safety", {}).get("production_enabled") is not False
    ):
        raise ValueError("M76 enabled interpolation, scoring, or maturity without truth")
    return {
        "status": report["status"],
        **scope,
        **counts,
        "metrics": report["metrics"],
    }


def _m77_fs09_phase_truth_summary(
    manifest: dict, validation: dict, report: dict
) -> dict:
    validate_fs09_phase_truth_manifest(manifest)
    validate_fs09_phase_truth_report_sources(report)
    counts = validation.get("counts", {})
    scope = report.get("scope", {})
    if (
        validation.get("status") != "annotation_required"
        or report.get("status") != "annotation_required"
        or int(scope.get("task_count", -1)) != 2
        or scope.get("indicator_ids") != ["FS09-M05"]
        or scope.get("target_unavailable_feature")
        != "hip_deceleration_to_double_support_proxy_ms"
        or int(counts.get("raw_annotations", -1)) != 0
        or int(counts.get("accepted_adjudications", -1)) != 0
        or report.get("event_metrics") is not None
        or report.get("phase_metrics") is not None
        or report.get("feature_boundary_conditioning") is not None
        or report.get("details") != []
    ):
        raise ValueError("published M77 FS09 phase truth must remain empty and fail closed")
    if (
        report.get("acceptance", {}).get("runtime_event_change_allowed") is not False
        or report.get("acceptance", {}).get("runtime_feature_change_allowed") is not False
        or report.get("acceptance", {}).get("f2_to_f3_allowed") is not False
    ):
        raise ValueError("M77 cannot modify runtime or maturity without external truth")
    return {
        "status": report["status"],
        "task_count": int(scope["task_count"]),
        "phase_count": len(scope["phase_keys"]),
        "required_feature_count": len(scope["required_features"]),
        "target_unavailable_feature": scope["target_unavailable_feature"],
        "raw_annotations": int(counts["raw_annotations"]),
        "accepted_adjudications": int(counts["accepted_adjudications"]),
    }


def _m78_blocker_taxonomy_summary(audit: dict) -> dict:
    validate_blocker_taxonomy_replay_audit(audit)
    comparison = audit["replay_comparison"]
    if (
        audit["scope"]["indicator_event_instance_count"] != 2366
        or len(audit["scope"]["video_ids"]) != 3
        or len(audit["scope"]["indicator_ids"]) != 13
        or not comparison["score_semantics_exact"]
        or not comparison["all_nonconfidence_payloads_exact"]
        or audit["assertions"]["replay_behavior_preserved"] is not True
        or any(audit["safety"].values())
    ):
        raise ValueError("published M78 taxonomy audit is not fail-closed or has drifted")
    return {
        "status": audit["status"],
        "taxonomy_version": audit["source"]["taxonomy"]["taxonomy_version"],
        "video_count": len(audit["scope"]["video_ids"]),
        "indicator_count": len(audit["scope"]["indicator_ids"]),
        "indicator_event_instance_count": audit["scope"][
            "indicator_event_instance_count"
        ],
        "confidence_difference_count": comparison[
            "differing_confidence_leaf_count"
        ],
        "max_abs_confidence_delta": comparison["max_abs_confidence_delta"],
        "priority": audit["blocker_recovery_priority"],
    }


def _m79_pose_diagnostic_clip_truth_summary(
    manifest: dict, report: dict
) -> dict:
    validate_clip_truth_manifest_sources(manifest)
    validate_empty_clip_truth_report(report)
    counts = manifest["plan"]["counts"]
    if (
        counts["videos"] != 3
        or counts["clips"] != 103
        or counts["candidate_tasks"] != 1371
        or counts["candidate_tasks_by_type"]
        != {"keypoint_jump": 1017, "left_right_swap": 354}
        or counts["unique_affected_indicator_instances"] != 1336
        or report["status"] != "annotation_required"
        or report["metrics_by_diagnostic_type"] is not None
    ):
        raise ValueError("published M80 diagnostic truth pack has drifted")
    pose_bindings = [
        segment["adjudication_pose_evidence"]
        for video in manifest["plan"]["videos"]
        for segment in video["segments"]
    ]
    if (
        len(pose_bindings) != counts["clips"]
        or not manifest["truth_contract"]["adjudication_pose_overlay_required"]
        or manifest["truth_contract"]["adjudication_pose_overlay_is_ground_truth"]
    ):
        raise ValueError("published M80 adjudication pose evidence has drifted")
    return {
        "status": report["status"],
        "video_count": counts["videos"],
        "clip_count": counts["clips"],
        "candidate_task_count": counts["candidate_tasks"],
        "jump_task_count": counts["candidate_tasks_by_type"]["keypoint_jump"],
        "swap_task_count": counts["candidate_tasks_by_type"]["left_right_swap"],
        "unique_affected_indicator_instances": counts[
            "unique_affected_indicator_instances"
        ],
        "source_frame_observations": counts["source_frame_observations"],
        "pose_evidence_files": len(pose_bindings),
        "pose_evidence_frames": sum(item["frame_count"] for item in pose_bindings),
        "pose_evidence_formats": sorted(
            {item["keypoint_format"] for item in pose_bindings}
        ),
        "coverage_annotations": report["counts"]["coverage_annotations"],
        "positive_annotations": report["counts"]["positive_annotations"],
        "candidate_adjudications": report["counts"][
            "candidate_adjudications"
        ],
    }


def _small_roi_truth_summary(
    manifest: dict, validation: dict, error_report: dict
) -> dict:
    safety = manifest.get("safety", {})
    for field in (
        "model_keypoints_embedded_in_annotation_ui",
        "truth_prefilled_from_model",
        "accuracy_claim",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"small ROI truth pack has unsafe {field}")
    if safety.get("independent_adjudication_required") is not True:
        raise ValueError("small ROI truth pack does not require independent adjudication")
    scope = manifest.get("scope", {})
    frames = int(scope.get("frame_count", -1))
    tasks = int(scope.get("joint_task_count", -1))
    joints = list(scope.get("joint_names", []))
    if frames <= 0 or len(joints) != 14 or tasks != frames * len(joints):
        raise ValueError("small ROI truth task accounting is inconsistent")
    if int(scope.get("required_independent_annotators", -1)) != 2:
        raise ValueError("small ROI truth pack must require two independent annotators")
    counts = validation.get("counts", {})
    if int(counts.get("joint_tasks", -1)) != tasks:
        raise ValueError("small ROI truth validation task count is inconsistent")
    if validation.get("status") == "annotation_required":
        if int(counts.get("raw_annotations", -1)) != 0 or int(
            counts.get("accepted_adjudications", -1)
        ) != 0:
            raise ValueError("published empty small ROI truth pack is not empty")
        if error_report.get("status") != "annotation_required" or error_report.get(
            "metrics"
        ) is not None:
            raise ValueError("small ROI error report must withhold metrics without truth")
    report_safety = error_report.get("safety", {})
    for field in (
        "model_values_used_as_truth",
        "acceptance_threshold_generated",
        "production_enabled",
        "automatic_profile_fallback_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if report_safety.get(field) is not False:
            raise ValueError(f"small ROI error report has unsafe {field}")
    routing = error_report.get("routing_decision", {})
    if routing.get("switch_allowed") is not False:
        raise ValueError("small ROI truth report enabled an unapproved routing switch")
    return {
        "status": validation.get("status"),
        "frames": frames,
        "tasks": tasks,
        "joint_count": len(joints),
        "raw_annotations": int(counts.get("raw_annotations", 0)),
        "accepted_adjudications": int(counts.get("accepted_adjudications", 0)),
        "metrics": error_report.get("metrics"),
        "routing": routing,
    }


def _residual_computability_summary(audit: dict, video_validation: dict) -> dict:
    expected_status = (
        "all_registry_indicators_have_real_measured_instances_residuals_fail_closed"
    )
    if audit.get("status") != expected_status:
        raise ValueError("residual computability audit is not complete")
    safety = audit.get("safety", {})
    for field in (
        "accuracy_claim",
        "event_ground_truth_provided",
        "keypoint_ground_truth_provided",
        "feature_quality_gate_modified",
        "zero_fill_used",
        "cross_model_cherry_picking_used",
        "production_route_changed",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"residual computability audit has unsafe {field}")
    counts = audit.get("counts", {})
    registry_count = int(counts.get("registry_indicator_count", -1))
    with_measured = int(counts.get("indicator_with_measured_instance_count", -1))
    without_measured = int(counts.get("indicator_without_measured_instance_count", -1))
    instances = int(counts.get("indicator_event_instance_count", -1))
    measured = int(counts.get("measured_indicator_event_instance_count", -1))
    unavailable = int(counts.get("unavailable_indicator_event_instance_count", -1))
    if registry_count <= 0 or with_measured != registry_count or without_measured != 0:
        raise ValueError("not every registry indicator has a measured real instance")
    if measured + unavailable != instances or unavailable <= 0:
        raise ValueError("residual computability instance accounting is inconsistent")
    per_indicator = audit.get("per_indicator", [])
    if len(per_indicator) != registry_count:
        raise ValueError("residual computability per-indicator coverage is incomplete")
    measured_counts = []
    for item in per_indicator:
        count = int(item.get("measured_instance_count", -1))
        if count <= 0 or item.get("all_measured_instances_contract_complete") is not True:
            raise ValueError("residual computability evidence is incomplete")
        measured_counts.append(count)
    classifications = audit.get("classification_counts_by_indicator_instance", {})
    if sum(int(value) for value in classifications.values()) != unavailable:
        raise ValueError("residual classification counts do not cover unavailable instances")
    video_safety = video_validation.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_route_changed",
        "feature_quality_gate_modified",
        "zero_fill_used",
        "grade_generated",
        "threshold_generated",
    ):
        if video_safety.get(field) is not False:
            raise ValueError(f"residual evidence video has unsafe {field}")
    video = video_validation.get("video", {})
    if video_validation.get("status") != "passed" or str(
        video.get("codec", "")
    ).lower() not in {"h264", "avc1"}:
        raise ValueError("residual evidence video is not safely validated H.264")
    if not video.get("sample_decode") or any(
        item.get("decoded") is not True for item in video["sample_decode"]
    ):
        raise ValueError("residual evidence video sample decode is incomplete")
    return {
        "status": audit["status"],
        "counts": counts,
        "classifications": classifications,
        "per_indicator": per_indicator,
        "min_measured_per_indicator": min(measured_counts),
        "max_measured_per_indicator": max(measured_counts),
        "video": video,
    }


def _metric_disagreement_text(metric: dict) -> str:
    comparison = metric["cross_model_disagreement"]
    eligible = int(metric["comparison_eligibility"]["eligible_count"])
    if metric.get("comparison_kind") == "categorical":
        return f"{float(comparison['agreement_rate']) * 100:.1f}% agreement (n={eligible})"
    return (
        f"MAD {float(comparison['mean_absolute_cross_model_disagreement']):.3f} / "
        f"P95 {float(comparison['p95_absolute_cross_model_disagreement']):.3f} "
        f"{html.escape(str(metric['unit']))} (n={eligible})"
    )


def _deployment_smoke_summary(report: dict) -> dict:
    if report.get("status") != "passed":
        raise ValueError("deployment smoke must be passed before publication")
    semantics = report.get("semantics", {})
    if semantics.get("accuracy_claim") is not False:
        raise ValueError("deployment smoke must not claim accuracy")
    registry = report.get("feasibility_registry", {})
    indicator_ids = sorted(str(value) for value in report.get("indicator_ids", []))
    registry_ids = sorted(str(value) for value in registry.get("indicator_ids", []))
    if not indicator_ids or indicator_ids != registry_ids:
        raise ValueError("deployment smoke indicator set does not match its registry")
    if int(registry.get("indicator_count", -1)) != len(indicator_ids):
        raise ValueError("deployment smoke registry count does not match indicator IDs")
    if int(report.get("non_null_grade_count", -1)) != 0:
        raise ValueError("uncalibrated deployment smoke must not contain grades")
    if int(report.get("non_null_threshold_version_count", -1)) != 0:
        raise ValueError("uncalibrated deployment smoke must not contain thresholds")
    bundle = report.get("bundle_validation", {})
    if bundle.get("status") != "passed":
        raise ValueError("deployment smoke bundle validation did not pass")
    return {
        "preset": str(report["preset"]),
        "native_keypoint_format": str(report["pose_backend"]["native_keypoint_format"]),
        "native_keypoint_count": int(report["pose_backend"]["native_keypoint_count"]),
        "processed_frames": int(report["processed_frames"]),
        "effective_processed_fps": float(report["effective_processed_fps"]),
        "event_counts": {
            str(code): int(count) for code, count in report.get("event_counts", {}).items()
        },
        "indicator_count": len(indicator_ids),
        "indicator_record_count": sum(
            int(count) for count in report.get("score_status_counts", {}).values()
        ),
        "score_status_counts": {
            str(status): int(count)
            for status, count in report.get("score_status_counts", {}).items()
        },
        "bundle_status": str(bundle["status"]),
    }


def _default_pose_routing_summary(report: dict) -> dict:
    validate_default_pose_routing_audit(report)
    full = report["full_video_production_measurement"]
    smoke = report["real_worker_default_smoke"]
    return {
        "status": report["status"],
        "deployment_default": report["deployment_default"],
        "smoke": smoke,
        "full": full,
        "experimental": report["experimental_small_roi_measurement"],
        "indicator_count": int(report["scope"]["indicator_count"]),
        "min_production_measured_per_indicator": min(
            int(item["measured_instance_count"]) for item in full["per_indicator"]
        ),
        "max_production_measured_per_indicator": max(
            int(item["measured_instance_count"]) for item in full["per_indicator"]
        ),
    }


def _calculation_readiness_summary(report: dict) -> dict:
    validate_indicator_calculation_readiness(report)
    return {
        "status": report["status"],
        "summary": report["summary"],
        "per_indicator": report["per_indicator"],
        "measurement_remediation": report["measurement_remediation"],
        "formal_scoring_truth_requirements": report[
            "formal_scoring_truth_requirements"
        ],
    }


def _measurement_portfolio_summary(report: dict) -> dict:
    validate_indicator_measurement_portfolio(report)
    return {
        "status": report["status"],
        "summary": report["summary"],
        "indicators": report["indicators"],
        "selection_policy": report["selection_policy"],
    }


def _cycle_measurement_summary(report: dict) -> dict:
    validate_scoring_cycle_measurement(report)
    return {
        "status": report["status"],
        "summary": report["summary"],
        "representative_cycle": report["representative_cycle"],
        "selection_policy": report["selection_policy"],
    }


def _pose_diagnostic_review_summary(report: dict) -> dict:
    safety = report.get("safety", {})
    if safety.get("accuracy_claim") is not False:
        raise ValueError("pose diagnostic review queue must not claim accuracy")
    if safety.get("diagnostic_candidates_are_ground_truth") is not False:
        raise ValueError("pose diagnostic candidates must not be declared truth")
    if safety.get("quality_gate_modified") is not False:
        raise ValueError("review queue must not modify quality gates")
    tasks = report.get("tasks", [])
    counts = report.get("counts", {})
    if not isinstance(tasks, list) or int(counts.get("tasks", -1)) != len(tasks):
        raise ValueError("pose diagnostic review task count mismatch")
    if any(item.get("review_status") != "pending" for item in tasks):
        raise ValueError("published review queues must contain pending tasks only")
    return {
        "task_count": len(tasks),
        "unique_candidate_frames": int(counts["unique_candidate_frames"]),
        "unique_affected_indicator_instances": int(
            counts["unique_affected_indicator_instances"]
        ),
        "by_diagnostic_type": {
            str(name): int(count)
            for name, count in counts.get("by_diagnostic_type", {}).items()
        },
        "scoring_loop_version": str(report["source"]["scoring_loop_version"]),
    }


def _pose_diagnostic_truth_summary(manifest: dict, evaluation: dict) -> dict:
    safety = manifest.get("safety", {})
    if safety.get("manual_truth_present") is not False:
        raise ValueError("published blank diagnostic truth pack must not claim truth")
    if safety.get("quality_gate_modified") is not False:
        raise ValueError("diagnostic truth pack must not modify quality gates")
    if manifest.get("status") != "annotation_required":
        raise ValueError("published diagnostic truth pack must require annotation")
    if evaluation.get("status") != "annotation_required":
        raise ValueError("published blank diagnostic evaluation must require annotation")
    if evaluation.get("source", {}).get("truth_pack_source_queue_sha256") != manifest.get(
        "source_queue", {}
    ).get("sha256"):
        raise ValueError("diagnostic truth pack/evaluation queue binding mismatch")
    evaluation_safety = evaluation.get("safety", {})
    if any(
        evaluation_safety.get(field) is not False
        for field in (
            "candidate_only_review_used_as_recall_truth",
            "quality_gate_modified",
            "formal_scoring_accuracy_claim",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
        )
    ):
        raise ValueError("diagnostic evaluation contains unsafe claims")
    counts = evaluation.get("annotation_counts", {})
    if counts.get("accepted_coverage_rows") != 0 or counts.get(
        "accepted_positive_rows"
    ) != 0:
        raise ValueError("published blank diagnostic truth pack contains annotations")
    return {
        "frame_count": int(manifest["frame_domain"]["frame_count"]),
        "candidate_task_count": int(manifest["source_queue"]["candidate_task_count"]),
        "diagnostic_type_count": len(manifest["diagnostic_types"]),
        "status": str(evaluation["status"]),
        "accepted_coverage_rows": int(counts["accepted_coverage_rows"]),
        "accepted_positive_rows": int(counts["accepted_positive_rows"]),
    }


def _pose_diagnostic_policy_review_summary(report: dict) -> dict:
    if report.get("status") != "annotation_and_protocol_required":
        raise ValueError("published diagnostic policy review must remain blocked")
    if report.get("protocol") is not None:
        raise ValueError("published empty diagnostic policy review must not bind a protocol")
    blockers = set(report.get("blockers", []))
    required_blockers = {
        "external_preregistered_acceptance_protocol_missing",
        "full_timeline_diagnostic_truth_missing",
    }
    if not required_blockers.issubset(blockers):
        raise ValueError("diagnostic policy review is missing required blockers")
    decision = report.get("policy_decision", {})
    if decision.get("automatic_policy_change_allowed") is not False or decision.get(
        "quality_policy_change_applied"
    ) is not False:
        raise ValueError("diagnostic policy review must not modify quality policy")
    if decision.get("next_quality_policy_version") is not None:
        raise ValueError("blocked diagnostic review must not declare a next policy")
    safety = report.get("safety", {})
    if any(
        safety.get(field) is not False
        for field in (
            "A_to_E_thresholds_generated",
            "grades_generated",
            "quality_gate_modified",
            "maturity_promoted",
            "self_declared_protocol_identity_is_trusted",
        )
    ):
        raise ValueError("diagnostic policy review contains unsafe claims")
    rows = report.get("by_diagnostic_type", {})
    if not rows or any(
        row.get("status") != "acceptance_protocol_required"
        or any(row.get(metric) is not None for metric in ("precision", "recall", "f1"))
        for row in rows.values()
    ):
        raise ValueError("published diagnostic policy review must contain null metrics")
    sources = report.get("sources", [])
    source_video_ids = [str(source.get("video_id", "")) for source in sources]
    if (
        len(sources) != 3
        or any(not video_id for video_id in source_video_ids)
        or len(set(source_video_ids)) != 3
    ):
        raise ValueError(
            "published diagnostic policy review must bind three unique full-video scopes"
        )
    return {
        "status": str(report["status"]),
        "quality_policy_version": str(report["quality_policy_version_under_review"]),
        "source_count": len(sources),
        "diagnostic_type_count": len(rows),
        "blockers": sorted(blockers),
    }


def main() -> None:
    root = Path.cwd()
    directory = root / "reports" / "pose-scoring-ab"
    scoring = json.loads((directory / "pose-scoring-ab.json").read_text(encoding="utf-8"))
    legacy = json.loads((directory / "legacy-298-audit.json").read_text(encoding="utf-8"))
    full_body_coverage = json.loads((directory / "full-body-keypoint-coverage.json").read_text(encoding="utf-8"))
    wholebody_metadata = json.loads(
        (
            root
            / "runs"
            / "pose-wholebody133"
            / "850cb0006b406c7176eeda8d711cd065-930-1530"
            / "inference-metadata.json"
        ).read_text(encoding="utf-8")
    )
    pose_wave = json.loads(
        (
            root
            / "reports"
            / "scoring-candidate-multivideo-m63"
            / "reports"
            / "850cb0006b406c7176eeda8d711cd065.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    same_window = json.loads(
        (
            root
            / "reports"
            / "fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json"
        ).read_text(encoding="utf-8")
    )
    event_disagreement = json.loads(
        (
            root
            / "reports"
            / "event-disagreement-halpe26-vs-wholebody133-same-window.json"
        ).read_text(encoding="utf-8")
    )
    fixed_boundary_reports = {
        "Halpe26 候选边界": json.loads(
            (
                root
                / "reports"
                / "fixed-boundary-pose-ab-halpe-candidates-930-1530.json"
            ).read_text(encoding="utf-8")
        ),
        "WholeBody133 候选边界": json.loads(
            (
                root
                / "reports"
                / "fixed-boundary-pose-ab-wholebody-candidates-930-1530.json"
            ).read_text(encoding="utf-8")
        ),
    }
    pose_profile_routing = json.loads(
        (root / "reports" / "pose-profile-routing-audit-full.json").read_text(
            encoding="utf-8"
        )
    )
    scoring_blocker_audit = json.loads(
        (root / "reports" / "scoring-blocker-audit-halpe256-full-m53.json").read_text(
            encoding="utf-8"
        )
    )
    feature_observation_gap_audit = json.loads(
        (root / "reports" / "feature-observation-gap-audit-halpe256-full-m53.json").read_text(
            encoding="utf-8"
        )
    )
    smoothing_counterfactual_coverage = json.loads(
        (root / "reports" / "smoothing-counterfactual-coverage-m63.json").read_text(
            encoding="utf-8"
        )
    )
    f2_error_budget_readiness = json.loads(
        (root / "reports" / "f2-error-budget-readiness-m64.json").read_text(
            encoding="utf-8"
        )
    )
    small_roi_experiment_directory = (
        root
        / "reports"
        / "experiments"
        / "small-roi-pose-recovery-halpe256-full-v1"
    )
    small_roi_experiment = json.loads(
        (small_roi_experiment_directory / "report.json").read_text(encoding="utf-8")
    )
    small_roi_video_validation = json.loads(
        (small_roi_experiment_directory / "video-validation.json").read_text(
            encoding="utf-8"
        )
    )
    multivideo_recovery_directory = (
        root
        / "reports"
        / "measurement-recovery-m66"
        / "multivideo-small-roi-v1"
    )
    multivideo_recovery = json.loads(
        (multivideo_recovery_directory / "report.json").read_text(
            encoding="utf-8"
        )
    )
    multivideo_recovery_validations = {
        video_id: json.loads(
            (
                root
                / "reports"
                / "measurement-recovery-m66"
                / "small-roi-v1.1"
                / video_id
                / "video-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        )
    }
    m67_recovery_directory = root / "reports" / "measurement-recovery-m67"
    m67_recovery = json.loads(
        (m67_recovery_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m67_recovery_validations = {
        video_id: json.loads(
            (
                m67_recovery_directory
                / "roi-margin-0.30"
                / video_id
                / "video-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        )
    }
    m68_router_directory = root / "reports" / "measurement-recovery-m68"
    m68_router = json.loads(
        (m68_router_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m68_router_validations = {
        video_id: json.loads(
            (
                m68_router_directory
                / "required-joint-superset"
                / video_id
                / "video-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        )
    }
    m69_high_resolution_directory = root / "reports" / "measurement-recovery-m69"
    m69_residual_audit = json.loads(
        (m69_high_resolution_directory / "residual-audit" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m69_high_resolution = json.loads(
        (m69_high_resolution_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m69_high_resolution_validations = {
        video_id: json.loads(
            (
                m69_high_resolution_directory
                / "high-resolution-384-margin030"
                / video_id
                / "video-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
            "8d7754d0de6d315674013d5b69a0b6ba",
        )
    }
    m70_pose_profile_directory = root / "reports" / "measurement-recovery-m70"
    m70_pose_profile = json.loads(
        (m70_pose_profile_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m70_pose_profile_validations = {
        video_id: json.loads(
            (
                m70_pose_profile_directory
                / "m69-anchored-extension-v1"
                / video_id
                / "video-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
        )
    }
    m71_larger_pose_directory = root / "reports" / "measurement-recovery-m71"
    m71_larger_pose = json.loads(
        (m71_larger_pose_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m71_larger_pose_validations = {
        video_id: json.loads(
            (
                m71_larger_pose_directory
                / "rtmpose-l-384-context-extension-v1"
                / video_id
                / "m70-vs-rtmpose-l-extension-changed-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
        )
    }
    m72_additive_fusion_directory = root / "reports" / "measurement-recovery-m72"
    m72_additive_fusion = json.loads(
        (m72_additive_fusion_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m72_additive_fusion_validation = json.loads(
        (
            m72_additive_fusion_directory
            / "additive-keypoint-fusion-v1"
            / "3ae77ee3271d67de171585a5c39ddd69"
            / "m71-vs-additive-keypoint-fusion-changed-validation.json"
        ).read_text(encoding="utf-8")
    )
    m73_wholebody_mapped_directory = root / "reports" / "measurement-recovery-m73"
    m73_wholebody_mapped = json.loads(
        (m73_wholebody_mapped_directory / "summary" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    m73_wholebody_mapped_validations = {
        video_id: json.loads(
            (
                m73_wholebody_mapped_directory
                / "wholebody133-mapped-fusion-v1"
                / video_id
                / "m72-vs-wholebody133-mapped-changed-validation.json"
            ).read_text(encoding="utf-8")
        )
        for video_id in (
            "3ae77ee3271d67de171585a5c39ddd69",
            "850cb0006b406c7176eeda8d711cd065",
        )
    }
    m74_event_gap_directory = root / "reports" / "measurement-recovery-m74"
    m74_event_gap = json.loads(
        (
            m74_event_gap_directory
            / "event-bounded-gap-audit-v1"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    m75_event_gap_truth_directory = (
        root
        / "data"
        / "annotations"
        / "event-bounded-pose-gap-truth-m75-v1"
    )
    m75_event_gap_truth_manifest = json.loads(
        (m75_event_gap_truth_directory / "manifest.json").read_text(encoding="utf-8")
    )
    m75_event_gap_truth_validation = json.loads(
        (
            m75_event_gap_truth_directory
            / "compiled"
            / "validation-report.json"
        ).read_text(encoding="utf-8")
    )
    m75_event_gap_error_path = (
        root
        / "reports"
        / "measurement-recovery-m75"
        / "event-gap-keypoint-error-empty.json"
    )
    m75_event_gap_error = json.loads(
        m75_event_gap_error_path.read_text(encoding="utf-8")
    )
    m76_event_gap_feature_error_path = (
        root
        / "reports"
        / "measurement-recovery-m76"
        / "event-gap-feature-error-empty.json"
    )
    m76_event_gap_feature_error = json.loads(
        m76_event_gap_feature_error_path.read_text(encoding="utf-8")
    )
    m77_fs09_phase_truth_directory = (
        root / "data" / "annotations" / "fs09-phase-truth-m77-v1"
    )
    m77_fs09_phase_truth_manifest = json.loads(
        (m77_fs09_phase_truth_directory / "manifest.json").read_text(encoding="utf-8")
    )
    m77_fs09_phase_truth_validation = json.loads(
        (m77_fs09_phase_truth_directory / "compiled" / "validation-report.json").read_text(
            encoding="utf-8"
        )
    )
    m77_fs09_phase_error_path = (
        root / "reports" / "measurement-recovery-m77" / "fs09-phase-truth-empty.json"
    )
    m77_fs09_phase_error = json.loads(
        m77_fs09_phase_error_path.read_text(encoding="utf-8")
    )
    m78_blocker_taxonomy_audit_path = (
        root
        / "reports"
        / "measurement-recovery-m78"
        / "blocker-taxonomy-audit.json"
    )
    m78_blocker_taxonomy_audit = json.loads(
        m78_blocker_taxonomy_audit_path.read_text(encoding="utf-8")
    )
    m79_pose_diagnostic_truth_directory = (
        root
        / "data"
        / "annotations"
        / "pose-diagnostic-clip-truth-m80-v1.2.2"
    )
    m79_pose_diagnostic_truth_manifest = json.loads(
        (m79_pose_diagnostic_truth_directory / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    m79_pose_diagnostic_truth_report = json.loads(
        (
            m79_pose_diagnostic_truth_directory
            / "compiled"
            / "evaluation.json"
        ).read_text(encoding="utf-8")
    )
    m81_scoring_observer_directory = root / "reports" / "scoring-visual-observer-m81"
    m81_scoring_observer_manifest = json.loads(
        (m81_scoring_observer_directory / "manifest.json").read_text(encoding="utf-8")
    )
    if m81_scoring_observer_manifest.get("safety") != {
        "accuracy_claim": False,
        "formal_scoring_claim": False,
        "model_pose_is_ground_truth": False,
    }:
        raise ValueError("M81 scoring observer safety contract drift")
    small_roi_truth_directory = (
        root / "data" / "annotations" / "small-roi-keypoint-truth-halpe256-v1"
    )
    small_roi_truth_manifest = json.loads(
        (small_roi_truth_directory / "manifest.json").read_text(encoding="utf-8")
    )
    small_roi_truth_validation = json.loads(
        (small_roi_truth_directory / "compiled" / "validation-report.json").read_text(
            encoding="utf-8"
        )
    )
    small_roi_error_path = root / "reports" / "small-roi-keypoint-error-halpe256-v1-empty.json"
    small_roi_error_report = json.loads(small_roi_error_path.read_text(encoding="utf-8"))
    residual_computability_path = (
        root / "reports" / "residual-indicator-computability-small-roi-v1.json"
    )
    residual_computability = json.loads(
        residual_computability_path.read_text(encoding="utf-8")
    )
    residual_video_validation_path = (
        root / "reports" / "residual-computability-video-validation.json"
    )
    residual_video_validation = json.loads(
        residual_video_validation_path.read_text(encoding="utf-8")
    )
    default_pose_routing_path = root / "reports" / "default-pose-routing-audit-m37.json"
    default_pose_routing = json.loads(
        default_pose_routing_path.read_text(encoding="utf-8")
    )
    m42_full_directory = (
        root
        / "reports"
        / "fs09-pose-wave-v2-m42"
        / "850cb0006b406c7176eeda8d711cd065"
    )
    m42_worker_directory = (
        root / "runs" / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
    )
    m42_worker_summary = json.loads(
        (m42_worker_directory / "summary.json").read_text(encoding="utf-8")
    )
    full_calculation_readiness_path = m42_full_directory / "calculation-readiness.json"
    full_calculation_readiness = json.loads(
        full_calculation_readiness_path.read_text(encoding="utf-8")
    )
    default_smoke_calculation_readiness_path = (
        m42_worker_directory / "calculation-readiness.json"
    )
    default_smoke_calculation_readiness = json.loads(
        default_smoke_calculation_readiness_path.read_text(encoding="utf-8")
    )
    full_measurement_portfolio_path = (
        m42_full_directory / "indicator-measurement-portfolio.json"
    )
    full_measurement_portfolio = json.loads(
        full_measurement_portfolio_path.read_text(encoding="utf-8")
    )
    default_smoke_measurement_portfolio_path = (
        m42_worker_directory / "indicator-measurement-portfolio.json"
    )
    default_smoke_measurement_portfolio = json.loads(
        default_smoke_measurement_portfolio_path.read_text(encoding="utf-8")
    )
    full_cycle_measurement_path = m42_full_directory / "scoring-cycle-measurement.json"
    full_cycle_measurement = json.loads(
        full_cycle_measurement_path.read_text(encoding="utf-8")
    )
    default_smoke_cycle_measurement_path = (
        m42_worker_directory / "scoring-cycle-measurement.json"
    )
    default_smoke_cycle_measurement = json.loads(
        default_smoke_cycle_measurement_path.read_text(encoding="utf-8")
    )
    m41_reference_context_path = (
        root
        / "data"
        / "annotations"
        / "scoring-reference-context-v1"
        / "850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json"
    )
    m41_reference_context = json.loads(
        m41_reference_context_path.read_text(encoding="utf-8")
    )
    m41_context_report_path = (
        root / "reports" / "fs09-pose-wave-v2-context-m41-smoke.json"
    )
    m41_context_report = json.loads(
        m41_context_report_path.read_text(encoding="utf-8")
    )
    m41_worker_summary_path = (
        root
        / "runs"
        / "rtmpose-m-halpe26-default-m41-context-current-smoke"
        / "summary.json"
    )
    m41_worker_summary = json.loads(
        m41_worker_summary_path.read_text(encoding="utf-8")
    )
    m41_cycle_measurement_path = (
        root
        / "reports"
        / "scoring-cycle-measurement-halpe256-full-m41-context-pending.json"
    )
    m41_cycle_measurement = json.loads(
        m41_cycle_measurement_path.read_text(encoding="utf-8")
    )
    truth_priority_worklist = json.loads(
        (
            root
            / "reports"
            / "scoring-truth-priority-worklist-halpe256-full"
            / "worklist.json"
        ).read_text(encoding="utf-8")
    )
    truth_action_worklist = json.loads(
        (
            root
            / "reports"
            / "scoring-truth-action-worklist-halpe256-full-m53"
            / "worklist.json"
        ).read_text(encoding="utf-8")
    )
    truth_action_readiness = json.loads(
        (
            root
            / "reports"
            / "scoring-truth-refresh"
            / "m53-empty-registry-17-v1"
            / "action-readiness.json"
        ).read_text(encoding="utf-8")
    )
    truth_evidence_plan = json.loads(
        (
            root
            / "reports"
            / "scoring-truth-refresh"
            / "m53-empty-registry-17-v1"
            / "evidence-plan"
            / "plan.json"
        ).read_text(encoding="utf-8")
    )
    truth_refresh_manifest = load_latest_scoring_truth_refresh(
        root / "reports" / "scoring-truth-refresh" / "latest.json"
    )
    truth_calibration_handoff_manifest = (
        load_latest_scoring_truth_calibration_handoff(
            root
            / "reports"
            / "scoring-truth-calibration-handoff"
            / "latest.json"
        )
    )
    truth_calibration_portfolio_manifest = (
        load_latest_scoring_truth_calibration_portfolio(
            root
            / "reports"
            / "scoring-truth-calibration-portfolio"
            / "latest.json"
        )
    )
    multivideo_calculation_coverage_manifest = (
        load_latest_multivideo_indicator_calculation_coverage(
            root
            / "reports"
            / "multivideo-indicator-calculation-coverage"
            / "latest.json"
        )
    )
    multivideo_readiness_manifest = (
        load_latest_multivideo_scoring_readiness_decomposition(
            root
            / "reports"
            / "multivideo-scoring-readiness"
            / "latest.json"
        )
    )
    deployment_smoke_paths = {
        "Halpe26 Online 256×192": root
        / "runs"
        / "rtmpose-m-halpe26-online-smoke"
        / "deployment-smoke.json",
        "Halpe26 Analysis 384×288": root
        / "runs"
        / "rtmpose-m-halpe26-analysis-smoke"
        / "deployment-smoke.json",
        "WholeBody133 Analysis 256×192": root
        / "runs"
        / "rtmpose-m-wholebody133-analysis-smoke"
        / "deployment-smoke.json",
    }
    deployment_smokes = {
        label: json.loads(path.read_text(encoding="utf-8"))
        for label, path in deployment_smoke_paths.items()
    }
    diagnostic_review_paths = {
        "Halpe26 视频 3ae77e 全片": root
        / "reports"
        / "pose-diagnostic-review"
        / "m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full"
        / "queue.json",
        "Halpe26 视频 850cb0 全片": root
        / "reports"
        / "pose-diagnostic-review"
        / "m65-850cb0006b406c7176eeda8d711cd065-halpe26-full"
        / "queue.json",
        "Halpe26 视频 8d7754 全片": root
        / "reports"
        / "pose-diagnostic-review"
        / "m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full"
        / "queue.json",
    }
    diagnostic_reviews = {
        label: json.loads(path.read_text(encoding="utf-8"))
        for label, path in diagnostic_review_paths.items()
    }
    for review in diagnostic_reviews.values():
        validate_pose_diagnostic_review_queue_sources(review)
    diagnostic_truth_paths = {
        "Halpe26 视频 3ae77e 全片": root
        / "reports"
        / "pose-diagnostic-truth"
        / "m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full-v1",
        "Halpe26 视频 850cb0 全片": root
        / "reports"
        / "pose-diagnostic-truth"
        / "m65-850cb0006b406c7176eeda8d711cd065-halpe26-full-v1",
        "Halpe26 视频 8d7754 全片": root
        / "reports"
        / "pose-diagnostic-truth"
        / "m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full-v1",
    }
    diagnostic_truth = {
        label: {
            "manifest": json.loads(
                (path / "manifest.json").read_text(encoding="utf-8")
            ),
            "evaluation": json.loads(
                (path / "evaluation.json").read_text(encoding="utf-8")
            ),
        }
        for label, path in diagnostic_truth_paths.items()
    }
    for payload in diagnostic_truth.values():
        validate_pose_diagnostic_truth_pack_sources(payload["manifest"])
        validate_pose_diagnostic_evaluation(payload["evaluation"])
    diagnostic_policy_review_path = (
        root
        / "reports"
        / "pose-diagnostic-quality-gate-review-m65-three-video-empty.json"
    )
    diagnostic_policy_review = json.loads(
        diagnostic_policy_review_path.read_text(encoding="utf-8")
    )
    validate_diagnostic_policy_review(diagnostic_policy_review)
    truth_validation = json.loads(
        (
            root
            / "data"
            / "annotations"
            / "scoring-truth-pack-v1"
            / "compiled"
            / "validation-report.json"
        ).read_text(encoding="utf-8")
    )
    calibration_dataset = json.loads(
        Path(
            truth_calibration_portfolio_manifest["artifacts"]["calibration_dataset"][
                "manifest"
            ]["path"]
        ).read_text(encoding="utf-8")
    )
    calibration_readiness = json.loads(
        Path(
            truth_calibration_portfolio_manifest["artifacts"]["calibration_dataset"][
                "readiness"
            ]["path"]
        ).read_text(encoding="utf-8")
    )
    truth_evaluation = json.loads(
        (
            root
            / "reports"
            / "scoring-truth-refresh"
            / "m53-empty-registry-17-v1"
            / "scoring-truth-evaluation.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    calibration_interface = json.loads(
        (root / "reports" / "calibration-interface-report.json").read_text(
            encoding="utf-8"
        )
    )
    feasibility_registry = json.loads(
        (root / "metric-feasibility-pose-wave-v2.json").read_text(encoding="utf-8")
    )
    truth_counts = truth_validation["counts"]
    calibration_counts = calibration_dataset["counts"]
    feasibility_levels = sorted(
        {item["feasibility_level"] for item in feasibility_registry["indicators"]}
    )
    production_calibration_asset_count = len(
        _production_calibration_asset_paths(root / "calibration")
    )
    event_truth_metrics = truth_evaluation["event_evaluation"]
    feature_truth_evaluation = truth_evaluation["feature_evaluation"]
    truth_promotion_guard = truth_evaluation.get("promotion_guard", {})
    target_alignment_truth_metric = feature_truth_evaluation.get(
        "feature_metrics", {}
    ).get("target_direction_alignment_error_deg", {})
    target_alignment_truth_overall = target_alignment_truth_metric.get("overall", {})
    calibration_readiness_rows = []
    for item in calibration_readiness["indicators"]:
        counts = item["counts"]
        blockers = "、".join(item["blockers"])
        calibration_readiness_rows.append(
            "<tr>"
            f"<td><code>{html.escape(item['indicator_id'])}</code></td>"
            f"<td>{html.escape(item['registry_feasibility_level'])}</td>"
            f"<td>{html.escape(item['status'])}</td>"
            f"<td>{counts['complete_valid_feature_vectors']}/{counts['samples']}</td>"
            f"<td>{counts['grade_labels']} / {counts['ranking_labels']}</td>"
            f"<td>{counts['multi_coach_grade_overlap_samples']}</td>"
            f"<td>{html.escape(item['agreement']['status'])}</td>"
            f"<td>{str(bool(item['F3_claimed'])).lower()}</td>"
            f"<td>{html.escape(blockers)}</td>"
            "</tr>"
        )
    baseline_indicator_ids = _shared_model_indicator_ids(scoring)
    baseline_indicator_count = len(baseline_indicator_ids)
    pose_wave_scope = _pose_wave_summary(pose_wave)
    same_window_scope = _same_window_scope(same_window)
    event_disagreement_scope = _event_disagreement_summary(event_disagreement)
    fixed_boundary_scopes = {
        label: _fixed_boundary_summary(report)
        for label, report in fixed_boundary_reports.items()
    }
    pose_profile_routing_scope = _pose_profile_routing_summary(pose_profile_routing)
    scoring_blocker_scope = _scoring_blocker_audit_summary(scoring_blocker_audit)
    blocker_decomposition = scoring_blocker_scope["counts"][
        "mutually_exclusive_decomposition"
    ]
    blocker_reason_counts = scoring_blocker_scope["reason_code_counts"]
    feature_gap_scope = _feature_observation_gap_summary(feature_observation_gap_audit)
    validate_smoothing_counterfactual_coverage_sources(
        smoothing_counterfactual_coverage
    )
    validate_f2_error_budget_readiness_sources(f2_error_budget_readiness)
    smoothing_coverage_counts = smoothing_counterfactual_coverage["counts"]
    smoothing_coverage_by_feature = {
        item["feature_name"]: item
        for item in smoothing_counterfactual_coverage["feature_metrics"]
    }
    f2_error_budget_counts = f2_error_budget_readiness["counts"]
    f2_error_budget_rows = []
    for item in f2_error_budget_readiness["indicators"]:
        smoothing_features = item["smoothing_counterfactual"]["features"]
        full_count = sum(
            feature["status"] == "fully_reconstructable"
            for feature in smoothing_features
        )
        partial_names = [
            feature["feature_name"]
            for feature in smoothing_features
            if feature["status"] != "fully_reconstructable"
        ]
        smoothing_text = (
            f"{item['smoothing_counterfactual']['status']} "
            f"({full_count}/{len(smoothing_features)})"
        )
        if partial_names:
            smoothing_text += "<br><small>" + html.escape("、".join(partial_names)) + "</small>"
        f2_error_budget_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['indicator_id']))}</code></td>"
            f"<td>{html.escape(str(item['calculation']['status']))}<br><small>{int(item['calculation']['measured_feature_vectors'])}/{int(item['calculation']['candidate_instances'])}</small></td>"
            f"<td>{smoothing_text}</td>"
            f"<td>{html.escape(str(item['event_error']['status']))}</td>"
            f"<td>feature {html.escape(str(item['feature_error']['status']))}<br><small>Pose {html.escape(str(item['pose_error']['status']))} · boundary {html.escape(str(item['event_boundary_error']['status']))} · smoothing {html.escape(str(item['truth_conditioned_smoothing_error']['status']))} · missing {html.escape(str(item['missing_value_error_impact']['status']))}</small></td>"
            f"<td>{html.escape(str(item['grade_gap_assessment']['status']))}</td>"
            f"<td>{html.escape('、'.join(item['blockers']))}</td>"
            "<td>false</td>"
            "</tr>"
        )
    small_roi_scope = _small_roi_recovery_summary(
        small_roi_experiment, small_roi_video_validation
    )
    multivideo_recovery_scope = _multivideo_measurement_recovery_summary(
        multivideo_recovery, multivideo_recovery_validations
    )
    m67_recovery_scope = _m67_measurement_recovery_summary(
        m67_recovery, m67_recovery_validations
    )
    m68_router_scope = _m68_pose_router_summary(
        m68_router, m68_router_validations
    )
    m69_high_resolution_scope = _m69_high_resolution_summary(
        m69_high_resolution,
        m69_residual_audit,
        m69_high_resolution_validations,
    )
    m70_pose_profile_scope = _m70_pose_profile_ablation_summary(
        m70_pose_profile,
        m70_pose_profile_validations,
    )
    m71_larger_pose_scope = _m71_larger_pose_model_summary(
        m71_larger_pose,
        m71_larger_pose_validations,
    )
    m72_additive_fusion_scope = _m72_additive_keypoint_fusion_summary(
        m72_additive_fusion,
        m72_additive_fusion_validation,
    )
    m73_wholebody_mapped_scope = _m73_wholebody_mapped_fusion_summary(
        m73_wholebody_mapped,
        m73_wholebody_mapped_validations,
    )
    m74_event_gap_scope = _m74_event_bounded_gap_summary(m74_event_gap)
    m75_event_gap_truth_scope = _m75_event_gap_truth_summary(
        m75_event_gap_truth_directory,
        m75_event_gap_truth_manifest,
        m75_event_gap_truth_validation,
        m75_event_gap_error,
    )
    m76_event_gap_feature_truth_scope = _m76_event_gap_feature_truth_summary(
        m76_event_gap_feature_error
    )
    m77_fs09_phase_truth_scope = _m77_fs09_phase_truth_summary(
        m77_fs09_phase_truth_manifest,
        m77_fs09_phase_truth_validation,
        m77_fs09_phase_error,
    )
    m78_blocker_taxonomy_scope = _m78_blocker_taxonomy_summary(
        m78_blocker_taxonomy_audit
    )
    m79_pose_diagnostic_truth_scope = _m79_pose_diagnostic_clip_truth_summary(
        m79_pose_diagnostic_truth_manifest,
        m79_pose_diagnostic_truth_report,
    )
    m81_scoring_observer_scope = {
        "video_count": len(m81_scoring_observer_manifest["videos"]),
        "frame_count": sum(
            int(item["frame_count"])
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "event_count": sum(
            int(item["event_count"])
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "indicator_instance_count": sum(
            int(item["indicator_instance_count"])
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "measured_count": sum(
            int(item["feature_status_counts"].get("measured", 0))
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "feature_unavailable_count": sum(
            int(item["feature_status_counts"].get("unavailable", 0))
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "calibration_required_count": sum(
            int(item["scoring_status_counts"].get("calibration_required", 0))
            for item in m81_scoring_observer_manifest["videos"]
        ),
        "score_unavailable_count": sum(
            int(item["scoring_status_counts"].get("unavailable", 0))
            for item in m81_scoring_observer_manifest["videos"]
        ),
    }
    small_roi_truth_scope = _small_roi_truth_summary(
        small_roi_truth_manifest, small_roi_truth_validation, small_roi_error_report
    )
    residual_computability_scope = _residual_computability_summary(
        residual_computability, residual_video_validation
    )
    default_pose_routing_scope = _default_pose_routing_summary(default_pose_routing)
    full_calculation_scope = _calculation_readiness_summary(
        full_calculation_readiness
    )
    default_smoke_calculation_scope = _calculation_readiness_summary(
        default_smoke_calculation_readiness
    )
    full_measurement_portfolio_scope = _measurement_portfolio_summary(
        full_measurement_portfolio
    )
    default_smoke_measurement_portfolio_scope = _measurement_portfolio_summary(
        default_smoke_measurement_portfolio
    )
    full_cycle_measurement_scope = _cycle_measurement_summary(
        full_cycle_measurement
    )
    default_smoke_cycle_measurement_scope = _cycle_measurement_summary(
        default_smoke_cycle_measurement
    )
    m41_reference_observations = validate_scoring_reference_context(
        m41_reference_context,
        expected_video_id="850cb0006b406c7176eeda8d711cd065",
        expected_video_sha256=(
            "71D3F59B7A8B966EF7645CAC412CB03679376F70A2F080D270391A32697D7FA9"
        ),
    )
    if any(
        observation["status"] != "pending"
        for observation in m41_reference_observations.values()
    ):
        raise ValueError("published M41 reference worklist must remain blank")
    m41_cycle_measurement_scope = _cycle_measurement_summary(
        m41_cycle_measurement
    )
    m41_context_summary = m41_context_report["summary"][
        "scoring_reference_context"
    ]
    if (
        int(m41_context_summary["available_alignment_count"]) != 0
        or m41_context_summary["movement_direction_inferred_as_target"] is not False
        or m41_context_summary["grades_or_thresholds_generated"] is not False
    ):
        raise ValueError("published M41 context report has unsafe reference claims")
    m41_worker_context_summary = m41_worker_summary["scoring_reference_context"]
    if (
        int(m41_worker_context_summary["input_observation_count"]) != 3
        or int(m41_worker_context_summary["applied_observation_count"]) != 3
        or int(m41_worker_context_summary["available_alignment_count"]) != 0
        or m41_worker_context_summary["input_status_counts"] != {"pending": 3}
    ):
        raise ValueError("M41 Worker reference-context accounting is inconsistent")
    m42_worker_loop = m42_worker_summary["minimum_scoring_loop"]
    m42_worker_context = m42_worker_summary["scoring_reference_context"]
    if (
        m42_worker_loop["loop_version"] != "minimum-scoring-loop-v0.6.0"
        or m42_worker_loop["model_versions"]["feasibility_registry"]
        != "pose-wave-2026-08-22.15"
        or m42_worker_loop["result_state"]["measured_indicator_count"] != 13
        or m42_worker_context["available_alignment_count"] != 0
        or m42_worker_context["pending_count"] != 3
        or m42_worker_loop["grade_counts"]
    ):
        raise ValueError("published M42 GPU scoring-vector smoke is inconsistent")
    truth_priority_scope = _truth_priority_worklist_summary(truth_priority_worklist)
    truth_action_scope = _truth_action_worklist_summary(truth_action_worklist)
    truth_action_readiness_scope = _truth_action_readiness_summary(
        truth_action_readiness
    )
    truth_evidence_plan_scope = _truth_evidence_plan_summary(truth_evidence_plan)
    truth_refresh_scope = _truth_refresh_summary(truth_refresh_manifest)
    truth_refresh_index_href = Path(
        os.path.relpath(
            Path(truth_refresh_scope["artifacts"]["index_html"]["path"]),
            directory,
        )
    ).as_posix()
    truth_calibration_handoff_scope = _truth_calibration_handoff_summary(
        truth_calibration_handoff_manifest
    )
    truth_calibration_handoff_index_path = Path(
        truth_calibration_handoff_scope["artifacts"]["index_html"]["path"]
    )
    truth_calibration_handoff_index_href = Path(
        os.path.relpath(truth_calibration_handoff_index_path, directory)
    ).as_posix()
    truth_calibration_handoff_repo_path = truth_calibration_handoff_index_path.relative_to(
        root
    ).as_posix()
    truth_calibration_portfolio_scope = _truth_calibration_portfolio_summary(
        truth_calibration_portfolio_manifest
    )
    truth_calibration_portfolio_index_path = Path(
        truth_calibration_portfolio_scope["artifacts"]["index_html"]["path"]
    )
    truth_calibration_portfolio_index_href = Path(
        os.path.relpath(truth_calibration_portfolio_index_path, directory)
    ).as_posix()
    truth_calibration_portfolio_repo_path = (
        truth_calibration_portfolio_index_path.relative_to(root).as_posix()
    )
    multivideo_calculation_coverage_scope = (
        _multivideo_calculation_coverage_summary(
            multivideo_calculation_coverage_manifest
        )
    )
    multivideo_calculation_coverage_index_path = Path(
        multivideo_calculation_coverage_scope["artifacts"]["index_html"]["path"]
    )
    multivideo_calculation_coverage_index_href = Path(
        os.path.relpath(multivideo_calculation_coverage_index_path, directory)
    ).as_posix()
    multivideo_calculation_coverage_repo_path = (
        multivideo_calculation_coverage_index_path.relative_to(root).as_posix()
    )
    multivideo_indicator_rows = []
    for item in multivideo_calculation_coverage_scope["indicators"]:
        failure_text = "、".join(
            f"{reason}:{count}"
            for reason, count in item["measurement_failure_reason_counts"].items()
        ) or "—"
        multivideo_indicator_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['indicator_id']))}</code></td>"
            f"<td>{int(item['measured_feature_vectors'])}/{int(item['candidate_instances'])} ({float(item['measurement_rate']) * 100:.2f}%)</td>"
            f"<td>{int(item['videos_with_measured_vector'])}/{int(multivideo_calculation_coverage_scope['counts']['videos'])}</td>"
            f"<td>{html.escape(failure_text)}</td>"
            "<td>—（无真值/标定）</td>"
            "</tr>"
        )
    multivideo_readiness_scope = _multivideo_readiness_summary(
        multivideo_readiness_manifest
    )
    multivideo_readiness_index_path = Path(
        multivideo_readiness_scope["artifacts"]["index_html"]["path"]
    )
    multivideo_readiness_index_href = Path(
        os.path.relpath(multivideo_readiness_index_path, directory)
    ).as_posix()
    multivideo_readiness_repo_path = (
        multivideo_readiness_index_path.relative_to(root).as_posix()
    )
    readiness_category_labels = {
        "measurement_hard_fail": "事件测量 hard fail",
        "measurement_vector_incomplete": "Pose 测量向量不完整",
        "scoring_context_incomplete": "评分上下文不完整",
        "scoring_evidence_blocked": "身份/关键点/阶段等评分证据未验证",
        "calibration_only_missing": "仅缺教练标定与独立测试",
    }
    readiness_decomposition_rows = []
    readiness_total = int(
        multivideo_readiness_scope["counts"]["indicator_event_instances"]
    )
    for category, label in readiness_category_labels.items():
        count = int(
            multivideo_readiness_scope["counts"]["exclusive_decomposition"][
                category
            ]
        )
        readiness_decomposition_rows.append(
            "<tr>"
            f"<td>{html.escape(label)}<br><code>{html.escape(category)}</code></td>"
            f"<td>{count}</td>"
            f"<td>{count / readiness_total * 100:.2f}%</td>"
            "</tr>"
        )
    multivideo_recovery_rows = []
    for item in multivideo_readiness_scope["scoring_block_recovery_priority"]:
        multivideo_recovery_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['flag']))}</code></td>"
            f"<td>{int(item['indicator_instance_count'])}</td>"
            f"<td>{int(item['unique_event_count'])}</td>"
            f"<td>{int(item['sole_block_to_calibration_required_indicator_instance_count'])}</td>"
            f"<td>{int(item['sole_block_to_calibration_required_unique_event_count'])}</td>"
            f"<td><code>{html.escape(str(item['truth_requirement']))}</code></td>"
            "</tr>"
        )
    deployment_smoke_scopes = {
        label: _deployment_smoke_summary(report)
        for label, report in deployment_smokes.items()
    }
    diagnostic_review_scopes = {
        label: _pose_diagnostic_review_summary(report)
        for label, report in diagnostic_reviews.items()
    }
    diagnostic_truth_scopes = {
        label: _pose_diagnostic_truth_summary(
            payload["manifest"], payload["evaluation"]
        )
        for label, payload in diagnostic_truth.items()
    }
    diagnostic_policy_review_scope = _pose_diagnostic_policy_review_summary(
        diagnostic_policy_review
    )
    full_detail_href = _report_href(
        pose_wave["artifacts"]["scoring_loop_report_html"],
        root=root,
        page_directory=directory,
    )
    small_roi_video_href = _report_href(
        small_roi_video_validation["video"]["path"],
        root=root,
        page_directory=directory,
    )
    small_roi_report_href = _report_href(
        str(small_roi_experiment_directory / "report.json"),
        root=root,
        page_directory=directory,
    )
    small_roi_fixed_href = _report_href(
        str(small_roi_experiment_directory / "fixed-boundary-comparison.json"),
        root=root,
        page_directory=directory,
    )
    multivideo_recovery_index_href = _report_href(
        str(multivideo_recovery_directory / "index.html"),
        root=root,
        page_directory=directory,
    )
    multivideo_recovery_report_href = _report_href(
        str(multivideo_recovery_directory / "report.json"),
        root=root,
        page_directory=directory,
    )
    multivideo_recovery_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in multivideo_recovery_validations.items()
    }
    m67_recovery_report_href = _report_href(
        str(m67_recovery_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m67_recovery_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m67_recovery_validations.items()
    }
    m68_router_report_href = _report_href(
        str(m68_router_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m68_router_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m68_router_validations.items()
    }
    m69_high_resolution_report_href = _report_href(
        str(m69_high_resolution_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m69_residual_audit_href = _report_href(
        str(m69_high_resolution_directory / "residual-audit" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m69_high_resolution_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m69_high_resolution_validations.items()
    }
    m70_pose_profile_report_href = _report_href(
        str(m70_pose_profile_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m70_pose_profile_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m70_pose_profile_validations.items()
    }
    m71_larger_pose_report_href = _report_href(
        str(m71_larger_pose_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m71_larger_pose_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m71_larger_pose_validations.items()
    }
    m72_additive_fusion_report_href = _report_href(
        str(m72_additive_fusion_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m72_additive_fusion_video_href = _report_href(
        m72_additive_fusion_validation["video"]["path"],
        root=root,
        page_directory=directory,
    )
    m73_wholebody_mapped_report_href = _report_href(
        str(m73_wholebody_mapped_directory / "summary" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m73_wholebody_mapped_video_hrefs = {
        video_id: _report_href(
            validation["video"]["path"],
            root=root,
            page_directory=directory,
        )
        for video_id, validation in m73_wholebody_mapped_validations.items()
    }
    m74_event_gap_report_href = _report_href(
        str(m74_event_gap_directory / "event-bounded-gap-audit-v1" / "report.json"),
        root=root,
        page_directory=directory,
    )
    m75_event_gap_truth_href = _report_href(
        str(m75_event_gap_truth_directory / "review.html"),
        root=root,
        page_directory=directory,
    )
    m75_event_gap_truth_validation_href = _report_href(
        str(m75_event_gap_truth_directory / "compiled" / "validation-report.json"),
        root=root,
        page_directory=directory,
    )
    m75_event_gap_error_href = _report_href(
        str(m75_event_gap_error_path),
        root=root,
        page_directory=directory,
    )
    m76_event_gap_feature_error_href = _report_href(
        str(m76_event_gap_feature_error_path),
        root=root,
        page_directory=directory,
    )
    m77_fs09_phase_truth_href = _report_href(
        str(m77_fs09_phase_truth_directory / "review.html"),
        root=root,
        page_directory=directory,
    )
    m77_fs09_phase_validation_href = _report_href(
        str(m77_fs09_phase_truth_directory / "compiled" / "validation-report.json"),
        root=root,
        page_directory=directory,
    )
    m77_fs09_phase_error_href = _report_href(
        str(m77_fs09_phase_error_path),
        root=root,
        page_directory=directory,
    )
    m78_blocker_taxonomy_audit_href = _report_href(
        str(m78_blocker_taxonomy_audit_path),
        root=root,
        page_directory=directory,
    )
    m79_pose_diagnostic_truth_review_href = _report_href(
        str(m79_pose_diagnostic_truth_directory / "review.html"),
        root=root,
        page_directory=directory,
    )
    m79_pose_diagnostic_truth_adjudicate_href = _report_href(
        str(m79_pose_diagnostic_truth_directory / "adjudicate.html"),
        root=root,
        page_directory=directory,
    )
    m79_pose_diagnostic_truth_evaluation_href = _report_href(
        str(m79_pose_diagnostic_truth_directory / "compiled" / "evaluation.json"),
        root=root,
        page_directory=directory,
    )
    m81_scoring_observer_href = _report_href(
        str(m81_scoring_observer_directory / "index.html"),
        root=root,
        page_directory=directory,
    )
    small_roi_truth_href = _report_href(
        str(small_roi_truth_directory / "review.html"),
        root=root,
        page_directory=directory,
    )
    small_roi_truth_validation_href = _report_href(
        str(small_roi_truth_directory / "compiled" / "validation-report.json"),
        root=root,
        page_directory=directory,
    )
    small_roi_error_href = _report_href(
        str(small_roi_error_path),
        root=root,
        page_directory=directory,
    )
    residual_computability_href = _report_href(
        str(residual_computability_path),
        root=root,
        page_directory=directory,
    )
    residual_video_href = _report_href(
        residual_video_validation["video"]["path"],
        root=root,
        page_directory=directory,
    )
    residual_video_validation_href = _report_href(
        str(residual_video_validation_path),
        root=root,
        page_directory=directory,
    )
    default_pose_routing_href = _report_href(
        str(default_pose_routing_path),
        root=root,
        page_directory=directory,
    )
    default_worker_smoke_href = _report_href(
        str(
            root
            / "runs"
            / "rtmpose-m-halpe26-default-m37-smoke"
            / "deployment-smoke.json"
        ),
        root=root,
        page_directory=directory,
    )
    default_worker_scoring_report_href = _report_href(
        str(
            root
            / "runs"
            / "rtmpose-m-halpe26-default-m37-smoke"
            / "scoring-loop-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    full_calculation_readiness_href = _report_href(
        str(full_calculation_readiness_path),
        root=root,
        page_directory=directory,
    )
    default_smoke_calculation_readiness_href = _report_href(
        str(default_smoke_calculation_readiness_path),
        root=root,
        page_directory=directory,
    )
    default_m38_analysis_report_href = _report_href(
        str(
            m42_worker_directory / "analysis-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    full_measurement_portfolio_href = _report_href(
        str(full_measurement_portfolio_path),
        root=root,
        page_directory=directory,
    )
    default_smoke_measurement_portfolio_href = _report_href(
        str(default_smoke_measurement_portfolio_path),
        root=root,
        page_directory=directory,
    )
    default_m39_analysis_report_href = _report_href(
        str(
            m42_worker_directory / "analysis-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    full_cycle_measurement_href = _report_href(
        str(full_cycle_measurement_path), root=root, page_directory=directory
    )
    default_smoke_cycle_measurement_href = _report_href(
        str(default_smoke_cycle_measurement_path),
        root=root,
        page_directory=directory,
    )
    default_m40_analysis_report_href = _report_href(
        str(
            m42_worker_directory / "analysis-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    m41_reference_context_href = _report_href(
        str(m41_reference_context_path), root=root, page_directory=directory
    )
    m41_context_report_href = _report_href(
        str(m41_context_report_path), root=root, page_directory=directory
    )
    m41_cycle_measurement_href = _report_href(
        str(m41_cycle_measurement_path), root=root, page_directory=directory
    )
    m41_analysis_report_href = _report_href(
        str(
            root
            / "reports"
            / "fs09-pose-wave-v2-context-m41"
            / "850cb0006b406c7176eeda8d711cd065"
            / "scoring-loop-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    m41_worker_analysis_report_href = _report_href(
        str(
            root
            / "runs"
            / "rtmpose-m-halpe26-default-m41-context-current-smoke"
            / "analysis-report.html"
        ),
        root=root,
        page_directory=directory,
    )
    m42_worker_analysis_report_href = _report_href(
        str(m42_worker_directory / "analysis-report.html"),
        root=root,
        page_directory=directory,
    )
    m42_full_analysis_report_href = _report_href(
        str(m42_full_directory / "scoring-loop-report.html"),
        root=root,
        page_directory=directory,
    )
    same_window_detail_hrefs = {}
    for model_key, model in same_window["models"].items():
        model_report_path = Path(model["report"])
        if not model_report_path.is_absolute():
            model_report_path = root / model_report_path
        model_report = json.loads(model_report_path.read_text(encoding="utf-8"))
        same_window_detail_hrefs[model_key] = _report_href(
            model_report["artifacts"]["scoring_loop_report_html"],
            root=root,
            page_directory=directory,
        )
    legacy_by_backend = {item["backend"]: item for item in legacy["models"]}
    common_by_backend = {
        item["backend"]: item
        for item in scoring["common_event_feature_comparison"]["models"]
    }
    summary_rows = []
    indicator_rows = []
    for model in scoring["models"]:
        backend = model["backend"]
        statuses = model["score_status_counts"]
        total = sum(statuses.values())
        unavailable = statuses.get("unavailable", 0)
        left = model["foot_angle_diagnostics"]["left_ankle_shank_foot_angle_deg"]
        right = model["foot_angle_diagnostics"]["right_ankle_shank_foot_angle_deg"]
        legacy_model = legacy_by_backend[backend]
        common_model = common_by_backend[backend]
        summary_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(LABELS[backend])}</strong></td>"
            f"<td>{unavailable}/{total} ({unavailable / max(total, 1) * 100:.2f}%)</td>"
            f"<td>{statuses.get('calibration_required', 0)}</td>"
            f"<td>{common_model['unavailable_indicator_records']}/{common_model['indicator_records']} ({common_model['unavailable_rate'] * 100:.2f}%)</td>"
            f"<td>{_percent(left['valid_rate'])} / {_percent(right['valid_rate'])}</td>"
            f"<td>{left['median'] if left['median'] is not None else '—'}° / {right['median'] if right['median'] is not None else '—'}°</td>"
            f"<td>{legacy_model['observed_evidence']['partial']} / {legacy_model['observed_evidence']['blocked']}</td>"
            f"<td>未纳入本轮闭环（{legacy_model['score_blocked_all_videos']} 条）</td>"
            "</tr>"
        )
        for indicator in baseline_indicator_ids:
            value = model["indicator_feature_validity"][indicator]
            indicator_rows.append(
                f"<tr><td>{html.escape(LABELS[backend])}</td><td>{indicator}</td>"
                f"<td>{value['valid']}/{value['total']}</td><td>{_percent(value['valid_rate'])}</td></tr>"
            )
    semantic_labels = {
        "hip_center_motion": "髋中心位移/速度",
        "body_center_or_center_of_mass": "重心/身体中心",
        "knee_ankle_mechanics": "膝踝力学",
        "wrist_elbow_motion": "腕肘轨迹与速度",
        "shoulder_hip_rotation": "肩髋旋转",
        "head_orientation": "头部位置与朝向",
        "fine_grip_or_hand_articulation": "精细握拍/手部关节",
    }
    semantic_status = {
        "direct_center_point_plus_bilateral_hips": "RTMPose 新增原生 hip 中心，并保留双髋交叉检查",
        "derived_2d_proxy_only": "只能派生二维代理，不是真实人体质心",
        "improved_by_big_toe_small_toe_and_heel": "大/小脚趾和脚跟补足前足方向，改善二维踝角",
        "direct_wrist_and_elbow_only": "腕肘点直接存在，但没有手掌、手指和握拍几何",
        "direct_source_points": "肩线与髋线直接存在，但胸/腰椎不能分段",
        "improved_head_and_neck_but_gaze_is_proxy": "新增 head/neck 改善头轴，真实视线仍不可用",
        "missing": "Halpe26 缺失",
    }
    wholebody_status = {
        "derived_from_bilateral_hips": "双髋可派生骨盆中心，但不是模型原生点",
        "derived_2d_proxy_only": "仍只能派生二维重心代理，不是真实人体质心",
        "improved_by_big_toe_small_toe_and_heel": "保留双脚6点，可计算二维足方向和踝角代理",
        "improved_by_42_hand_landmarks": "新增双手42点，可观测手掌/手指/拇指结构",
        "direct_source_points_spine_still_missing": "肩髋直接可见，胸椎/腰椎分段仍缺失",
        "improved_by_68_face_landmarks_true_gaze_missing": "新增面部68点，改善头向代理但不等于真实视线",
        "hand_geometry_present_racket_relation_missing": "手部几何已存在；精确握拍仍需球拍轴和教练真值",
    }
    semantic_rows = []
    for item in full_body_coverage["semantic_requirement_coverage"]:
        semantic_rows.append(
            "<tr>"
            f"<td>{html.escape(semantic_labels[item['capability']])}</td>"
            f"<td>{item['card_text_mention_count']}</td>"
            f"<td>{html.escape(semantic_status[item['halpe26_status']])}</td>"
            f"<td>{html.escape(wholebody_status[item['wholebody133_status']])}</td>"
            "</tr>"
        )
    video_name = "yolo-vs-rtmpose-scoring-comparison-browser.mp4"
    highlight_name = "yolo-vs-rtmpose-scoring-highlight-40s-60s-browser.mp4"
    keypoint_focus_name = "yolo-vs-rtmpose-keypoint-focus-31s-51s-browser.mp4"
    full_body_name = "rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4"
    wholebody_name = "rtmpose-wholebody133-full-detail-31s-51s-browser.mp4"
    three_way_name = "yolo17-vs-halpe26-vs-wholebody133-31s-51s-browser.mp4"
    pose_wave_rows = []
    for indicator_id in pose_wave_scope["indicator_ids"]:
        item = pose_wave["indicator_results"][indicator_id]
        measured = item["feature_status_counts"].get("measured", 0)
        calibration = item["scoring_status_counts"].get("calibration_required", 0)
        unavailable_count = item["scoring_status_counts"].get("unavailable", 0)
        pose_wave_rows.append(
            "<tr>"
            f"<td>{html.escape(indicator_id)}</td>"
            f"<td>{measured}/{item['instances']}</td>"
            f"<td>{calibration}</td>"
            f"<td>{unavailable_count}</td>"
            "<td>—</td>"
            "</tr>"
        )
    same_window_rows = []
    same_window_blocker_rows = []
    for model_key, model_label in (
        ("halpe26", "RTMPose-M Halpe26（26点）"),
        ("wholebody133", "RTMPose-M WholeBody（133点）"),
    ):
        model = same_window["models"][model_key]
        event_counts = model["event_counts"]
        score_counts = model["score_status_counts"]
        gate_counts = model["quality_gate_status_counts"]
        same_window_rows.append(
            "<tr>"
            f'<td><strong><a href="{html.escape(same_window_detail_hrefs[model_key])}">{html.escape(model_label)}</a></strong></td>'
            f"<td>{' / '.join(str(event_counts.get(code, 0)) for code in same_window_scope['event_codes'])}</td>"
            f"<td>{sum(item[model_key]['feature_valid'] for item in same_window['indicators'].values())}/"
            f"{sum(item[model_key]['feature_total'] for item in same_window['indicators'].values())}</td>"
            f"<td>{score_counts.get('calibration_required', 0)}</td>"
            f"<td>{score_counts.get('unavailable', 0)}</td>"
            f"<td>{gate_counts.get('pass', 0)} / {gate_counts.get('advisory', 0)} / {gate_counts.get('hard_fail', 0)}</td>"
            f"<td>{model['grade_count']}</td>"
            "</tr>"
        )
        reason_counts = model.get("score_block_reason_code_counts", {})
        same_window_blocker_rows.append(
            "<tr>"
            f"<td>{html.escape(model_label)}</td>"
            + "".join(
                f"<td>{int(reason_counts.get(reason, 0))}</td>"
                for reason, _ in SCORE_BLOCKER_REASON_LABELS
            )
            + "</tr>"
        )
    same_window_event_header = " / ".join(same_window_scope["event_codes"])
    pose_wave_event_text = _event_counts_text(pose_wave_scope["event_counts"])
    same_window_markdown_parts = []
    for model_key, model_label in (
        ("halpe26", "Halpe26"),
        ("wholebody133", "WholeBody133"),
    ):
        model = same_window["models"][model_key]
        score_counts = model["score_status_counts"]
        feature_measured = sum(
            item[model_key]["feature_valid"]
            for item in same_window["indicators"].values()
        )
        feature_total = sum(
            item[model_key]["feature_total"]
            for item in same_window["indicators"].values()
        )
        same_window_markdown_parts.append(
            f"{model_label} 特征层为 {feature_measured}/{feature_total} 条 `measured`，"
            f"评分层为 {score_counts.get('calibration_required', 0)} 条 "
            f"`calibration_required`、{score_counts.get('unavailable', 0)} 条 "
            "`unavailable`"
        )
    same_window_markdown_summary = "；".join(same_window_markdown_parts)
    event_disagreement_rows = []
    for result in event_disagreement_scope["threshold_results"]:
        absolute = result["mean_absolute_differences_ms"]
        event_disagreement_rows.append(
            "<tr>"
            f"<td>{float(result['minimum_segment_iou']):.1f}</td>"
            f"<td>{int(result['matched_count'])}</td>"
            f"<td>{float(result['left_to_right_match_rate']) * 100:.2f}% / "
            f"{float(result['right_to_left_match_rate']) * 100:.2f}%</td>"
            f"<td>{float(result['mean_segment_iou']):.4f}</td>"
            f"<td>{float(absolute['start_difference_ms']):.2f} / "
            f"{float(absolute['end_difference_ms']):.2f} / "
            f"{float(absolute['center_difference_ms']):.2f} / "
            f"{float(absolute['duration_difference_ms']):.2f}</td>"
            "</tr>"
        )
    fixed_boundary_rows = []
    for label, scope in fixed_boundary_scopes.items():
        a_name, b_name = scope["model_order"]
        a_model, b_model = scope["models"][a_name], scope["models"][b_name]
        states = scope["validity_states"]
        fixed_boundary_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(label)}</strong><br>candidate_source_not_truth</td>"
            f"<td>{scope['event_count']}</td>"
            f"<td>{scope['feature_pair_count']}（{scope['feature_count']} 种 required feature）</td>"
            f"<td>{states.get('both_valid', 0)} / {states.get('model_a_only', 0)} / "
            f"{states.get('model_b_only', 0)} / {states.get('neither_valid', 0)}</td>"
            f"<td>{a_model['measured_indicator_event_count']}/{a_model['indicator_event_pair_count']}</td>"
            f"<td>{b_model['measured_indicator_event_count']}/{b_model['indicator_event_pair_count']}</td>"
            "</tr>"
        )
    selected_features = (
        "hip_center_y_body",
        "stance_width_body",
        "left_knee_flexion_deg",
        "right_knee_flexion_deg",
        "torso_lean_deg",
        "hip_center_motion_direction_deg",
        "launch_direction_deg",
        "drive_side_code",
        "launch_side_code",
        "braking_side_code",
    )
    fixed_feature_rows = []
    for feature_name in selected_features:
        cells = []
        for report in fixed_boundary_reports.values():
            cells.append(
                _metric_disagreement_text(report["feature_metrics"][feature_name])
            )
        fixed_feature_rows.append(
            "<tr>"
            f"<td><code>{html.escape(feature_name)}</code></td>"
            f"<td>{cells[0]}</td><td>{cells[1]}</td>"
            "</tr>"
        )
    hip_mads = [
        float(
            report["feature_metrics"]["hip_center_y_body"]
            ["cross_model_disagreement"]
            ["mean_absolute_cross_model_disagreement"]
        )
        for report in fixed_boundary_reports.values()
    ]
    full_profile_rows = []
    current_profile = pose_profile_routing_scope["current_primary"]
    current_fixed = next(iter(pose_profile_routing_scope["comparisons"].values()))[
        "fixed_boundary_measurement"
    ]
    for profile_name, profile in pose_profile_routing_scope["profiles"].items():
        if profile_name == current_profile:
            fixed_measured = int(current_fixed["current_primary_measured"])
            fixed_total = int(current_fixed["indicator_event_count"])
            fixed_delta = 0
            event_agreement = "基准边界来源"
        else:
            comparison = pose_profile_routing_scope["comparisons"][profile_name]
            fixed = comparison["fixed_boundary_measurement"]
            event = comparison["event_candidate_agreement"]
            fixed_measured = int(fixed["candidate_measured"])
            fixed_total = int(fixed["indicator_event_count"])
            fixed_delta = int(fixed["candidate_minus_current"])
            event_agreement = (
                f"{int(event['matched_count'])}/{int(event['current_event_count'])}；"
                f"IoU {float(event['mean_segment_iou']):.4f}；"
                f"center MAE {float(event['boundary_mean_absolute_difference_ms']['center_difference_ms']):.2f} ms"
            )
        score_counts = profile["score_status_counts"]
        full_profile_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(LABELS.get(profile_name, profile_name))}</strong><br>"
            f"{html.escape(profile['native_keypoint_format'])} / {int(profile['native_keypoint_count'])} 点</td>"
            f"<td>{int(profile['event_count'])} 个候选；"
            f"{int(profile['measured_indicator_event_count'])}/{int(profile['indicator_event_count'])} measured</td>"
            f"<td>{fixed_measured}/{fixed_total}</td>"
            f"<td>{fixed_delta:+d}</td>"
            f"<td>{html.escape(event_agreement)}</td>"
            f"<td>{int(score_counts.get('calibration_required', 0))} / "
            f"{int(score_counts.get('unavailable', 0))} / 0 grade</td>"
            f"<td>{html.escape(str(profile['role']))}</td>"
            "</tr>"
        )
    blocker_priority_rows = []
    for item in scoring_blocker_scope["priority"]:
        flag = str(item["flag"])
        blocker_priority_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(SCORE_BLOCK_FLAG_LABELS.get(flag, flag))}</strong><br><code>{html.escape(flag)}</code></td>"
            f"<td>{int(item['indicator_record_count'])} / {int(item['unique_event_count'])}</td>"
            f"<td>{int(item['complete_feature_score_only_record_count'])} / "
            f"{int(item['complete_feature_score_only_unique_event_count'])}</td>"
            f"<td>{int(item['sole_block_recoverable_to_calibration_required_count'])}</td>"
            f"<td><code>{html.escape(str(item['truth_requirement']))}</code></td>"
            "</tr>"
        )
    feature_gap_reason_labels = {
        "valid_fraction_below_quality_gate": "事件内有效 Pose 覆盖不足",
        "bilateral_rise_proxy_not_observable": "双脚同步上抬代理不可观察",
        "insufficient_variability_samples": "姿态波动样本不足",
        "insufficient_valid_speed_and_angular_velocity_samples": "速度/角速度样本不足",
    }
    feature_gap_rows = []
    for item in feature_gap_scope["reasons"]:
        feature_gap_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(feature_gap_reason_labels.get(str(item['reason']), str(item['reason'])))}</strong><br><code>{html.escape(str(item['reason']))}</code></td>"
            f"<td>{int(item['unique_event_feature_count'])}</td>"
            f"<td>{int(item['affected_indicator_instance_count'])} / {int(item['affected_event_count'])}</td>"
            f"<td><code>{html.escape(str(item['required_action']))}</code></td>"
            "</tr>"
        )
    feature_gap_profile_rows = []
    for item in feature_gap_scope["profiles"]:
        profile = str(item["alternative_profile"])
        feature_gap_profile_rows.append(
            "<tr>"
            f"<td>{html.escape(LABELS.get(profile, profile))}</td>"
            f"<td>{int(item['current_unavailable_alternative_measured_count'])}</td>"
            f"<td>{int(item['current_measured_alternative_unavailable_count'])}</td>"
            "<td>禁止自动回退；仅为同候选边界可观测性旁证</td>"
            "</tr>"
        )
    residual_indicator_rows = []
    for item in residual_computability_scope["per_indicator"]:
        evidence = item.get("evidence_example", {})
        residual_indicator_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['indicator_id']))}</code></td>"
            f"<td>{int(item['measured_instance_count'])}/{int(item['instance_count'])}</td>"
            f"<td>{int(item['unavailable_instance_count'])}</td>"
            f"<td>{len(item.get('required_features', []))}</td>"
            f"<td><code>{html.escape(str(evidence.get('event_id', '—')))}</code></td>"
            f"<td>{str(bool(item.get('all_measured_instances_contract_complete'))).lower()}</td>"
            "</tr>"
        )
    default_pose_indicator_rows = []
    default_smoke_calculation_by_id = {
        item["indicator_id"]: item
        for item in default_smoke_calculation_scope["per_indicator"]
    }
    for item in default_pose_routing_scope["full"]["per_indicator"]:
        smoke_item = default_smoke_calculation_by_id[item["indicator_id"]]
        default_pose_indicator_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['indicator_id']))}</code></td>"
            f"<td>{int(smoke_item['measured_instance_count'])}/{int(smoke_item['event_instance_count'])}<br><small>{html.escape(smoke_item['calculation_status'])}</small></td>"
            f"<td>{int(item['measured_instance_count'])}/{int(item['instance_count'])}</td>"
            f"<td>{int(item['unavailable_instance_count'])}</td>"
            f"<td>{str(bool(item['has_real_measured_instance'])).lower()}</td>"
            "</tr>"
        )
    representative_measurement_rows = []
    for item in full_measurement_portfolio_scope["indicators"]:
        representative = item["representative_measurement"]
        if representative is None:
            feature_text = "—"
            event_text = "—"
        else:
            event_text = (
                f"{representative['event_id']} · "
                f"{representative['start_ms']}–{representative['end_ms']} ms"
            )
            feature_text = "<br>".join(
                f"<code>{html.escape(str(feature['feature_name']))}</code> = "
                f"{html.escape(str(feature['value']))} {html.escape(str(feature['unit']))} "
                f"<small>(conf {float(feature['confidence']):.3f})</small>"
                for feature in representative["feature_vector"]
            )
        representative_measurement_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item['indicator_id']))}</code></td>"
            f"<td>{html.escape(str(item['measurement_status']))}</td>"
            f"<td>{html.escape(event_text)}</td>"
            f"<td>{feature_text}</td>"
            f"<td>{int(item['measured_candidate_count'])}/{int(item['candidate_event_count'])}</td>"
            "<td>—（F2，无 A～E）</td>"
            "</tr>"
        )
    deployment_rows = []
    deployment_markdown_parts = []
    for label, scope in deployment_smoke_scopes.items():
        statuses = scope["score_status_counts"]
        event_text = " / ".join(
            f"{code} {count}" for code, count in sorted(scope["event_counts"].items())
        )
        report_href = _report_href(
            deployment_smoke_paths[label], root=root, page_directory=directory
        )
        deployment_rows.append(
            "<tr>"
            f'<td><a href="{html.escape(report_href)}"><strong>{html.escape(label)}</strong></a></td>'
            f"<td>{html.escape(scope['native_keypoint_format'])} / {scope['native_keypoint_count']} 点</td>"
            f"<td>{scope['processed_frames']} / {scope['effective_processed_fps']:.3f}</td>"
            f"<td>{html.escape(event_text)}</td>"
            f"<td>{scope['indicator_count']} 项 / {scope['indicator_record_count']} 条</td>"
            f"<td>{statuses.get('calibration_required', 0)} / {statuses.get('unavailable', 0)}</td>"
            "<td>0 / 0</td>"
            f"<td>{html.escape(scope['bundle_status'])}</td>"
            "</tr>"
        )
        deployment_markdown_parts.append(
            f"{label} {scope['processed_frames']} 帧 {scope['effective_processed_fps']:.3f} FPS，"
            f"{scope['indicator_record_count']} 条指标事件记录（"
            f"{statuses.get('calibration_required', 0)} calibration_required / "
            f"{statuses.get('unavailable', 0)} unavailable）"
        )
    deployment_markdown_summary = "；".join(deployment_markdown_parts)
    blocker_reason_label_by_code = dict(SCORE_BLOCKER_REASON_LABELS)
    m78_blocker_rows = []
    for item in m78_blocker_taxonomy_scope["priority"]:
        m78_blocker_rows.append(
            "<tr>"
            f"<td><code>{html.escape(item['flag'])}</code></td>"
            f"<td>{html.escape(blocker_reason_label_by_code.get(item['reason_code'], item['reason_code']))}<br><code>{html.escape(item['reason_code'])}</code></td>"
            f"<td><code>{html.escape(item['truth_requirement'])}</code></td>"
            f"<td>{item['active_indicator_occurrence_count']}</td>"
            f"<td>{item['recovery_indicator_occurrence_count']}</td>"
            f"<td>{item['sole_block_indicator_occurrence_count']}</td>"
            "</tr>"
        )
    diagnostic_review_rows = []
    diagnostic_review_markdown_parts = []
    for label, scope in diagnostic_review_scopes.items():
        type_text = " / ".join(
            f"{name} {count}"
            for name, count in sorted(scope["by_diagnostic_type"].items())
        )
        report_href = _report_href(
            diagnostic_review_paths[label].parent / "index.html",
            root=root,
            page_directory=directory,
        )
        diagnostic_review_rows.append(
            "<tr>"
            f'<td><a href="{html.escape(report_href)}"><strong>{html.escape(label)}</strong></a></td>'
            f"<td>{scope['task_count']}</td>"
            f"<td>{scope['unique_candidate_frames']}</td>"
            f"<td>{scope['unique_affected_indicator_instances']}</td>"
            f"<td>{html.escape(type_text)}</td>"
            "<td>pending / not truth</td>"
            "</tr>"
        )
        diagnostic_review_markdown_parts.append(
            f"{label} {scope['task_count']} 个去重任务、"
            f"{scope['unique_candidate_frames']} 个候选帧、"
            f"{scope['unique_affected_indicator_instances']} 个受影响指标实例"
        )
    diagnostic_review_markdown_summary = "；".join(
        diagnostic_review_markdown_parts
    )
    diagnostic_truth_rows = []
    diagnostic_truth_markdown_parts = []
    for label, scope in diagnostic_truth_scopes.items():
        workbench_href = _report_href(
            diagnostic_truth_paths[label] / "index.html",
            root=root,
            page_directory=directory,
        )
        evaluation_href = _report_href(
            diagnostic_truth_paths[label] / "evaluation.json",
            root=root,
            page_directory=directory,
        )
        diagnostic_truth_rows.append(
            "<tr>"
            f'<td><a href="{html.escape(workbench_href)}"><strong>{html.escape(label)}</strong></a></td>'
            f"<td>{scope['frame_count']}</td>"
            f"<td>{scope['diagnostic_type_count']}</td>"
            f"<td>{scope['candidate_task_count']}</td>"
            f"<td>{scope['accepted_coverage_rows']} / {scope['accepted_positive_rows']}</td>"
            f'<td><a href="{html.escape(evaluation_href)}"><code>{html.escape(scope["status"])}</code></a></td>'
            "</tr>"
        )
        diagnostic_truth_markdown_parts.append(
            f"{label} {scope['frame_count']} 帧、"
            f"{scope['diagnostic_type_count']} 类诊断、"
            f"覆盖/真阳性均为 0，状态 {scope['status']}"
        )
    diagnostic_truth_markdown_summary = "；".join(diagnostic_truth_markdown_parts)
    output = directory / "index.html"
    output.write_text(
        f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RallyMate YOLO / RTMPose 动态评分对比</title><style>
body{{font:15px/1.6 system-ui,sans-serif;max-width:1320px;margin:28px auto;padding:0 20px;background:#f4f4ef;color:#172019}}section{{background:white;border:1px solid #c9cec9;padding:18px;margin:16px 0}}video{{width:100%;background:#000}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d6dbd6;padding:8px;text-align:left}}th{{background:#edf0ec}}code{{font-family:ui-monospace,monospace}}.warning{{border-left:5px solid #d97706;padding-left:12px}}.good{{border-left:5px solid #16a34a;padding-left:12px}}</style></head><body>
<h1>YOLO、Halpe26 与 WholeBody133：动态骨架和评分闭环对比</h1>
<section><h2>当前能否正式输出 A～E？</h2>
<table><thead><tr><th>门禁</th><th>当前机器状态</th><th>结论</th></tr></thead><tbody>
<tr><td>F0～F4 成熟度</td><td>{len(feasibility_registry['indicators'])} 项，当前层级：{html.escape(' / '.join(feasibility_levels))}</td><td>仅特征可测，不代表可区分或已标定</td></tr>
<tr><td>当前版本绑定</td><td>registry <code>{html.escape(feasibility_registry['registry_version'])}</code>；scoring loop <code>{html.escape(pose_wave['summary']['loop_version'])}</code>；primary <code>{html.escape(pose_wave['summary']['model_versions']['primary_player'])}</code>；event <code>{html.escape(pose_wave['summary']['model_versions']['event'])}</code>；FS01/FS02 <code>{html.escape(pose_wave['summary']['model_versions']['feature_contract']['FS02-M02'])}</code>；quality <code>{html.escape(pose_wave['summary']['model_versions']['quality_policy'])}</code></td><td>97 秒全片与实际 GPU Worker 均绑定当前 registry；下方同窗模型 A/B 是明确标识的历史比较快照</td></tr>
<tr><td>人工事件真值</td><td>{truth_counts['manual_events']} 条</td><td>缺失</td></tr>
<tr><td>人工校正关键点</td><td>{truth_counts['accepted_keypoint_joint_rows']} 条已接受；{truth_counts['pending_keypoint_joint_rows']} 条待标注</td><td>缺失</td></tr>
<tr><td>人工语义真值</td><td>{truth_counts['semantic_truth_records']} 条已接受；{truth_counts['pending_semantic_rows']} 条待标注</td><td>缺失</td></tr>
<tr><td>教练等级/排序</td><td>{truth_counts['coach_labels']} 条</td><td>缺失</td></tr>
<tr><td>可拟合样本</td><td>{calibration_counts['samples']} 条；已生成 {calibration_counts['prepared_indicator_files']} 份空白指标级契约</td><td>{html.escape(calibration_dataset['status'])}</td></tr>
<tr><td>生产标定资产</td><td>{production_calibration_asset_count} 个</td><td>没有真实资产时只能 calibration_required / unavailable</td></tr>
</tbody></table>
<p>当前人工真值误差评测：Event F1={event_truth_metrics['event_f1'] if event_truth_metrics['event_f1'] is not None else '—'}，Segment IoU={event_truth_metrics['mean_segment_iou'] if event_truth_metrics['mean_segment_iou'] is not None else '—'}，Boundary MAE={event_truth_metrics['boundary_mae_ms'] if event_truth_metrics['boundary_mae_ms'] is not None else '—'} ms；事件与特征评测状态均为 <code>{html.escape(event_truth_metrics['status'])}</code> / <code>{html.escape(feature_truth_evaluation['status'])}</code>。M43 已将 <code>target_direction_alignment_error_deg</code> 纳入同一误差预算，但当前人工 target semantic 记录为 {int(truth_promotion_guard.get('manual_semantic_record_count', 0))}，所以该特征 MAE/P95/Bias 均为 {target_alignment_truth_overall.get('mae') if target_alignment_truth_overall.get('mae') is not None else '—'} / {target_alignment_truth_overall.get('p95') if target_alignment_truth_overall.get('p95') is not None else '—'} / {target_alignment_truth_overall.get('bias') if target_alignment_truth_overall.get('bias') is not None else '—'}，上下文真值完整性为 <code>{str(bool(truth_promotion_guard.get('context_feature_truth_complete'))).lower()}</code>。阈值后端为 <code>{html.escape(calibration_interface['threshold_backend']['interface'])}</code>、资产为空；序数回归后端为 <code>{html.escape(calibration_interface['ordinal_regression_backend']['interface'])}</code>、模型为空。这些接口已实现，但没有数据就不会训练或产出等级。</p>
<details><summary>展开查看 13 项指标的 F2→F3 阻断矩阵</summary><table><thead><tr><th>指标</th><th>成熟度</th><th>数据状态</th><th>完整特征向量/样本</th><th>等级/排序标签</th><th>多教练重叠</th><th>一致性</th><th>F3 claimed</th><th>阻断项</th></tr></thead><tbody>{''.join(calibration_readiness_rows)}</tbody></table></details>
<p class="warning">答案是：现在可以稳定输出候选事件、版本化特征、有效性、证据帧和安全状态，但还不能诚实地输出正式 A～E。系统没有从视频分布或经验值生成伪阈值。请先在 <a href="../../data/annotations/scoring-truth-pack-v1/review.html">人工真值标注工作台</a> 完成事件、阶段、关键点和语义，再进入多教练标注、候选拟合、封存独立测试与人工晋级。<a href="../../data/annotations/scoring-truth-pack-v1/compiled/validation-report.json">真值就绪报告</a> · <a href="{html.escape(truth_calibration_portfolio_index_href)}">三视频标定组合状态</a> · <a href="{html.escape(multivideo_calculation_coverage_index_href)}">M63 三视频计算覆盖</a> · <a href="{html.escape(multivideo_readiness_index_href)}">M63 就绪层级拆解</a> · <a href="../../metric-feasibility-pose-wave-v2.json">F0～F4 注册表</a>。</p></section>
<section><h2>完整全身精细采样点：WholeBody-133（31–51 秒）</h2><video controls playsinline preload="metadata" poster="wholebody133-full-detail-midframe.jpg"><source src="{wholebody_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>这是官方 OpenMMLab RTMPose-M COCO-WholeBody 256×192 的真实逐帧输出：17 个身体点、6 个足部点、68 个面部点、左手 21 点、右手 21 点，共 133 点。画面按组分色并同时放大全身、脸、双手和脚；低于 0.25 或落在画面外的点不会绘制。该片段 600/600 帧完成推理，模型点没有合成或插值。<a href="{wholebody_name}" target="_blank">单独打开 WholeBody-133 完整视频</a></p>
<p class="warning">覆盖率不是准确率。尤其该片段为背面/侧背视角，模型即使给出高置信面部点，也必须通过人工关键点真值再判断是否准确。WholeBody-133 尚未晋级为默认评分模型。</p></section>
<section><h2>同帧三路动态对比：YOLO-17 / Halpe-26 / WholeBody-133</h2><video controls playsinline preload="metadata" poster="yolo17-vs-halpe26-vs-wholebody133-midframe.jpg"><source src="{three_way_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>三栏使用同一视频、同一 31–51 秒、同一主球员 ROI 与时间轴。左栏 YOLO 只有 COCO-17；中栏 Halpe-26 另有原生 head/neck/hip 与双脚6点；右栏 WholeBody-133 是另一套拓扑，在 COCO-17 上加入双脚6点、面部68点和双手42点，neck/hip center 需要派生。<a href="{three_way_name}" target="_blank">单独打开三路对比视频</a></p></section>
<section><h2>历史同窗快照：20 秒窗口接入 {same_window_scope['indicator_count']} 项指标安全计算闭环</h2>
<p>两种 RTMPose 输出均复用同一主球员时间线，并分别经过同一套 {html.escape(same_window_event_header)} 规则事件层、版本化特征、质量门禁和安全评分接口。窗口为 600 帧（31,000–50,966 ms）。</p>
<table><thead><tr><th>模型</th><th>{html.escape(same_window_event_header)} 候选</th><th>指标事件特征可测</th><th>calibration_required</th><th>unavailable</th><th>质量门禁 pass / advisory / hard</th><th>A～E 数量</th></tr></thead><tbody>{''.join(same_window_rows)}</tbody></table>
<table><thead><tr><th>模型</th>{''.join(f'<th>{html.escape(label)}</th>' for _, label in SCORE_BLOCKER_REASON_LABELS)}</tr></thead><tbody>{''.join(same_window_blocker_rows)}</tbody></table>
<p>上表按“指标×事件实例”统计评分阻断原因，同一实例可同时命中多类，因此各列不能相加当作失败总数。<code>{html.escape(pose_wave['summary']['loop_version'])}</code> 会分别输出身份、跳点、左右点、目标方向、边界截断、低样本和启动脚侧别 reason code，不再用泛化的“评分上下文未确认”掩盖具体原因。</p>
<p class="good">WholeBody-133 已能为 registry 声明的 {same_window_scope['indicator_count']} 项指标生成同一特征与安全状态契约产物；没有真值和标定时仍严格只输出 <code>calibration_required</code> 或 <code>unavailable</code>，两模型均未产生等级或阈值。这不表示事件或特征准确，也不表示指标已经正式可评分。</p>
<p>阶段代理为 <code>pose-event-phase-proxies-v0.3.0</code>：事件右边界的脚速峰只有在边界后两个连续、时间间隔合规的实测样本都严格下降时，才可作为“减速起点代理”；边界后样本只确认趋势，不进入事件特征。既有右截断/两样本代理也继续保留显式质量标记。缺失、平台或回升仍返回不可用；所有代理均阻断依赖指标的正式 A～E，不声称识别到真实离地、触地或落地。<code>indicator-event-quality-v1.6.0</code> 进一步区分“required feature 是否由直接观测证据完整测得”和“整个事件的主球员 Pose/自动边界覆盖是否足以正式评分”：前者完整时保留 F2 特征供误差审计，后两类覆盖不足仍明确阻断 A～E；Track 覆盖、确认 ID Switch、必需阶段与真实 feature 缺失仍硬失败。</p>
<p class="warning">Halpe 检出每类10段、WholeBody每类11段，且 WholeBody advisory 更多。表中的“特征可测”与评分层 <code>unavailable</code> 是两个不同口径：特征已经测得，也仍可能因身份连续性、关键阶段或正式评分质量门禁而不可评分，不能称为特征失败。这说明当前规则事件边界会随 Pose 时序变化，不能据此判定 WholeBody 更准。下一步必须在同一人工事件边界与人工校正关键点上比较 Event F1、Boundary MAE 和特征 MAE。点击表中模型名可查看逐事件时间轴、特征值、有效性、阻断原因和证据帧；<a href="../fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json">打开同窗机器对照</a>。</p></section>
<section><h2>Pose 异常候选视频复核队列</h2><p>跳点、左右交换和 source Track 切换已按“源帧 + 关节/关节对/Track transition”跨重叠事件去重。点击模型名可在连续动态视频中直接跳转候选时刻，并导出人工复核 CSV。</p>
<table><thead><tr><th>复核队列</th><th>去重任务</th><th>唯一候选帧</th><th>受影响指标实例</th><th>候选类型</th><th>当前状态</th></tr></thead><tbody>{''.join(diagnostic_review_rows)}</tbody></table>
<p class="warning">队列中的坐标和异常标志全部来自模型诊断，不是人工真值；浏览器决定不会自动修改 Pose、质量门禁、成熟度或评分。只有经过独立人工复核、Python 校验和裁决后，才能用于评估诊断 precision/recall 或考虑门禁调整。</p></section>
<section><h2>Pose 诊断全时间线真值与 precision / recall 入口</h2><p>候选队列只能复核模型已经报出的条目，不能测漏检。以下工作台把“已完整审阅的帧区间”和“人工确认真阳性”分成两份 CSV；只有某类诊断覆盖全部 600 帧时，其 recall 才标记为 full timeline。</p>
<table><thead><tr><th>盲审工作台</th><th>时间线帧数</th><th>诊断类型</th><th>待对照候选</th><th>已接受覆盖 / 真阳性</th><th>当前评测状态</th></tr></thead><tbody>{''.join(diagnostic_truth_rows)}</tbody></table>
<p class="warning">当前三套真实包都还是空白，其中新增的 Halpe26 当前全片包覆盖 2,911 帧并绑定 M42 的 261 个去重候选任务；仍没有人工覆盖、没有人工真阳性，precision / recall / F1 均为 null，质量门禁不会自动改变。协议要求至少两名独立标注者和一名独立裁决者，并对 queue、frames、primary timeline、events、scores、summary、复核视频和源视频重新计算 SHA-256。</p>
<p>质量策略审查同时绑定了 {diagnostic_policy_review_scope['source_count']} 个真实范围和 {diagnostic_policy_review_scope['diagnostic_type_count']} 类诊断，当前状态为 <a href="../pose-diagnostic-quality-gate-review-v1-empty.json"><code>{html.escape(diagnostic_policy_review_scope['status'])}</code></a>。仓库没有替教练填写 precision / recall / F1 门槛；必须先由外部可信登记预注册版本化接受协议，并在结果揭示前冻结协议。即使未来满足协议，也只能进入人工策略审核，不能自动发布新质量门禁。</p></section>
<section><h2>先拆开两个问题：事件边界分歧 vs. Pose 特征分歧</h2>
<h3>1）两套 Pose 各自驱动事件规则时</h3><p>Halpe26 有 {event_disagreement_scope['left_event_count']} 个候选，WholeBody133 有 {event_disagreement_scope['right_event_count']} 个候选。以下采用按事件码与主球员分组的时序一对一全局匹配；IoU 阈值只用于敏感性分析，不是评分阈值。</p>
<table><thead><tr><th>最小 Segment IoU</th><th>匹配数</th><th>Halpe→WholeBody / 反向匹配率</th><th>平均 Segment IoU</th><th>边界绝对差 start/end/center/duration（ms）</th></tr></thead><tbody>{''.join(event_disagreement_rows)}</tbody></table>
<p>IoU≥0.3 时平均 Segment IoU 为 {float(event_disagreement_scope['selected']['mean_segment_iou']):.4f}，但 WholeBody 仍多出 3 个未匹配候选。这里只能说明候选切分的一致程度，不能判断谁正确。<a href="../event-disagreement-halpe26-vs-wholebody133-same-window.json">打开逐事件机器对齐结果</a>。</p>
<h3>2）强制使用完全相同的事件 ID、Track、起止时间和阶段时间时</h3>
<table><thead><tr><th>公共边界来源</th><th>事件数</th><th>事件×特征对</th><th>双方有效 / Halpe-only / WholeBody-only / 双方无效</th><th>Halpe 指标事件完整</th><th>WholeBody 指标事件完整</th></tr></thead><tbody>{''.join(fixed_boundary_rows)}</tbody></table>
<table><thead><tr><th>关键特征</th><th>Halpe 候选边界</th><th>WholeBody 候选边界</th></tr></thead><tbody>{''.join(fixed_feature_rows)}</tbody></table>
<p class="warning">固定边界后，大部分 required feature 可以成对计算，但数值并不等价。例如 <code>hip_center_y_body</code> 的跨模型 MAD 在两种候选边界下分别为 {hip_mads[0]:.3f} 和 {hip_mads[1]:.3f} body。这个分歧足以继续阻断模型替换后的正式评分；没有人工校正关键点，不能知道哪一侧更接近真值。质量门禁在此对比中明确为 <code>not_evaluated</code>，没有 grade 或阈值。<a href="../fixed-boundary-pose-ab-halpe-candidates-930-1530.json">Halpe 边界 JSON</a> · <a href="../fixed-boundary-pose-ab-wholebody-candidates-930-1530.json">WholeBody 边界 JSON</a>。</p></section>
<section><h2>97 秒全片三种 Pose 配置的评分路由审计</h2>
<p>三种配置都处理同一 2,911 帧视频。第一列“各自切分”会同时受到 Pose 与事件边界变化影响；“固定 Halpe256 边界”强制复用完全相同的 102 个事件 ID、Track、起止时间和关键阶段，才用于本轮测量覆盖路由。</p>
<table><thead><tr><th>Pose 配置</th><th>各自切分事件 / 特征可测</th><th>固定同边界可测</th><th>相对 Halpe256</th><th>自动事件一致性（IoU≥0.3）</th><th>评分状态 calibration / unavailable / grade</th><th>当前角色</th></tr></thead><tbody>{''.join(full_profile_rows)}</tbody></table>
<p class="good">固定边界后，Halpe26 256×192 为 403/442，Halpe26 384×288 为 399/442，WholeBody133 为 369/442。384 档在自己的边界上出现 415/442，不能解释为模型净提升：其自动事件与 256 档只有 90/102 在 IoU≥0.3 下匹配，中心边界平均绝对差为 64.26 ms。WholeBody133 增加了手、脸和足部原生点，但对当前 13 项没有新增一个完整的 WholeBody-only 指标事件。</p>
<p class="warning">路由结论是继续保留 Halpe26 256×192 作为当前评分主配置；384 作为分析候选、WholeBody133 作为精细可视化与后续拓扑证据。系统禁止按每个模型各自事件挑更好数字，也禁止逐事件或逐特征把不同模型结果拼成官方值。该选择仅依据同边界测量覆盖和最小变更原则，不是准确率排名；人工事件、人工关键点和分视角特征误差到位前不自动切换模型。<a href="../pose-profile-routing-audit-full.json">打开机器可读路由审计</a> · <a href="../fixed-boundary-pose-ab-full-halpe256-vs-halpe384-halpe-candidates.json">256/384 固定边界</a> · <a href="../fixed-boundary-pose-ab-full-halpe-candidates.json">256/WholeBody 固定边界</a>。</p></section>
<section><h2>全片 266 条评分 unavailable 的精确拆解</h2>
<p>当前全片 442 条指标事件记录中，{int(blocker_decomposition.get('calibration_required_complete_features_no_score_block', 0))} 条完整评分向量只缺标定，状态为 <code>calibration_required</code>；另外 {int(blocker_decomposition.get('unavailable_complete_features_score_only', 0))} 条完整评分向量被正式评分证据门禁拦截。后者不是“模型没算出完整评分向量”，人工真值若确认并经受控策略发布解除全部相关门禁，最多只能恢复到 <code>calibration_required</code>，仍不能直接产生 A～E。</p>
<p>其余 unavailable 包含 {int(blocker_decomposition.get('unavailable_feature_incomplete_without_score_block', 0))} 条评分向量不完整且无 score-only 阻断、{int(blocker_decomposition.get('unavailable_feature_incomplete_and_score_blocked', 0))} 条评分向量不完整且同时有 score-only 阻断、{int(blocker_decomposition.get('unavailable_hard_fail', 0))} 条事件 hard fail。这里的“评分向量不完整”包含 FS02-M02 缺人工目标方向；它不等于 Pose 测量失败。Pose 测量层仍单独报告 403/442 measured。系统没有用 0 补缺失，也没有为提高覆盖率降低关节、阶段、Track 或事件证据要求。</p>
<table><thead><tr><th>评分阻断</th><th>全部指标记录 / 唯一事件</th><th>特征完整且仅评分阻断 / 唯一事件</th><th>若只确认这一类可恢复到 calibration_required</th><th>所需人工真值</th></tr></thead><tbody>{''.join(blocker_priority_rows)}</tbody></table>
<p class="warning">优先顺序来自“单一阻断可恢复数”，不是诊断准确率或接受阈值。最高收益项仍是关键点跳变全时间线真值：它单独阻断 100 条完整评分向量记录，并参与 {int(blocker_reason_counts.get('keypoint_jump_diagnostic_unverified', 0))} 条全部记录；左右交换为 {int(blocker_reason_counts.get('left_right_assignment_unverified', 0))} 条、目标方向为 {int(blocker_reason_counts.get('tactical_target_direction_required', 0))} 条、身份连续性为 {int(blocker_reason_counts.get('event_identity_continuity_unverified', 0))} 条。M43 审计会拒绝没有对应活动 flag 的多报 reason，当前每个类型化原因均 100% 有原始门禁支撑。人工覆盖仍为 0，quality policy 没有改变。<a href="../scoring-blocker-audit-halpe256-full-m43.json">打开完整机器审计</a>。</p>
<p class="good">M53 已从当前 scores、blocker 审计、registry、目标方向文件和 Pose 诊断队列生成 <a href="../scoring-truth-action-worklist-halpe256-full-m53/index.html">统一视频行动清单</a>：{int(truth_action_scope['actionable_unavailable_indicator_instances'])}/{int(truth_action_scope['unavailable_indicator_instances'])} 条 unavailable 全部至少有一个人工动作，共 {int(truth_action_scope['work_items'])} 个去重 event×真值要求和 {int(truth_action_scope['instance_action_links'])} 条实例×动作关联。四类工作项为 Pose 诊断 {int(truth_action_scope['by_review_type'].get('pose_diagnostic_truth', 0))}、人工关键点/特征 {int(truth_action_scope['by_review_type'].get('manual_keypoint_feature_truth', 0))}、目标方向 {int(truth_action_scope['by_review_type'].get('scoring_reference_context', 0))}、事件/阶段/其他语义 {int(truth_action_scope['by_review_type'].get('manual_event_or_semantic_truth', 0))}；Pose 队列缺失关联为 {int(truth_action_scope['pose_diagnostic_items_missing_queue_task'])}。</p>
<p class="warning">M45 <a href="../scoring-truth-action-readiness-halpe256-full-m45.json">行动证据状态机</a>已逐项重算 M44 完成度：当前 {int(truth_action_readiness_scope['by_status'].get('evidence_satisfied', 0))}/{int(truth_action_readiness_scope['work_items'])} 个工作项证据满足、{int(truth_action_readiness_scope['by_status'].get('review_in_progress_not_adjudicated', 0))} 个仅复核未裁决、{int(truth_action_readiness_scope['by_status'].get('annotation_required', 0))} 个仍需标注；受影响指标实例中 {int(truth_action_readiness_scope['indicator_instances_by_status'].get('evidence_satisfied', 0))}/{int(truth_action_readiness_scope['indicator_instances'])} 个全部关联动作证据满足。评估器会从绑定的事件/特征真值与 Pose coverage/positive CSV 重新计算，不能靠修改状态 JSON 冒充完成。完成工作项也不等于重算后门禁必然解除，更不等于产生 A～E。</p>
<p class="good">M46 <a href="../scoring-truth-evidence-plan-halpe256-full-m46/index.html">共享证据获取计划</a>把 {int(truth_evidence_plan_scope['input_work_items'])} 个 work item 的重复依赖折叠为 {int(truth_evidence_plan_scope['evidence_units'])} 个证据单元：其中 4 个全时间线 Pose 单元可共同支撑跳点、左右交换和身份连续性任务，其余按事件边界、阶段、密集关键点、特征真值、侧别语义和目标方向精确绑定。当前 {int(truth_evidence_plan_scope['by_status'].get('evidence_satisfied', 0))}/{int(truth_evidence_plan_scope['evidence_units'])} 单元满足。该优先级按受影响实例数排序，不是准确率排名，也不会自动解除评分门禁。</p>
<p class="warning">M47 <a href="{html.escape(truth_refresh_index_href)}">最新不可变真值刷新</a>（<code>{html.escape(truth_refresh_scope['refresh_id'])}</code>）已在同一 hash-bound bundle 内串联 truth compile、事件/特征误差、Pose 诊断误差、M45 与 M46。当前人工事件/关键点帧/语义/教练标签为 {int(truth_refresh_scope['counts']['manual_events'])}/{int(truth_refresh_scope['counts']['manual_keypoint_frames'])}/{int(truth_refresh_scope['counts']['manual_semantics'])}/{int(truth_refresh_scope['counts']['coach_labels'])}，满足 work item {int(truth_refresh_scope['counts']['satisfied_work_items'])}/{int(truth_refresh_scope['counts']['work_items'])}、证据单元 {int(truth_refresh_scope['counts']['satisfied_evidence_units'])}/{int(truth_refresh_scope['counts']['evidence_units'])}。只有全部验证通过后才原子更新 latest 指针；该刷新不运行 A～E 评分、不生成阈值或成熟度晋级。</p>
<p class="warning">M48 <a href="{html.escape(truth_calibration_handoff_index_href)}">真值到标定交接</a>（<code>{html.escape(truth_calibration_handoff_scope['handoff_id'])}</code>）已从 M47 的冻结人工边界重新构建人工事件特征，再按精确 event_id 编译 13 项标定数据集。当前人工特征 {int(truth_calibration_handoff_scope['counts']['manual_feature_records'])} 条、samples {int(truth_calibration_handoff_scope['counts']['calibration_samples'])} 条、fit-ready {int(truth_calibration_handoff_scope['counts']['fit_ready_indicators'])}/{int(truth_calibration_handoff_scope['counts']['indicators'])}；13/13 prepared 契约存在，但空真值全部被拟合门禁拒绝。该交接未调用候选事件检测器或拟合器，也未生成候选资产、阈值、模型、等级或 F3/F4。</p>
<p class="warning">M49 <a href="{html.escape(truth_calibration_portfolio_index_href)}">三视频真值到标定组合</a>（<code>{html.escape(truth_calibration_portfolio_scope['portfolio_id'])}</code>）把真值包的 {int(truth_calibration_portfolio_scope['counts']['measurement_source_videos'])}/{int(truth_calibration_portfolio_scope['counts']['truth_manifest_videos'])} 段视频、{int(truth_calibration_portfolio_scope['counts']['measurement_source_frames'])} 帧现有 RTMPose-M/Halpe26 结果统一接入当前主球员时间线，并按每段人工事件边界独立重算特征后汇总为一份 13 项标定数据集。当前人工事件/特征/samples 为 {int(truth_calibration_portfolio_scope['counts']['accepted_manual_events'])}/{int(truth_calibration_portfolio_scope['counts']['manual_feature_records'])}/{int(truth_calibration_portfolio_scope['counts']['calibration_samples'])}，fit-ready {int(truth_calibration_portfolio_scope['counts']['fit_ready_indicators'])}/{int(truth_calibration_portfolio_scope['counts']['indicators'])}。本轮复用已有 Pose 帧，没有运行 GPU、候选事件检测或拟合，也没有生成阈值、模型、等级或 F3/F4；M48 单视频交接保留为历史可追溯入口。</p>
<p>历史 M31 <a href="../scoring-truth-priority-worklist-halpe256-full/index.html">工作清单</a>仍可用于对照当时 {truth_priority_scope['work_items']} 个任务，但不再是当前入口。M44 清单只做任务路由，候选不是真值；即使所有人工动作完成，没有多教练标定和独立测试时最高仍是 <code>calibration_required</code>。</p></section>
<section><h2>M63：三段完整视频的 13 项计算覆盖</h2>
<p class="good">复用统一 measurement source 的 {int(multivideo_calculation_coverage_scope['counts']['videos'])} 段视频、{int(multivideo_calculation_coverage_scope['counts']['frames'])} 帧现有 RTMPose-M/Halpe26 Pose 与当前主球员时间线，共得到 {int(multivideo_calculation_coverage_scope['counts']['candidate_events'])} 个候选事件和 {int(multivideo_calculation_coverage_scope['counts']['indicator_event_instances'])} 条指标事件实例。Pose 特征向量 {int(multivideo_calculation_coverage_scope['counts']['measured_feature_vectors'])} 条 measured、{int(multivideo_calculation_coverage_scope['counts']['unavailable_feature_vectors'])} 条 unavailable；13/13 指标在每一段视频中都至少有一条完整、可追溯的 measured 向量。</p>
<table><thead><tr><th>指标</th><th>特征 measured / 候选实例</th><th>有 measured 的视频</th><th>测量失败原因</th><th>A～E</th></tr></thead><tbody>{''.join(multivideo_indicator_rows)}</tbody></table>
<p class="warning">这是“计算合同能否执行”的跨视频证据，不是 Event F1、关键点准确率、特征 MAE 或动作质量结论。候选事件尚无人工真值；评分状态为 {int(multivideo_calculation_coverage_scope['counts']['score_status_counts'].get('calibration_required', 0))} 条 <code>calibration_required</code>、{int(multivideo_calculation_coverage_scope['counts']['score_status_counts'].get('unavailable', 0))} 条 <code>unavailable</code>，grade=0、threshold=0。代表证据只按最早事件选择，不按特征值或动作表现挑选。<a href="{html.escape(multivideo_calculation_coverage_index_href)}">打开逐视频、逐指标、逐特征证据</a>。</p></section>
<section><h2>M65：把“算不出”和“暂时不能评分”分开</h2>
<p class="good">M65 从当前三视频覆盖的原始 <code>features</code>、完整 <code>scoring_features</code> 和 quality gate 逐条重放。{readiness_total} 条指标实例中，{int(multivideo_readiness_scope['counts']['raw_measurement_vector_complete'])} 条原始 Pose 测量向量完整，{int(multivideo_readiness_scope['counts']['operational_feature_measured'])} 条通过事件测量门禁；{int(multivideo_readiness_scope['counts']['ready_for_calibration_application'])} 条完整评分向量已通过评分证据门禁，目前只缺教练标定和独立测试。</p>
<table><thead><tr><th>最先阻断层</th><th>实例</th><th>占全部实例</th></tr></thead><tbody>{''.join(readiness_decomposition_rows)}</tbody></table>
<p class="warning">因此 {int(multivideo_readiness_scope['counts']['score_status_counts'].get('unavailable', 0))} 条 <code>unavailable</code> 不能笼统理解为“模型计算失败”：其中只有 {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['measurement_vector_incomplete'])} 条是普通测量向量不完整，{int(multivideo_readiness_scope['counts']['exclusive_decomposition']['measurement_hard_fail'])} 条是事件测量 hard fail；{int(multivideo_readiness_scope['counts']['exclusive_decomposition']['scoring_context_incomplete'])} 条缺评分上下文，{int(multivideo_readiness_scope['counts']['exclusive_decomposition']['scoring_evidence_blocked'])} 条因身份、关键点、阶段或其他评分证据未验证而 fail closed。该拆解没有补 0、没有解除任何门禁，也没有生成 grade/threshold。<a href="{html.escape(multivideo_readiness_index_href)}">打开逐视频、逐指标互斥拆解</a>。</p></section>
<section><h2>M65：三视频评分阻断的可验证优先级</h2>
<p class="good">M65 在同一 2,366 条指标实例上逐条反查 <code>reason_codes</code> 与原始 <code>scoring_block_flags</code>：所有类型化原因都有活动 flag 支撑，没有把跳点、左右交换、目标方向或阶段问题误写成身份问题。对 1,277 条“完整评分向量但证据门禁未通过”的实例，进一步计算单一活动阻断的反事实收益。</p>
<table><thead><tr><th>活动阻断</th><th>参与完整实例</th><th>事件</th><th>唯一阻断实例</th><th>唯一阻断事件</th><th>必须取得的人工真值</th></tr></thead><tbody>{''.join(multivideo_recovery_rows)}</tbody></table>
<p class="warning">关键点跳变是唯一阻断的 645 条、左右交换为 173 条，因此三视频全时间线 Pose 诊断真值是当前最高收益人工任务。但这不证明候选是误报，也不授权自动解除门禁；即使人工真值和预注册协议通过，也只能进入独立人工策略审核，随后仍停在 <code>calibration_required</code>。M65 已为三段完整视频生成 1,516 个去重候选复核任务及空白 coverage/positive 真值表；当前人工覆盖仍为 0，策略审查为 <code>{html.escape(str(diagnostic_policy_review_scope['status']))}</code>。<a href="{html.escape(multivideo_readiness_index_href)}">打开机器归因</a> · <a href="../pose-diagnostic-review/m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full/index.html">视频 1</a> · <a href="../pose-diagnostic-review/m65-850cb0006b406c7176eeda8d711cd065-halpe26-full/index.html">视频 2</a> · <a href="../pose-diagnostic-review/m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full/index.html">视频 3</a>。</p></section>
<section><h2>M66：三视频小 ROI 可观测性恢复（实验投影）</h2>
<p>当前生产 32px ROI 门槛下，三视频 {multivideo_recovery_scope['baseline']['indicator_instances']} 条指标实例中，门禁后真实测量状态为 {multivideo_recovery_scope['baseline']['operational_measured']} measured / {multivideo_recovery_scope['baseline']['operational_unavailable']} unavailable；仅看 Pose 特征向量则为 {multivideo_recovery_scope['baseline']['feature_vector_complete']} complete / {multivideo_recovery_scope['baseline']['feature_vector_incomplete']} incomplete。两种口径不能互换。</p>
<div class="grid"><div><h3>视频 2：13 秒完整受影响窗口</h3><video controls playsinline preload="metadata"><source src="{html.escape(multivideo_recovery_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(multivideo_recovery_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开动态 A/B</a></p></div><div><h3>视频 3：8.25 秒完整受影响窗口</h3><video controls playsinline preload="metadata"><source src="{html.escape(multivideo_recovery_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(multivideo_recovery_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}" target="_blank">单独打开动态 A/B</a></p></div></div>
<p class="good">仅对原来被 32px 尺寸保护跳过、仍有主球员检测且位于原调度范围内的 {multivideo_recovery_scope['experiment']['eligible_target_frames']} 帧，用相同 RTMPose-M Halpe26 权重把最小 ROI 改为 8px：{multivideo_recovery_scope['experiment']['pose_output_recovered_frames']}/{multivideo_recovery_scope['experiment']['eligible_target_frames']} 帧产生 Pose；恢复 {multivideo_recovery_scope['experiment']['feature_vector_recovered']} 条特征向量，但保留原事件/身份测量门禁后只有 {multivideo_recovery_scope['experiment']['operational_measurement_recovered']} 条真正从 unavailable 变为 measured，回归 0 条。实验投影为 {multivideo_recovery_scope['experimental_projection']['operational_measured']} measured / {multivideo_recovery_scope['experimental_projection']['operational_unavailable']} unavailable。</p>
<p class="warning">这不是当前生产结果：生产默认仍为 32px，未启用自动 fallback。{multivideo_recovery_scope['experimental_projection']['measurement_hard_fail']} 条 measurement hard fail 完全不变，另有 {multivideo_recovery_scope['experimental_projection']['non_hard_fail_feature_incomplete']} 条非 hard-fail 特征缺口仍未恢复；视频 1 因目标缺口帧没有可用主球员检测，实验目标为 0，未伪造 ROI。没有人工关键点真值时，149/149 只能说明“模型有输出”，不能说明关键点准确，也不能推进 F3/F4 或生成 A～E。<a href="{html.escape(multivideo_recovery_index_href)}">打开三视频汇总</a> · <a href="{html.escape(multivideo_recovery_report_href)}">机器 JSON</a>。</p></section>
<section><h2>M67：同模型 ROI 上下文实验与精确组合复算</h2>
<div class="grid"><div><h3>视频 1：0.15 vs 0.30 ROI margin</h3><video controls playsinline preload="metadata"><source src="{html.escape(m67_recovery_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m67_recovery_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开动态 A/B</a></p></div><div><h3>视频 2：发生唯一回归的真实范围</h3><video controls playsinline preload="metadata"><source src="{html.escape(m67_recovery_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m67_recovery_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开动态 A/B</a></p></div></div>
<p>保持同一 RTMPose-M Halpe26 256×192 权重、检测、Track、主球员时间线、候选事件和质量门禁，只在已有 Pose 但特征观测不完整的帧上把 ROI margin 从 {m67_recovery_scope['m67_uniform_roi_margin']['baseline_margin']:.2f} 改为 {m67_recovery_scope['m67_uniform_roi_margin']['experimental_margin']:.2f}。三视频共重推理 {m67_recovery_scope['m67_uniform_roi_margin']['target_frames']} 帧，全部产生 Pose；特征向量恢复 {m67_recovery_scope['m67_uniform_roi_margin']['feature_vector_impact']['recovered']} 条、回归 {m67_recovery_scope['m67_uniform_roi_margin']['feature_vector_impact']['regressed']} 条，保留原门禁后实际恢复 {m67_recovery_scope['m67_uniform_roi_margin']['operational_impact']['recovered']} 条、回归 {m67_recovery_scope['m67_uniform_roi_margin']['operational_impact']['regressed']} 条。</p>
<p class="warning">扩大裁剪上下文并不保证点更多：有效关键点计数增加 {m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['increased']} 帧、减少 {m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['decreased']} 帧、不变 {m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['unchanged']} 帧。只挑“原 ROI 被画面边缘裁切”的 {m67_recovery_scope['m67_clipped_only_candidate']['target_frames']} 帧仍保留 1 条 operational 回归，未能形成安全自动路由条件。</p>
<p class="good">M66 小 ROI 与 M67 margin 目标集合在每段视频内互斥；系统把两类实验 Pose 合入完整帧序列后重新运行一次固定边界特征计算，不是把两份恢复计数相加。{m67_recovery_scope['composed_experimental_projection']['target_frames']} 个目标帧的组合投影将特征完整实例从 {m67_recovery_scope['baseline']['feature_vector_complete']}/{m67_recovery_scope['baseline']['indicator_instances']} 提升到 {m67_recovery_scope['composed_experimental_projection']['feature_vector_complete']}/{m67_recovery_scope['baseline']['indicator_instances']}，门禁后 measured 从 {m67_recovery_scope['baseline']['operational_measured']} 提升到 {m67_recovery_scope['composed_experimental_projection']['operational_measured']}；净恢复 29 条（恢复 {m67_recovery_scope['composed_experimental_projection']['operational_impact']['recovered']}、回归 {m67_recovery_scope['composed_experimental_projection']['operational_impact']['regressed']}）。hard fail 仍为 {m67_recovery_scope['composed_experimental_projection']['measurement_hard_fail']}，非 hard-fail 特征缺口仍有 {m67_recovery_scope['composed_experimental_projection']['non_hard_fail_feature_incomplete']} 条。</p>
<p class="warning">因此生产继续使用 0.15 margin 与 32px 最小 ROI，不启用自动 fallback，也不改变事件或评分门禁。当前缺少人工校正关键点、分视角特征误差和预注册独立视频发布测试；grade=0、threshold=0、F2 不晋级。<a href="{html.escape(m67_recovery_report_href)}">打开机器汇总 JSON</a> · <a href="{html.escape(m67_recovery_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}" target="_blank">视频 3 动态 A/B</a>。</p></section>
<section><h2>M68：评分关节有效集严格超集路由（实验候选）</h2>
<div class="grid"><div><h3>视频 1：15 个 margin 帧被安全选中</h3><video controls playsinline preload="metadata"><source src="{html.escape(m68_router_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m68_router_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开动态对比</a></p></div><div><h3>视频 2：29 个 margin 帧被安全选中</h3><video controls playsinline preload="metadata"><source src="{html.escape(m68_router_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m68_router_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开动态对比</a></p></div></div>
<p>M68 不按最终特征值、事件结果或等级挑模型。它只比较当前 0.15 ROI 与实验 0.30 ROI 在注册表所需的 {len(m68_router_scope['scope']['required_joints'])} 个肩、髋、膝、踝关节上的有效集合：只有候选集合严格包含当前集合、且不丢失任何当前有效评分关节时，才允许该帧进入实验组合。410 个 margin 候选帧中选中 {m68_router_scope['router']['selected_margin_frames']} 个，拒绝 {m68_router_scope['router']['rejected_margin_frames']} 个；再与 M66 的小 ROI 恢复合并后，实际路由帧共 {m68_router_scope['router']['total_composed_experimental_frames']} 个。</p>
<p class="good">固定同一事件边界并重新计算全部特征后，特征完整实例由 {m68_router_scope['baseline']['feature_vector_complete']}/{m68_router_scope['scope']['indicator_instances']} 提升到 {m68_router_scope['routed_projection']['feature_vector_complete']}/{m68_router_scope['scope']['indicator_instances']}，恢复 {m68_router_scope['routed_projection']['feature_vector_recovered']}、回归 {m68_router_scope['routed_projection']['feature_vector_regressed']}；保留原质量门禁后 measured 由 {m68_router_scope['baseline']['operational_measured']} 提升到 {m68_router_scope['routed_projection']['operational_measured']}，恢复 {m68_router_scope['routed_projection']['operational_recovered']}、回归 {m68_router_scope['routed_projection']['operational_regressed']}。它获得与 M67 无筛选组合相同的净 operational 增益，但没有保留那 1 条当前集合回归，并把 margin 路由范围从 410 帧收窄到 44 帧。</p>
<p class="warning">“三段当前视频零回归”不是准确率，也不是独立发布验证。人工校正关键点、分视角特征误差和预注册独立视频测试仍缺失，因此生产仍使用 0.15 margin、32px 最小 ROI且不启用自动路由；grade=0、threshold=0、F2 不晋级。<a href="{html.escape(m68_router_report_href)}">打开机器汇总 JSON</a> · <a href="{html.escape(m68_router_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}" target="_blank">视频 3 动态对比</a>。</p></section>
<section><h2>M69：残差事件帧高分辨率 Pose 路由（实验候选）</h2>
<div class="grid"><div><h3>视频 1：恢复 2 个指标实例</h3><video controls playsinline preload="metadata"><source src="{html.escape(m69_high_resolution_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m69_high_resolution_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开动态对比</a></p></div><div><h3>视频 2：恢复 3 个指标实例</h3><video controls playsinline preload="metadata"><source src="{html.escape(m69_high_resolution_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m69_high_resolution_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开动态对比</a></p></div></div>
<p>M69 只处理 M68 后仍然非 hard-fail、但特征向量不完整的残差：{m69_high_resolution_scope['scope']['input_residual_indicator_instances']} 个指标实例、{m69_high_resolution_scope['scope']['input_residual_events']} 个事件、{m69_high_resolution_scope['inference']['target_frame_count']} 个事件帧。RTMPose-M Halpe26 384×288 候选在 0.30 ROI 上成功输出 {m69_high_resolution_scope['inference']['pose_output_produced']} 帧；同一 required-joint 严格超集门禁只选中 {m69_high_resolution_scope['inference']['selected_frame_count']} 帧，拒绝 {m69_high_resolution_scope['inference']['rejected_frame_count']} 帧。</p>
<p class="good">固定事件边界、保留原质量门禁后，特征完整实例由 {m69_high_resolution_scope['baseline']['feature_vector_complete']}/{m69_high_resolution_scope['scope']['indicator_instances']} 提升为 {m69_high_resolution_scope['experimental_projection']['feature_vector_complete']}/{m69_high_resolution_scope['scope']['indicator_instances']}（恢复 {m69_high_resolution_scope['experimental_projection']['feature_vector_recovered']}、回归 {m69_high_resolution_scope['experimental_projection']['feature_vector_regressed']}）；operational measured 由 {m69_high_resolution_scope['baseline']['operational_measured']} 提升为 {m69_high_resolution_scope['experimental_projection']['operational_measured']}（恢复 {m69_high_resolution_scope['experimental_projection']['operational_recovered']}、回归 {m69_high_resolution_scope['experimental_projection']['operational_regressed']}）。非 hard-fail 残差由 {m69_high_resolution_scope['baseline']['non_hard_fail_feature_incomplete']} 降到 {m69_high_resolution_scope['experimental_projection']['non_hard_fail_feature_incomplete']}，hard-fail 仍为 {m69_high_resolution_scope['experimental_projection']['measurement_hard_fail']}。</p>
<p class="warning">候选同时改变 Pose 权重/输入分辨率和裁剪上下文，当前不能把增益单独归因于“高分辨率”或“更大 ROI”；三段视频零回归也不是关键点准确率或独立发布验证。生产路径、F2、grade=0、threshold=0 均不变。<a href="{html.escape(m69_high_resolution_report_href)}">机器汇总</a> · <a href="{html.escape(m69_residual_audit_href)}">残差审计</a> · <a href="{html.escape(m69_high_resolution_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}" target="_blank">视频 3 动态对比</a>。</p></section>
<section><h2>M70：分离 Pose 分辨率与裁剪上下文，并保留既有恢复</h2>
<div class="grid"><div><h3>视频 1：新增恢复 1 个 operational 实例</h3><video controls playsinline preload="metadata"><source src="{html.escape(m70_pose_profile_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m70_pose_profile_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开 M69 vs M70 动态对比</a></p></div><div><h3>视频 2：采样点变化但无新增指标恢复</h3><video controls playsinline preload="metadata"><source src="{html.escape(m70_pose_profile_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m70_pose_profile_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开 M69 vs M70 动态对比</a></p></div></div>
<p>M70 对同一 {m70_pose_profile_scope['scope']['target_event_frame_count']} 个残差事件帧重放 M68 实际使用的裁剪策略：baseline / 0.30 margin / 8px 小 ROI 分别为 {m70_pose_profile_scope['context_replay']['source_frame_count_by_policy']['baseline']} / {m70_pose_profile_scope['context_replay']['source_frame_count_by_policy']['roi_margin_candidate']} / {m70_pose_profile_scope['context_replay']['source_frame_count_by_policy']['small_roi_min8']} 帧。这样 384×288 候选只改变 Pose profile，不再混入裁剪上下文变化；{m70_pose_profile_scope['context_replay']['pose_output_produced']}/{m70_pose_profile_scope['context_replay']['target_frame_count']} 帧产生 Pose。</p>
<p>固定边界结果显示：同上下文 384 profile 只恢复 {m70_pose_profile_scope['strategies']['context_matched_profile_only']['operational_recovered']} 个 operational 实例；M69 统一 0.30/8px 候选恢复 {m70_pose_profile_scope['strategies']['uniform_context_m69']['operational_recovered']} 个。直接从两个候选中选“必需关节有效数最多”虽然总恢复仍为 {m70_pose_profile_scope['strategies']['naive_multicandidate']['operational_recovered']}，但丢失 {m70_pose_profile_scope['strategies']['naive_multicandidate']['lost_uniform_operational_recovery_count']} 个 M69 已恢复实例，所以明确拒绝。</p>
<p class="good">安全方案从 M69 路由结果出发，只在同上下文候选严格增加必需关节有效集时替换 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['extension_selected_frame_count']} 帧。它保留全部 M69 恢复，特征完整实例由 {m70_pose_profile_scope['baseline']['feature_vector_complete']} 提升到 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['feature_vector_complete']}（恢复 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['feature_recovered']}、回归 0），门禁后 operational measured 由 {m70_pose_profile_scope['baseline']['operational_measured']} 提升到 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['operational_measured']}（恢复 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['operational_recovered']}、回归 0），比 M69 额外恢复 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['additional_operational_recovery_over_m69']} 个实例；非 hard-fail 残差降至 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['non_hard_fail_feature_incomplete']}。</p>
<p class="warning">这仍是三视频、固定候选边界上的可观测性结果，不是关键点准确率、事件准确率或正式评分。生产默认、F2、grade=0、threshold=0 均不变；只有人工校正关键点、分视角特征误差与预注册独立视频发布测试通过后才可讨论推广。<a href="{html.escape(m70_pose_profile_report_href)}">机器汇总</a>。</p></section>
<section><h2>M71 更大 RTMPose-L 模型的受控扩展</h2>
<p>这里直接回答“换更强 Pose 模型后是否改善评分颗粒度”：候选换为官方 RTMPose-L Halpe26 384×288，但只重推 M70 已审计的 {m71_larger_pose_scope['scope']['target_frame_count']} 个残差帧，并且只有当 L 的必需关节有效集严格包含 M70 时才替换。它不是整条管线的盲目模型替换，也没有按特征值、事件结果或等级择优。</p>
<div class="grid"><div><h3>视频 1：新增恢复 3 个 operational 实例</h3><video controls playsinline preload="metadata"><source src="{html.escape(m71_larger_pose_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m71_larger_pose_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开 M70 vs RTMPose-L 动态对比</a></p></div><div><h3>视频 2：新增恢复 3 个 operational 实例</h3><video controls playsinline preload="metadata"><source src="{html.escape(m71_larger_pose_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m71_larger_pose_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开 M70 vs RTMPose-L 动态对比</a></p></div></div>
<p class="good">三视频固定边界上，严格路由选择 {m71_larger_pose_scope['m71_projection']['selected_frame_count']} 帧。相对 M68，特征完整实例达到 {m71_larger_pose_scope['m71_projection']['feature_vector_complete']}/{m71_larger_pose_scope['scope']['indicator_instances']}（恢复 {m71_larger_pose_scope['m71_projection']['feature_recovered_from_m68']}、回归 0），门禁后 operational measured 达到 {m71_larger_pose_scope['m71_projection']['operational_measured']}/{m71_larger_pose_scope['scope']['indicator_instances']}（恢复 {m71_larger_pose_scope['m71_projection']['operational_recovered_from_m68']}、回归 0）。相对 M70 各新增 {m71_larger_pose_scope['m71_projection']['additional_feature_recovery_over_m70']} 个完整特征实例和 {m71_larger_pose_scope['m71_projection']['additional_operational_recovery_over_m70']} 个门禁后可测实例，且没有丢失 M70 已恢复项；非 hard-fail operational 残差降至 {m71_larger_pose_scope['m71_projection']['non_hard_fail_operational_residual']}。</p>
<p class="warning">可直观看到 L 在少量遮挡/边缘帧补出了更多有效关节，但这只证明三段视频上的可观测性增益，不证明关节位置更准确，更不证明能给出可靠 A～E。第三段虽然产生 {m71_larger_pose_scope['by_video'][2]['pose_output_produced']} 个 L 候选 Pose，却没有一帧满足严格超集条件，因此未制作无变化视频。生产默认、F2、grade=0、threshold=0 均未改变。<a href="{html.escape(m71_larger_pose_report_href)}">机器汇总</a>。</p></section>
<section><h2>M72 同拓扑缺失点加法融合</h2>
<p>M71 的 whole-pose 路由会拒绝“候选补出一个关节、同时丢掉另一个关节”的帧。M72 不替换整副骨架：它逐点保留 M71 所有有效坐标，只允许同一 Halpe26 拓扑的 RTMPose-L 把候选有效、M71 无效的评分相关点补入。既有有效点覆盖数固定为 0；选择不读取特征值、事件结果、grade 或 threshold。</p>
<div class="grid"><div><h3>唯一产生完整指标恢复的视频</h3><video controls playsinline preload="metadata"><source src="{html.escape(m72_additive_fusion_video_href)}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m72_additive_fusion_video_href)}" target="_blank">单独打开 M71 vs M72 动态对比</a></p></div><div><h3>恢复与剩余阻断</h3><p>46 个帧发生点级补充，共增加 {m72_additive_fusion_scope['m72_projection']['added_valid_joint_observation_count']} 个有效关节观测；只有视频 1 的 FS01-M02、FS02-M02 两个指标实例因此变为完整可测。视频 2 虽补入 31 个点，却没有跨过完整事件向量门槛；视频 3 没有可补点。</p></div></div>
<p class="good">固定相同候选边界上，特征完整实例由 M71 的 {m72_additive_fusion_scope['baseline_m71']['feature_vector_complete']} 提升到 {m72_additive_fusion_scope['m72_projection']['feature_vector_complete']}/{m72_additive_fusion_scope['scope']['indicator_instances']}，门禁后 operational measured 由 {m72_additive_fusion_scope['baseline_m71']['operational_measured']} 提升到 {m72_additive_fusion_scope['m72_projection']['operational_measured']}；相对 M71 各新增 {m72_additive_fusion_scope['m72_projection']['additional_feature_recovery_over_m71']} 个，回归与丢失 M71 恢复均为 0。</p>
<p class="warning">剩余 {m72_additive_fusion_scope['remaining_residual']['indicator_instance_count']} 个非 hard-fail 缺口已逐项重放：事件内观测覆盖不足 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['event_observation_coverage_below_feature_contract']}、视频起始边界截断 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['video_start_boundary_censored_observation']}、FS09 所需 Pose 阶段代理未观察到 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['required_pose_phase_proxy_not_observed']}。这些缺口不能靠降低 valid-fraction 门槛或写 0 消除。融合本身仍需要人工逐点误差与独立视频验证，生产默认、F2、grade=0、threshold=0 不变。<a href="{html.escape(m72_additive_fusion_report_href)}">机器汇总与残差清单</a>。</p></section>
<section><h2>M73 WholeBody133 跨拓扑同名点补充</h2>
<p>为了直接验证“更多、更精细的采样点能否解决剩余评分缺口”，M73 在同一批 258 个冻结残差帧上运行官方 RTMPose-M WholeBody133，并只允许按显式同名映射把 WholeBody 的肩、髋、膝、踝和 6 个足部点补入 M72 无效位置。M72 所有有效坐标和 Halpe26 输出拓扑逐点保持，事件、Track、特征公式和门禁不变。</p>
<div class="grid"><div><h3>视频 1：85 个补点</h3><video controls playsinline preload="metadata"><source src="{html.escape(m73_wholebody_mapped_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m73_wholebody_mapped_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}" target="_blank">单独打开 M72 vs WholeBody133</a></p></div><div><h3>视频 2：16 个补点</h3><video controls playsinline preload="metadata"><source src="{html.escape(m73_wholebody_mapped_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" type="video/mp4">浏览器无法内嵌播放，请打开直链。</video><p><a href="{html.escape(m73_wholebody_mapped_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}" target="_blank">单独打开 M72 vs WholeBody133</a></p></div></div>
<p class="warning">254/258 个候选帧得到 WholeBody133 Pose，49 帧共新增 {m73_wholebody_mapped_scope['m73_projection']['added_valid_joint_observation_count']} 个有效同名关节点，但固定边界上的新增完整特征实例和 operational measured 实例都为 0；最终仍为 {m73_wholebody_mapped_scope['m73_projection']['feature_vector_complete']}/{m73_wholebody_mapped_scope['scope']['indicator_instances']} feature complete、{m73_wholebody_mapped_scope['m73_projection']['operational_measured']}/{m73_wholebody_mapped_scope['scope']['indicator_instances']} operational measured，剩余 13 条不变。这个受控负结果说明当前瓶颈是事件内连续时序覆盖、视频边界和阶段代理，不是简单增加采样点数量。点数不是准确率，WholeBody133 不进入生产或评分标定。<a href="{html.escape(m73_wholebody_mapped_report_href)}">机器汇总</a>。</p></section>
<section><h2>M74 事件内有界短缺口反事实</h2>
<p>M74 不再增加模型或采样点，而是在 M73 冻结的 13 个残余实例上逐关节审计 timestamp 缺口。仅允许同一事件内部、左右两侧都有真实有效观测、两侧观测跨度不超过 160 ms 的线性插值；首尾缺失、跨事件借帧、补 0 和阶段代理生成全部禁止。</p>
<p class="warning">去除跨指标重复后共有 {m74_event_gap_scope['counterfactual_summary']['unique_event_joint_observation_count']} 个 event×joint×frame 候选点；反事实可让 {m74_event_gap_scope['counterfactual_summary']['counterfactual_recovered_indicator_instance_count']}/13 个残余特征向量完整，仍有 {m74_event_gap_scope['counterfactual_summary']['counterfactual_remaining_indicator_instance_count']}/13 无法补齐。但这是由插值构造的测量敏感性上限，不是真实 Pose 观测或准确率结论；生产恢复数保持 0，所有实例仍 unavailable，直到人工关键点真值证明插值误差可接受。<a href="{html.escape(m74_event_gap_report_href)}">逐点机器审计</a>。</p></section>
<section><h2>M75 短缺口关键点双人盲标与独立裁决</h2>
<p>M75 把 M74 去重后的 {m75_event_gap_truth_scope['joint_task_count']} 个插值候选冻结为 {m75_event_gap_truth_scope['frame_count']} 帧、{m75_event_gap_truth_scope['video_count']} 段视频上的人工关节点任务。页面只播放无骨架原视频；插值坐标单独密封在 JSONL 中，不加载到浏览器 bootstrap。每个点要求两名标注者独立作答，再由未参与标注的 reviewer 裁决。<a href="{html.escape(m75_event_gap_truth_href)}">打开 M75 盲标工作台</a>。</p>
<p class="warning">当前人工标注 {m75_event_gap_truth_scope['raw_annotations']}、接受裁决 {m75_event_gap_truth_scope['accepted_adjudications']}，所以插值 MAE、P95、Bias、有效率及分视角结果全部保持 null；生产插值仍禁用，M74 的 7/13 只保留为反事实上限。<a href="{html.escape(m75_event_gap_truth_validation_href)}">编译门禁</a> · <a href="{html.escape(m75_event_gap_error_href)}">空白误差报告</a>。</p></section>
<section><h2>M76 真值条件特征误差重放</h2>
<p>M76 把 M75 裁决坐标接入版本化特征库，固定非缺口 Pose、候选事件边界和 Track，只比较“M74 有界插值”与“人工缺口坐标条件”下的特征。可评测范围为 {m76_event_gap_feature_truth_scope['indicator_instances_with_interpolation_points']} 个指标实例、{m76_event_gap_feature_truth_scope['required_feature_records']} 条 required-feature 记录和 {m76_event_gap_feature_truth_scope['unique_required_features']} 个唯一特征；另有 {m76_event_gap_feature_truth_scope['phase_only_residual_indicator_instances_excluded']} 个 FS09 实例缺的是阶段代理，不能用关键点插值伪造。</p>
<p class="warning">当前 M75 裁决真值为 0，因此 M76 的特征 MAE、P95、Bias、有效率及分指标结果全部为 null。它不是总 Pose 误差、不是事件检测准确率，也不会启用生产插值、生成 A～E/threshold 或推进 F3/F4。<a href="{html.escape(m76_event_gap_feature_error_href)}">打开 M76 机器报告</a>。</p></section>
<section><h2>M77 FS09 稳定阶段盲标与边界条件重放</h2>
<p>M77 处理 M76 不能覆盖的 {m77_fs09_phase_truth_scope['task_count']} 个 FS09-M05 实例。它们的髋/踝 Pose 在事件内覆盖 100%，但 <code>{html.escape(m77_fs09_phase_truth_scope['target_unavailable_feature'])}</code> 没有找到“双踝同时低运动”代理起点。盲标页不显示候选边界、候选阶段或 Pose；两段无叠加 H.264 短片解决长视频随机跳转问题，并将局部播放时间映射回原视频绝对 timestamp。每项要求两名标注者独立标注 FS09 存在性、边界和 {m77_fs09_phase_truth_scope['phase_count']} 个可见阶段，再由第三人裁决。<a href="{html.escape(m77_fs09_phase_truth_href)}">打开 M77 视频盲标工作台</a>。</p>
<p class="warning">当前人工标注 {m77_fs09_phase_truth_scope['raw_annotations']}、接受裁决 {m77_fs09_phase_truth_scope['accepted_adjudications']}，所以 Event IoU/Boundary MAE、阶段 MAE 和 {m77_fs09_phase_truth_scope['required_feature_count']} 项边界条件特征差均为 null。人工“稳定控制”不是双支撑、足底接触或受力真值；如果 Pose 代理仍找不到，目标特征必须继续 unavailable。<a href="{html.escape(m77_fs09_phase_validation_href)}">编译门禁</a> · <a href="{html.escape(m77_fs09_phase_error_href)}">空白评测报告</a>。</p></section>
<section><h2>M78 评分阻断类型与人工证据路由</h2>
<p>M78 将散落在评分、就绪审计和人工工作清单中的 flag→reason→truth 映射收敛到 <code>{html.escape(m78_blocker_taxonomy_scope['taxonomy_version'])}</code>。关键点跳变、左右点交换、目标方向、主球员身份、事件阶段和 Pose 覆盖现在分别保留独立原因，不再允许用“身份连续性”概括所有阻断。下表的计数来自三视频 {m78_blocker_taxonomy_scope['indicator_event_instance_count']} 个指标×事件实例；同一实例可包含多个 flag，禁止把各行相加当成唯一实例数。</p>
<table><thead><tr><th>原始 flag</th><th>类型化解释</th><th>需要的人工证据</th><th>全部出现次数</th><th>评分证据阻断实例</th><th>唯一阻断实例</th></tr></thead><tbody>{''.join(m78_blocker_rows)}</tbody></table>
<p class="good">用当前代码无 GPU 重放 {m78_blocker_taxonomy_scope['video_count']} 段视频后，事件键、全部非 confidence 载荷以及 score status/grade/reason/feedback/quality gate 与 M63 基线逐条一致；仅 {m78_blocker_taxonomy_scope['confidence_difference_count']} 个嵌套 confidence 叶值在序列化末位出现差异，最大绝对差 {m78_blocker_taxonomy_scope['max_abs_confidence_delta']:.6f}。这是重放舍入容差，不是 A～E 阈值。</p>
<p class="warning">本里程碑没有改变 measurement/scoring gate，没有解除任何 unavailable，也没有产生 grade、threshold、事件准确率或诊断准确率结论。优先级仅说明先补哪类真值能覆盖更多当前阻断，仍必须由盲化人工标注和独立评测回答。<a href="{html.escape(m78_blocker_taxonomy_audit_href)}">打开 M78 机器审计</a>。</p></section>
<section><h2>M80 跳点/左右交换动态视频双盲 + 骨架裁决闭环</h2>
<p>M80 继承 M79 的独立动态片段，把 M78 中最高频的两类评分证据阻断冻结为 {m79_pose_diagnostic_truth_scope['candidate_task_count']} 个候选，其中关键点跳变 {m79_pose_diagnostic_truth_scope['jump_task_count']} 个、左右点交换 {m79_pose_diagnostic_truth_scope['swap_task_count']} 个，覆盖 {m79_pose_diagnostic_truth_scope['unique_affected_indicator_instances']} 个去重指标实例。{m79_pose_diagnostic_truth_scope['video_count']} 段完整视频被合并为 {m79_pose_diagnostic_truth_scope['clip_count']} 个连续候选窗口、{m79_pose_diagnostic_truth_scope['source_frame_observations']} 帧，并分别编码成独立 H.264 MP4；因此切到任意片段不依赖长视频随机 seek。<a href="{html.escape(m79_pose_diagnostic_truth_review_href)}">打开第一阶段独立盲标</a>。</p>
<p>两名标注者仍使用不同 ID 观看无骨架、无候选帧/关节提示的视频并独立导出原始 CSV。第三名 reviewer 才能在隔离页解封候选，并加载 {m79_pose_diagnostic_truth_scope['pose_evidence_files']} 份逐帧 {html.escape(' / '.join(m79_pose_diagnostic_truth_scope['pose_evidence_formats']))} 模型证据（共 {m79_pose_diagnostic_truth_scope['pose_evidence_frames']} 帧）：完整骨架、目标关节和前后轨迹都与视频同步显示。叠加层是待评测模型输出，不是真值。<a href="{html.escape(m79_pose_diagnostic_truth_adjudicate_href)}">打开第二阶段动态骨架裁决</a>。</p>
<p class="warning">当前 coverage / positive / adjudication 为 {m79_pose_diagnostic_truth_scope['coverage_annotations']}/{m79_pose_diagnostic_truth_scope['positive_annotations']}/{m79_pose_diagnostic_truth_scope['candidate_adjudications']}，所以候选 precision 仍为 null；候选窗口方案从设计上不能测完整时间线 recall/F1。即使完成，它也不会自动解除评分门禁、改写候选、生成 grade/threshold 或推进 F3/F4。请用本仓库的 Range 服务启动报告，逐帧定位才可用。<a href="{html.escape(m79_pose_diagnostic_truth_evaluation_href)}">打开空白机器评测</a>。</p></section>
<section><h2>M81 三视频动态评分观察器</h2>
<p>M81 将当前 {m81_scoring_observer_scope['video_count']} 段完整视频、{m81_scoring_observer_scope['frame_count']:,} 帧 Halpe26 Pose、{m81_scoring_observer_scope['event_count']} 个 FS01/FS02/FS09 候选事件和 {m81_scoring_observer_scope['indicator_instance_count']:,} 条指标实例放到同一个同步播放器。播放或拖动视频时，模型骨架、候选事件、关键阶段、指标依赖关节、版本化特征值、证据帧与质量门禁一起更新；可点击事件和指标直接观察计算依据。<a href="{html.escape(m81_scoring_observer_href)}">打开 M81 动态评分观察器</a>。</p>
<p class="good">三视频合计 {m81_scoring_observer_scope['measured_count']:,} 条特征向量 measured、{m81_scoring_observer_scope['feature_unavailable_count']} 条 feature unavailable；正式评分状态为 {m81_scoring_observer_scope['calibration_required_count']} 条 calibration_required、{m81_scoring_observer_scope['score_unavailable_count']:,} 条 unavailable。观察器将 measurement 与 scoring 分列，避免把“特征算出来”误写成“已经评分”。</p>
<p class="warning">页面显示的是 RTMPose 模型输出与规则候选，不是人工真值；grade=0、threshold=0，也没有总分。每个数据文件在浏览器中校验原始 SHA-256，机器端同时校验源视频、frames、primary timeline、events、indicator-features、scores 与 registry 绑定。</p></section>
<section><h2>M63：51 个 Pose 特征的平滑反事实闭环</h2>
<p class="good">M63 对当前 registry 的 {int(smoothing_counterfactual_coverage['registry']['required_pose_feature_count'])} 个 Pose required feature、三段 M63 <code>features.jsonl</code> 做版本与 SHA 绑定审计。{int(smoothing_coverage_counts['valid_numeric_record_count'])} 条有效数值记录中，{int(smoothing_coverage_counts['computed_counterfactual_count'])} 条可以从序列化 raw evidence 无歧义重放“不使用平滑”的对照值，覆盖率 {float(smoothing_coverage_counts['counterfactual_coverage']) * 100:.2f}%；{int(smoothing_coverage_counts['features_fully_reconstructable_count'])} 个特征全部可重放，{int(smoothing_coverage_counts['features_partially_reconstructable_count'])} 个部分可重放，{int(smoothing_coverage_counts['features_not_reconstructable_count'])} 个完全不可重放。</p>
<p>FS01-M03 的四个必需特征已经形成同一证据合同 <code>fs01_m03_raw_and_prepared_kinematic_series_v1</code>：<code>bilateral_foot_rise_min_body</code>、<code>bilateral_foot_rise_synchrony_ms</code>、<code>bilateral_foot_rise_proxy_duration_ms</code>、<code>hip_center_vertical_velocity_body_s</code> 分别为 {int(smoothing_coverage_by_feature['bilateral_foot_rise_min_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['bilateral_foot_rise_min_body']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['bilateral_foot_rise_synchrony_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['bilateral_foot_rise_synchrony_ms']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['bilateral_foot_rise_proxy_duration_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['bilateral_foot_rise_proxy_duration_ms']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['hip_center_vertical_velocity_body_s']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['hip_center_vertical_velocity_body_s']['valid_numeric_record_count'])} 可重放。P95 raw-vs-smoothed 差依次为 {float(smoothing_coverage_by_feature['bilateral_foot_rise_min_body']['counterfactual_impact']['p95_absolute_difference']):.4f} body、{float(smoothing_coverage_by_feature['bilateral_foot_rise_synchrony_ms']['counterfactual_impact']['p95_absolute_difference']):.2f} ms、{float(smoothing_coverage_by_feature['bilateral_foot_rise_proxy_duration_ms']['counterfactual_impact']['p95_absolute_difference']):.2f} ms、{float(smoothing_coverage_by_feature['hip_center_vertical_velocity_body_s']['counterfactual_impact']['p95_absolute_difference']):.4f} body/s。它们只是平滑敏感性，不是人工真值误差。</p>
<p>FS01-M04 的三个必需特征采用 <code>fs01_m04_raw_and_prepared_slowdown_series_v1</code>，并共用同一实际时间戳双脚垂直减速锚点。<code>bilateral_foot_vertical_slowdown_time_offset_ms</code>、<code>post_slowdown_stance_width_body</code>、<code>hip_center_lateral_variability_body</code> 分别为 {int(smoothing_coverage_by_feature['bilateral_foot_vertical_slowdown_time_offset_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['bilateral_foot_vertical_slowdown_time_offset_ms']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['post_slowdown_stance_width_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['post_slowdown_stance_width_body']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['hip_center_lateral_variability_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['hip_center_lateral_variability_body']['valid_numeric_record_count'])} 可重放；P95 raw-vs-smoothed 差为 {float(smoothing_coverage_by_feature['bilateral_foot_vertical_slowdown_time_offset_ms']['counterfactual_impact']['p95_absolute_difference']):.2f} ms、{float(smoothing_coverage_by_feature['post_slowdown_stance_width_body']['counterfactual_impact']['p95_absolute_difference']):.4f} body、{float(smoothing_coverage_by_feature['hip_center_lateral_variability_body']['counterfactual_impact']['p95_absolute_difference']):.4f} body。站距项唯一一条 raw 序列无法形成减速锚点，继续输出 counterfactual unavailable，没有补 0 或放宽门禁。</p>
<p>FS02-M04 的四项共用 <code>fs02_m04_raw_and_prepared_launch_kinematics_v1</code>：raw 与 production prepared 都保存同一网格的髋位置、左右脚位置和左右脚速度，再由同一个纯函数完成启动方向、启动侧、峰速、投影位移与半峰运动持续时间。<code>launch_side_code</code> 为 {int(smoothing_coverage_by_feature['launch_side_code']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_side_code']['valid_numeric_record_count'])}，raw/production 侧别一致率 {float(smoothing_coverage_by_feature['launch_side_code']['counterfactual_impact']['agreement_rate']) * 100:.2f}%；峰速、相对位移、持续时间分别为 {int(smoothing_coverage_by_feature['launch_foot_speed_peak_body_s']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_speed_peak_body_s']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['launch_foot_relative_displacement_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_relative_displacement_body']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['launch_foot_motion_duration_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_motion_duration_ms']['valid_numeric_record_count'])}。后三项各有一条 raw 轨迹无法选择正向启动侧，继续 unavailable；不得使用 production 侧别倒填 raw。</p>
<p>FS02-M05 的五项新增共享证据合同 <code>fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1</code>，raw 与 prepared 均保存同一实际时间戳网格上的髋位置/速度、左右脚位置/速度，并冻结事件检测器给出的 <code>first_step_slowdown_proxy_ms</code>，不允许 raw 重放重新漂移阶段。<code>launch_foot_speed_drop_body_s</code>、<code>first_step_displacement_body</code>、<code>post_step_hip_direction_consistency</code>、<code>launch_foot_slowdown_to_post_hip_direction_ms</code>、<code>post_step_stance_width_body</code> 分别为 {int(smoothing_coverage_by_feature['launch_foot_speed_drop_body_s']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_speed_drop_body_s']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['first_step_displacement_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['first_step_displacement_body']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['post_step_hip_direction_consistency']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['post_step_hip_direction_consistency']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['launch_foot_slowdown_to_post_hip_direction_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_slowdown_to_post_hip_direction_ms']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['post_step_stance_width_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['post_step_stance_width_body']['valid_numeric_record_count'])} 可重放。前两项唯一一条 raw 轨迹没有正向启动侧，继续 unavailable；其余三项全部重放。该相位仍是 2D 运动学代理，不代表真实落地、触地或受力。</p>
<p>FS09 v0.2 的 <code>braking_side_code</code> 与 <code>braking_ankle_speed_drop_body_s</code> 已统一复用生产侧别纯函数：正向净速度下降优先；双侧净下降均不为正时，再使用 raw 踝速度的不规则时间局部减速峰。两项均由旧评测的不完整语义恢复为 {int(smoothing_coverage_by_feature['braking_side_code']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['braking_side_code']['valid_numeric_record_count'])} 与 {int(smoothing_coverage_by_feature['braking_ankle_speed_drop_body_s']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['braking_ankle_speed_drop_body_s']['valid_numeric_record_count'])} 可重放；合法诊断码 0 不再被当成缺失。</p>
<p>FS02-M03 现在也直接复用生产候选侧纯函数，并从 raw 膝屈曲按实际时间戳重算伸展速度：<code>drive_side_code</code>、<code>support_knee_extension_velocity_deg_s</code>、<code>support_drive_to_moving_foot_rise_proxy_ms</code> 均为 179/179。raw 与 production 平滑结果的候选侧一致率为 {float(smoothing_coverage_by_feature['drive_side_code']['counterfactual_impact']['agreement_rate']) * 100:.2f}%；所选膝伸展速度的平均/P95 绝对平滑差为 {float(smoothing_coverage_by_feature['support_knee_extension_velocity_deg_s']['counterfactual_impact']['mean_absolute_difference']):.2f}/{float(smoothing_coverage_by_feature['support_knee_extension_velocity_deg_s']['counterfactual_impact']['p95_absolute_difference']):.2f} deg/s，时差代理为 {float(smoothing_coverage_by_feature['support_drive_to_moving_foot_rise_proxy_ms']['counterfactual_impact']['mean_absolute_difference']):.2f}/{float(smoothing_coverage_by_feature['support_drive_to_moving_foot_rise_proxy_ms']['counterfactual_impact']['p95_absolute_difference']):.2f} ms。这说明一阶/二阶时序代理对平滑明显敏感，后续必须用人工关键点真值判断哪一侧更接近真实动作。</p>
<p>原始六项中的 FS02-M02 也得到更完整的误差预算入口：<code>launch_direction_deg</code> 直接以 raw 髋中心二维轨迹调用生产方向纯函数，182/182 可重放。角度差按环形最短差计算，平均/P95 绝对平滑差为 {float(smoothing_coverage_by_feature['launch_direction_deg']['counterfactual_impact']['mean_absolute_difference']):.2f}/{float(smoothing_coverage_by_feature['launch_direction_deg']['counterfactual_impact']['p95_absolute_difference']):.2f}°；最大个例为 {float(smoothing_coverage_by_feature['launch_direction_deg']['counterfactual_impact']['maximum_absolute_difference']):.2f}°，已绑定具体 video/event。该 raw 方向也可被目标方向对齐误差的人工真值评测复用。</p>
<p><code>stability_duration_ms</code> 升级为证据版 v0.2：生产值和 raw 反事实都调用同一纯函数，输入为实际 <code>timestamp_ms</code>、髋中心速度和肩髋绝对角速度。177/177 条有效实例可重放；平滑前后平均/P95 绝对差为 {float(smoothing_coverage_by_feature['stability_duration_ms']['counterfactual_impact']['mean_absolute_difference']):.2f}/{float(smoothing_coverage_by_feature['stability_duration_ms']['counterfactual_impact']['p95_absolute_difference']):.2f} ms，最大 {float(smoothing_coverage_by_feature['stability_duration_ms']['counterfactual_impact']['maximum_absolute_difference']):.2f} ms。三视频重放验证生产 value/valid/reason/source_frames 未变化，只有证据合同和 feature version 更新。</p>
<p><code>hip_acceleration_along_launch_direction_body_s2</code> 现在同时序列化 raw/smoothed 髋中心二维位置和 body-normalized 二维加速度，并与生产共用方向组合与投影纯函数。182/182 条有效实例可重放；平滑前后平均/P95 绝对差为 {float(smoothing_coverage_by_feature['hip_acceleration_along_launch_direction_body_s2']['counterfactual_impact']['mean_absolute_difference']):.2f}/{float(smoothing_coverage_by_feature['hip_acceleration_along_launch_direction_body_s2']['counterfactual_impact']['p95_absolute_difference']):.2f} body/s²，最大 {float(smoothing_coverage_by_feature['hip_acceleration_along_launch_direction_body_s2']['counterfactual_impact']['maximum_absolute_difference']):.2f} body/s²。极端值表明二阶导数对噪声和平滑高度敏感，必须优先进入人工关键点误差预算。</p>
<p><code>braking_ankle_slowdown_to_hip_deceleration_ms</code> 为 178/178，<code>hip_deceleration_to_double_support_proxy_ms</code> 为 176/176；两者均复用生产纯函数和真实 <code>timestamp_ms</code>。这只量化 raw-vs-smoothed 时差变化，不是边界真值误差。</p>
<p class="warning">该结果只说明 raw 与 smoothed 计算链能否做单因素反事实重放，不是特征 MAE、Pose 准确率、事件准确率或可加和的误差分解。当前 6 条部分覆盖来自少量 raw 轨迹无法形成合法侧别/锚点，系统保持 unavailable，没有借用 production 结果倒填；不存在完全不可重放的特征。人工校正关键点和人工事件边界仍为 0，F2/F3 状态、quality gate、grade 与 threshold 均未改变。<a href="../smoothing-counterfactual-coverage-m63.json">打开 51 特征机器审计</a>。</p></section>
<section><h2>M64：13 项 F2 误差预算就绪矩阵</h2>
<p class="good">M64 将当前 registry、M63 三视频 calculation coverage、9,965/9,971 条平滑反事实和三段当前事件/特征真值评测绑定到一份可重放报告。{int(f2_error_budget_counts['indicators_with_complete_smoothing_counterfactual'])}/13 项的全部 Pose 特征平滑反事实完整，{int(f2_error_budget_counts['indicators_with_partial_smoothing_counterfactual'])}/13 项含少量诚实 partial；但人工事件、关键点、语义记录仍分别为 {int(f2_error_budget_counts['manual_event_records'])}/{int(f2_error_budget_counts['manual_keypoint_records'])}/{int(f2_error_budget_counts['manual_semantic_records'])}，所以 Event F1/IoU/Boundary MAE、Pose/特征 MAE/P95/Bias、缺失值误差影响和外部等级间距判断均不能产生。</p>
<table><thead><tr><th>指标</th><th>当前计算</th><th>平滑反事实</th><th>事件误差</th><th>Pose/特征误差</th><th>等级间距评估</th><th>当前阻断</th><th>F2→F3</th></tr></thead><tbody>{''.join(f2_error_budget_rows)}</tbody></table>
<p class="warning">这张表把“平滑链可审计”和“评分误差已经验证”明确分开。当前 F2→F3 ready 为 {int(f2_error_budget_counts['indicators_ready_for_f2_to_f3'])}/13；报告不会把模型候选事件当真值、不会把测量覆盖当准确率，也不会生成 grade、threshold 或 maturity promotion。<a href="../f2-error-budget-readiness-m64.json">打开机器可读误差预算就绪报告</a>。</p></section>
<section><h2>Pose 测量层剩余 39 条 unavailable 的观测缺口</h2>
<p>这部分只审计 Pose measurement vector，与包含人工目标方向的完整 scoring vector 分开。全片 442 条指标事件中，Pose 测量层 {feature_gap_scope['counts']['feature_measured_indicator_count']} 条 measured、{feature_gap_scope['counts']['feature_unavailable_indicator_count']} 条 unavailable；后者集中在 {feature_gap_scope['counts']['affected_event_count']} 个候选事件，共 {feature_gap_scope['counts']['invalid_required_feature_occurrence_count']} 次测量特征失败，去重后为 {feature_gap_scope['counts']['unique_invalid_event_feature_count']} 个 event×feature 缺口。</p>
<table><thead><tr><th>根因</th><th>唯一 event×feature</th><th>受影响指标实例 / 事件</th><th>安全恢复动作</th></tr></thead><tbody>{''.join(feature_gap_rows)}</tbody></table>
<p>其中 134/138 个唯一缺口都是 <code>valid_fraction_below_quality_gate</code>，说明首要问题是候选事件内 Pose 观测不完整，而不是再增加一批与这 13 项无关的静态点。系统保留 null/invalid，不会用 0、插值或降低门槛伪造完整特征。</p>
<table><thead><tr><th>同边界替代 Pose 配置</th><th>当前 unavailable、替代 measured</th><th>当前 measured、替代 unavailable</th><th>路由约束</th></tr></thead><tbody>{''.join(feature_gap_profile_rows)}</tbody></table>
<p class="warning">384 档仅对当前 39 条中的 2 条提供可测旁证，同时丢失 6 条当前可测实例；WholeBody133 没有恢复其中任何一条，反而丢失 34 条当前可测实例。这只是同一候选边界下的可观测性，不是人工真值准确率。系统继续禁止逐事件或逐特征挑模型拼接结果。<a href="../feature-observation-gap-audit-halpe256-full.json">打开逐事件、逐特征机器审计</a>。</p></section>
<section><h2>小 ROI Pose 恢复实验：同一窗口动态 A/B</h2>
<video controls playsinline preload="metadata"><source src="{html.escape(small_roi_video_href)}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>左侧保持当前生产默认最小 ROI {small_roi_scope['settings']['baseline_min_roi_size_px']}px，右侧只把实验最小 ROI 改为 {small_roi_scope['settings']['experimental_min_roi_size_px']}px；视频、检测、主球员时间线、模型权重和 {small_roi_scope['indicator_instance_count']} 条固定候选指标实例均相同。对原先被 32px 尺寸保护跳过且仍在 <code>max_players=2</code> 调度内的 {small_roi_scope['inference_counts']['inference_attempted']} 帧重跑后，{small_roi_scope['inference_counts']['pose_output_recovered']}/{small_roi_scope['inference_counts']['inference_attempted']} 帧得到 Pose 输出。<a href="{html.escape(small_roi_video_href)}" target="_blank">单独打开 13 秒动态对比</a></p>
<p class="good">固定同一 102 个候选事件后，特征完整实例从 {small_roi_scope['transitions'].get('measured_to_measured', 0)}/{small_roi_scope['indicator_instance_count']} 增至 {small_roi_scope['transitions'].get('measured_to_measured', 0) + small_roi_scope['recovered_indicator_instance_count']}/{small_roi_scope['indicator_instance_count']}：恢复 {small_roi_scope['recovered_indicator_instance_count']} 条，实验中回归 {small_roi_scope['regressed_indicator_instance_count']} 条，仍有 {small_roi_scope['transitions'].get('unavailable_to_unavailable', 0)} 条不可测。</p>
<p class="warning">这里的“恢复”仅表示小框裁剪能产生满足现有特征观测门槛的二维 Pose，不表示远场关键点更准确，也不会解除身份、事件边界、人工真值、F3/F4 或 A～E 标定门禁。生产默认仍是 32px，未启用自动 fallback；在人工小框关键点误差按视角评测前，不会切换到 8px。<a href="{html.escape(small_roi_report_href)}">实验审计 JSON</a> · <a href="{html.escape(small_roi_fixed_href)}">固定边界逐特征对比</a>。</p></section>
<section><h2>小 ROI 关键点盲标与误差评测</h2>
<p>已把全部 {small_roi_truth_scope['frames']} 个恢复帧冻结为 {small_roi_truth_scope['tasks']} 个关节点任务（每帧 {small_roi_truth_scope['joint_count']} 个评分相关肩、髋、膝、踝、脚趾与脚跟点）。工作台只播放无骨架原视频并按检测框放大，不向标注者预填模型坐标；每个点必须由两名独立标注者分别提交，再由未参与标注的 reviewer 裁决。<a href="{html.escape(small_roi_truth_href)}">打开盲标工作台</a></p>
<p class="warning">当前原始人工标注 {small_roi_truth_scope['raw_annotations']}、已接受裁决 {small_roi_truth_scope['accepted_adjudications']}，状态为 <code>{html.escape(str(small_roi_truth_scope['status']))}</code>。因此像素 MAE、P95、x/y Bias、bbox 尺度归一化误差和分关节/分小框四分位结果均保持 null；PCK 还需外部预注册阈值。生产 32px 默认与 F2 成熟度不变。<a href="{html.escape(small_roi_truth_validation_href)}">真值门禁报告</a> · <a href="{html.escape(small_roi_error_href)}">空白误差报告</a></p></section>
<section><h2>13/13 指标可计算性证书与剩余 unavailable 动态证据</h2>
<video controls playsinline preload="metadata"><source src="{html.escape(residual_video_href)}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p class="good">在同一真实视频中的 102 个模型候选事件和实验 8px Pose 上，注册表 {residual_computability_scope['counts']['registry_indicator_count']}/{residual_computability_scope['counts']['registry_indicator_count']} 个指标都至少有一个 required-feature 契约完整、值非 null、单位/版本/置信度和证据帧齐全的真实测量实例；每项有 {residual_computability_scope['min_measured_per_indicator']}～{residual_computability_scope['max_measured_per_indicator']} 个 measured 实例。总计 {residual_computability_scope['counts']['measured_indicator_event_instance_count']}/{residual_computability_scope['counts']['indicator_event_instance_count']} measured，证明当前 13 项都能在合格观测上有效计算。</p>
<table><thead><tr><th>指标</th><th>measured / 实例</th><th>unavailable</th><th>required features</th><th>可追溯样例事件</th><th>合同完整</th></tr></thead><tbody>{''.join(residual_indicator_rows)}</tbody></table>
<p>剩余 {residual_computability_scope['counts']['unavailable_indicator_event_instance_count']} 条集中在 {residual_computability_scope['counts']['residual_event_count']} 个候选事件：{residual_computability_scope['classifications'].get('primary_bbox_clipped_at_image_boundary', 0)} 条球员实际越出画面边界，{residual_computability_scope['classifications'].get('primary_source_track_transition', 0)} 条跨 source Track，{residual_computability_scope['classifications'].get('video_start_boundary_censored', 0)} 条缺少视频开始前的上下文。动态视频逐帧展示三类原始证据。<a href="{html.escape(residual_video_href)}" target="_blank">单独打开证据视频</a></p>
<p class="warning">这些是源视频或身份连续性证据不足，不是可以靠填 0、降低质量门禁、放大已截断画面或跨模型挑值修复的数值缺口；系统继续输出 <code>unavailable</code>。13/13 可计算不等于事件准确、特征准确、F3/F4 或 A～E 可评分。<a href="{html.escape(residual_computability_href)}">机器可读可计算性审计</a> · <a href="{html.escape(residual_video_validation_href)}">视频验证</a></p></section>
<section><h2>Halpe26 基线的完整 26 点动态输出（31–51 秒）</h2><video controls playsinline preload="metadata" poster="full-body-26points-midframe.jpg"><source src="{full_body_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>画面左侧将主球员放大到全身，0–25 编号对应右侧逐帧点名和置信度列表。白色 0–16 是 COCO-17 公共点；橙色 17–19 是 RTMPose 新增的 <code>head</code>、<code>neck</code>、<code>hip</code>；紫/青/黄色 20–25 是左右大脚趾、小脚趾和脚跟。绿色菱形是明确标注的二维身体中心派生代理，不计入 26 个模型点。该片段 600/600 帧的主球员 26 点均通过当前点置信度门槛。<a href="{full_body_name}" target="_blank">单独打开全身 26 点视频</a></p>
<p class="warning">这是“当前 Halpe26 模型的完整实际输出”，不是“评分标准所需全部人体拓扑”。Halpe26 仍不输出胸椎/腰椎分段、手掌/手指/拇指、足中部/足弓/足底接触、深度和真实视线；这些缺失会继续阻断精细握拍、躯干分段和真实三维关节评分。</p></section>
<section><h2>从 298 项评分卡反推的人体采样需求</h2><table><thead><tr><th>人体证据语义</th><th>卡片文本涉及数（可重叠）</th><th>Halpe26</th><th>WholeBody133</th></tr></thead><tbody>{''.join(semantic_rows)}</tbody></table>
<p>298 项中有 291 项依赖 Pose、117 项是纯 Pose；288 张明确写出 J 编号的 Pose 卡全部能映射到 COCO-17，但编号映射不等于几何可测。完整机器审计：<a href="full-body-keypoint-coverage.json">full-body-keypoint-coverage.json</a>。</p></section>
<section><h2>先看这里：新增足部采样点动态聚焦（31–51 秒）</h2><video controls playsinline preload="metadata" poster="keypoint-focus-midframe.jpg"><source src="{keypoint_focus_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>左侧 YOLO COCO-17 只有左右脚踝点，因此足部放大窗明确显示 <code>NO HEEL / TOE OUTPUT</code>；右侧 RTMPose-M Halpe26 额外显示大脚趾（紫）、小脚趾（青）和脚跟（黄），并附逐帧置信度与短轨迹。这是同一视频、同一帧的动态双栏对比，不是截图。该 20 秒片段的主球员新增六点在 600/600 帧均通过当前点置信度门槛。<a href="{keypoint_focus_name}" target="_blank">单独打开聚焦视频</a></p>
<p class="warning">“点有输出”不等于“点是真值准确的”。二维踝角只证明 Halpe26 提供了 YOLO 所缺失的前足方向；在人工校正关键点、人工事件边界和教练标定完成前，评分仍保持 <code>calibration_required</code>。</p></section>
<section><h2>重点片段（40–60 秒）</h2><video controls playsinline preload="metadata" poster="comparison-video-midframe.jpg"><source src="{highlight_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video><p>用于快速查看站位、移动和足部点差异。<a href="{highlight_name}" target="_blank">单独打开 20 秒视频</a></p></section>
<section><h2>完整同步对比视频</h2><video controls playsinline preload="metadata" poster="comparison-video-midframe.jpg"><source src="{video_name}" type="video/mp4">当前浏览器无法内嵌播放 H.264 MP4，请使用下方直接打开链接。</video>
<p>四宫格为同一 97 秒视频、同一帧时间轴。黄色足部骨架来自 Halpe26 heel/toe；YOLO COCO17 不具备这些点。逐帧显示当前事件、有效指标数和二维踝角。<a href="{video_name}" target="_blank">单独打开 97 秒完整视频</a></p></section>
<section><h2>结论</h2><p class="good">模型输出颗粒度已明显提升：Halpe-26 补上前足方向；WholeBody-133 提供完整双手42点、面部68点和双脚6点。在原 {baseline_indicator_count} 项 A/B 特征可测性对照中，RTMPose-M Halpe26 将 unavailable 比例从 YOLO 的 4.23% 降至约 2.30%；WholeBody133 也已生成同一契约产物。该变化是可测性差异，不是准确率或正式评分能力证明。</p>
<p class="warning">新增点解决的是“能否观测”，不是“评分是否准确”。WholeBody-133 仍不提供胸椎/腰椎分段、真实质心、三维深度、足底压力/确认接触、球拍轴和真实视线。事件真值、特征误差与教练标定未完成前，所有有效结果继续保持 <code>calibration_required</code>。</p></section>
<section><h2>真实视频 {pose_wave_scope['indicator_count']} 项 registry 指标安全计算闭环</h2><p>在同一 97 秒 RTMPose-M 视频上，{pose_wave_event_text}。{_pose_wave_intro(pose_wave_scope)}</p>
<table><thead><tr><th>指标</th><th>特征可测/总数</th><th>calibration_required</th><th>unavailable</th><th>grade</th></tr></thead><tbody>{''.join(pose_wave_rows)}</tbody></table>
<p class="warning">当前事件检测仍是可解释规则基线，不是经人工事件标签验证的准确识别器；候选段数不能当作真实动作次数。<a href="{html.escape(full_detail_href)}">打开逐事件特征与证据报告</a> · <a href="../scoring-candidate-multivideo-m63/reports/850cb0006b406c7176eeda8d711cd065.json">打开当前 M63 机器 JSON</a>。</p></section>
<section><h2>正常上传现已默认走 RTMPose-M Halpe26</h2>
<p class="good">默认模型替换已经落到真实 Worker，而不只是离线脚本：未传 <code>RALLYMATE_POSE_PRESET</code>、也未传命令行 preset 覆盖时，服务注册表自动选择 <code>{html.escape(default_pose_routing_scope['deployment_default']['preset_id'])}</code>，实际执行 <code>{html.escape(default_pose_routing_scope['smoke']['execution_path'])}</code>。本次 GPU 烟测处理 {default_pose_routing_scope['smoke']['processed_frames']} 帧，{default_pose_routing_scope['smoke']['effective_processed_fps']:.3f} FPS，原生输出 {default_pose_routing_scope['smoke']['native_keypoint_count']} 点 {html.escape(default_pose_routing_scope['smoke']['native_keypoint_format'])}；bundle 验证为 <code>{html.escape(default_pose_routing_scope['smoke']['bundle_validation_status'])}</code>。</p>
<p>烟测使用的 RTMPose 权重 SHA256 与 97 秒全片证据完全一致。保持生产 32px 最小 ROI 时，全片同一 102 个模型候选事件上，{default_pose_routing_scope['full']['measured_indicator_event_instance_count']}/{default_pose_routing_scope['full']['indicator_event_instance_count']} 条指标实例的 Pose measurement features 完整；当前注册表 {default_pose_routing_scope['full']['indicator_with_measured_instance_count']}/{default_pose_routing_scope['indicator_count']} 个指标均至少有一个真实 measured 实例，每项 {default_pose_routing_scope['min_production_measured_per_indicator']}～{default_pose_routing_scope['max_production_measured_per_indicator']} 条。</p>
<p>新的每次上传计算就绪契约已实际接入 Pipeline。当前 M42 真实 GPU 120 帧烟测中 {default_smoke_calculation_scope['summary']['indicator_with_measured_candidate_count']}/{default_smoke_calculation_scope['summary']['registry_indicator_count']} 项至少有一个 measured 候选，36/39 条指标事件实例在 Pose 测量层完整；97 秒全片达到 {full_calculation_scope['summary']['indicator_with_measured_candidate_count']}/{full_calculation_scope['summary']['registry_indicator_count']}，403/442 条测量层完整。无效实例明确保留 unavailable，而不是显示为 0 分。</p>
<details><summary>展开查看短烟测与全片的 13 项逐项计算覆盖</summary><table><thead><tr><th>指标</th><th>120 帧默认 Worker measured / 实例</th><th>97 秒生产 32px measured / 实例</th><th>全片 unavailable</th><th>全片至少一个真实可测实例</th></tr></thead><tbody>{''.join(default_pose_indicator_rows)}</tbody></table></details>
<h3>97 秒全片：每项一个可直接消费的代表特征向量</h3><p class="good">M39 不是只证明“有记录”：它从全片每项的 measured 候选中，仅按 required-feature 最低/平均置信度、事件置信度和质量状态选择一个代表实例，完全不读取特征值本身，也不按运动表现挑最好结果。当前 {full_measurement_portfolio_scope['summary']['measured_indicator_count']}/{full_measurement_portfolio_scope['summary']['registry_indicator_count']} 项均有实际值、单位、置信度、事件、Track 和证据帧。</p>
<div style="overflow:auto"><table><thead><tr><th>指标</th><th>测量状态</th><th>代表事件</th><th>版本化特征值</th><th>measured 候选 / 全部候选</th><th>A～E</th></tr></thead><tbody>{''.join(representative_measurement_rows)}</tbody></table></div>
<h3>同一次 FS01→FS02→FS09 动作周期内算全 13 项</h3><p class="good">M40 不再把不同动作的“最佳可观测事件”拼成一组：全片 {full_cycle_measurement_scope['summary']['linked_cycle_count']} 个三阶段周期全部用 initiation/peak phase 精确闭合，其中 {full_cycle_measurement_scope['summary']['complete_cycle_count']} 个周期在同一 Track、同一次动作内同时具备 13/13 项完整 Pose 测量。代表周期为 13/13 测量完整、{full_cycle_measurement_scope['summary']['best_cycle_scoring_gate_passed_indicator_count']}/13 评分上下文通过。当前 M42 真实 GPU 120 帧烟测有 {default_smoke_cycle_measurement_scope['summary']['linked_cycle_count']} 个周期，其中 {default_smoke_cycle_measurement_scope['summary']['complete_cycle_count']} 个周期为 13/13 测量完整；最佳周期 {default_smoke_cycle_measurement_scope['summary']['best_cycle_scoring_gate_passed_indicator_count']}/13 评分上下文通过。测量完整与可评分被明确分开，系统不跨周期补齐。</p>
<h3>FS02-M02 目标方向参考上下文</h3><p class="good">M41 已把原先只显示“目标方向缺失”的阻断变成可执行契约：全片 {len(m41_reference_observations)} 个 FS02 候选事件都有按视频 SHA、事件边界和独立复核要求生成的目标方向工作项；运行时可计算 <code>target_direction_alignment_error_deg</code>，并且只有已接受的人工/操作员参考才能解除该单项门禁。实际 Worker 120 帧检测到 3 个 FS02，并对本次边界生成的 3 条 pending 参考完成 3/3 精确绑定。</p><p class="warning">当前真实工作清单仍为 {m41_context_summary['pending_count']} 项 pending、{m41_context_summary['available_alignment_count']} 项 available，因此 FS02-M02 继续 unavailable；系统没有从人体移动方向反推战术目标，也没有生成 grade 或 threshold。<a href="{html.escape(m41_reference_context_href)}">目标方向工作清单</a> · <a href="{html.escape(m41_worker_analysis_report_href)}">M41 实际 Worker 报告</a> · <a href="{html.escape(m41_analysis_report_href)}">M41 全片评分报告</a> · <a href="{html.escape(m41_context_report_href)}">M41 机器摘要</a> · <a href="{html.escape(m41_cycle_measurement_href)}">M41 同周期测量</a></p>
<h3>M42：Pose 测量向量与完整评分向量分离</h3><p class="good"><code>FS02-M02</code> 的 Pose 测量向量仍由 4 个可观测运动学特征构成；完整评分/标定向量新增第 5 个 <code>target_direction_alignment_error_deg</code>。它只由已接受的目标方向与实测启动方向计算，单位为 deg，0 表示真实 0° 对齐，不再用“缺失=0”。当前 GPU Worker 的 3 个 FS02 均完成 4/4 Pose 测量，但 3 个目标方向仍 pending，因此完整评分向量均为 unavailable；97 秒全片 34 个 FS02 同样保持 fail closed。</p><p class="warning">M42 真实 GPU 路径处理 {m42_worker_summary['processing']['processed_frames']} 帧，输出 9 个事件、39 条指标实例，13/13 指标有 measured 候选；同周期最佳为 13/13 Pose 测量、8/13 评分上下文通过。全片为 403/442 测量完整，评分仍为 {pose_wave['summary']['score_status_counts'].get('calibration_required', 0)} calibration_required / {pose_wave['summary']['score_status_counts'].get('unavailable', 0)} unavailable，grade=0、threshold=0。<a href="{html.escape(m42_worker_analysis_report_href)}">M42 GPU 报告</a> · <a href="{html.escape(m42_full_analysis_report_href)}">M42 全片逐事件报告</a></p>
<p class="warning">这里替换的是 Pose 默认后端：Player 检测仍可继续使用 YOLO；<code>yolo-baseline</code> 保留为显式回滚 preset。RTMPose 默认只代表 F2 特征测量路径，生产 ROI 仍为 32px，8px 仍是实验配置。烟测和全片均为 0 grade、0 threshold；没有人工事件、人工关键点、教练真值和独立测试时，不会因为默认模型已替换就输出正式 A～E。<a href="{html.escape(default_pose_routing_href)}">机器可读默认路由审计</a> · <a href="{html.escape(default_worker_smoke_href)}">真实 Worker 烟测</a> · <a href="{html.escape(default_m38_analysis_report_href)}">带计算就绪的 Worker 报告</a> · <a href="{html.escape(default_m39_analysis_report_href)}">带代表测量的新 Worker 报告</a> · <a href="{html.escape(default_m40_analysis_report_href)}">带同周期测量的 Worker 报告</a> · <a href="{html.escape(default_smoke_measurement_portfolio_href)}">短烟测代表测量</a> · <a href="{html.escape(full_measurement_portfolio_href)}">全片 13 项代表测量</a> · <a href="{html.escape(default_smoke_cycle_measurement_href)}">短烟测同周期测量</a> · <a href="{html.escape(full_cycle_measurement_href)}">全片同周期 13 项测量</a> · <a href="{html.escape(default_smoke_calculation_readiness_href)}">短烟测就绪 JSON</a> · <a href="{html.escape(full_calculation_readiness_href)}">全片就绪 JSON</a></p></section>
<section><h2>服务 Worker 已直接接入当前 13 项闭环</h2><p>三套显式预设均已真实走通 <code>JobDatabase → PersistentVisionRunner → process_one → run_pipeline</code>。指标集合由请求与服务共同指向的 v2 注册表动态派生，不再复用历史六项硬编码。</p>
<table><thead><tr><th>预设</th><th>原生拓扑</th><th>帧 / FPS</th><th>FS01 / FS02 / FS09 候选</th><th>指标 / 记录</th><th>calibration_required / unavailable</th><th>grade / threshold</th><th>bundle</th></tr></thead><tbody>{''.join(deployment_rows)}</tbody></table>
<p class="good">WholeBody133 的常驻 Worker 产物确认保留原生 133 点并进入相同的事件、特征、质量门禁与评分状态契约；不是仅生成了一段可视化视频。</p><p class="warning">三档短片使用的时间窗口和输入尺寸不同，FPS 与状态数量只验证部署链路，不用于模型准确率排名。全部报告显式记录 <code>accuracy_claim=false</code>，且没有生成 grade 或 threshold。</p></section>
<section><h2>模型汇总</h2><table><thead><tr><th>模型</th><th>端到端 {baseline_indicator_count} 项指标 unavailable</th><th>calibration_required</th><th>固定同一事件后的 unavailable</th><th>左右踝角可测率</th><th>左右踝角中位数（仅诊断）</th><th>原 298 项证据 partial / blocked</th><th>原 298 项静态审计状态（三视频合计）</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table>
<p>“端到端”允许各模型各自切分候选事件；“固定同一事件”复用 YOLO 的 591 个候选区间，只比较 Pose 对 {baseline_indicator_count} 项指标特征有效性的影响。两组边界都尚非人工事件真值。</p></section>
<section><h2>{baseline_indicator_count} 项指标逐项有效率</h2><table><thead><tr><th>模型</th><th>指标</th><th>有效/总数</th><th>有效率</th></tr></thead><tbody>{''.join(indicator_rows)}</tbody></table></section>
<section><h2>脚踝角的含义</h2><p><code>knee–ankle–forefoot center</code> 是由膝、踝、大脚趾、小脚趾构成的二维角度。它能直观证明 Halpe26 的足部颗粒度高于 COCO17，但受机位透视和遮挡影响，不能直接称为真实三维踝背屈角，也没有被用作 A～E 阈值。</p></section>
<section><h2>原指标卡的测量缺口</h2><p>FS01-M02 的文字定义包含“踝关节进入弹性准备状态”，但旧卡只要求左右 ankle 位置点。一个点无法定义关节角；至少还需要膝与前足方向。当前闭环已把 Halpe26 左右 <code>knee–ankle–forefoot</code> 角作为 FS01-M02 和 FS09-M03 的 <code>supplemental_features</code> 写入逐事件产物。YOLO 对这两个补充特征明确 unavailable，RTMPose 可测，但在人工关键点和教练标定前只作为颗粒度证据。</p></section>
<section><h2>M67 可观测性实验产物</h2><ul><li><a href="{html.escape(m67_recovery_report_href)}"><code>measurement-recovery-m67/summary/report.json</code></a>：0.15→0.30 ROI margin 与 M66 小 ROI 的三视频精确组合复算。</li><li><a href="{html.escape(m67_recovery_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态 A/B</a> · <a href="{html.escape(m67_recovery_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态 A/B</a> · <a href="{html.escape(m67_recovery_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}">视频 3 动态 A/B</a>。这些产物只证明同模型可观测性，不是准确率，也未进入生产路由。</li></ul></section>
<section><h2>M68 严格超集路由实验产物</h2><ul><li><a href="{html.escape(m68_router_report_href)}"><code>measurement-recovery-m68/summary/report.json</code></a>：评分关节有效集严格超集路由与 M66 小 ROI 的三视频固定边界重算。</li><li><a href="{html.escape(m68_router_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态对比</a> · <a href="{html.escape(m68_router_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态对比</a> · <a href="{html.escape(m68_router_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}">视频 3 动态对比</a>。路由只看 required-joint validity，不看特征结果、事件结果或等级；当前仍是实验候选。</li></ul></section>
<section><h2>M69 残差高分辨率实验产物</h2><ul><li><a href="{html.escape(m69_residual_audit_href)}"><code>measurement-recovery-m69/residual-audit/report.json</code></a>：M68 后非 hard-fail 特征残差与目标事件帧。</li><li><a href="{html.escape(m69_high_resolution_report_href)}"><code>measurement-recovery-m69/summary/report.json</code></a>：384×288、0.30 ROI 候选经 required-joint 严格超集路由后的三视频固定边界重算。</li><li><a href="{html.escape(m69_high_resolution_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态对比</a> · <a href="{html.escape(m69_high_resolution_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态对比</a> · <a href="{html.escape(m69_high_resolution_video_hrefs['8d7754d0de6d315674013d5b69a0b6ba'])}">视频 3 动态对比</a>。不自动改生产模型，不声称准确率或正式评分。</li></ul></section>
<section><h2>M70 Pose profile / 裁剪上下文消融产物</h2><ul><li><a href="{html.escape(m70_pose_profile_report_href)}"><code>measurement-recovery-m70/summary/report.json</code></a>：三视频同上下文 384×288 消融、直接多候选反例及 M69 锚定扩展的机器汇总。</li><li><a href="{html.escape(m70_pose_profile_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态对比</a> · <a href="{html.escape(m70_pose_profile_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态对比</a>。两段均为 H.264 全时序，不是单帧截图；视频 3 的锚定扩展选择 0 帧，因此不伪造一段“变化视频”。</li></ul></section>
<section><h2>M71 RTMPose-L 受控扩展产物</h2><ul><li><a href="{html.escape(m71_larger_pose_report_href)}"><code>measurement-recovery-m71/summary/report.json</code></a>：三视频、258 个相同残差帧、严格关节超集路由及 M70 保留审计。</li><li><a href="{html.escape(m71_larger_pose_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态对比</a> · <a href="{html.escape(m71_larger_pose_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态对比</a>。两段均为 H.264 全时序；视频 3 未选择 L 帧，因此没有伪造变化。</li></ul></section>
<section><h2>M72 缺失点加法融合产物</h2><ul><li><a href="{html.escape(m72_additive_fusion_report_href)}"><code>measurement-recovery-m72/summary/report.json</code></a>：三视频点级加法融合、M71 恢复保留审计与 13 个剩余残差的逐项分类。</li><li><a href="{html.escape(m72_additive_fusion_video_href)}">M71 vs M72 动态对比</a>：仅展示实际产生两个完整指标恢复的事件窗口；其他视频没有伪造“恢复视频”。</li></ul></section>
<section><h2>M73 WholeBody133 映射补点产物</h2><ul><li><a href="{html.escape(m73_wholebody_mapped_report_href)}"><code>measurement-recovery-m73/summary/report.json</code></a>：三视频 254 个候选输出、101 个补点、0 个新增完整指标和 13 个剩余残差的 source replay。</li><li><a href="{html.escape(m73_wholebody_mapped_video_hrefs['3ae77ee3271d67de171585a5c39ddd69'])}">视频 1 动态对比</a> · <a href="{html.escape(m73_wholebody_mapped_video_hrefs['850cb0006b406c7176eeda8d711cd065'])}">视频 2 动态对比</a>：展示真实点级变化，即使没有指标恢复也不隐藏负结果。</li></ul></section>
<section><h2>M74 时间缺口审计产物</h2><ul><li><a href="{html.escape(m74_event_gap_report_href)}"><code>measurement-recovery-m74/event-bounded-gap-audit-v1/report.json</code></a>：13 个残余实例、142 个唯一候选插值点、每点双侧证据帧、20 个 invalid→valid 特征转换及 7/13 个反事实完整向量。</li><li>该文件明确记录 production recovered=0、无真值、无阈值、无等级，也没有生成可被生产评分误用的 Pose JSONL。</li></ul></section>
<section><h2>M75 人工关键点真值产物</h2><ul><li><a href="{html.escape(m75_event_gap_truth_href)}"><code>event-bounded-pose-gap-truth-m75-v1/review.html</code></a>：142 个双人独立盲标任务与第三方逐点裁决工作台，页面不加载密封插值坐标。</li><li><a href="{html.escape(m75_event_gap_truth_validation_href)}">编译验证</a>与<a href="{html.escape(m75_event_gap_error_href)}">误差报告</a>：当前 0 条人工真值，MAE/P95/Bias/valid rate 均为 null，生产插值和成熟度晋级均关闭。</li></ul></section>
<section><h2>M76 真值条件特征误差产物</h2><ul><li><a href="{html.escape(m76_event_gap_feature_error_href)}"><code>measurement-recovery-m76/event-gap-feature-error-empty.json</code></a>：已绑定 M74/M75、registry 与三段视频输入，待真值就绪后重算 11 个指标实例的 47 条特征记录。</li><li>当前 status=annotation_required，metrics/per-feature/per-indicator 均为 null；2 个 phase-only FS09 残差显式排除，不以插值伪造阶段。</li></ul></section>
<section><h2>M77 FS09 阶段真值产物</h2><ul><li><a href="{html.escape(m77_fs09_phase_truth_href)}"><code>fs09-phase-truth-m77-v1/review.html</code></a>：2 个无骨架视频盲标任务，候选事件/阶段单独密封，要求双人独立标注与第三方裁决。</li><li><a href="{html.escape(m77_fs09_phase_validation_href)}">编译验证</a>与<a href="{html.escape(m77_fs09_phase_error_href)}">评测报告</a>：当前 0 条真值，Event/阶段/特征边界敏感性指标均为 null，运行时事件、特征、等级和成熟度均不修改。</li></ul></section>
<section><h2>M78 类型化阻断机器产物</h2><ul><li><a href="{html.escape(m78_blocker_taxonomy_audit_href)}"><code>measurement-recovery-m78/blocker-taxonomy-audit.json</code></a>：统一 taxonomy、M65 三视频阻断审计与 M63→M78 无 GPU 行级重放绑定。</li><li>三视频共 2,366 个指标实例；输出只改解释代码的维护来源，不改事件、特征值、评分状态、质量门禁、grade 或 threshold。</li></ul></section>
<section><h2>机器产物</h2><ul><li><a href="wholebody133-h264-validation.json"><code>wholebody133-h264-validation.json</code></a>：两段 H.264 视频首/中/末帧、codec、时长与 SHA256 验证。</li><li><a href="full-body-keypoint-coverage.json"><code>full-body-keypoint-coverage.json</code></a>：298 项人体证据语义与三种拓扑缺口。</li><li><code>pose-scoring-ab.json</code>：四模型原 {baseline_indicator_count} 项指标和足部诊断汇总。</li><li><a href="../f2-error-budget-readiness-m64.json"><code>f2-error-budget-readiness-m64.json</code></a>：13 项 calculation、平滑反事实、事件/特征真值误差与等级间距证据的 fail-closed 就绪矩阵。</li><li><a href="{html.escape(default_pose_routing_href)}"><code>default-pose-routing-audit-m37.json</code></a>：无环境/CLI preset 覆盖的服务默认 RTMPose 路由、模型哈希、全片 13 项覆盖与无等级门禁。</li><li><a href="{html.escape(default_smoke_calculation_readiness_href)}"><code>calculation-readiness.json</code></a> 与 <a href="{html.escape(full_calculation_readiness_href)}">全片逐指标报告</a>：每次上传的 13 项计算状态、证据帧、测量修复动作和独立评分真值阻断。</li><li><a href="{html.escape(default_smoke_measurement_portfolio_href)}"><code>indicator-measurement-portfolio.json</code></a> 与 <a href="{html.escape(full_measurement_portfolio_href)}">全片 13 项代表测量</a>：不按运动表现挑值的逐指标实际特征向量、事件、Track、置信度与证据帧。</li><li><a href="{html.escape(default_smoke_cycle_measurement_href)}"><code>scoring-cycle-measurement.json</code></a> 与 <a href="{html.escape(full_cycle_measurement_href)}">全片同周期 13 项</a>：用 initiation/peak phase 绑定同一次 FS01→FS02→FS09，禁止跨动作拼接。</li><li><a href="{html.escape(m41_reference_context_href)}"><code>scoring-reference-context-v1</code></a>：34 个 FS02 目标方向待标注项；视频 SHA、事件边界、观察者和独立复核者均受契约约束。</li><li><a href="../pose-profile-routing-audit-full.json"><code>pose-profile-routing-audit-full.json</code></a>：97 秒三种 Pose 配置的同边界测量覆盖与安全路由决定。</li><li><a href="../scoring-blocker-audit-halpe256-full-m43.json"><code>scoring-blocker-audit-halpe256-full-m43.json</code></a>：历史单视频评分阻断审计；当前三视频归因见 <a href="{html.escape(multivideo_readiness_index_href)}">M65 就绪拆解</a>。</li><li><a href="../scoring-truth-action-worklist-halpe256-full-m44/index.html">M44 当前行动清单</a> · <a href="../scoring-truth-action-readiness-halpe256-full-m45.json">M45 行动证据完成度</a> · <a href="../scoring-truth-evidence-plan-halpe256-full-m46/index.html">M46 共享证据计划</a> · <a href="{html.escape(truth_refresh_index_href)}">M47 最新真值刷新</a> · <a href="{html.escape(truth_calibration_handoff_index_href)}">M48 标定交接</a>。</li><li><a href="../scoring-truth-priority-worklist-halpe256-full/index.html"><code>scoring-truth-priority-worklist-*/</code></a>：历史 M31 的视频可定位工作项，不作为当前三视频计数真源。</li><li><a href="{html.escape(small_roi_report_href)}"><code>small-roi-pose-recovery-*/report.json</code></a>、<a href="{html.escape(small_roi_video_href)}">动态 A/B 视频</a> 与 <a href="{html.escape(small_roi_fixed_href)}">固定边界对比</a>：32px→8px 小 ROI Pose 动态 A/B；非生产、非准确率。</li><li><a href="{html.escape(small_roi_truth_href)}"><code>small-roi-keypoint-truth-halpe256-v1/</code></a> 与 <a href="{html.escape(small_roi_error_href)}">误差报告</a>：双人独立盲标、第三方裁决和分小框尺度误差接口；当前真值为 0。</li><li><a href="{html.escape(residual_computability_href)}"><code>residual-indicator-computability-*.json</code></a>、<a href="{html.escape(residual_video_href)}">动态证据视频</a> 与 <a href="{html.escape(residual_video_validation_href)}">视频验证</a>：13/13 指标可计算性证书及 19 条 fail-closed 残余分类。</li><li><a href="../fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json"><code>fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json</code></a>：历史 600 帧同窗、{same_window_scope['indicator_count']} 项 Pose 模型安全状态对照。</li><li><a href="../event-disagreement-halpe26-vs-wholebody133-same-window.json"><code>event-disagreement-*.json</code></a>：候选事件跨模型一对一分歧与 IoU 敏感性。</li><li><a href="../fixed-boundary-pose-ab-halpe-candidates-930-1530.json"><code>fixed-boundary-*-halpe-candidates.json</code></a> 与 <a href="../fixed-boundary-pose-ab-wholebody-candidates-930-1530.json">WholeBody 边界结果</a>：历史双向公共边界特征分歧。</li><li><a href="../fs09-pose-wave-v2-m42-smoke.json"><code>fs09-pose-wave-v2-m42-smoke.json</code></a>：{pose_wave_scope['indicator_count']} 项 registry 指标当前 M42 全片特征/完整评分向量/质量门禁/安全状态。</li><li><a href="../pose-diagnostic-review/m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full/index.html">M65 视频 1 Pose 诊断复核</a> · <a href="../pose-diagnostic-review/m65-850cb0006b406c7176eeda8d711cd065-halpe26-full/index.html">视频 2</a> · <a href="../pose-diagnostic-review/m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full/index.html">视频 3</a>。</li><li><a href="../pose-diagnostic-truth/m65-3ae77ee3271d67de171585a5c39ddd69-halpe26-full-v1/index.html">M65 视频 1 诊断真值盲审</a> · <a href="../pose-diagnostic-truth/m65-850cb0006b406c7176eeda8d711cd065-halpe26-full-v1/index.html">视频 2</a> · <a href="../pose-diagnostic-truth/m65-8d7754d0de6d315674013d5b69a0b6ba-halpe26-full-v1/index.html">视频 3</a> · <a href="../pose-diagnostic-quality-gate-review-m65-three-video-empty.json">三视频质量门禁审查状态</a>。</li><li><a href="../../data/annotations/scoring-truth-pack-v1/review.html">人工真值标注工作台</a> 与 <a href="../../data/annotations/scoring-truth-pack-v1/compiled/validation-report.json">真值验证报告</a>。</li><li><a href="../truth-pack-empty-evaluation.json">事件/特征/目标方向真值评测门禁</a>、<a href="{html.escape(truth_calibration_handoff_index_href)}">M48 13 项标定 readiness</a>、<a href="../calibration-interface-report.json">标定接口报告</a>。</li></ul></section>
<p class="warning">当前标定机器真源：<a href="{html.escape(truth_calibration_portfolio_index_href)}">三视频 portfolio</a>；当前候选事件计算覆盖：<a href="{html.escape(multivideo_calculation_coverage_index_href)}">M63 三视频 13 项证据</a>；当前状态口径：<a href="{html.escape(multivideo_readiness_index_href)}">M63 互斥就绪拆解</a>。当前 fit-ready {int(truth_calibration_portfolio_scope['counts']['fit_ready_indicators'])}/{int(truth_calibration_portfolio_scope['counts']['indicators'])}；grade=0、threshold=0。旧单视频 handoff 仅保留历史追溯。</p>
</body></html>''', encoding="utf-8"
    )
    markdown = root / "docs" / "POSE_SCORING_MODEL_COMPARISON.md"
    markdown.write_text(
        "# RallyMate YOLO / RTMPose 动态评分对比\n\n"
        "完整可播放报告：`reports/pose-scoring-ab/index.html`。\n\n"
        "对比视频不是抽帧截图：\n\n"
        "- `reports/pose-scoring-ab/rtmpose-wholebody133-full-detail-31s-51s-browser.mp4`：20 秒完整 WholeBody-133 分组动态输出；\n"
        "- `reports/pose-scoring-ab/yolo17-vs-halpe26-vs-wholebody133-31s-51s-browser.mp4`：20 秒三模型同帧同步对比；\n"
        "- `reports/pose-scoring-ab/rtmpose-halpe26-full-body-26points-31s-51s-browser.mp4`：20 秒主球员全身放大、全部 26 个实际点编号、逐帧名称/置信度与缺口说明；\n"
        "- `reports/pose-scoring-ab/yolo-vs-rtmpose-keypoint-focus-31s-51s-browser.mp4`：20 秒 YOLO / RTMPose 双栏全身与左右足部放大动态视频；\n"
        "- `reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-highlight-40s-60s-browser.mp4`：20 秒 H.264 重点片段；\n"
        "- `reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-comparison-browser.mp4`：97 秒 H.264 完整同步四宫格；\n"
        "- `reports/experiments/small-roi-pose-recovery-halpe256-full-v1/small-roi-pose-recovery-comparison-browser.mp4`：13 秒当前 32px 与实验 8px ROI Pose 动态 A/B；\n"
        "- `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4`：4.77 秒剩余 19 条 unavailable 的源视频动态证据。\n\n"
        "- `reports/measurement-recovery-m67/roi-margin-0.30/3ae77ee3271d67de171585a5c39ddd69/current15-vs-experimental30-changed-browser.mp4`：视频 1 的 0.15/0.30 ROI margin 动态 A/B；\n"
        "- `reports/measurement-recovery-m67/roi-margin-0.30/850cb0006b406c7176eeda8d711cd065/current15-vs-experimental30-changed-browser.mp4`：视频 2 的动态 A/B，包含唯一 operational 回归范围；\n"
        "- `reports/measurement-recovery-m67/roi-margin-0.30/8d7754d0de6d315674013d5b69a0b6ba/current15-vs-experimental30-changed-browser.mp4`：视频 3 的动态 A/B。\n\n"
        "- `reports/measurement-recovery-m68/required-joint-superset/3ae77ee3271d67de171585a5c39ddd69/current-vs-required-joint-router-changed-browser.mp4`：视频 1 的当前 Pose 与 required-joint 严格超集路由动态对比；\n"
        "- `reports/measurement-recovery-m68/required-joint-superset/850cb0006b406c7176eeda8d711cd065/current-vs-required-joint-router-changed-browser.mp4`：视频 2 的动态路由对比；\n"
        "- `reports/measurement-recovery-m68/required-joint-superset/8d7754d0de6d315674013d5b69a0b6ba/current-vs-required-joint-router-changed-browser.mp4`：视频 3 的动态路由对比。\n\n"
        "- `reports/measurement-recovery-m69/high-resolution-384-margin030/3ae77ee3271d67de171585a5c39ddd69/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 1 的 M68 当前 Pose 与 384×288/0.30 ROI 残差路由动态对比；\n"
        "- `reports/measurement-recovery-m69/high-resolution-384-margin030/850cb0006b406c7176eeda8d711cd065/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 2 的残差路由动态对比；\n"
        "- `reports/measurement-recovery-m69/high-resolution-384-margin030/8d7754d0de6d315674013d5b69a0b6ba/current-m68-vs-halpe384-margin030-router-changed-browser.mp4`：视频 3 的残差路由动态对比。\n\n"
        "- `reports/measurement-recovery-m70/m69-anchored-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m69-vs-anchored-profile-extension-changed-browser.mp4`：视频 1 的 M69 与 M70 锚定扩展动态对比；\n"
        "- `reports/measurement-recovery-m70/m69-anchored-extension-v1/850cb0006b406c7176eeda8d711cd065/m69-vs-anchored-profile-extension-changed-browser.mp4`：视频 2 的 M69 与 M70 锚定扩展动态对比。视频 3 未选择扩展帧，未生成伪变化视频。\n\n"
        "- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/3ae77ee3271d67de171585a5c39ddd69/m70-vs-rtmpose-l-extension-changed-browser.mp4`：视频 1 的 M70 与 RTMPose-L 受控扩展动态对比；\n"
        "- `reports/measurement-recovery-m71/rtmpose-l-384-context-extension-v1/850cb0006b406c7176eeda8d711cd065/m70-vs-rtmpose-l-extension-changed-browser.mp4`：视频 2 的动态对比。视频 3 未选择 L 候选帧，未生成伪变化视频。\n\n"
        "- `reports/measurement-recovery-m72/additive-keypoint-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m71-vs-additive-keypoint-fusion-changed-browser.mp4`：M71 与 M72 同拓扑缺失点加法融合动态对比；仅该视频产生完整指标恢复。\n\n"
        "- `reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/3ae77ee3271d67de171585a5c39ddd69/m72-vs-wholebody133-mapped-changed-browser.mp4`：M72 与 WholeBody133 显式同名点映射动态对比（视频 1）。\n"
        "- `reports/measurement-recovery-m73/wholebody133-mapped-fusion-v1/850cb0006b406c7176eeda8d711cd065/m72-vs-wholebody133-mapped-changed-browser.mp4`：同一合同的视频 2 动态对比；101 个总补点没有新增完整指标。\n\n"
        f"当前正式评分门禁：注册表共 {len(feasibility_registry['indicators'])} 项，层级为 {', '.join(feasibility_levels)}；人工事件 {truth_counts['manual_events']} 条、人工校正关键点 {truth_counts['accepted_keypoint_joint_rows']} 条、人工语义 {truth_counts['semantic_truth_records']} 条、教练标注 {truth_counts['coach_labels']} 条、可拟合样本 {calibration_counts['samples']} 条、生产标定资产 {production_calibration_asset_count} 个。因此现在能跑通事件/特征/质量状态契约，但不能正式输出 A～E，状态必须保持 `calibration_required` 或 `unavailable`。人工入口为 `data/annotations/scoring-truth-pack-v1/review.html`，当前三视频标定组合见 `{truth_calibration_portfolio_repo_path}`。\n\n"
        f"当前版本绑定：registry `{feasibility_registry['registry_version']}`，scoring loop `{pose_wave['summary']['loop_version']}`，primary `{pose_wave['summary']['model_versions']['primary_player']}`，event `{pose_wave['summary']['model_versions']['event']}`，FS01/FS02 `{pose_wave['summary']['model_versions']['feature_contract']['FS02-M02']}`，FS09 `{pose_wave['summary']['model_versions']['feature_contract']['FS09-M02']}`，quality `{pose_wave['summary']['model_versions']['quality_policy']}`。97 秒全片 M53 使用既有 Pose/Track 做当前版本无 GPU 后处理重算；M42 GPU Worker 是 `.15` 历史部署烟测，同窗/固定边界模型 A/B 也保留为历史比较快照。\n\n"
        f"历史 31–51 秒窗口曾为 Halpe26 与 WholeBody133 生成 registry 声明的 {same_window_scope['indicator_count']} 项指标特征与安全状态：{same_window_markdown_summary}；两边均为 0 个 A～E、0 个伪阈值。事件候选差异只能说明规则边界受 Pose 输出影响，不能当作准确率或正式评分能力，也不能冒充当前 `{feasibility_registry['registry_version']}` 评分向量产物。机器对照见 `reports/fs09-pose-wave-v2-halpe26-vs-wholebody133-same-window.json`。\n\n"
        f"Pose 诊断现已生成可跳转连续视频的去重人工复核队列：{diagnostic_review_markdown_summary}。浏览器只导出人工复核 CSV，所有条目仍为 pending candidate，不是真值，也不会自动修改质量门禁。入口位于 `reports/pose-diagnostic-review/`。\n\n"
        f"为避免只看正候选导致无法测漏检，另生成全时间线诊断真值包：{diagnostic_truth_markdown_summary}。工作台采用覆盖区间 + 稀疏真阳性；无人工裁决时 precision/recall/F1 必须保持 null，入口位于 `reports/pose-diagnostic-truth/`。\n\n"
        f"诊断质量策略审查绑定 {diagnostic_policy_review_scope['source_count']} 个完整视频范围、{diagnostic_policy_review_scope['diagnostic_type_count']} 类诊断，当前为 `{diagnostic_policy_review_scope['status']}`。缺少外部预注册接受协议和全时间线真值时，质量门禁不会改变；未来即使指标满足协议，也只进入人工策略审核。机器报告为 `reports/pose-diagnostic-quality-gate-review-m65-three-video-empty.json`。\n\n"
        f"候选事件跨模型全局匹配在 IoU≥0.3 时为 {event_disagreement_scope['selected']['matched_count']} 对，平均 Segment IoU={event_disagreement_scope['selected']['mean_segment_iou']:.4f}；这不是 Event F1。固定 Halpe 候选边界时双方有效 required feature 对为 {fixed_boundary_scopes['Halpe26 候选边界']['validity_states'].get('both_valid', 0)}/{fixed_boundary_scopes['Halpe26 候选边界']['feature_pair_count']}；固定 WholeBody 候选边界时为 {fixed_boundary_scopes['WholeBody133 候选边界']['validity_states'].get('both_valid', 0)}/{fixed_boundary_scopes['WholeBody133 候选边界']['feature_pair_count']}。`hip_center_y_body` 跨模型 MAD 分别为 {hip_mads[0]:.3f}/{hip_mads[1]:.3f} body，因此当前不能据颗粒度直接完成生产模型替换或正式评分。\n\n"
        "97 秒全片模型路由审计使用同一套 Halpe256 候选边界：Halpe256 为 403/442、Halpe384 为 399/442、WholeBody133 为 369/442 个指标事件特征完整。Halpe384 在各自切分边界上为 415/442，但与 Halpe256 的自动事件在 IoU≥0.3 时只匹配 90/102，中心边界平均绝对差 64.26 ms，因此不能把各自切分数字当模型净提升。当前保留 Halpe256 作为评分主配置，禁止逐事件/逐特征跨模型拼接；这只是覆盖路由，不是准确率结论。机器报告为 `reports/pose-profile-routing-audit-full.json`。\n\n"
        f"全片 266 条 `unavailable` 已按完整 scoring vector 做互斥归因：{int(blocker_decomposition.get('unavailable_complete_features_score_only', 0))} 条评分向量完整、仅被正式评分证据阻断；{int(blocker_decomposition.get('unavailable_feature_incomplete_without_score_block', 0))} 条仅评分向量不完整；{int(blocker_decomposition.get('unavailable_feature_incomplete_and_score_blocked', 0))} 条评分向量不完整且同时有 score-only 阻断；{int(blocker_decomposition.get('unavailable_hard_fail', 0))} 条 hard fail。评分向量不完整包含 FS02-M02 人工目标方向缺失，不等于 Pose 测量失败。若相关人工真值与受控策略门禁未来都通过，完整向量的 {int(blocker_decomposition.get('unavailable_complete_features_score_only', 0))} 条最多恢复为 `calibration_required`，不会直接生成等级。M43 机器报告为 `reports/scoring-blocker-audit-halpe256-full-m43.json`。\n\n"
        f"M43 类型化原因逐条反查 raw gate：跳点 {int(blocker_reason_counts.get('keypoint_jump_diagnostic_unverified', 0))}、左右交换 {int(blocker_reason_counts.get('left_right_assignment_unverified', 0))}、目标方向 {int(blocker_reason_counts.get('tactical_target_direction_required', 0))}、身份连续性 {int(blocker_reason_counts.get('event_identity_continuity_unverified', 0))} 条，均无多报或错归因。历史 M31 视频工作清单有 {truth_priority_scope['work_items']} 项，但早于 M42 scoring vector，不再作为当前计数真源。\n\n"
        f"M53 当前视频行动清单覆盖 {int(truth_action_scope['actionable_unavailable_indicator_instances'])}/{int(truth_action_scope['unavailable_indicator_instances'])} 条 unavailable，收敛为 {int(truth_action_scope['work_items'])} 个 event×真值要求、{int(truth_action_scope['instance_action_links'])} 条实例×动作关联；Pose 诊断/关键点特征/目标方向/事件阶段分别 {int(truth_action_scope['by_review_type'].get('pose_diagnostic_truth', 0))}/{int(truth_action_scope['by_review_type'].get('manual_keypoint_feature_truth', 0))}/{int(truth_action_scope['by_review_type'].get('scoring_reference_context', 0))}/{int(truth_action_scope['by_review_type'].get('manual_event_or_semantic_truth', 0))} 项，Pose 队列缺失关联 0。入口为 `reports/scoring-truth-action-worklist-halpe256-full-m53/index.html`；它不自动解除门禁或产生等级。\n\n"
        f"M45 行动证据状态机对 M44 的每个 work_item 与受影响指标实例逐项重算：当前证据满足/仅复核未裁决/仍需标注为 {int(truth_action_readiness_scope['by_status'].get('evidence_satisfied', 0))}/{int(truth_action_readiness_scope['by_status'].get('review_in_progress_not_adjudicated', 0))}/{int(truth_action_readiness_scope['by_status'].get('annotation_required', 0))}，全部关联动作证据满足的指标实例为 {int(truth_action_readiness_scope['indicator_instances_by_status'].get('evidence_satisfied', 0))}/{int(truth_action_readiness_scope['indicator_instances'])}。它重新计算事件匹配、特征误差与 Pose 全时间线 truth CSV，拒绝自报状态；完成任务不自动改 score/quality，也不生成 grade/threshold。机器报告为 `reports/scoring-truth-action-readiness-halpe256-full-m45.json`。\n\n"
        f"M46 将这 {int(truth_evidence_plan_scope['input_work_items'])} 个 work item 的共享依赖折叠为 {int(truth_evidence_plan_scope['evidence_units'])} 个证据获取单元（当前满足 {int(truth_evidence_plan_scope['by_status'].get('evidence_satisfied', 0))}）：4 个完整时间线 Pose 单元优先，其余精确绑定事件边界、阶段、密集关键点、特征真值、侧别语义和目标方向。入口为 `reports/scoring-truth-evidence-plan-halpe256-full-m46/index.html`；优先级按影响实例数排序，不是准确率排名，也不会自动解除门禁。\n\n"
        f"M47 最新不可变刷新 `{truth_refresh_scope['refresh_id']}` 把 truth compile、事件/特征误差、Pose 诊断误差、M45 与 M46 放在同一 hash-bound bundle；人工事件/关键点帧/语义/教练标签为 {int(truth_refresh_scope['counts']['manual_events'])}/{int(truth_refresh_scope['counts']['manual_keypoint_frames'])}/{int(truth_refresh_scope['counts']['manual_semantics'])}/{int(truth_refresh_scope['counts']['coach_labels'])}，work item 与证据单元满足数为 {int(truth_refresh_scope['counts']['satisfied_work_items'])}/{int(truth_refresh_scope['counts']['work_items'])}、{int(truth_refresh_scope['counts']['satisfied_evidence_units'])}/{int(truth_refresh_scope['counts']['evidence_units'])}。只有全链验证通过才原子更新 `reports/scoring-truth-refresh/latest.json`；该刷新不运行 A～E 评分或成熟度晋级。入口为 `{truth_refresh_index_href}`。\n\n"
        f"M48 标定交接 `{truth_calibration_handoff_scope['handoff_id']}` 从 M47 冻结的人工边界重算 manual-event features，并以精确 event_id 编译 13 项 prepared dataset：人工特征 {int(truth_calibration_handoff_scope['counts']['manual_feature_records'])}、samples {int(truth_calibration_handoff_scope['counts']['calibration_samples'])}、fit-ready {int(truth_calibration_handoff_scope['counts']['fit_ready_indicators'])}/{int(truth_calibration_handoff_scope['counts']['indicators'])}。当前 13/13 prepared 文件结构有效但全部被拟合前门禁拒绝；未调用候选事件检测器或拟合器，也未生成候选资产、阈值、模型、等级或 F3/F4。入口为 `{truth_calibration_handoff_repo_path}`。\n\n"
        f"M49 三视频标定组合 `{truth_calibration_portfolio_scope['portfolio_id']}` 覆盖真值包 {int(truth_calibration_portfolio_scope['counts']['measurement_source_videos'])}/{int(truth_calibration_portfolio_scope['counts']['truth_manifest_videos'])} 段视频、{int(truth_calibration_portfolio_scope['counts']['measurement_source_frames'])} 帧已有 RTMPose-M/Halpe26 结果，并统一使用当前主球员时序。每段只接受人工事件边界重算特征，再汇总为同一 13 项 calibration dataset；当前人工事件/特征/samples 为 {int(truth_calibration_portfolio_scope['counts']['accepted_manual_events'])}/{int(truth_calibration_portfolio_scope['counts']['manual_feature_records'])}/{int(truth_calibration_portfolio_scope['counts']['calibration_samples'])}，fit-ready {int(truth_calibration_portfolio_scope['counts']['fit_ready_indicators'])}/{int(truth_calibration_portfolio_scope['counts']['indicators'])}。本轮复用既有 Pose 帧、未运行 GPU/候选事件检测/拟合，也未生成阈值、模型、等级或 F3/F4。入口为 `{truth_calibration_portfolio_repo_path}`。\n\n"
        f"M63 三视频计算覆盖 `{multivideo_calculation_coverage_scope['coverage_id']}` 对 {int(multivideo_calculation_coverage_scope['counts']['videos'])} 段、{int(multivideo_calculation_coverage_scope['counts']['frames'])} 帧既有 Pose 运行当前 registry 的候选事件与 13 项特征合同：{int(multivideo_calculation_coverage_scope['counts']['candidate_events'])} 个候选事件、{int(multivideo_calculation_coverage_scope['counts']['indicator_event_instances'])} 条指标实例中，{int(multivideo_calculation_coverage_scope['counts']['measured_feature_vectors'])} 条特征 measured、{int(multivideo_calculation_coverage_scope['counts']['unavailable_feature_vectors'])} 条 unavailable；13/13 指标在每段视频均至少有一条完整向量。评分状态仍为 {int(multivideo_calculation_coverage_scope['counts']['score_status_counts'].get('calibration_required', 0))} calibration_required / {int(multivideo_calculation_coverage_scope['counts']['score_status_counts'].get('unavailable', 0))} unavailable，grade=0、threshold=0。候选事件未做人工真值评测，计算覆盖不是准确率。入口为 `{multivideo_calculation_coverage_repo_path}`。\n\n"
        f"M65 就绪拆解 `{multivideo_readiness_scope['audit_id']}` 将同一 {readiness_total} 条实例按最先阻断层互斥重放：measurement hard fail {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['measurement_hard_fail'])}、普通测量向量不完整 {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['measurement_vector_incomplete'])}、评分上下文不完整 {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['scoring_context_incomplete'])}、评分证据未验证 {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['scoring_evidence_blocked'])}、仅缺标定/独立测试 {int(multivideo_readiness_scope['counts']['exclusive_decomposition']['calibration_only_missing'])}。原始 Pose 向量完整 {int(multivideo_readiness_scope['counts']['raw_measurement_vector_complete'])}，通过测量门禁 {int(multivideo_readiness_scope['counts']['operational_feature_measured'])}，完整评分向量且通过评分证据门禁 {int(multivideo_readiness_scope['counts']['ready_for_calibration_application'])}。该审计不补 0、不解除门禁、不生成等级或阈值，入口为 `{multivideo_readiness_repo_path}`。\n\n"
        f"M65 在上述三视频拆解上增加类型化原因反查和单一阻断反事实：`keypoint_jump_candidates_present` 参与 {int(multivideo_readiness_scope['scoring_block_recovery_priority'][0]['indicator_instance_count'])} 条完整评分向量，并且是 {int(multivideo_readiness_scope['scoring_block_recovery_priority'][0]['sole_block_to_calibration_required_indicator_instance_count'])} 条的唯一活动阻断；全部类型化原因均有原始 scoring block flag 支撑。系统为三段完整视频生成 1,516 个去重 Pose 诊断复核任务和空白全时间线 truth pack，当前 coverage=0、precision/recall/F1=null、quality policy 未改变。\n\n"
        f"M78 把该映射正式收敛为 `{m78_blocker_taxonomy_scope['taxonomy_version']}`：每个 scoring block flag 只路由到对应的 typed reason、人工真值要求和 blocker group，跳点/左右交换/目标方向不会再被误写成身份连续性。无 GPU 重放 {m78_blocker_taxonomy_scope['video_count']} 段、{m78_blocker_taxonomy_scope['indicator_event_instance_count']} 个指标实例后，全部非 confidence 载荷和 score status/grade/reason/feedback/quality gate 逐条一致；仅 {m78_blocker_taxonomy_scope['confidence_difference_count']} 个 confidence 叶值有最大 {m78_blocker_taxonomy_scope['max_abs_confidence_delta']:.6f} 的序列化末位差。它不改 gate、不解除 unavailable、不生成 A～E 或准确率结论。机器审计为 `reports/measurement-recovery-m78/blocker-taxonomy-audit.json`。\n\n"
        f"M79 将跳点/左右点交换的 {m79_pose_diagnostic_truth_scope['candidate_task_count']} 个候选冻结为真正的两阶段视频真值流程：{m79_pose_diagnostic_truth_scope['clip_count']} 个候选窗口分别编码为独立 H.264 MP4，覆盖 {m79_pose_diagnostic_truth_scope['source_frame_observations']} 帧与 {m79_pose_diagnostic_truth_scope['unique_affected_indicator_instances']} 个去重指标实例。两名不同 annotator 在不显示候选帧、关节或骨架的页面独立导出原始 CSV，第三名独立 reviewer 才在隔离页查看密封候选并裁决。当前 coverage/positive/adjudication 为 {m79_pose_diagnostic_truth_scope['coverage_annotations']}/{m79_pose_diagnostic_truth_scope['positive_annotations']}/{m79_pose_diagnostic_truth_scope['candidate_adjudications']}，所以 candidate precision=null；候选窗口不具备完整时间线 recall/F1，且不会自动改 gate、grade、threshold 或 F3/F4。入口为 `data/annotations/pose-diagnostic-clip-truth-m79-v1.1/review.html`。\n\n"
        f"M81 把当前 {m81_scoring_observer_scope['video_count']} 段完整视频、{m81_scoring_observer_scope['frame_count']} 帧 Halpe26、{m81_scoring_observer_scope['event_count']} 个候选事件和 {m81_scoring_observer_scope['indicator_instance_count']} 条指标实例放入一个同步播放器：骨架、事件阶段、13 项指标、特征值、证据帧与质量门禁随视频联动。特征状态为 {m81_scoring_observer_scope['measured_count']} measured/{m81_scoring_observer_scope['feature_unavailable_count']} unavailable，评分状态为 {m81_scoring_observer_scope['calibration_required_count']} calibration_required/{m81_scoring_observer_scope['score_unavailable_count']} unavailable；grade=0、threshold=0。入口为 `reports/scoring-visual-observer-m81/index.html`。\n\n"
        f"M63 平滑反事实覆盖审计绑定 registry `{smoothing_counterfactual_coverage['registry']['registry_version']}` 与三段 M63 features SHA：{int(smoothing_coverage_counts['valid_numeric_record_count'])} 条有效 Pose 特征记录中 {int(smoothing_coverage_counts['computed_counterfactual_count'])} 条可由序列化 raw evidence 无歧义重放无平滑对照（{float(smoothing_coverage_counts['counterfactual_coverage']) * 100:.2f}%），51 个 Pose required feature 中 {int(smoothing_coverage_counts['features_fully_reconstructable_count'])} 个全覆盖、{int(smoothing_coverage_counts['features_partially_reconstructable_count'])} 个部分覆盖、{int(smoothing_coverage_counts['features_not_reconstructable_count'])} 个完全未覆盖。FS02-M05 的启动脚速度下降/第一步位移/髋方向一致性/启动脚减速到髋方向建立时差/步后站距分别为 {int(smoothing_coverage_by_feature['launch_foot_speed_drop_body_s']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_speed_drop_body_s']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['first_step_displacement_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['first_step_displacement_body']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['post_step_hip_direction_consistency']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['post_step_hip_direction_consistency']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['launch_foot_slowdown_to_post_hip_direction_ms']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['launch_foot_slowdown_to_post_hip_direction_ms']['valid_numeric_record_count'])}、{int(smoothing_coverage_by_feature['post_step_stance_width_body']['computed_counterfactual_count'])}/{int(smoothing_coverage_by_feature['post_step_stance_width_body']['valid_numeric_record_count'])} 可重放。前两项各一条 raw 轨迹无法形成正向启动侧，继续 unavailable；事件阶段固定为 production <code>first_step_slowdown_proxy_ms</code>，不随 raw 重放漂移。该覆盖不是 MAE/准确率，也不是可加和误差分解；人工真值、F2、grade=0 与 threshold=0 均不变。机器报告为 `reports/smoothing-counterfactual-coverage-m63.json`。\n\n"
        f"M64 误差预算就绪矩阵把当前 registry、M63 三视频计算覆盖、平滑反事实和三段当前真值评测逐源重放：{int(f2_error_budget_counts['indicators_with_complete_smoothing_counterfactual'])}/13 项的全部 Pose 特征平滑证据完整，{int(f2_error_budget_counts['indicators_with_partial_smoothing_counterfactual'])}/13 项仍有少量 raw partial；人工事件/关键点/语义均为 0，因此 Event F1/IoU/Boundary MAE、特征 MAE/P95/Bias、缺失值误差影响和外部等级间距判断仍未评测，F2→F3 ready 为 0/13。该矩阵不把平滑敏感性或测量覆盖当准确率，不生成 grade/threshold，机器报告为 `reports/f2-error-budget-readiness-m64.json`。\n\n"
        f"剩余 {feature_gap_scope['counts']['feature_unavailable_indicator_count']} 条 feature unavailable 已追到 {feature_gap_scope['counts']['affected_event_count']} 个候选事件、{feature_gap_scope['counts']['unique_invalid_event_feature_count']} 个唯一 event×feature 缺口；其中 134 个是事件内有效 Pose 覆盖不足。固定相同候选边界时，Halpe384 只恢复当前缺口中的 2 条并另丢失 6 条，WholeBody133 恢复 0 条并另丢失 34 条，因此继续禁止逐事件/逐特征跨模型拼接。机器报告为 `reports/feature-observation-gap-audit-halpe256-full.json`。\n\n"
        f"小 ROI 实验保持同一视频、检测、主球员时间线、模型权重和 102 个候选事件，只把最小 ROI 从 {small_roi_scope['settings']['baseline_min_roi_size_px']}px 改为 {small_roi_scope['settings']['experimental_min_roi_size_px']}px：{small_roi_scope['inference_counts']['pose_output_recovered']}/{small_roi_scope['inference_counts']['inference_attempted']} 个目标帧得到 Pose，固定边界特征完整实例从 {small_roi_scope['transitions'].get('measured_to_measured', 0)}/{small_roi_scope['indicator_instance_count']} 增至 {small_roi_scope['transitions'].get('measured_to_measured', 0) + small_roi_scope['recovered_indicator_instance_count']}/{small_roi_scope['indicator_instance_count']}，恢复 {small_roi_scope['recovered_indicator_instance_count']} 条、回归 {small_roi_scope['regressed_indicator_instance_count']} 条。这只证明实验可观测性，不证明远场关键点准确；生产默认仍为 32px，A～E 和成熟度未改变。机器报告与动态视频位于 `reports/experiments/small-roi-pose-recovery-halpe256-full-v1/`。\n\n"
        f"M66 将同模型小 ROI 实验扩展到当前三视频机器真源，并把 Pose 特征向量状态与原事件/身份测量门禁后的 operational 状态分开。当前生产为 {multivideo_recovery_scope['baseline']['operational_measured']}/{multivideo_recovery_scope['baseline']['indicator_instances']} measured、{multivideo_recovery_scope['baseline']['operational_unavailable']} unavailable；149 个合格目标帧全部产生实验 Pose，恢复 {multivideo_recovery_scope['experiment']['feature_vector_recovered']} 条特征向量，但保留原质量门禁后只恢复 {multivideo_recovery_scope['experiment']['operational_measurement_recovered']} 条指标实例，实验投影为 {multivideo_recovery_scope['experimental_projection']['operational_measured']}/{multivideo_recovery_scope['baseline']['indicator_instances']} measured、{multivideo_recovery_scope['experimental_projection']['operational_unavailable']} unavailable，回归 0。生产 32px 默认、18 条 hard fail、grade=0、threshold=0 均不变；149/149 是可观测性，不是关键点准确率。汇总与两段动态 A/B 位于 `reports/measurement-recovery-m66/`。\n\n"
        f"M67 在同一 RTMPose-M Halpe26 256×192 权重、检测、Track、事件与质量门禁上评测 0.15→0.30 ROI margin。410 个目标帧全部产生 Pose，特征向量恢复 {m67_recovery_scope['m67_uniform_roi_margin']['feature_vector_impact']['recovered']} 条/回归 {m67_recovery_scope['m67_uniform_roi_margin']['feature_vector_impact']['regressed']} 条，门禁后恢复 {m67_recovery_scope['m67_uniform_roi_margin']['operational_impact']['recovered']} 条/回归 {m67_recovery_scope['m67_uniform_roi_margin']['operational_impact']['regressed']} 条；有效关键点计数增加/减少/不变为 {m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['increased']}/{m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['decreased']}/{m67_recovery_scope['m67_uniform_roi_margin']['valid_keypoint_count_transition_counts']['unchanged']} 帧。与 M66 的互斥目标集合合入完整帧后实际重算，特征完整实例为 {m67_recovery_scope['composed_experimental_projection']['feature_vector_complete']}/{m67_recovery_scope['baseline']['indicator_instances']}，门禁后 measured 为 {m67_recovery_scope['composed_experimental_projection']['operational_measured']}/{m67_recovery_scope['baseline']['indicator_instances']}，净恢复 29 条，hard fail 仍为 {m67_recovery_scope['composed_experimental_projection']['measurement_hard_fail']}。由于真实发生 1 条回归且人工关键点/独立视频发布测试仍缺，生产保持 0.15 margin、32px 最小 ROI、无自动 fallback；grade=0、threshold=0、F2 不晋级。汇总为 `reports/measurement-recovery-m67/summary/report.json`。\n\n"
        f"M68 将 margin 候选限制为注册表所需 {len(m68_router_scope['scope']['required_joints'])} 个肩/髋/膝/踝关节的有效集严格超集：候选不得丢失任何当前有效评分关节，选择过程不读取特征值、事件结果、grade 或 threshold。410 个 margin 候选帧只选中 {m68_router_scope['router']['selected_margin_frames']} 个，拒绝 {m68_router_scope['router']['rejected_margin_frames']} 个；与 M66 合并后的真实路由帧为 {m68_router_scope['router']['total_composed_experimental_frames']} 个。固定边界重算后，特征完整实例由 {m68_router_scope['baseline']['feature_vector_complete']}/{m68_router_scope['scope']['indicator_instances']} 提升为 {m68_router_scope['routed_projection']['feature_vector_complete']}/{m68_router_scope['scope']['indicator_instances']}（恢复 {m68_router_scope['routed_projection']['feature_vector_recovered']}、回归 {m68_router_scope['routed_projection']['feature_vector_regressed']}），门禁后 measured 由 {m68_router_scope['baseline']['operational_measured']} 提升为 {m68_router_scope['routed_projection']['operational_measured']}（恢复 {m68_router_scope['routed_projection']['operational_recovered']}、回归 {m68_router_scope['routed_projection']['operational_regressed']}）。它在当前三视频保留 M67 的净 operational 增益并消除当前集合回归，但这不是人工关键点准确率或独立发布验证；生产配置、F2、grade=0、threshold=0 均不变。汇总为 `reports/measurement-recovery-m68/summary/report.json`。\n\n"
        f"M69 在 M68 后 {m69_high_resolution_scope['scope']['input_residual_indicator_instances']} 条非 hard-fail 特征残差、{m69_high_resolution_scope['scope']['input_residual_events']} 个事件内，仅对 {m69_high_resolution_scope['inference']['target_frame_count']} 个目标帧运行 RTMPose-M Halpe26 384×288/0.30 ROI 候选；{m69_high_resolution_scope['inference']['pose_output_produced']} 帧得到 Pose，但 required-joint 严格超集门禁只选中 {m69_high_resolution_scope['inference']['selected_frame_count']} 帧。固定边界重算后特征完整实例由 {m69_high_resolution_scope['baseline']['feature_vector_complete']}/{m69_high_resolution_scope['scope']['indicator_instances']} 提升为 {m69_high_resolution_scope['experimental_projection']['feature_vector_complete']}/{m69_high_resolution_scope['scope']['indicator_instances']}，门禁后 measured 由 {m69_high_resolution_scope['baseline']['operational_measured']} 提升为 {m69_high_resolution_scope['experimental_projection']['operational_measured']}，均恢复 6、回归 0；非 hard-fail 残差由 {m69_high_resolution_scope['baseline']['non_hard_fail_feature_incomplete']} 降到 {m69_high_resolution_scope['experimental_projection']['non_hard_fail_feature_incomplete']}。候选同时改变权重/输入分辨率与裁剪上下文，不能单因子归因；没有人工关键点误差与独立视频测试，因此不改生产、F2、grade=0 或 threshold=0。汇总为 `reports/measurement-recovery-m69/summary/report.json`。\n\n"
        f"M70 用 M68 每帧真实裁剪来源重放同一 {m70_pose_profile_scope['scope']['target_event_frame_count']} 个残差帧，将 384×288 Pose profile 与 ROI 上下文分离。只换 profile 恢复 {m70_pose_profile_scope['strategies']['context_matched_profile_only']['operational_recovered']} 个 operational 实例；M69 统一上下文恢复 {m70_pose_profile_scope['strategies']['uniform_context_m69']['operational_recovered']} 个。直接多候选择优会丢失 {m70_pose_profile_scope['strategies']['naive_multicandidate']['lost_uniform_operational_recovery_count']} 个 M69 已恢复实例，因此拒绝；以 M69 为锚、只接受必需关节严格超集的扩展选择 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['extension_selected_frame_count']} 帧，特征完整实例达到 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['feature_vector_complete']}/{m70_pose_profile_scope['scope']['indicator_instances']}，门禁后 measured 达到 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['operational_measured']}/{m70_pose_profile_scope['scope']['indicator_instances']}，相对 M68 恢复 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['operational_recovered']}、回归 0，相对 M69 额外恢复 {m70_pose_profile_scope['strategies']['m69_anchored_extension']['additional_operational_recovery_over_m69']}。这仍不是准确率或生产发布证据；F2、grade=0、threshold=0 不变。汇总与动态视频位于 `reports/measurement-recovery-m70/`。\n\n"
        f"M71 将官方 RTMPose-L Halpe26 384×288 仅运行在上述 {m71_larger_pose_scope['scope']['target_frame_count']} 个残差帧，并从 M70 出发仅接受 required-joint 有效集严格超集。三视频共选择 {m71_larger_pose_scope['m71_projection']['selected_frame_count']} 帧；相对 M68 特征完整实例达到 {m71_larger_pose_scope['m71_projection']['feature_vector_complete']}/{m71_larger_pose_scope['scope']['indicator_instances']}（恢复 {m71_larger_pose_scope['m71_projection']['feature_recovered_from_m68']}、回归 0），门禁后 measured 达到 {m71_larger_pose_scope['m71_projection']['operational_measured']}/{m71_larger_pose_scope['scope']['indicator_instances']}（恢复 {m71_larger_pose_scope['m71_projection']['operational_recovered_from_m68']}、回归 0）；相对 M70 再新增 {m71_larger_pose_scope['m71_projection']['additional_feature_recovery_over_m70']} 个完整特征实例和 {m71_larger_pose_scope['m71_projection']['additional_operational_recovery_over_m70']} 个 operational 实例，丢失 M70 恢复为 0。该结果证明少量残差帧的可观测性增益，不证明关键点准确率或模型已可生产替换；F2、grade=0、threshold=0 不变。汇总与动态视频位于 `reports/measurement-recovery-m71/`。\n\n"
        f"M72 不再替换整副 Pose，只把同一 Halpe26 拓扑中 M71 无效、RTMPose-L 有效的评分相关点加到 M71，并逐点证明所有 baseline-valid 坐标未覆盖。46 帧共新增 {m72_additive_fusion_scope['m72_projection']['added_valid_joint_observation_count']} 个有效关节观测，但固定边界只新增恢复 2 个完整特征/operational 实例，最终为 {m72_additive_fusion_scope['m72_projection']['feature_vector_complete']}/{m72_additive_fusion_scope['scope']['indicator_instances']} feature complete、{m72_additive_fusion_scope['m72_projection']['operational_measured']}/{m72_additive_fusion_scope['scope']['indicator_instances']} operational measured，回归与 M71 恢复丢失均为 0。剩余 {m72_additive_fusion_scope['remaining_residual']['indicator_instance_count']} 个非 hard-fail 缺口分为事件观测覆盖不足 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['event_observation_coverage_below_feature_contract']}、视频起始边界截断 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['video_start_boundary_censored_observation']}、所需 FS09 Pose 阶段未观察到 {m72_additive_fusion_scope['remaining_residual']['classification_counts']['required_pose_phase_proxy_not_observed']}。不能靠降低有效率门槛、补 0 或伪造阶段消除；融合也必须经人工逐点误差与独立视频验证。生产、F2、grade=0、threshold=0 不变，机器汇总在 `reports/measurement-recovery-m72/summary/report.json`。\n\n"
        f"M73 进一步使用官方 WholeBody133 做跨拓扑显式同名点补充，仍逐点保持 M72 的全部有效 Halpe26 观测。258 个冻结目标帧中 254 个产生候选 Pose，49 帧新增 {m73_wholebody_mapped_scope['m73_projection']['added_valid_joint_observation_count']} 个有效点，但新增 feature complete 与 operational measured 都是 0；最终仍为 {m73_wholebody_mapped_scope['m73_projection']['feature_vector_complete']}/{m73_wholebody_mapped_scope['scope']['indicator_instances']} 和 {m73_wholebody_mapped_scope['m73_projection']['operational_measured']}/{m73_wholebody_mapped_scope['scope']['indicator_instances']}。这个负结果是有价值的：更密拓扑没有解决剩余事件内连续覆盖、视频起点截断或 FS09 阶段代理缺失，不能继续把“点更多”当成评分能力。机器汇总在 `reports/measurement-recovery-m73/summary/report.json`；生产、F2、grade=0、threshold=0 不变。\n\n"
        f"M74 对 M73 剩余 13 个实例做事件内 timestamp 缺口审计：只允许同一事件内两个真实有效端点夹住、端点跨度≤160 ms 的线性插值，不允许首尾外推、跨事件借帧、补 0 或生成 phase。去重后有 {m74_event_gap_scope['counterfactual_summary']['unique_event_joint_observation_count']} 个 event×joint×frame 候选点，反事实可使 {m74_event_gap_scope['counterfactual_summary']['counterfactual_recovered_indicator_instance_count']}/13 个特征向量完整，仍有 {m74_event_gap_scope['counterfactual_summary']['counterfactual_remaining_indicator_instance_count']}/13 不完整。由于这些点不是模型观测且尚无人工关键点真值，production recovered 固定为 0，所有原实例保持 unavailable；机器逐点证据为 `reports/measurement-recovery-m74/event-bounded-gap-audit-v1/report.json`。\n\n"
        f"M75 已把上述去重候选冻结为 {m75_event_gap_truth_scope['joint_task_count']} 个关节点任务，覆盖 {m75_event_gap_truth_scope['video_count']} 段视频、{m75_event_gap_truth_scope['frame_count']} 帧；插值坐标保存在密封 JSONL 中，不进入浏览器 bootstrap。每点需要两名标注者独立作答及未参与标注的 reviewer 裁决。当前人工标注 {m75_event_gap_truth_scope['raw_annotations']}、接受裁决 {m75_event_gap_truth_scope['accepted_adjudications']}，所以 MAE/P95/Bias/valid rate 与分视角结果均为 null，生产插值仍关闭。入口为 `data/annotations/event-bounded-pose-gap-truth-m75-v1/review.html`，空白误差报告为 `reports/measurement-recovery-m75/event-gap-keypoint-error-empty.json`。\n\n"
        f"M76 已把该裁决合同接入真值条件特征重放：只替换 M75 缺口坐标，保留非缺口 Pose、候选事件边界和 Track。固定范围为 {m76_event_gap_feature_truth_scope['indicator_instances_with_interpolation_points']} 个指标实例、{m76_event_gap_feature_truth_scope['required_feature_records']} 条 required-feature 记录和 {m76_event_gap_feature_truth_scope['unique_required_features']} 个唯一特征；{m76_event_gap_feature_truth_scope['phase_only_residual_indicator_instances_excluded']} 个 FS09 phase-only 残差不使用插值伪造。当前人工裁决为 0，因此特征 MAE/P95/Bias/valid rate 及分特征/分指标结果均为 null，生产插值、A～E、threshold 和 F3/F4 晋级全部关闭。机器报告为 `reports/measurement-recovery-m76/event-gap-feature-error-empty.json`。\n\n"
        f"M77 已把 M76 显式排除的 {m77_fs09_phase_truth_scope['task_count']} 个 FS09-M05 phase-only 残差变成无骨架视频盲标任务。这两段事件的髋/踝 Pose 覆盖完整，但 `{m77_fs09_phase_truth_scope['target_unavailable_feature']}` 找不到双踝同时低运动代理起点。两段无叠加 H.264 短片替代长文件随机 seek，局部时间按绑定 offset 写回原视频绝对 timestamp；bootstrap 不含候选 event_id/边界/阶段。每个任务需双人独立标注 FS09 存在性、边界和 {m77_fs09_phase_truth_scope['phase_count']} 个可见阶段，再由第三人裁决。当前人工标注 {m77_fs09_phase_truth_scope['raw_annotations']}、接受裁决 {m77_fs09_phase_truth_scope['accepted_adjudications']}，所以 Event IoU/Boundary MAE、phase MAE 和边界条件特征差均为 null。人工稳定控制不会被当成双支撑/接触/受力真值；如果 Pose 代理仍缺失，目标特征继续 unavailable。入口为 `data/annotations/fs09-phase-truth-m77-v1/review.html`，空白报告为 `reports/measurement-recovery-m77/fs09-phase-truth-empty.json`。\n\n"
        f"M35 已把上述 {small_roi_truth_scope['frames']} 个恢复帧冻结为 {small_roi_truth_scope['tasks']} 个关节点盲标任务：两名独立标注者分别作答，第三名 reviewer 裁决，UI 不显示或预填模型坐标。当前人工标注 {small_roi_truth_scope['raw_annotations']}、裁决 {small_roi_truth_scope['accepted_adjudications']}，因此像素/归一化 MAE、P95、Bias 和 PCK 均不计算，生产 32px 路由不变。入口为 `data/annotations/small-roi-keypoint-truth-halpe256-v1/review.html`，空白误差报告为 `reports/small-roi-keypoint-error-halpe256-v1-empty.json`。\n\n"
        f"M36 可计算性证书逐项验证了 registry required feature 的值、单位、版本、置信度和证据帧：13/13 个指标均有真实 measured 实例，每项 {residual_computability_scope['min_measured_per_indicator']}～{residual_computability_scope['max_measured_per_indicator']} 条，总计 {residual_computability_scope['counts']['measured_indicator_event_instance_count']}/{residual_computability_scope['counts']['indicator_event_instance_count']}。剩余 {residual_computability_scope['counts']['unavailable_indicator_event_instance_count']} 条由画面边界裁切 {residual_computability_scope['classifications'].get('primary_bbox_clipped_at_image_boundary', 0)}、source Track 过渡 {residual_computability_scope['classifications'].get('primary_source_track_transition', 0)}、视频起始边界 {residual_computability_scope['classifications'].get('video_start_boundary_censored', 0)} 构成，继续 fail closed；机器证书为 `reports/residual-indicator-computability-small-roi-v1.json`，动态证据为 `reports/pose-scoring-ab/residual-computability-evidence-browser.mp4`。这不代表事件/特征准确或 A～E 可评分。\n\n"
        f"M37 已把正常上传的 Pose 默认从 YOLO COCO-17 切换为 `rtmpose-m-halpe26-online`，同时保留 `yolo-baseline` 显式回滚。未设置环境 preset、也未传 CLI override 的真实 GPU Worker 烟测处理 {default_pose_routing_scope['smoke']['processed_frames']} 帧，{default_pose_routing_scope['smoke']['effective_processed_fps']:.3f} FPS，bundle passed；其模型权重与全片证据 SHA 一致。保持生产 32px ROI 时，全片 {default_pose_routing_scope['full']['measured_indicator_event_instance_count']}/{default_pose_routing_scope['full']['indicator_event_instance_count']} 条特征实例 measured，13/13 指标各有 {default_pose_routing_scope['min_production_measured_per_indicator']}～{default_pose_routing_scope['max_production_measured_per_indicator']} 条真实可测实例。默认替换只推进 F2 测量入口，不推进 F3/F4，不生成 grade/threshold；机器审计为 `reports/default-pose-routing-audit-m37.json`。\n\n"
        f"M38 的 `indicator-calculation-readiness-v1.0.0` 已迁移到当前 M42 产物：按 registry 精确列出每项 measurement event/feature、候选实例、measured/unavailable、证据帧、特征失败、measurement hard flag、独立 scoring-only flag 和不放宽门禁的补救动作。M42 真实 GPU Worker 为 {default_smoke_calculation_scope['summary']['indicator_with_measured_candidate_count']}/{default_smoke_calculation_scope['summary']['registry_indicator_count']} 项至少有 measured 候选、{default_smoke_calculation_scope['summary']['measured_indicator_event_instance_count']}/{default_smoke_calculation_scope['summary']['indicator_event_instance_count']} 条 measured；97 秒全片为 13/13、{full_calculation_scope['summary']['measured_indicator_event_instance_count']}/{full_calculation_scope['summary']['indicator_event_instance_count']}。不足不会显示成 0 分，报告位于 `runs/rtmpose-m-halpe26-default-m42-scoring-vector-smoke/calculation-readiness.json` 与 `reports/fs09-pose-wave-v2-m42/850cb0006b406c7176eeda8d711cd065/calculation-readiness.json`。\n\n"
        f"M39 的 `indicator-measurement-portfolio-v1.0.0` 已迁移到当前 M42：把多事件 JSONL 收敛为每项一个可直接消费的代表 F2 Pose 测量向量。选择规则不读取动作数值、不按运动表现挑最好动作、也不跨模型拼接。M42 真实 GPU Worker 为 {default_smoke_measurement_portfolio_scope['summary']['measured_indicator_count']}/{default_smoke_measurement_portfolio_scope['summary']['registry_indicator_count']} 项有代表测量；97 秒全片为 {full_measurement_portfolio_scope['summary']['measured_indicator_count']}/{full_measurement_portfolio_scope['summary']['registry_indicator_count']}，每项都含实际值、单位、版本、事件、Track 与证据帧，grade/threshold 仍为 0。\n\n"
        f"M40 新增 `scoring-cycle-measurement-v1.0.0`：FS01.initiation 精确连接 FS02.start，FS02 与 FS09 共享 peak_speed，并要求同一 Track/检测来源。全片 {full_cycle_measurement_scope['summary']['linked_cycle_count']} 个周期中 {full_cycle_measurement_scope['summary']['complete_cycle_count']} 个在同一次动作内 13/13 项全部 measured；代表周期为 13/13 特征完整、{full_cycle_measurement_scope['summary']['best_cycle_scoring_gate_passed_indicator_count']}/13 评分上下文通过。120 帧真实 Worker 有 {default_smoke_cycle_measurement_scope['summary']['linked_cycle_count']} 个周期，最佳 {default_smoke_cycle_measurement_scope['summary']['best_cycle_measured_indicator_count']}/13 特征完整、{default_smoke_cycle_measurement_scope['summary']['best_cycle_scoring_gate_passed_indicator_count']}/13 评分上下文通过，系统不跨动作补齐。选择只看完整度和观测/评分上下文质量，不看动作数值；grade/threshold 仍为 0。机器产物为 `runs/rtmpose-m-halpe26-default-m40-smoke/scoring-cycle-measurement.json` 与 `reports/scoring-cycle-measurement-halpe256-full.json`。\n\n"
        f"M41 新增 `scoring-reference-context-v1.0.0`：FS02-M02 可消费按视频 SHA 与事件边界绑定的教练/操作员目标方向，并计算 `target_direction_alignment_error_deg`；已接受记录要求观察者与独立复核者不同。当前真实全片工作清单含 {len(m41_reference_observations)} 个 FS02 事件，全部 pending、available=0，因此继续 unavailable；系统明确没有把运动方向当作战术目标，且 grade/threshold 均为 0。\n\n"
        f"M42 把 Pose 测量向量和完整评分/标定向量正式分离：FS02-M02 的 4 个 Pose 特征可以独立 measured，但评分向量还必须包含 `target_direction_alignment_error_deg`。当前 GPU 120 帧 13/13 指标有 measured 候选、36/39 指标实例测量完整，1 个同周期 13/13 测量完整；3 个 FS02 目标仍 pending，所以完整评分向量不允许丢弃目标特征。97 秒全片为 403/442 测量完整、{pose_wave['summary']['score_status_counts'].get('calibration_required', 0)} calibration_required / {pose_wave['summary']['score_status_counts'].get('unavailable', 0)} unavailable；grade=0、threshold=0。\n\n"
        f"M43 将 `target_direction_alignment_error_deg` 纳入人工特征误差评测：同一入口可用人工事件边界、人工校正 Pose 和独立复核的 image-plane target 计算 MAE/P95/Bias、分视角结果，以及 Pose/边界/平滑/缺失四类误差预算。当前人工 semantic 记录 {int(truth_promotion_guard.get('manual_semantic_record_count', 0))} 条、context truth complete={str(bool(truth_promotion_guard.get('context_feature_truth_complete'))).lower()}，所以相关误差仍为 null，F2 不晋级。机器报告为 `reports/truth-pack-empty-evaluation.json`。\n\n"
        f"- YOLO {baseline_indicator_count} 项指标 unavailable：50/1,182（4.23%）。\n"
        "- RTMPose-S：39/1,218（3.20%）。\n"
        "- RTMPose-M 256：28/1,218（2.30%）。\n"
        "- RTMPose-M 384：28/1,212（2.31%）。\n"
        "- YOLO 踝角可测率 0%；RTMPose-M 256 左右均 97.70%。\n"
        "- 足部聚焦片段的 RTMPose 主球员新增六点在 600/600 帧均通过当前点置信度门槛；这只是输出有效性，不是关键点准确率。\n"
        "- 全身 26 点片段中 600/600 帧的所有 26 点均通过当前点置信度门槛；它是当前 Halpe26 输出完整性，不代表评分标准拓扑完整或像素准确。\n"
        "- 所有有效结果仍为 calibration_required；原 298 项静态卡不在本轮最小闭环，不显示为数字 0。\n\n"
        "在复用完全相同的 591 个 YOLO 候选事件区间、只比较 Pose 对特征有效性的影响时，unavailable 分别为：YOLO 50/1,182（4.23%）、RTMPose-M 256 15/1,182（1.27%）、RTMPose-M 384 14/1,182（1.18%）。因此 384 档相对 YOLO 减少约 72%，但这仍是候选事件上的特征可测率，不是人工事件真值或评分准确率。\n\n"
        f"2026-08-13 已用三套实际服务预设走通 `JobDatabase -> PersistentVisionRunner -> process_one -> run_pipeline`：{deployment_markdown_summary}。三档都由当前 v2 注册表动态派生 {next(iter(deployment_smoke_scopes.values()))['indicator_count']} 项指标，grade=0、threshold=0、bundle passed；该烟测只证明部署和安全状态契约跑通，`accuracy_claim=false`。详见 `docs/POSE_DEPLOYMENT_PROFILES.md`。\n\n"
        "二维踝角只是 Halpe26 足部颗粒度诊断，不是三维踝背屈真值或 A～E 标准。\n",
        encoding="utf-8",
    )
    markdown_text = markdown.read_text(encoding="utf-8")
    legacy_m79_sentence = "第三名独立 reviewer 才在隔离页查看密封候选并裁决。"
    if legacy_m79_sentence not in markdown_text:
        raise ValueError("M80 markdown migration source sentence is missing")
    markdown_text = (
        markdown_text.replace(
            "M79 将跳点/左右点交换的",
            "M80 将跳点/左右点交换的",
            1,
        )
        .replace(
            legacy_m79_sentence,
            "第三名 reviewer 才能在隔离页查看密封候选和 "
            f"{m79_pose_diagnostic_truth_scope['pose_evidence_files']} 份逐帧模型骨架/目标关节轨迹；"
            "模型叠加明确不是真值。",
            1,
        )
        .replace(
            "data/annotations/pose-diagnostic-clip-truth-m79-v1.1/review.html",
            "data/annotations/pose-diagnostic-clip-truth-m80-v1.2.2/review.html",
            1,
        )
    )
    markdown.write_text(markdown_text, encoding="utf-8")
    print(json.dumps({"html": str(output), "markdown": str(markdown)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
