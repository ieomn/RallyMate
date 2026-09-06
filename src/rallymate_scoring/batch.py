from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from rallymate_events.schemas import validate_event_record
from rallymate_scoring.indicator_requirements import event_code_for_indicator
from rallymate_scoring.quality_policy import (
    evaluate_indicator_event_quality,
    indicator_event_quality_flags,
)
from rallymate_scoring.runtime_profile_binding import (
    RuntimeProfileBoundProductionCalibration,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.scoring_context import (
    TARGET_DIRECTION_INDICATOR_ID,
    TARGET_DIRECTION_MISSING_FLAG,
    bind_scoring_context_evidence_to_video,
    validate_resolved_target_direction_context,
)


def _required(record: dict[str, Any], names: set[str]) -> None:
    missing = names - set(record)
    if missing:
        raise ValueError(f"indicator feature record missing fields: {sorted(missing)}")


def _index_authoritative_records(
    records: list[Mapping[str, Any]] | None,
    *,
    id_field: str,
    source_name: str,
) -> dict[str, Mapping[str, Any]] | None:
    if records is None:
        return None
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"{source_name}[{index}] must be an object")
        record_id = record.get(id_field)
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(
                f"{source_name}[{index}].{id_field} must be a non-empty string"
            )
        if record_id in indexed:
            raise ValueError(f"duplicate {source_name} {id_field}: {record_id}")
        indexed[record_id] = record
    return indexed


def _authoritative_production_quality_flags(
    record: Mapping[str, Any],
    *,
    event_by_id: dict[str, Mapping[str, Any]] | None,
    indicator_by_id: dict[str, Mapping[str, Any]] | None,
    scoring_features: list[dict[str, Any]],
) -> list[str]:
    if event_by_id is None:
        raise ValueError(
            "runtime-profile-bound production scoring requires authoritative events"
        )
    if indicator_by_id is None:
        raise ValueError(
            "runtime-profile-bound production scoring requires authoritative "
            "feasibility indicators"
        )

    event_id = record["event_id"]
    event = event_by_id.get(event_id)
    if event is None:
        raise ValueError(
            f"authoritative event is missing for production record: {event_id}"
        )
    indicator_id = record["indicator_id"]
    indicator = indicator_by_id.get(indicator_id)
    if indicator is None:
        raise ValueError(
            "authoritative feasibility indicator is missing for production record: "
            f"{indicator_id}"
        )

    validate_event_record(dict(event))
    for field in ("video_id", "event_code", "person_track_id"):
        if event.get(field) != record.get(field):
            raise ValueError(
                "production indicator feature record does not exactly match "
                f"authoritative event {field}: {event_id}"
            )
    expected_event_code = event_code_for_indicator(indicator_id)
    if event.get("event_code") != expected_event_code:
        raise ValueError(
            "production indicator feature event_code does not match the "
            f"authoritative feasibility indicator: {indicator_id}"
        )
    if record.get("feasibility_level") != indicator.get("feasibility_level"):
        raise ValueError(
            "production indicator feature feasibility_level does not match the "
            f"authoritative feasibility indicator: {indicator_id}"
        )

    quality_flags = indicator_event_quality_flags(event, indicator)
    scoring_context = record.get("scoring_context")
    if indicator_id == TARGET_DIRECTION_INDICATOR_ID and scoring_context is not None:
        launch_direction_feature = next(
            (
                feature
                for feature in scoring_features
                if feature.get("feature_name") == "launch_direction_deg"
            ),
            None,
        )
        validate_resolved_target_direction_context(
            scoring_context,
            event=event,
            launch_direction_feature=launch_direction_feature,
        )
        if scoring_context.get("status") == "available":
            quality_flags = [
                flag
                for flag in quality_flags
                if flag != TARGET_DIRECTION_MISSING_FLAG
            ]
    return quality_flags


def score_indicator_records(
    records: list[dict[str, Any]],
    *,
    calibrations: dict[str, Mapping[str, Any]],
    model_versions: dict[str, str | None],
    allow_test_only: bool = False,
    authoritative_events: list[Mapping[str, Any]] | None = None,
    authoritative_indicators: list[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply versioned calibrations to event-level indicator feature records."""

    event_by_id = _index_authoritative_records(
        authoritative_events,
        id_field="event_id",
        source_name="authoritative_events",
    )
    indicator_by_id = _index_authoritative_records(
        authoritative_indicators,
        id_field="indicator_id",
        source_name="authoritative_indicators",
    )
    outputs = []
    for record in records:
        _required(
            record,
            {
                "video_id",
                "event_id",
                "event_code",
                "person_track_id",
                "indicator_id",
                "features",
            },
        )
        if not isinstance(record["features"], list) or not record["features"]:
            raise ValueError("indicator feature record features must be non-empty")
        scoring_context = record.get("scoring_context")
        if (
            scoring_context is not None
            and record["indicator_id"] != TARGET_DIRECTION_INDICATOR_ID
        ):
            raise ValueError("scoring_context is only valid for FS02-M02")
        calibration = calibrations.get(record["indicator_id"])
        runtime_bound_production = isinstance(
            calibration, RuntimeProfileBoundProductionCalibration
        )
        supplied_gate = record.get("quality_gate")
        if supplied_gate is None:
            if runtime_bound_production or not allow_test_only:
                raise ValueError(
                    "indicator feature record quality_gate is required for scoring"
                )
            quality_flags: list[str] = []
        else:
            if not isinstance(supplied_gate, dict):
                raise ValueError("indicator feature record quality_gate must be an object")
            quality_flags = supplied_gate.get("input_quality_flags")
            if not isinstance(quality_flags, list) or any(
                not isinstance(flag, str) or not flag for flag in quality_flags
            ):
                raise ValueError(
                    "indicator feature record quality_gate.input_quality_flags "
                    "must be a string list"
                )
        scoring_features = record.get("scoring_features", record["features"])
        if not isinstance(scoring_features, list) or not scoring_features:
            raise ValueError("indicator feature record scoring_features must be non-empty")
        if runtime_bound_production:
            authoritative_quality_flags = _authoritative_production_quality_flags(
                record,
                event_by_id=event_by_id,
                indicator_by_id=indicator_by_id,
                scoring_features=scoring_features,
            )
            if quality_flags != authoritative_quality_flags:
                raise ValueError(
                    "indicator feature record quality flags do not match the "
                    "authoritative event and feasibility indicator"
                )
            quality_flags = authoritative_quality_flags
        if supplied_gate is not None:
            expected_gate = evaluate_indicator_event_quality(
                record["indicator_id"], quality_flags
            )
            if supplied_gate != expected_gate:
                raise ValueError(
                    "indicator feature record quality_gate does not match the "
                    "versioned quality policy"
                )
        expected_feature_status = (
            "measured"
            if all(item.get("valid") is True for item in record["features"])
            and (supplied_gate is None or supplied_gate["measurement_allowed"])
            else "unavailable"
        )
        if record.get("feature_status") != expected_feature_status:
            raise ValueError(
                "indicator feature record feature_status disagrees with feature "
                "validity and quality gate"
            )
        expected_scoring_feature_status = (
            "measured"
            if all(item.get("valid") is True for item in scoring_features)
            else "unavailable"
        )
        if (
            record.get("scoring_feature_status", expected_scoring_feature_status)
            != expected_scoring_feature_status
        ):
            raise ValueError(
                "indicator feature record scoring_feature_status disagrees with "
                "scoring feature validity"
            )
        source_frames = sorted(
            {
                int(frame)
                for feature in scoring_features
                for frame in feature.get("source_frames", [])
            }
        )
        evidence = {
            "video_id": record["video_id"],
            "event_id": record["event_id"],
            "person_track_id": record["person_track_id"],
            "source_frames": source_frames,
        }
        provenance = record.get("provenance")
        if isinstance(provenance, dict):
            for name in ("video_path", "video_sha256", "frames_path", "frames_sha256"):
                if provenance.get(name) is not None:
                    evidence[name] = provenance[name]
        evidence_items = [evidence]
        if scoring_context is not None:
            evidence_items.append(
                bind_scoring_context_evidence_to_video(
                    scoring_context,
                    video_id=record["video_id"],
                    video_sha256=evidence.get("video_sha256"),
                )
            )
        result = score_indicator(
            indicator_id=record["indicator_id"],
            features=scoring_features,
            calibration=calibration,
            model_versions=model_versions,
            evidence=evidence_items,
            allow_test_only=allow_test_only,
            feasibility_level=record.get("feasibility_level"),
            event_quality_flags=quality_flags,
        )
        if scoring_context is not None:
            result["scoring_context"] = scoring_context
        outputs.append(
            {
                **result,
                "video_id": record["video_id"],
                "event_id": record["event_id"],
                "event_code": record["event_code"],
                "person_track_id": record["person_track_id"],
                "indicator_id": record["indicator_id"],
                "feasibility_level": record.get("feasibility_level"),
            }
        )
    status_counts = Counter(item["status"] for item in outputs)
    grade_counts = Counter(
        item["grade"] for item in outputs if item.get("grade") is not None
    )
    scored_without_gate = [
        item
        for item in outputs
        if item["status"] == "scored"
        and not {
            "independent_test_passed",
            "test_only_override",
        }.intersection(item["reason_codes"])
    ]
    return outputs, {
        "schema_version": "1.0.0",
        "record_count": len(outputs),
        "status_counts": dict(status_counts),
        "grade_counts": dict(grade_counts),
        "calibrated_indicator_ids": sorted(calibrations),
        "safety": {
            "generated_thresholds": False,
            "allow_test_only": allow_test_only,
            "scored_without_independent_test_or_test_override": len(
                scored_without_gate
            ),
        },
    }
