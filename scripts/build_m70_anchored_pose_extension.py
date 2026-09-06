#!/usr/bin/env python3
"""Extend M69 only where a context-matched Pose strictly dominates M69."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_evaluation.fixed_boundary_ab import evaluate_fixed_boundary_pose_ab_files
from rallymate_evaluation.pose_observability_residual import (
    operational_transitions_with_preserved_source_gate,
)
from rallymate_evaluation.pose_observability_router import (
    pose_required_joints_from_registry,
    select_required_joint_superset_frames,
)
from rallymate_evaluation.pose_policy_composition import compose_disjoint_pose_policy_rows
from rallymate_evaluation.small_roi_recovery import feature_vector_transitions
try:
    from scripts.run_residual_pose_profile_ablation import (
        validate_pose_profile_ablation_report,
    )
except ModuleNotFoundError:  # direct script execution puts scripts/ on sys.path
    import importlib.util

    _ablation_path = Path(__file__).with_name(
        "run_residual_pose_profile_ablation.py"
    )
    _ablation_spec = importlib.util.spec_from_file_location(
        "run_residual_pose_profile_ablation", _ablation_path
    )
    if _ablation_spec is None or _ablation_spec.loader is None:
        raise RuntimeError(f"cannot load {_ablation_path}")
    _ablation_module = importlib.util.module_from_spec(_ablation_spec)
    _ablation_spec.loader.exec_module(_ablation_module)
    validate_pose_profile_ablation_report = (
        _ablation_module.validate_pose_profile_ablation_report
    )


REPORT_VERSION = "anchored-pose-profile-extension-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _bound(binding: dict[str, Any], label: str) -> Path:
    path = Path(str(binding.get("path", "")))
    if not path.is_file() or _sha256(path) != str(binding.get("sha256", "")).upper():
        raise ValueError(f"{label} source binding failed")
    return path


def _rows(path: Path) -> list[dict[str, Any]]:
    result = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not result:
        raise ValueError(f"empty JSONL: {path}")
    return result


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _identity_set(impact: dict[str, Any], field: str) -> set[tuple[str, str]]:
    return {
        (str(item["event_id"]), str(item["indicator_id"]))
        for item in impact.get(field, [])
    }


def validate_anchored_pose_extension_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported anchored Pose extension")
    status = report.get("status")
    if status not in {
        "experimental_m69_anchored_extension_regression_free_requires_truth",
        "experimental_m69_anchored_extension_rejected",
    }:
        raise ValueError("unsafe anchored extension status")
    audit = report.get("router_audit", {})
    if int(audit.get("selected_frame_count", -1)) + int(
        audit.get("rejected_frame_count", -1)
    ) != int(audit.get("target_frame_count", -1)):
        raise ValueError("anchored router accounting is inconsistent")
    for field in (
        "selection_uses_feature_values",
        "selection_uses_event_outcomes",
        "selection_uses_grades_or_thresholds",
        "required_joint_validity_regression_allowed",
    ):
        if audit.get(field) is not False:
            raise ValueError(f"unsafe anchored router field: {field}")
    comparison = report.get("comparison_to_m68", {})
    regressions = sum(
        int(comparison[kind].get("regressed_indicator_instance_count", -1))
        for kind in ("feature_vector", "operational_measurement")
    )
    preservation = report.get("m69_preservation", {})
    safe = (
        regressions == 0
        and int(preservation.get("lost_m69_feature_recovery_count", -1)) == 0
        and int(preservation.get("lost_m69_operational_recovery_count", -1)) == 0
    )
    expected = (
        "experimental_m69_anchored_extension_regression_free_requires_truth"
        if safe
        else "experimental_m69_anchored_extension_rejected"
    )
    if status != expected:
        raise ValueError("anchored extension status hides regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get("candidate_promoted") is not False:
        raise ValueError("anchored extension changed production")
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
            raise ValueError(f"unsafe anchored extension claim: {field}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation-report", type=Path, required=True)
    parser.add_argument("--m69-report", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists")
    ablation = json.loads(args.ablation_report.read_text(encoding="utf-8"))
    m69 = json.loads(args.m69_report.read_text(encoding="utf-8"))
    validate_pose_profile_ablation_report(ablation)
    if ablation.get("video_id") != m69.get("video_id"):
        parser.error("ablation and M69 video IDs differ")
    if (
        Path(ablation["sources"]["m69_uniform_high_resolution_report"]["path"]).resolve()
        != args.m69_report.resolve()
        or str(ablation["sources"]["m69_uniform_high_resolution_report"]["sha256"]).upper()
        != _sha256(args.m69_report)
    ):
        parser.error("ablation does not bind the supplied M69 report")

    current_path = _bound(ablation["sources"]["current_frames"], "M68 current frames")
    m69_path = _bound(m69["artifacts"]["routed_frames"], "M69 routed frames")
    candidate_path = _bound(
        ablation["artifacts"]["context_matched_candidate_frames"],
        "context-matched candidate frames",
    )
    timeline_path = _bound(ablation["sources"]["primary_timeline"], "primary timeline")
    events_path = _bound(ablation["sources"]["events"], "events")
    indicator_path = _bound(ablation["sources"]["indicator_features"], "indicator-features")
    registry_path = _bound(ablation["sources"]["registry"], "registry")
    model_path = _bound(ablation["sources"]["candidate_model"], "candidate model")
    current_rows = _rows(current_path)
    m69_rows = _rows(m69_path)
    candidate_rows = _rows(candidate_path)
    timeline_rows = _rows(timeline_path)
    indicator_rows = _rows(indicator_path)
    targets = {
        int(item["processed_index"]) for item in ablation["inference"]["frame_results"]
    }
    required_joints = pose_required_joints_from_registry(
        json.loads(registry_path.read_text(encoding="utf-8"))
    )
    selected, audit = select_required_joint_superset_frames(
        baseline_rows=m69_rows,
        candidate_rows=candidate_rows,
        primary_timeline=timeline_rows,
        target_processed_indexes=targets,
        required_joints=required_joints,
    )
    anchored_rows = (
        compose_disjoint_pose_policy_rows(
            baseline_rows=m69_rows,
            timeline_rows=timeline_rows,
            experiments=[
                {
                    "name": "context_matched_profile_extension",
                    "frame_rows": candidate_rows,
                    "target_processed_indexes": selected,
                }
            ],
        )[0]
        if selected
        else m69_rows
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    anchored_path = args.output_directory / "frames.m69-anchored-extension.jsonl"
    _write_rows(anchored_path, anchored_rows)

    video_id = str(ablation["video_id"])
    current_profile = "m68-current-routed"
    anchored_profile = "m69-plus-context-matched-profile-extension"
    if selected:
        comparison = evaluate_fixed_boundary_pose_ab_files(
            events_path=events_path,
            registry_path=registry_path,
            model_inputs={
                current_profile: {
                    "frames_path": current_path,
                    "primary_timeline_path": timeline_path,
                    "model_sha256": str(
                        json.loads(
                            Path(ablation["sources"]["m68_router_report"]["path"])
                            .read_text(encoding="utf-8")
                        )["settings"]["pose_model_sha256"]
                    ),
                    "pose_backend": "rtmpose",
                    "pose_profile": current_profile,
                },
                anchored_profile: {
                    "frames_path": anchored_path,
                    "primary_timeline_path": timeline_path,
                    "model_sha256": _sha256(model_path),
                    "pose_backend": "rtmpose",
                    "pose_profile": anchored_profile,
                },
            },
            boundary_source_kind="candidate",
            boundary_source_label="candidate_boundaries_not_truth_m70_m69_anchored_extension",
            source_id=f"{video_id}:m70-m69-anchored-extension",
        )
        comparison_path = args.output_directory / "fixed-boundary-comparison.json"
        comparison_path.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
    else:
        comparison_path = _bound(
            ablation["artifacts"]["fixed_boundary_comparisons"][
                "uniform_context_combined"
            ],
            "unchanged M69 fixed-boundary comparison",
        )
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        anchored_profile = str(comparison["model_order"][1])
    feature = feature_vector_transitions(comparison, current_profile, anchored_profile)
    operational = operational_transitions_with_preserved_source_gate(
        comparison, current_profile, anchored_profile, indicator_rows
    )
    m69_feature = ablation["impacts"]["uniform_context_combined"]["feature_vector"]
    m69_operational = ablation["impacts"]["uniform_context_combined"]["operational_measurement"]
    feature_recovered = _identity_set(feature, "recovered_indicator_instances")
    operational_recovered = _identity_set(operational, "recovered_indicator_instances")
    m69_feature_recovered = _identity_set(m69_feature, "recovered_indicator_instances")
    m69_operational_recovered = _identity_set(m69_operational, "recovered_indicator_instances")
    preservation = {
        "m69_feature_recovery_count": len(m69_feature_recovered),
        "anchored_feature_recovery_count": len(feature_recovered),
        "additional_feature_recovery_count": len(feature_recovered - m69_feature_recovered),
        "lost_m69_feature_recovery_count": len(m69_feature_recovered - feature_recovered),
        "m69_operational_recovery_count": len(m69_operational_recovered),
        "anchored_operational_recovery_count": len(operational_recovered),
        "additional_operational_recovery_count": len(operational_recovered - m69_operational_recovered),
        "lost_m69_operational_recovery_count": len(m69_operational_recovered - operational_recovered),
        "additional_operational_recoveries": [
            {"event_id": event_id, "indicator_id": indicator_id}
            for event_id, indicator_id in sorted(
                operational_recovered - m69_operational_recovered
            )
        ],
    }
    safe = (
        feature["regressed_indicator_instance_count"] == 0
        and operational["regressed_indicator_instance_count"] == 0
        and preservation["lost_m69_feature_recovery_count"] == 0
        and preservation["lost_m69_operational_recovery_count"] == 0
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "experimental_m69_anchored_extension_regression_free_requires_truth"
            if safe
            else "experimental_m69_anchored_extension_rejected"
        ),
        "video_id": video_id,
        "sources": {
            "ablation_report": _source(args.ablation_report),
            "m69_report": _source(args.m69_report),
            "current_frames": _source(current_path),
            "m69_frames": _source(m69_path),
            "context_matched_candidate_frames": _source(candidate_path),
            "primary_timeline": _source(timeline_path),
            "events": _source(events_path),
            "indicator_features": _source(indicator_path),
            "registry": _source(registry_path),
        },
        "artifacts": {
            "anchored_frames": _source(anchored_path),
            "fixed_boundary_comparison": _source(comparison_path),
        },
        "router_audit": audit,
        "comparison_to_m68": {
            "feature_vector": feature,
            "operational_measurement": operational,
        },
        "m69_preservation": preservation,
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "current_video_regression_free": safe,
            "next_required_evidence": (
                "manual corrected keypoints and preregistered independent-video validation"
            ),
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_anchored_pose_extension_report(report)
    report_path = args.output_directory / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(report_path),
                "selected_frames": len(selected),
                "m69_operational_recovered": preservation["m69_operational_recovery_count"],
                "anchored_operational_recovered": preservation["anchored_operational_recovery_count"],
                "additional_operational_recovered": preservation["additional_operational_recovery_count"],
                "lost_m69_operational_recovered": preservation["lost_m69_operational_recovery_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
