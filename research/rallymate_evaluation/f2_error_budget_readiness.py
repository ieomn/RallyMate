from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rallymate_evaluation.scoring_truth import build_scoring_truth_evaluation
from rallymate_evaluation.smoothing_coverage import (
    validate_smoothing_counterfactual_coverage_sources,
)
from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.multivideo_coverage import (
    validate_multivideo_indicator_calculation_coverage,
)


SCHEMA_VERSION = "1.0.0"
REPORT_VERSION = "f2-error-budget-readiness-v1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"source artifact is missing: {resolved}")
    return {"path": str(resolved), "sha256": _sha256(resolved)}


def _nonempty_jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _truth_summary(video_id: str, evaluation: Mapping[str, Any]) -> dict[str, Any]:
    event = evaluation.get("event_evaluation", {})
    feature = evaluation.get("feature_evaluation", {})
    metrics = feature.get("feature_metrics", {})
    if not isinstance(metrics, Mapping):
        raise ValueError(f"{video_id} feature_metrics must be an object")
    budget_features = feature.get("error_budget", {}).get("features", {})
    if not isinstance(budget_features, Mapping):
        budget_features = {}

    def metric_names(component: str) -> list[str]:
        names: list[str] = []
        for name, row in budget_features.items():
            if not isinstance(row, Mapping):
                continue
            value = row.get(component)
            if component == "missing_value_impact":
                ready = isinstance(value, Mapping) and any(
                    value.get(field) is not None
                    for field in (
                        "mean_valid_fraction_loss",
                        "p95_valid_fraction_loss",
                    )
                )
            else:
                ready = (
                    isinstance(value, Mapping)
                    and isinstance(value.get("valid_count"), int)
                    and value.get("valid_count", 0) > 0
                )
            if ready:
                names.append(str(name))
        return sorted(names)

    evaluated_feature_names = sorted(
        str(name)
        for name, row in metrics.items()
        if isinstance(row, Mapping)
        and isinstance(row.get("overall"), Mapping)
        and row["overall"].get("valid_count", 0) > 0
    )
    inputs = evaluation.get("inputs", {})
    hashes = inputs.get("sha256", {})
    return {
        "video_id": video_id,
        "status": evaluation.get("status"),
        "event_evaluation_status": event.get("status"),
        "feature_evaluation_status": feature.get("status"),
        "evaluated_feature_names": evaluated_feature_names,
        "pose_error_feature_names": metric_names("pose_error"),
        "event_boundary_error_feature_names": metric_names(
            "event_boundary_error"
        ),
        "smoothing_error_feature_names": metric_names("smoothing_error"),
        "missing_value_impact_feature_names": metric_names(
            "missing_value_impact"
        ),
        "event_f1": event.get("event_f1"),
        "mean_segment_iou": event.get("mean_segment_iou"),
        "boundary_mae_ms": event.get("boundary_mae_ms"),
        "feature_truth_complete": bool(
            evaluation.get("promotion_guard", {}).get("feature_truth_complete")
        ),
        "context_feature_truth_complete": bool(
            evaluation.get("promotion_guard", {}).get(
                "context_feature_truth_complete"
            )
        ),
        "inputs": {
            "frames": {"path": inputs.get("frames"), "sha256": hashes.get("frames")},
            "primary_timeline": {
                "path": inputs.get("primary_timeline"),
                "sha256": hashes.get("primary_timeline"),
            },
            "predicted_events": {
                "path": inputs.get("predicted_events"),
                "sha256": hashes.get("predicted_events"),
            },
        },
    }


def _build_report(
    *,
    registry_path: Path,
    smoothing_coverage_path: Path,
    calculation_coverage_path: Path,
    run_directories: Sequence[Path],
    manual_events_path: Path,
    manual_keypoints_path: Path,
    manual_semantics_path: Path,
    generated_at: str,
) -> dict[str, Any]:
    registry_path = registry_path.resolve()
    smoothing_coverage_path = smoothing_coverage_path.resolve()
    calculation_coverage_path = calculation_coverage_path.resolve()
    manual_events_path = manual_events_path.resolve()
    manual_keypoints_path = manual_keypoints_path.resolve()
    manual_semantics_path = manual_semantics_path.resolve()
    run_directories = [path.resolve() for path in run_directories]

    registry = load_feasibility_registry(registry_path)
    smoothing = _read_json(smoothing_coverage_path)
    calculation = _read_json(calculation_coverage_path)
    validate_smoothing_counterfactual_coverage_sources(smoothing)
    validate_multivideo_indicator_calculation_coverage(
        calculation, verify_sources=True
    )

    registry_binding = _artifact(registry_path)
    if smoothing.get("registry", {}).get("sha256") != registry_binding["sha256"]:
        raise ValueError("smoothing coverage registry hash differs from current registry")
    if smoothing.get("registry", {}).get("registry_version") != registry.get(
        "registry_version"
    ):
        raise ValueError("smoothing coverage registry version differs from current registry")
    calculation_source = calculation.get("source", {})
    if calculation_source.get("registry", {}).get("sha256") != registry_binding[
        "sha256"
    ]:
        raise ValueError("calculation coverage registry hash differs from current registry")
    if calculation_source.get("registry_version") != registry.get("registry_version"):
        raise ValueError("calculation coverage registry version differs from current registry")

    expected_video_ids = sorted(calculation.get("scope", {}).get("video_ids", []))
    run_by_video = {path.name: path for path in run_directories}
    if len(run_by_video) != len(run_directories):
        raise ValueError("run directory video IDs must be unique")
    if sorted(run_by_video) != expected_video_ids:
        raise ValueError("run directories must exactly cover calculation coverage videos")
    smoothing_video_ids = sorted(
        str(item.get("video_id")) for item in smoothing.get("sources", [])
    )
    if smoothing_video_ids != expected_video_ids:
        raise ValueError("smoothing coverage videos differ from calculation coverage")

    truth_bindings = {
        "manual_events": {
            **_artifact(manual_events_path),
            "record_count": _nonempty_jsonl_count(manual_events_path),
        },
        "manual_keypoints": {
            **_artifact(manual_keypoints_path),
            "record_count": _nonempty_jsonl_count(manual_keypoints_path),
        },
        "manual_semantics": {
            **_artifact(manual_semantics_path),
            "record_count": _nonempty_jsonl_count(manual_semantics_path),
        },
    }

    truth_evaluations: list[dict[str, Any]] = []
    evidence_features_by_video: dict[str, dict[str, set[str]]] = {}
    for video_id in expected_video_ids:
        run = run_by_video[video_id]
        evaluation = build_scoring_truth_evaluation(
            frames_path=run / "frames.jsonl",
            primary_timeline_path=run / "primary-player.jsonl",
            predicted_events_path=run / "events.jsonl",
            registry_path=registry_path,
            manual_events_path=manual_events_path,
            manual_keypoints_path=manual_keypoints_path,
            manual_semantics_path=manual_semantics_path,
        )
        summary = _truth_summary(video_id, evaluation)
        truth_evaluations.append(summary)
        evidence_features_by_video[video_id] = {
            field: set(summary[field])
            for field in (
                "evaluated_feature_names",
                "pose_error_feature_names",
                "event_boundary_error_feature_names",
                "smoothing_error_feature_names",
                "missing_value_impact_feature_names",
            )
        }

    indicator_ids = [item["indicator_id"] for item in registry["indicators"]]
    calculation_indicators = {
        item["indicator_id"]: item for item in calculation.get("indicators", [])
    }
    if set(calculation_indicators) != set(indicator_ids):
        raise ValueError("calculation coverage indicators differ from registry")

    smoothing_by_feature = {
        item["feature_name"]: item for item in smoothing.get("feature_metrics", [])
    }
    excluded_context = set(
        smoothing.get("registry", {}).get("excluded_non_pose_context_features", [])
    )
    required_pose_features = sorted(
        {
            name
            for indicator in registry["indicators"]
            for name in indicator["required_features"]
            if name not in excluded_context
        }
    )
    if set(smoothing_by_feature) != set(required_pose_features):
        raise ValueError("smoothing feature set differs from registry Pose feature set")

    all_event_evaluated = all(
        row["event_evaluation_status"] == "evaluated" for row in truth_evaluations
    )
    all_feature_evaluated = all(
        row["feature_evaluation_status"] == "evaluated" for row in truth_evaluations
    )
    grade_gap_available = False
    indicator_rows: list[dict[str, Any]] = []
    smoothing_complete_indicators = 0
    for indicator in registry["indicators"]:
        indicator_id = indicator["indicator_id"]
        pose_features = [
            name for name in indicator["required_features"] if name not in excluded_context
        ]
        context_features = [
            name for name in indicator["required_features"] if name in excluded_context
        ]
        smoothing_features = [
            {
                "feature_name": name,
                "status": smoothing_by_feature[name]["status"],
                "computed_counterfactual_count": smoothing_by_feature[name][
                    "computed_counterfactual_count"
                ],
                "valid_numeric_record_count": smoothing_by_feature[name][
                    "valid_numeric_record_count"
                ],
                "counterfactual_coverage": smoothing_by_feature[name][
                    "counterfactual_coverage"
                ],
                "p95_absolute_difference": smoothing_by_feature[name][
                    "counterfactual_impact"
                ].get("p95_absolute_difference"),
            }
            for name in pose_features
        ]
        smoothing_status = (
            "complete"
            if all(row["status"] == "fully_reconstructable" for row in smoothing_features)
            else "partial"
            if any(row["status"] != "not_reconstructable" for row in smoothing_features)
            else "unavailable"
        )
        if smoothing_status == "complete":
            smoothing_complete_indicators += 1
        def component_complete(field: str) -> bool:
            return all_feature_evaluated and all(
                set(pose_features).issubset(
                    evidence_features_by_video[video_id][field]
                )
                for video_id in expected_video_ids
            )

        feature_truth_complete = component_complete("evaluated_feature_names")
        pose_error_complete = component_complete("pose_error_feature_names")
        event_boundary_error_complete = component_complete(
            "event_boundary_error_feature_names"
        )
        truth_smoothing_error_complete = component_complete(
            "smoothing_error_feature_names"
        )
        missing_value_error_complete = component_complete(
            "missing_value_impact_feature_names"
        )
        blockers: list[str] = []
        if not all_event_evaluated:
            blockers.append("manual_event_error_evaluation_required")
        if not feature_truth_complete:
            blockers.append("feature_mae_p95_bias_evaluation_required")
        if not pose_error_complete:
            blockers.append("pose_error_evaluation_required")
        if not event_boundary_error_complete:
            blockers.append("event_boundary_error_evaluation_required")
        if not truth_smoothing_error_complete:
            blockers.append("truth_conditioned_smoothing_error_evaluation_required")
        if not missing_value_error_complete:
            blockers.append("missing_value_error_impact_evaluation_required")
        if context_features and not all(
            row["context_feature_truth_complete"] for row in truth_evaluations
        ):
            blockers.append("manual_context_semantic_error_evaluation_required")
        if smoothing_status != "complete":
            blockers.append("smoothing_counterfactual_partial")
        blockers.append("external_grade_gap_assessment_required")
        coverage = calculation_indicators[indicator_id]
        indicator_rows.append(
            {
                "indicator_id": indicator_id,
                "feasibility_level": indicator["feasibility_level"],
                "required_events": indicator["required_events"],
                "required_pose_features": pose_features,
                "required_context_features": context_features,
                "calculation": {
                    "status": (
                        "measured_in_every_video"
                        if coverage.get("measured_in_every_video") is True
                        else "incomplete"
                    ),
                    "candidate_instances": coverage["candidate_instances"],
                    "measured_feature_vectors": coverage["measured_feature_vectors"],
                    "unavailable_feature_vectors": coverage[
                        "unavailable_feature_vectors"
                    ],
                    "measurement_rate": coverage["measurement_rate"],
                },
                "smoothing_counterfactual": {
                    "status": smoothing_status,
                    "features": smoothing_features,
                },
                "event_error": {
                    "status": "evaluated" if all_event_evaluated else "ground_truth_required"
                },
                "feature_error": {
                    "status": "evaluated" if feature_truth_complete else "ground_truth_required"
                },
                "pose_error": {
                    "status": "evaluated" if pose_error_complete else "ground_truth_required"
                },
                "event_boundary_error": {
                    "status": "evaluated" if event_boundary_error_complete else "ground_truth_required"
                },
                "truth_conditioned_smoothing_error": {
                    "status": "evaluated" if truth_smoothing_error_complete else "ground_truth_required"
                },
                "missing_value_error_impact": {
                    "status": "evaluated" if missing_value_error_complete else "ground_truth_required"
                },
                "grade_gap_assessment": {
                    "status": "passed" if grade_gap_available else "external_assessment_required"
                },
                "f2_to_f3_ready": False,
                "blockers": sorted(set(blockers)),
            }
        )

    truth_counts = {
        name: int(binding["record_count"]) for name, binding in truth_bindings.items()
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": generated_at,
        "status": (
            "ground_truth_required"
            if sum(truth_counts.values()) == 0
            else "error_budget_and_external_grade_gap_incomplete"
        ),
        "registry": {
            **registry_binding,
            "registry_version": registry["registry_version"],
            "indicator_count": len(indicator_ids),
            "required_pose_feature_count": len(required_pose_features),
            "excluded_context_features": sorted(excluded_context),
        },
        "sources": {
            "smoothing_coverage": {
                **_artifact(smoothing_coverage_path),
                "report_version": smoothing["report_version"],
            },
            "calculation_coverage": {
                **_artifact(calculation_coverage_path),
                "coverage_id": calculation["coverage_id"],
            },
            "manual_truth": truth_bindings,
            "truth_evaluations": truth_evaluations,
        },
        "counts": {
            "videos": len(expected_video_ids),
            "indicators": len(indicator_ids),
            "required_pose_features": len(required_pose_features),
            "smoothing_fully_reconstructable_features": smoothing["counts"][
                "features_fully_reconstructable_count"
            ],
            "smoothing_partially_reconstructable_features": smoothing["counts"][
                "features_partially_reconstructable_count"
            ],
            "smoothing_not_reconstructable_features": smoothing["counts"][
                "features_not_reconstructable_count"
            ],
            "smoothing_computed_records": smoothing["counts"][
                "computed_counterfactual_count"
            ],
            "smoothing_valid_numeric_records": smoothing["counts"][
                "valid_numeric_record_count"
            ],
            "smoothing_counterfactual_coverage": smoothing["counts"][
                "counterfactual_coverage"
            ],
            "indicators_with_complete_smoothing_counterfactual": smoothing_complete_indicators,
            "indicators_with_partial_smoothing_counterfactual": len(indicator_ids)
            - smoothing_complete_indicators,
            "indicators_with_event_error_evaluation": (
                len(indicator_ids) if all_event_evaluated else 0
            ),
            "indicators_with_feature_error_evaluation": sum(
                row["feature_error"]["status"] == "evaluated"
                for row in indicator_rows
            ),
            "indicators_with_pose_error_evaluation": sum(
                row["pose_error"]["status"] == "evaluated"
                for row in indicator_rows
            ),
            "indicators_with_event_boundary_error_evaluation": sum(
                row["event_boundary_error"]["status"] == "evaluated"
                for row in indicator_rows
            ),
            "indicators_with_truth_conditioned_smoothing_error": sum(
                row["truth_conditioned_smoothing_error"]["status"] == "evaluated"
                for row in indicator_rows
            ),
            "indicators_with_missing_value_error_impact": sum(
                row["missing_value_error_impact"]["status"] == "evaluated"
                for row in indicator_rows
            ),
            "indicators_with_grade_gap_assessment": 0,
            "indicators_ready_for_f2_to_f3": 0,
            "manual_event_records": truth_counts["manual_events"],
            "manual_keypoint_records": truth_counts["manual_keypoints"],
            "manual_semantic_records": truth_counts["manual_semantics"],
        },
        "indicators": indicator_rows,
        "source_fingerprint_sha256": _canonical_sha256(
            {
                "registry": registry_binding["sha256"],
                "smoothing_coverage": _sha256(smoothing_coverage_path),
                "calculation_coverage": _sha256(calculation_coverage_path),
                "truth": {
                    name: binding["sha256"] for name, binding in truth_bindings.items()
                },
                "runs": {
                    row["video_id"]: {
                        name: binding["sha256"]
                        for name, binding in row["inputs"].items()
                    }
                    for row in truth_evaluations
                },
            }
        ),
        "safety": {
            "gpu_inference_executed": False,
            "existing_pose_frames_reused": True,
            "candidate_events_are_ground_truth": False,
            "smoothing_counterfactual_is_accuracy": False,
            "measurement_coverage_is_accuracy": False,
            "event_accuracy_claim": False,
            "feature_accuracy_claim": False,
            "grade_gap_claim": False,
            "formal_score_claim": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "automatic_f2_to_f3_promotion": False,
            "maximum_maturity_claim": "F2",
        },
    }
    validate_f2_error_budget_readiness(report)
    return report


def build_f2_error_budget_readiness(
    *,
    registry_path: str | Path,
    smoothing_coverage_path: str | Path,
    calculation_coverage_path: str | Path,
    run_directories: Sequence[str | Path],
    manual_events_path: str | Path,
    manual_keypoints_path: str | Path,
    manual_semantics_path: str | Path,
) -> dict[str, Any]:
    return _build_report(
        registry_path=Path(registry_path),
        smoothing_coverage_path=Path(smoothing_coverage_path),
        calculation_coverage_path=Path(calculation_coverage_path),
        run_directories=[Path(path) for path in run_directories],
        manual_events_path=Path(manual_events_path),
        manual_keypoints_path=Path(manual_keypoints_path),
        manual_semantics_path=Path(manual_semantics_path),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def validate_f2_error_budget_readiness(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get(
        "report_version"
    ) != REPORT_VERSION:
        raise ValueError("unsupported F2 error-budget readiness report")
    safety = report.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("F2 error-budget readiness safety is missing")
    if safety.get("existing_pose_frames_reused") is not True:
        raise ValueError("existing Pose reuse must be explicit")
    for field in (
        "gpu_inference_executed",
        "candidate_events_are_ground_truth",
        "smoothing_counterfactual_is_accuracy",
        "measurement_coverage_is_accuracy",
        "event_accuracy_claim",
        "feature_accuracy_claim",
        "grade_gap_claim",
        "formal_score_claim",
        "grades_generated",
        "thresholds_generated",
        "automatic_f2_to_f3_promotion",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe F2 error-budget readiness claim: {field}")
    if safety.get("maximum_maturity_claim") != "F2":
        raise ValueError("F2 error-budget readiness maturity ceiling is invalid")
    indicators = report.get("indicators")
    counts = report.get("counts")
    if not isinstance(indicators, list) or not isinstance(counts, Mapping):
        raise ValueError("F2 error-budget readiness indicators/counts are missing")
    if len(indicators) != counts.get("indicators") or not indicators:
        raise ValueError("F2 error-budget readiness indicator count mismatch")
    ids = [row.get("indicator_id") for row in indicators]
    if len(ids) != len(set(ids)):
        raise ValueError("F2 error-budget readiness indicator IDs must be unique")
    for row in indicators:
        if row.get("feasibility_level") != "F2":
            raise ValueError("current readiness report may only claim F2")
        if row.get("f2_to_f3_ready") is not False:
            raise ValueError("F2-to-F3 cannot be claimed by readiness report")
        if not row.get("blockers"):
            raise ValueError("non-promoted indicator must retain blockers")
    if counts.get("indicators_ready_for_f2_to_f3") != 0:
        raise ValueError("F2-to-F3 ready count must remain zero")


def validate_f2_error_budget_readiness_sources(report: Mapping[str, Any]) -> None:
    validate_f2_error_budget_readiness(report)
    sources = report["sources"]
    truth = sources["manual_truth"]
    run_directories = sorted(
        {
            str(Path(row["inputs"]["frames"]["path"]).parent)
            for row in sources["truth_evaluations"]
        }
    )
    rebuilt = _build_report(
        registry_path=Path(report["registry"]["path"]),
        smoothing_coverage_path=Path(sources["smoothing_coverage"]["path"]),
        calculation_coverage_path=Path(sources["calculation_coverage"]["path"]),
        run_directories=[Path(path) for path in run_directories],
        manual_events_path=Path(truth["manual_events"]["path"]),
        manual_keypoints_path=Path(truth["manual_keypoints"]["path"]),
        manual_semantics_path=Path(truth["manual_semantics"]["path"]),
        generated_at=str(report["generated_at"]),
    )
    if dict(report) != rebuilt:
        raise ValueError("F2 error-budget readiness differs from source replay")


__all__ = [
    "REPORT_VERSION",
    "SCHEMA_VERSION",
    "build_f2_error_budget_readiness",
    "validate_f2_error_budget_readiness",
    "validate_f2_error_budget_readiness_sources",
]
