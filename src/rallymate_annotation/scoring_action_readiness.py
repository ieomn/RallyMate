from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_annotation.scoring_action_worklist import (
    validate_scoring_truth_action_worklist,
)
from rallymate_evaluation.feature_errors import evaluate_feature_errors
from rallymate_evaluation.ground_truth import (
    apply_keypoint_corrections,
    validate_keypoint_annotation,
)
from rallymate_evaluation.pose_diagnostic_truth import (
    DIAGNOSTIC_TYPES,
    evaluate_pose_diagnostic_truth,
    validate_pose_diagnostic_evaluation,
    validate_pose_diagnostic_truth_pack_sources,
)
from rallymate_events import evaluate_events
from rallymate_events.schemas import validate_event_record
from rallymate_features import pose_sequence_from_records
from rallymate_scoring.feasibility import (
    load_feasibility_registry,
    measurement_feature_names,
)
from rallymate_scoring.scoring_context import (
    SCORING_CONTEXT_FEATURE_DEFINITIONS,
    validate_scoring_reference_context,
)


SCHEMA_VERSION = "1.0.0"
READINESS_VERSION = "scoring-truth-action-readiness-v1.0.0"
EVENT_MATCH_IOU_MIN = 0.5

SATISFIED = "evidence_satisfied"
PARTIAL = "review_in_progress_not_adjudicated"
PENDING = "annotation_required"
STATUSES = (SATISFIED, PARTIAL, PENDING)

POSE_REQUIREMENT_TYPES = {
    "full_timeline_manual_keypoint_jump_truth": ("keypoint_jump",),
    "full_timeline_manual_left_right_swap_truth": ("left_right_swap",),
    "manual_primary_identity_continuity_truth": (
        "primary_identity_ambiguity",
        "source_track_switch",
    ),
}
PHASE_REQUIREMENTS = {
    "manual_landing_phase_boundary": "landing_proxy_ms",
    "manual_first_step_slowdown_phase_boundary": "first_step_slowdown_proxy_ms",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {line_number} is not an object: {path}")
        rows.append(value)
    return rows


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def _resolved_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("evidence source path must be non-empty")
    return Path(value).resolve()


def _assert_truth_validation_bindings(
    report: Mapping[str, Any],
    *,
    events_path: Path,
    keypoints_path: Path,
    semantics_path: Path,
    video_id: str,
) -> None:
    if report.get("schema_version") != "1.0.0":
        raise ValueError("unsupported truth validation schema")
    if report.get("errors") != []:
        raise ValueError("truth validation contains annotation errors")
    outputs = report.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("truth validation outputs are missing")
    supplied = {
        "manual_events": events_path.resolve(),
        "manual_keypoints": keypoints_path.resolve(),
        "manual_semantics": semantics_path.resolve(),
    }
    for name, path in supplied.items():
        if _resolved_path(outputs.get(name)) != path:
            raise ValueError(f"truth validation output path mismatch: {name}")
    by_video = outputs.get("by_video")
    if not isinstance(by_video, Mapping) or video_id not in by_video:
        raise ValueError("truth validation does not cover worklist video")
    counts = report.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError("truth validation counts are missing")


def _assert_truth_validation_counts(
    report: Mapping[str, Any],
    *,
    manual_events: list[dict[str, Any]],
    manual_keypoints: list[dict[str, Any]],
    manual_semantics: list[dict[str, Any]],
) -> None:
    counts = report["counts"]
    expected = {
        "manual_events": len(manual_events),
        "accepted_keypoint_frames": len(manual_keypoints),
        "accepted_keypoint_joint_rows": sum(
            len(row.get("joints", {})) for row in manual_keypoints
        ),
        "semantic_truth_records": len(manual_semantics),
    }
    for name, value in expected.items():
        if counts.get(name) != value:
            raise ValueError(f"truth validation count mismatch: {name}")


def _assert_feature_evaluation_bindings(
    report: Mapping[str, Any],
    *,
    worklist: Mapping[str, Any],
    truth_validation: Mapping[str, Any],
    manual_events_path: Path,
    manual_keypoints_path: Path,
    manual_semantics_path: Path,
) -> None:
    if report.get("schema_version") != "1.0.0":
        raise ValueError("unsupported scoring truth evaluation schema")
    inputs = report.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("scoring truth evaluation input lineage is missing")
    hashes = inputs.get("sha256")
    if not isinstance(hashes, Mapping):
        raise ValueError("scoring truth evaluation SHA lineage is missing")
    expected_paths = {
        "frames": Path(str(inputs.get("frames", ""))).resolve(),
        "primary_timeline": Path(str(inputs.get("primary_timeline", ""))).resolve(),
        "predicted_events": Path(worklist["source"]["events"]["path"]).resolve(),
        "manual_events": manual_events_path.resolve(),
        "manual_keypoints": manual_keypoints_path.resolve(),
        "manual_semantics": manual_semantics_path.resolve(),
        "registry": Path(
            str(worklist["source"]["feasibility_registry"]["path"])
        ).resolve(),
    }
    for name, path in expected_paths.items():
        if name in {"frames", "primary_timeline", "registry"} and not path.is_file():
            raise ValueError(f"scoring truth evaluation input is missing: {name}")
        if _resolved_path(inputs.get(name)) != path:
            raise ValueError(f"scoring truth evaluation path mismatch: {name}")
        if str(hashes.get(name, "")).upper() != sha256_file(path):
            raise ValueError(f"scoring truth evaluation SHA mismatch: {name}")
    registry = worklist["source"]["feasibility_registry"]
    if inputs.get("registry_version") != registry.get("registry_version"):
        raise ValueError("scoring truth evaluation registry version mismatch")
    if str(hashes.get("registry", "")).upper() != str(registry.get("sha256", "")).upper():
        raise ValueError("scoring truth evaluation registry SHA mismatch")
    scoring_binding = truth_validation.get("scoring_source_binding")
    bundle = scoring_binding.get("bundle") if isinstance(scoring_binding, Mapping) else None
    binding_registry = (
        scoring_binding.get("registry") if isinstance(scoring_binding, Mapping) else None
    )
    if not isinstance(bundle, Mapping) or not isinstance(binding_registry, Mapping):
        raise ValueError("truth validation scoring source binding is missing")
    expected_hashes = {
        "frames": bundle.get("frames_sha256"),
        "primary_timeline": bundle.get("primary_timeline_sha256"),
        "predicted_events": bundle.get("events_sha256"),
        "registry": binding_registry.get("sha256"),
    }
    for name, expected in expected_hashes.items():
        if str(hashes.get(name, "")).upper() != str(expected or "").upper():
            raise ValueError(f"scoring truth evaluation differs from truth-pack binding: {name}")
    if inputs.get("registry_version") != binding_registry.get("version"):
        raise ValueError("scoring truth evaluation registry differs from truth-pack binding")


def _recompute_scoring_truth_evaluation(
    report: Mapping[str, Any],
    *,
    manual_events: list[dict[str, Any]],
    manual_keypoints: list[dict[str, Any]],
    manual_semantics: list[dict[str, Any]],
) -> None:
    inputs = report["inputs"]
    frames = _read_jsonl(Path(inputs["frames"]))
    timeline = _read_jsonl(Path(inputs["primary_timeline"]))
    predictions = _read_jsonl(Path(inputs["predicted_events"]))
    sequence = pose_sequence_from_records(frames, timeline)
    corrected = (
        apply_keypoint_corrections(sequence, manual_keypoints)
        if manual_keypoints
        else None
    )
    registry = load_feasibility_registry(Path(inputs["registry"]))
    measurement_by_event: dict[str, set[str]] = defaultdict(set)
    context_by_event: dict[str, set[str]] = defaultdict(set)
    for indicator in registry["indicators"]:
        event_code = str(indicator["indicator_id"]).split("-", 1)[0]
        measurement = set(measurement_feature_names(indicator))
        measurement_by_event[event_code].update(measurement)
        context = set(indicator["required_features"]) - measurement
        unknown = context - set(SCORING_CONTEXT_FEATURE_DEFINITIONS)
        if unknown:
            raise ValueError(
                "unsupported scoring truth context feature: "
                + ", ".join(sorted(unknown))
            )
        context_by_event[event_code].update(context)
    expected_feature = evaluate_feature_errors(
        sequence,
        corrected,
        predictions,
        manual_events or None,
        {name: sorted(values) for name, values in measurement_by_event.items()},
        context_feature_names_by_event={
            name: sorted(values)
            for name, values in context_by_event.items()
            if values
        },
        semantic_ground_truth=manual_semantics,
    )
    expected_event = (
        evaluate_events(predictions, manual_events)
        if manual_events
        else {
            "schema_version": "1.0.0",
            "status": "ground_truth_required",
            "event_f1": None,
            "mean_segment_iou": None,
            "boundary_mae_ms": None,
            "boundary_p95_ms": None,
            "phase_boundary_mae_ms": None,
            "phase_boundary_p95_ms": None,
            "phase_boundary_valid_rate": None,
            "phase_boundary_by_name": {},
        }
    )
    if report.get("feature_evaluation") != expected_feature:
        raise ValueError("scoring truth feature evaluation differs from recomputation")
    if report.get("event_evaluation") != expected_event:
        raise ValueError("scoring truth event evaluation differs from recomputation")


def _validate_semantic_record(record: Mapping[str, Any], *, video_id: str) -> None:
    required = {
        "annotation_id",
        "video_id",
        "event_id",
        "indicator_id",
        "semantic_key",
        "semantic_type",
        "observable",
        "value",
        "null_reason",
        "annotation_confidence",
        "annotator_id",
        "reviewer_id",
        "adjudication_status",
    }
    if not required.issubset(record):
        raise ValueError("compiled semantic truth fields are incomplete")
    if record.get("video_id") != video_id:
        return
    if record.get("adjudication_status") != "accepted":
        raise ValueError("compiled semantic truth must be accepted")
    if not all(isinstance(record.get(name), str) and record[name] for name in (
        "annotation_id", "event_id", "indicator_id", "semantic_key", "annotator_id", "reviewer_id"
    )):
        raise ValueError("compiled semantic truth identity/reviewer is incomplete")
    if record["annotator_id"] == record["reviewer_id"]:
        raise ValueError("compiled semantic truth reviewer must be independent")
    if not isinstance(record.get("observable"), bool):
        raise ValueError("compiled semantic observable must be boolean")
    if record["observable"] is False and (
        record.get("value") is not None
        or not isinstance(record.get("null_reason"), str)
        or not record["null_reason"]
    ):
        raise ValueError("unobservable semantic truth requires null value and reason")


def _event_matches(
    *, predictions: list[dict[str, Any]], truth: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    result = evaluate_events(predictions, truth, match_iou_min=EVENT_MATCH_IOU_MIN)
    matches = {
        str(row["prediction_event_id"]): dict(row)
        for row in result.get("matches", [])
    }
    return matches, result


def _phase_name(requirement: str) -> str | None:
    if requirement.startswith("manual_phase_boundary:"):
        return requirement.split(":", 1)[1]
    return PHASE_REQUIREMENTS.get(requirement)


def _diagnostic_status(
    requirement: str,
    *,
    pose_evaluation: Mapping[str, Any],
) -> tuple[str, str, list[dict[str, Any]], list[str]]:
    types = POSE_REQUIREMENT_TYPES[requirement]
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    any_reviewed = False
    for diagnostic_type in types:
        row = pose_evaluation["by_diagnostic_type"][diagnostic_type]
        coverage = str(row["coverage_status"])
        any_reviewed = any_reviewed or int(row["reviewed_frame_count"]) > 0
        evidence.append(
            {
                "kind": "full_timeline_pose_diagnostic_truth",
                "diagnostic_type": diagnostic_type,
                "coverage_status": coverage,
                "reviewed_frame_count": int(row["reviewed_frame_count"]),
                "timeline_frame_count": int(row["timeline_frame_count"]),
                "truth_positive_count": int(row["truth_positive_count"]),
            }
        )
        if coverage != "full_timeline":
            missing.append(f"full_timeline_{diagnostic_type}_truth_missing")
    if not missing:
        return SATISFIED, "diagnostic_full_timeline_truth_available", evidence, []
    if any_reviewed:
        return PARTIAL, "diagnostic_truth_partial_coverage", evidence, missing
    return PENDING, "diagnostic_truth_annotation_required", evidence, missing


def _matched_truth_event(
    item: Mapping[str, Any],
    *,
    matches: Mapping[str, Mapping[str, Any]],
    truth_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    match = matches.get(str(item["event_id"]))
    if match is None:
        return None, None
    truth = truth_by_id.get(str(match["ground_truth_event_id"]))
    return truth, match


def _semantic_evidence(
    *,
    truth_event_id: str,
    semantic_key: str,
    affected_instances: list[str],
    semantics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indicator_ids = {value.split("::", 1)[1] for value in affected_instances}
    return [
        row
        for row in semantics
        if row.get("event_id") == truth_event_id
        and row.get("semantic_key") == semantic_key
        and row.get("indicator_id") in indicator_ids
    ]


def _evaluate_item(
    item: Mapping[str, Any],
    *,
    pose_evaluation: Mapping[str, Any],
    event_matches: Mapping[str, Mapping[str, Any]],
    truth_by_id: Mapping[str, Mapping[str, Any]],
    semantics: list[dict[str, Any]],
    feature_details: Mapping[tuple[str, str], Mapping[str, Any]],
    reference_context: Mapping[str, Mapping[str, Any]],
    keypoint_cells: Mapping[str, bool],
) -> dict[str, Any]:
    requirement = str(item["truth_requirement"])
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    outcome = "evidence_not_available"
    matched_event_id: str | None = None
    segment_iou: float | None = None

    if requirement in POSE_REQUIREMENT_TYPES:
        status, outcome, evidence, missing = _diagnostic_status(
            requirement, pose_evaluation=pose_evaluation
        )
    elif requirement == "manual_target_direction_semantics":
        observation = reference_context.get(str(item["event_id"]))
        if observation is None:
            status = PENDING
            missing = ["target_direction_observation_missing"]
        else:
            evidence.append(
                {
                    "kind": "scoring_reference_context",
                    "observation_id": observation["observation_id"],
                    "status": observation["status"],
                }
            )
            if observation["status"] == "accepted":
                status = SATISFIED
                outcome = "accepted_target_direction_reference"
            elif observation["status"] == "unobservable":
                status = SATISFIED
                outcome = "target_direction_adjudicated_unobservable"
            else:
                status = PENDING
                missing = ["accepted_or_unobservable_target_direction_required"]
    else:
        truth_event, match = _matched_truth_event(
            item, matches=event_matches, truth_by_id=truth_by_id
        )
        if truth_event is None or match is None:
            status = PENDING
            missing = ["matched_accepted_manual_event_missing"]
        else:
            matched_event_id = str(truth_event["event_id"])
            segment_iou = float(match["segment_iou"])
            evidence.append(
                {
                    "kind": "matched_manual_event",
                    "manual_event_id": matched_event_id,
                    "segment_iou": segment_iou,
                    "matching_protocol": "event-association-iou-v1",
                }
            )
            status = SATISFIED
            outcome = "accepted_manual_event_matched"
            phase = _phase_name(requirement)
            if phase is not None:
                value = truth_event.get("key_phases_ms", {}).get(phase)
                evidence.append(
                    {"kind": "manual_phase", "phase_name": phase, "timestamp_ms": value}
                )
                if not isinstance(value, int) or isinstance(value, bool):
                    status = PENDING
                    missing.append(f"accepted_manual_phase_missing:{phase}")
                else:
                    outcome = "accepted_manual_phase_available"
            elif requirement == "manual_launch_side_semantics":
                expected_semantic_by_indicator = {
                    "FS02-M03": "support_side",
                    "FS02-M04": "launch_side",
                    "FS02-M05": "launch_side",
                }
                expected_indicators = {
                    value.split("::", 1)[1]
                    for value in item["affected_indicator_instances"]
                }
                if not expected_indicators.issubset(expected_semantic_by_indicator):
                    raise ValueError("launch/support side requirement has unknown indicator")
                rows = [
                    row
                    for row in semantics
                    if row.get("event_id") == matched_event_id
                    and row.get("indicator_id") in expected_indicators
                    and row.get("semantic_key")
                    == expected_semantic_by_indicator[str(row.get("indicator_id"))]
                ]
                evidence.extend(
                    {
                        "kind": "manual_semantic",
                        "annotation_id": row["annotation_id"],
                        "indicator_id": row["indicator_id"],
                        "semantic_key": row["semantic_key"],
                        "observable": row["observable"],
                    }
                    for row in rows
                )
                covered = {str(row["indicator_id"]) for row in rows}
                if covered != expected_indicators:
                    status = PENDING
                    missing.append("accepted_launch_side_semantics_incomplete")
                else:
                    outcome = (
                        "launch_side_semantics_available"
                        if all(row["observable"] for row in rows)
                        else "launch_side_semantics_adjudicated_unobservable"
                    )
            elif requirement == "manual_keypoint_and_event_feature_truth":
                invalid_names = {
                    str(row["feature_name"]) for row in item["invalid_features"]
                }
                detail_rows = [
                    feature_details.get((str(item["event_id"]), name))
                    for name in sorted(invalid_names)
                ]
                evidence.extend(
                    {
                        "kind": "feature_error_detail",
                        "feature_name": name,
                        "ground_truth_event_id": (
                            detail.get("ground_truth_event_id") if detail else None
                        ),
                        "ground_truth_valid": (
                            detail.get("ground_truth_valid") if detail else None
                        ),
                    }
                    for name, detail in zip(sorted(invalid_names), detail_rows)
                )
                if not invalid_names or any(
                    not isinstance(detail, Mapping)
                    or detail.get("ground_truth_event_id") != matched_event_id
                    or detail.get("ground_truth_valid") is not True
                    for detail in detail_rows
                ):
                    status = PENDING
                    missing.append("valid_manual_feature_error_detail_missing")
                else:
                    outcome = "manual_event_and_feature_truth_available"
            elif requirement == "manual_pose_observation_and_event_boundary_review":
                covered = keypoint_cells.get(str(item["event_code"]), False)
                evidence.append(
                    {
                        "kind": "dense_keypoint_truth_cell",
                        "event_code": item["event_code"],
                        "covered": covered,
                    }
                )
                if not covered:
                    status = PENDING
                    missing.append("dense_adjudicated_keypoint_truth_missing")
                else:
                    outcome = "manual_event_and_dense_pose_truth_available"
            elif requirement == "manual_restabilization_phase_and_keypoint_truth":
                phase = "restabilization_onset_ms"
                phase_value = truth_event.get("key_phases_ms", {}).get(phase)
                covered = keypoint_cells.get(str(item["event_code"]), False)
                evidence.extend(
                    [
                        {
                            "kind": "manual_phase",
                            "phase_name": phase,
                            "timestamp_ms": phase_value,
                        },
                        {
                            "kind": "dense_keypoint_truth_cell",
                            "event_code": item["event_code"],
                            "covered": covered,
                        },
                    ]
                )
                if not isinstance(phase_value, int) or isinstance(phase_value, bool):
                    status = PENDING
                    missing.append(f"accepted_manual_phase_missing:{phase}")
                if not covered:
                    status = PENDING
                    missing.append("dense_adjudicated_keypoint_truth_missing")
                if not missing:
                    outcome = "manual_restabilization_phase_and_pose_truth_available"
            elif requirement.startswith("manual_scoring_context_truth:"):
                status = PENDING
                missing.append("unsupported_versioned_scoring_context_truth_required")

    return {
        "work_item_id": item["work_item_id"],
        "priority_rank": item["priority_rank"],
        "event_id": item["event_id"],
        "event_code": item["event_code"],
        "review_type": item["review_type"],
        "truth_requirement": requirement,
        "status": status,
        "evidence_outcome": outcome,
        "matched_manual_event_id": matched_event_id,
        "segment_iou": segment_iou,
        "evidence": evidence,
        "missing_evidence": sorted(set(missing)),
        "affected_indicator_instances": list(item["affected_indicator_instances"]),
    }


def build_scoring_truth_action_readiness(
    *,
    worklist_path: Path,
    truth_validation_path: Path,
    manual_events_path: Path,
    manual_keypoints_path: Path,
    manual_semantics_path: Path,
    scoring_truth_evaluation_path: Path,
    pose_truth_manifest_path: Path,
    pose_truth_evaluation_path: Path,
    reference_context_path: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    worklist = _read_json(worklist_path)
    validate_scoring_truth_action_worklist(worklist)
    video_id = str(worklist["source"]["video_id"])
    if str(worklist["source"]["scoring_reference_context"]["sha256"]).upper() != sha256_file(reference_context_path):
        raise ValueError("worklist does not bind supplied scoring reference context")

    truth_validation = _read_json(truth_validation_path)
    _assert_truth_validation_bindings(
        truth_validation,
        events_path=manual_events_path,
        keypoints_path=manual_keypoints_path,
        semantics_path=manual_semantics_path,
        video_id=video_id,
    )
    manual_events = _read_jsonl(manual_events_path)
    manual_keypoints = _read_jsonl(manual_keypoints_path)
    manual_semantics = _read_jsonl(manual_semantics_path)
    for event in manual_events:
        validate_event_record(event, annotation=True)
    for row in manual_keypoints:
        validate_keypoint_annotation(row)
    for row in manual_semantics:
        _validate_semantic_record(row, video_id=video_id)
    _assert_truth_validation_counts(
        truth_validation,
        manual_events=manual_events,
        manual_keypoints=manual_keypoints,
        manual_semantics=manual_semantics,
    )
    manual_events = [row for row in manual_events if row.get("video_id") == video_id]
    manual_keypoints = [row for row in manual_keypoints if row.get("video_id") == video_id]
    manual_semantics = [row for row in manual_semantics if row.get("video_id") == video_id]

    scoring_truth_evaluation = _read_json(scoring_truth_evaluation_path)
    _assert_feature_evaluation_bindings(
        scoring_truth_evaluation,
        worklist=worklist,
        truth_validation=truth_validation,
        manual_events_path=manual_events_path,
        manual_keypoints_path=manual_keypoints_path,
        manual_semantics_path=manual_semantics_path,
    )
    _recompute_scoring_truth_evaluation(
        scoring_truth_evaluation,
        manual_events=manual_events,
        manual_keypoints=manual_keypoints,
        manual_semantics=manual_semantics,
    )
    feature_evaluation = scoring_truth_evaluation.get("feature_evaluation", {})
    details = feature_evaluation.get("details", []) if isinstance(feature_evaluation, Mapping) else []
    feature_details: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in details if isinstance(details, list) else []:
        if not isinstance(row, Mapping):
            raise ValueError("feature evaluation detail must be an object")
        key = (str(row.get("prediction_event_id")), str(row.get("feature_name")))
        if key in feature_details:
            raise ValueError("duplicate feature evaluation detail")
        feature_details[key] = row

    pose_manifest = _read_json(pose_truth_manifest_path)
    queue, _ = validate_pose_diagnostic_truth_pack_sources(pose_manifest)
    pose_evaluation = _read_json(pose_truth_evaluation_path)
    validate_pose_diagnostic_evaluation(pose_evaluation)
    if pose_evaluation.get("source", {}).get("truth_pack_manifest_content_sha256") != canonical_hash(pose_manifest):
        raise ValueError("Pose diagnostic evaluation does not bind supplied manifest")
    if str(pose_manifest["source_queue"]["sha256"]).upper() != str(worklist["source"]["diagnostic_queue"]["sha256"]).upper():
        raise ValueError("Pose truth pack does not bind the worklist diagnostic queue")
    if queue.get("video_id") != video_id or pose_evaluation.get("video_id") != video_id:
        raise ValueError("Pose diagnostic truth video mismatch")
    coverage_path = Path(str(pose_evaluation["source"]["coverage"]["path"])).resolve()
    positives_path = Path(str(pose_evaluation["source"]["positives"]["path"])).resolve()
    recomputed_pose_evaluation = evaluate_pose_diagnostic_truth(
        manifest=pose_manifest,
        coverage_csv_text=coverage_path.read_text(encoding="utf-8"),
        positives_csv_text=positives_path.read_text(encoding="utf-8"),
        coverage_path=coverage_path,
        positives_path=positives_path,
        generated_at=pose_evaluation["generated_at"],
    )
    if pose_evaluation != recomputed_pose_evaluation:
        raise ValueError("Pose diagnostic evaluation differs from recomputation")

    context_payload = _read_json(reference_context_path)
    expected_video_sha = context_payload.get("video", {}).get("video_sha256")
    observations = validate_scoring_reference_context(
        context_payload,
        expected_video_id=video_id,
        expected_video_sha256=str(expected_video_sha),
    )

    predictions = _read_jsonl(Path(worklist["source"]["events"]["path"]))
    if sha256_file(Path(worklist["source"]["events"]["path"])) != str(worklist["source"]["events"]["sha256"]).upper():
        raise ValueError("worklist event source SHA mismatch")
    event_matches, event_evaluation = _event_matches(
        predictions=predictions, truth=manual_events
    )
    truth_by_id = {str(row["event_id"]): row for row in manual_events}

    keypoint_cells = {
        str(row["event_code"]): bool(row["covered"])
        for row in truth_validation.get("readiness", {}).get("keypoint_coverage", [])
        if row.get("video_id") == video_id
    }
    items = [
        _evaluate_item(
            item,
            pose_evaluation=pose_evaluation,
            event_matches=event_matches,
            truth_by_id=truth_by_id,
            semantics=manual_semantics,
            feature_details=feature_details,
            reference_context=observations,
            keypoint_cells=keypoint_cells,
        )
        for item in worklist["items"]
    ]

    items_by_id = {str(row["work_item_id"]): row for row in items}
    linked_by_instance: dict[str, list[str]] = defaultdict(list)
    for item in items:
        for instance_id in item["affected_indicator_instances"]:
            linked_by_instance[str(instance_id)].append(str(item["work_item_id"]))
    instances = []
    for instance_id in sorted(linked_by_instance):
        linked = sorted(linked_by_instance[instance_id])
        statuses = [str(items_by_id[item_id]["status"]) for item_id in linked]
        status = (
            SATISFIED
            if all(value == SATISFIED for value in statuses)
            else PARTIAL
            if any(value in {SATISFIED, PARTIAL} for value in statuses)
            else PENDING
        )
        event_id, indicator_id = instance_id.split("::", 1)
        instances.append(
            {
                "indicator_instance_id": instance_id,
                "event_id": event_id,
                "indicator_id": indicator_id,
                "linked_work_item_ids": linked,
                "status": status,
                "satisfied_work_item_count": sum(value == SATISFIED for value in statuses),
                "required_work_item_count": len(statuses),
            }
        )

    status_counts = Counter(str(item["status"]) for item in items)
    instance_status_counts = Counter(str(item["status"]) for item in instances)
    by_review: dict[str, dict[str, int]] = {}
    by_requirement: dict[str, dict[str, int]] = {}
    for field, destination in (("review_type", by_review), ("truth_requirement", by_requirement)):
        groups: dict[str, Counter[str]] = defaultdict(Counter)
        for item in items:
            groups[str(item[field])][str(item["status"])] += 1
        for name, counts in sorted(groups.items()):
            destination[name] = {
                "total": sum(counts.values()),
                SATISFIED: counts[SATISFIED],
                PARTIAL: counts[PARTIAL],
                PENDING: counts[PENDING],
            }

    status = (
        "evidence_satisfied_pending_scoring_recompute"
        if status_counts[SATISFIED] == len(items)
        else "review_in_progress"
        if status_counts[SATISFIED] or status_counts[PARTIAL]
        else "annotation_required"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "readiness_version": READINESS_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source": {
            "video_id": video_id,
            "worklist": _source(worklist_path),
            "truth_validation": _source(truth_validation_path),
            "manual_events": _source(manual_events_path),
            "manual_keypoints": _source(manual_keypoints_path),
            "manual_semantics": _source(manual_semantics_path),
            "scoring_truth_evaluation": _source(scoring_truth_evaluation_path),
            "pose_truth_manifest": _source(pose_truth_manifest_path),
            "pose_truth_evaluation": _source(pose_truth_evaluation_path),
            "scoring_reference_context": _source(reference_context_path),
            "event_matching": event_evaluation.get("matching"),
        },
        "counts": {
            "work_items": len(items),
            "by_status": {value: status_counts[value] for value in STATUSES},
            "indicator_instances": len(instances),
            "indicator_instances_by_status": {
                value: instance_status_counts[value] for value in STATUSES
            },
            "instance_action_links": sum(
                len(item["linked_work_item_ids"]) for item in instances
            ),
            "matched_manual_events": len(event_matches),
            "accepted_manual_events": len(manual_events),
            "accepted_manual_keypoint_frames": len(manual_keypoints),
            "accepted_manual_semantics": len(manual_semantics),
            "pose_full_timeline_diagnostic_types": sum(
                pose_evaluation["by_diagnostic_type"][name]["coverage_status"]
                == "full_timeline"
                for name in DIAGNOSTIC_TYPES
            ),
            "accepted_target_direction_observations": sum(
                row["status"] == "accepted" for row in observations.values()
            ),
            "by_review_type": by_review,
            "by_truth_requirement": by_requirement,
        },
        "items": items,
        "indicator_instances": instances,
        "safety": {
            "work_item_completion_is_score_recompute": False,
            "quality_gate_modified": False,
            "scoring_state_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "candidate_events_are_truth": False,
            "partial_review_is_adjudicated_truth": False,
            "coverage_is_accuracy": False,
            "maximum_status_without_calibration": "calibration_required",
        },
    }
    validate_scoring_truth_action_readiness(report)
    return report


def validate_scoring_truth_action_readiness(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported action readiness schema")
    if report.get("readiness_version") != READINESS_VERSION:
        raise ValueError("unsupported action readiness version")
    if report.get("status") not in {
        "annotation_required",
        "review_in_progress",
        "evidence_satisfied_pending_scoring_recompute",
    }:
        raise ValueError("invalid action readiness status")
    items = report.get("items")
    instances = report.get("indicator_instances")
    counts = report.get("counts")
    if not isinstance(items, list) or not items or not isinstance(instances, list) or not isinstance(counts, Mapping):
        raise ValueError("action readiness items/counts are missing")
    item_ids: set[str] = set()
    recomputed_status = Counter()
    links: dict[str, set[str]] = defaultdict(set)
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError("action readiness item must be an object")
        item_id = item.get("work_item_id")
        status = item.get("status")
        if not isinstance(item_id, str) or not item_id or item_id in item_ids:
            raise ValueError("action readiness work_item_id invalid or duplicate")
        item_ids.add(item_id)
        if status not in STATUSES:
            raise ValueError("action readiness item status invalid")
        if status == SATISFIED and item.get("missing_evidence"):
            raise ValueError("satisfied action item cannot have missing evidence")
        if status != SATISFIED and not item.get("missing_evidence"):
            raise ValueError("incomplete action item requires missing evidence")
        if item.get("priority_rank") != index + 1:
            raise ValueError("action readiness priority ranks are not contiguous")
        recomputed_status[str(status)] += 1
        for instance_id in item.get("affected_indicator_instances", []):
            links[str(instance_id)].add(item_id)
    if counts.get("work_items") != len(items):
        raise ValueError("action readiness work item count mismatch")
    if counts.get("by_status") != {value: recomputed_status[value] for value in STATUSES}:
        raise ValueError("action readiness status counts mismatch")
    expected_report_status = (
        "evidence_satisfied_pending_scoring_recompute"
        if recomputed_status[SATISFIED] == len(items)
        else "review_in_progress"
        if recomputed_status[SATISFIED] or recomputed_status[PARTIAL]
        else "annotation_required"
    )
    if report.get("status") != expected_report_status:
        raise ValueError("action readiness aggregate status mismatch")
    for field, count_field in (
        ("review_type", "by_review_type"),
        ("truth_requirement", "by_truth_requirement"),
    ):
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for item in items:
            grouped[str(item.get(field))][str(item["status"])] += 1
        expected_groups = {
            name: {
                "total": sum(values.values()),
                SATISFIED: values[SATISFIED],
                PARTIAL: values[PARTIAL],
                PENDING: values[PENDING],
            }
            for name, values in sorted(grouped.items())
        }
        if counts.get(count_field) != expected_groups:
            raise ValueError(f"action readiness {field} counts mismatch")
    if counts.get("indicator_instances") != len(instances):
        raise ValueError("action readiness indicator instance count mismatch")
    seen_instances: set[str] = set()
    instance_status = Counter()
    for row in instances:
        instance_id = row.get("indicator_instance_id")
        if not isinstance(instance_id, str) or instance_id in seen_instances:
            raise ValueError("action readiness indicator instance invalid or duplicate")
        seen_instances.add(instance_id)
        linked = set(row.get("linked_work_item_ids", []))
        if linked != links.get(instance_id, set()):
            raise ValueError("indicator instance work item links mismatch")
        statuses = [
            str(next(item["status"] for item in items if item["work_item_id"] == item_id))
            for item_id in linked
        ]
        expected = (
            SATISFIED
            if all(value == SATISFIED for value in statuses)
            else PARTIAL
            if any(value in {SATISFIED, PARTIAL} for value in statuses)
            else PENDING
        )
        if row.get("status") != expected:
            raise ValueError("indicator instance readiness status mismatch")
        if row.get("required_work_item_count") != len(linked):
            raise ValueError("indicator instance required action count mismatch")
        if row.get("satisfied_work_item_count") != sum(value == SATISFIED for value in statuses):
            raise ValueError("indicator instance satisfied action count mismatch")
        instance_status[expected] += 1
    if seen_instances != set(links):
        raise ValueError("indicator instance action coverage mismatch")
    if counts.get("indicator_instances_by_status") != {
        value: instance_status[value] for value in STATUSES
    }:
        raise ValueError("indicator instance readiness counts mismatch")
    if counts.get("instance_action_links") != sum(len(value) for value in links.values()):
        raise ValueError("action readiness link count mismatch")
    safety = report.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(name) is not False
        for name in (
            "work_item_completion_is_score_recompute",
            "quality_gate_modified",
            "scoring_state_modified",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
            "candidate_events_are_truth",
            "partial_review_is_adjudicated_truth",
            "coverage_is_accuracy",
        )
    ) or safety.get("maximum_status_without_calibration") != "calibration_required":
        raise ValueError("action readiness safety invariant failed")
