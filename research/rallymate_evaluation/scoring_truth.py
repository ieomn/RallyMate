from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from rallymate_evaluation.feature_errors import evaluate_feature_errors
from rallymate_evaluation.ground_truth import (
    apply_keypoint_corrections,
    validate_keypoint_annotation,
)
from rallymate_events import evaluate_events
from rallymate_events.schemas import validate_event_record
from rallymate_features import pose_sequence_from_records
from rallymate_scoring.feasibility import (
    measurement_feature_names,
    validate_feasibility_registry,
)
from rallymate_scoring.scoring_context import SCORING_CONTEXT_FEATURE_DEFINITIONS


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc


def _parse_jsonl(raw: bytes, *, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{label} line {line_number} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"{label} line {line_number} must be a JSON object")
        records.append(record)
    return records


def _freeze_input(
    path: Path,
    *,
    label: str,
    parser: Callable[[bytes], Any],
) -> tuple[Any, str]:
    raw = path.read_bytes()
    parsed = parser(raw)
    return parsed, hashlib.sha256(raw).hexdigest().upper()


def build_scoring_truth_evaluation(
    *,
    frames_path: Path,
    primary_timeline_path: Path,
    predicted_events_path: Path,
    registry_path: Path,
    manual_events_path: Path | None = None,
    manual_keypoints_path: Path | None = None,
    manual_semantics_path: Path | None = None,
) -> dict[str, Any]:
    required = [frames_path, primary_timeline_path, predicted_events_path, registry_path]
    optional = [manual_events_path, manual_keypoints_path, manual_semantics_path]
    for path in [*required, *(value for value in optional if value is not None)]:
        if not path.is_file():
            raise ValueError(f"scoring truth input is missing: {path}")
    frames_path = frames_path.resolve()
    primary_timeline_path = primary_timeline_path.resolve()
    predicted_events_path = predicted_events_path.resolve()
    registry_path = registry_path.resolve()
    manual_events_path = manual_events_path.resolve() if manual_events_path else None
    manual_keypoints_path = (
        manual_keypoints_path.resolve() if manual_keypoints_path else None
    )
    manual_semantics_path = (
        manual_semantics_path.resolve() if manual_semantics_path else None
    )

    records, frames_sha256 = _freeze_input(
        frames_path,
        label="frames",
        parser=lambda raw: _parse_jsonl(raw, label="frames"),
    )
    timeline, primary_timeline_sha256 = _freeze_input(
        primary_timeline_path,
        label="primary timeline",
        parser=lambda raw: _parse_jsonl(raw, label="primary timeline"),
    )
    predictions, predicted_events_sha256 = _freeze_input(
        predicted_events_path,
        label="predicted events",
        parser=lambda raw: _parse_jsonl(raw, label="predicted events"),
    )
    if manual_events_path:
        truth_events, manual_events_sha256 = _freeze_input(
            manual_events_path,
            label="manual events",
            parser=lambda raw: _parse_jsonl(raw, label="manual events"),
        )
        for record in truth_events:
            validate_event_record(record, annotation=True)
    else:
        truth_events = None
        manual_events_sha256 = None
    if manual_keypoints_path:
        annotations, manual_keypoints_sha256 = _freeze_input(
            manual_keypoints_path,
            label="manual keypoints",
            parser=lambda raw: _parse_jsonl(raw, label="manual keypoints"),
        )
        for record in annotations:
            validate_keypoint_annotation(record)
    else:
        annotations = None
        manual_keypoints_sha256 = None
    if manual_semantics_path:
        semantics, manual_semantics_sha256 = _freeze_input(
            manual_semantics_path,
            label="manual semantics",
            parser=lambda raw: _parse_jsonl(raw, label="manual semantics"),
        )
    else:
        semantics = []
        manual_semantics_sha256 = None
    registry, registry_sha256 = _freeze_input(
        registry_path,
        label="feasibility registry",
        parser=lambda raw: _parse_json(raw, label="feasibility registry"),
    )
    if not isinstance(registry, dict):
        raise ValueError("feasibility registry must be a JSON object")
    validate_feasibility_registry(registry)
    sequence = pose_sequence_from_records(records, timeline)
    corrected = apply_keypoint_corrections(sequence, annotations) if annotations else None
    feature_names_by_event: dict[str, set[str]] = defaultdict(set)
    context_feature_names_by_event: dict[str, set[str]] = defaultdict(set)
    for indicator in registry["indicators"]:
        event_code = indicator["indicator_id"].split("-")[0]
        measurement_names = measurement_feature_names(indicator)
        feature_names_by_event[event_code].update(measurement_names)
        context_names = [
            name
            for name in indicator["required_features"]
            if name not in measurement_names
        ]
        unknown = sorted(
            name
            for name in context_names
            if name not in SCORING_CONTEXT_FEATURE_DEFINITIONS
        )
        if unknown:
            raise ValueError(
                f"{indicator['indicator_id']} has unsupported context features: {unknown}"
            )
        context_feature_names_by_event[event_code].update(context_names)
    feature_result = evaluate_feature_errors(
        sequence,
        corrected,
        predictions,
        truth_events,
        {code: sorted(values) for code, values in feature_names_by_event.items()},
        context_feature_names_by_event={
            code: sorted(values)
            for code, values in context_feature_names_by_event.items()
            if values
        },
        semantic_ground_truth=semantics,
    )
    required_context_features = sorted(
        {
            feature_name
            for values in context_feature_names_by_event.values()
            for feature_name in values
        }
    )
    context_metrics = feature_result.get("feature_metrics", {})
    context_feature_truth_complete = all(
        isinstance(context_metrics.get(feature_name), dict)
        and context_metrics[feature_name].get("overall", {}).get("eligible_count", 0)
        > 0
        and context_metrics[feature_name].get("overall", {}).get("valid_count")
        == context_metrics[feature_name].get("overall", {}).get("eligible_count")
        for feature_name in required_context_features
    )
    event_result = (
        evaluate_events(predictions, truth_events)
        if truth_events
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
    return {
        "schema_version": "1.0.0",
        "status": (
            "evaluated"
            if event_result["status"] == "evaluated"
            and feature_result["status"] == "evaluated"
            else "evaluated_with_incomplete_feature_truth"
            if event_result["status"] == "evaluated"
            and feature_result["status"]
            == "evaluated_with_incomplete_feature_truth"
            else "partial_event_only"
            if event_result["status"] == "evaluated"
            else "ground_truth_required"
        ),
        "inputs": {
            "frames": str(frames_path),
            "primary_timeline": str(primary_timeline_path),
            "predicted_events": str(predicted_events_path),
            "manual_events": str(manual_events_path) if manual_events_path else None,
            "manual_keypoints": (
                str(manual_keypoints_path) if manual_keypoints_path else None
            ),
            "manual_semantics": (
                str(manual_semantics_path) if manual_semantics_path else None
            ),
            "registry": str(registry_path),
            "registry_version": registry["registry_version"],
            "sha256": {
                "frames": frames_sha256,
                "primary_timeline": primary_timeline_sha256,
                "predicted_events": predicted_events_sha256,
                "manual_events": manual_events_sha256,
                "manual_keypoints": manual_keypoints_sha256,
                "manual_semantics": manual_semantics_sha256,
                "registry": registry_sha256,
            },
        },
        "event_evaluation": event_result,
        "feature_evaluation": feature_result,
        "promotion_guard": {
            "F2_to_F3_automatic": False,
            "reason": "review_error_against_potential_grade_separation_and_acceptance_metrics",
            "feature_truth_complete": feature_result["status"] == "evaluated",
            "context_feature_truth_required": bool(
                any(context_feature_names_by_event.values())
            ),
            "required_context_features": required_context_features,
            "manual_semantics_provided": bool(manual_semantics_path),
            "manual_semantic_record_count": len(semantics),
            "context_feature_truth_complete": context_feature_truth_complete,
        },
    }
