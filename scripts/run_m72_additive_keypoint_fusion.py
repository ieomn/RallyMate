#!/usr/bin/env python3
"""Fill M71-invalid Halpe26 joints from RTMPose-L without overwriting valid points."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
)
from rallymate_evaluation.pose_policy_composition import (
    add_missing_keypoints_from_same_topology_candidate_rows,
)

try:
    from scripts.run_m71_larger_pose_model_extension import (
        _bound,
        _identity_set,
        _rows,
        _sha256,
        _source,
        _write_rows,
        feature_vector_transitions,
        operational_transitions_with_preserved_source_gate,
        validate_larger_pose_model_extension_report,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_m71_larger_pose_model_extension import (
        _bound,
        _identity_set,
        _rows,
        _sha256,
        _source,
        _write_rows,
        feature_vector_transitions,
        operational_transitions_with_preserved_source_gate,
        validate_larger_pose_model_extension_report,
    )


REPORT_VERSION = "same-topology-additive-keypoint-fusion-v1.0.0"
FINE_FOOT_REFERENCE_JOINTS = frozenset(
    {
        "left_big_toe",
        "right_big_toe",
        "left_small_toe",
        "right_small_toe",
        "left_heel",
        "right_heel",
    }
)


def validate_additive_keypoint_fusion_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported M72 additive keypoint fusion report")
    if report.get("status") not in {
        "experimental_additive_keypoint_fusion_regression_free_requires_truth",
        "experimental_additive_keypoint_fusion_rejected",
    }:
        raise ValueError("unsafe M72 report status")
    audit = report.get("fusion_audit", {})
    for field, expected in {
        "baseline_valid_coordinates_overwritten": 0,
        "non_target_frames_preserved": True,
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
        "same_topology_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }.items():
        if audit.get(field) != expected:
            raise ValueError(f"unsafe M72 fusion audit field: {field}")
    if int(audit.get("changed_frame_count", -1)) + int(
        audit.get("unchanged_target_frame_count", -1)
    ) != int(audit.get("target_frame_count", -2)):
        raise ValueError("M72 fusion target accounting is inconsistent")
    comparison = report.get("comparison_to_m68", {})
    preservation = report.get("m71_preservation", {})
    regression_count = sum(
        int(comparison.get(kind, {}).get("regressed_indicator_instance_count", -1))
        for kind in ("feature_vector", "operational_measurement")
    )
    safe = (
        regression_count == 0
        and int(preservation.get("lost_m71_feature_recovery_count", -1)) == 0
        and int(preservation.get("lost_m71_operational_recovery_count", -1)) == 0
    )
    expected_status = (
        "experimental_additive_keypoint_fusion_regression_free_requires_truth"
        if safe
        else "experimental_additive_keypoint_fusion_rejected"
    )
    if report.get("status") != expected_status:
        raise ValueError("M72 report hides a regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("M72 experiment changed production")
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "feature_gate_modified",
        "measurement_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise ValueError(f"unsafe M72 claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m71-report", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; experiments are immutable")

    m71 = json.loads(args.m71_report.read_text(encoding="utf-8"))
    validate_larger_pose_model_extension_report(m71)
    baseline_path = _bound(m71["artifacts"]["routed_frames"], "M71 routed frames")
    candidate_path = _bound(
        m71["artifacts"]["candidate_frames"], "M71 RTMPose-L candidate frames"
    )
    current_path = _bound(m71["sources"]["m68_current_frames"], "M68 frames")
    timeline_path = _bound(m71["sources"]["primary_timeline"], "primary timeline")
    events_path = _bound(m71["sources"]["events"], "events")
    indicator_path = _bound(m71["sources"]["indicator_features"], "indicator features")
    registry_path = _bound(m71["sources"]["registry"], "feasibility registry")
    previous_comparison_path = _bound(
        m71["artifacts"]["fixed_boundary_comparison"], "M71 comparison"
    )

    baseline_rows = _rows(baseline_path)
    candidate_rows = _rows(candidate_path)
    timeline_rows = _rows(timeline_path)
    indicator_rows = _rows(indicator_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    required_joints = set(pose_required_joints_from_registry(registry))
    allowed_joints = required_joints | set(FINE_FOOT_REFERENCE_JOINTS)
    targets = {
        int(item["processed_index"])
        for item in m71["inference"]["frame_results"]
    }
    fused_rows, audit = add_missing_keypoints_from_same_topology_candidate_rows(
        baseline_rows=baseline_rows,
        candidate_rows=candidate_rows,
        timeline_rows=timeline_rows,
        target_processed_indexes=targets,
        allowed_joint_names=allowed_joints,
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    fused_path = args.output_directory / "frames.m71-plus-additive-keypoints.jsonl"
    _write_rows(fused_path, fused_rows)

    previous_comparison = json.loads(previous_comparison_path.read_text(encoding="utf-8"))
    current_profile = str(previous_comparison["model_order"][0])
    current_model_sha = str(
        previous_comparison["models"][current_profile]["model_sha256"]
    )
    fused_profile = "m72-m71-plus-rtmpose-l-missing-keypoints"
    comparison = evaluate_fixed_boundary_pose_ab_files(
        events_path=events_path,
        registry_path=registry_path,
        model_inputs={
            current_profile: {
                "frames_path": current_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": current_model_sha,
                "pose_backend": "rtmpose",
                "pose_profile": current_profile,
            },
            fused_profile: {
                "frames_path": fused_path,
                "primary_timeline_path": timeline_path,
                "model_sha256": str(m71["sources"]["candidate_model"]["sha256"]),
                "pose_backend": "rtmpose_same_topology_additive_observability_experiment",
                "pose_profile": fused_profile,
            },
        },
        boundary_source_kind="candidate",
        boundary_source_label="candidate_boundaries_not_truth_m72_additive_keypoint_fusion",
        source_id=f"{m71['video_id']}:m72-additive-keypoint-fusion",
    )
    comparison_path = args.output_directory / "fixed-boundary-comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    feature = feature_vector_transitions(comparison, current_profile, fused_profile)
    operational = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, fused_profile, indicator_rows
    )
    m71_feature = m71["comparison_to_m68"]["feature_vector"]
    m71_operational = m71["comparison_to_m68"]["operational_measurement"]
    feature_recovered = _identity_set(feature, "recovered_indicator_instances")
    operational_recovered = _identity_set(
        operational, "recovered_indicator_instances"
    )
    m71_feature_recovered = _identity_set(
        m71_feature, "recovered_indicator_instances"
    )
    m71_operational_recovered = _identity_set(
        m71_operational, "recovered_indicator_instances"
    )
    preservation = {
        "m71_feature_recovery_count": len(m71_feature_recovered),
        "fused_feature_recovery_count": len(feature_recovered),
        "additional_feature_recovery_count": len(
            feature_recovered - m71_feature_recovered
        ),
        "lost_m71_feature_recovery_count": len(
            m71_feature_recovered - feature_recovered
        ),
        "m71_operational_recovery_count": len(m71_operational_recovered),
        "fused_operational_recovery_count": len(operational_recovered),
        "additional_operational_recovery_count": len(
            operational_recovered - m71_operational_recovered
        ),
        "lost_m71_operational_recovery_count": len(
            m71_operational_recovered - operational_recovered
        ),
        "additional_operational_recoveries": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in sorted(
                operational_recovered - m71_operational_recovered
            )
        ],
    }
    safe = (
        feature["regressed_indicator_instance_count"] == 0
        and operational["regressed_indicator_instance_count"] == 0
        and preservation["lost_m71_feature_recovery_count"] == 0
        and preservation["lost_m71_operational_recovery_count"] == 0
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_additive_keypoint_fusion_regression_free_requires_truth"
            if safe
            else "experimental_additive_keypoint_fusion_rejected"
        ),
        "video_id": str(m71["video_id"]),
        "sources": {
            "m71_report": _source(args.m71_report),
            "m71_routed_frames": _source(baseline_path),
            "rtmpose_l_candidate_frames": _source(candidate_path),
            "m68_current_frames": _source(current_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "registry": _source(registry_path),
        },
        "artifacts": {
            "fused_frames": _source(fused_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "fusion_audit": audit,
        "comparison_to_m68": {
            "feature_vector": feature,
            "operational_measurement": operational,
        },
        "m71_preservation": preservation,
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": safe,
            "next_required_evidence": (
                "independently adjudicated per-joint error on fusion-changed frames and "
                "a preregistered independent-video release validation"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_additive_keypoint_fusion_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "changed_frames": audit["changed_frame_count"],
                "added_joint_observations": audit[
                    "added_valid_joint_observation_count"
                ],
                "additional_feature_recovered": preservation[
                    "additional_feature_recovery_count"
                ],
                "additional_operational_recovered": preservation[
                    "additional_operational_recovery_count"
                ],
                "lost_m71_operational_recovered": preservation[
                    "lost_m71_operational_recovery_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
