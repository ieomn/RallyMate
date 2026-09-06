from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rallymate_events.schemas import load_event_annotations
from rallymate_features import (
    FEATURE_LIBRARY_VERSION,
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_scoring.calibration_dataset import canonical_sha256, file_sha256
from rallymate_scoring.feasibility import (
    load_feasibility_registry,
    measurement_feature_names,
)
from rallymate_scoring.quality_policy import (
    QUALITY_POLICY_VERSION,
    indicator_event_quality_flags,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.scoring_context import (
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
    TARGET_DIRECTION_INDICATOR_ID,
    build_target_direction_alignment_feature,
)
from rallymate_tracking import (
    diagnose_primary_timeline,
    primary_timeline_algorithm_version,
    registry_required_primary_player_version,
)


MANUAL_EVENT_FEATURE_BUILD_VERSION = "manual-event-features-v1.1.0"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL records must be objects: {path}:{line_number}")
            records.append(value)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _compact_feature(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_name": item["feature_name"],
        "feature_version": item["feature_version"],
        "value": item["value"],
        "unit": item["unit"],
        "valid": item["valid"],
        "confidence": item["confidence"],
        "reason": item["reason"],
        "source_frames": item["source_frames"],
    }


def _pose_versions(
    pose_model: dict[str, Any] | None,
    registry_version: str,
    primary_player_version: str,
) -> dict[str, Any]:
    pose_model = pose_model or {}
    return {
        "pose_backend": str(pose_model.get("backend", "unknown")),
        "pose_runtime": str(pose_model.get("runtime", "unknown")),
        "pose_profile": str(pose_model.get("profile", "unknown")),
        "pose_model_sha256": pose_model.get("model_sha256"),
        "native_keypoint_format": pose_model.get("native_keypoint_format"),
        "native_keypoint_count": pose_model.get("native_keypoint_count"),
        "primary_player": primary_player_version,
        "event": "accepted-manual-event-truth-v1",
        "feature": FEATURE_LIBRARY_VERSION,
        "quality_policy": QUALITY_POLICY_VERSION,
        "feasibility_registry": registry_version,
        "calibration": None,
    }


def build_manual_event_features(
    *,
    frames_path: str | Path,
    primary_timeline_path: str | Path,
    manual_events_path: str | Path,
    feasibility_registry_path: str | Path,
    video_id: str,
    output_dir: str | Path,
    pose_model: dict[str, Any] | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure registry features on accepted manual boundaries.

    This function never invokes event detection or overlap matching. Event IDs, bounds,
    phases and person identity come only from accepted manual-event records, making its
    indicator-features output safe for the calibration dataset compiler's exact join.
    """

    if not isinstance(video_id, str) or not video_id:
        raise ValueError("video_id must be a non-empty string")
    frames_path = Path(frames_path).resolve()
    primary_timeline_path = Path(primary_timeline_path).resolve()
    manual_events_path = Path(manual_events_path).resolve()
    registry_path = Path(feasibility_registry_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records = _load_jsonl(frames_path)
    timeline = _load_jsonl(primary_timeline_path)
    registry = load_feasibility_registry(registry_path)
    required_primary_version = registry_required_primary_player_version(
        registry["indicators"]
    )
    primary_player_version = primary_timeline_algorithm_version(
        timeline,
        expected_version=required_primary_version,
        require_declared=required_primary_version is not None,
    )
    all_events = load_event_annotations(manual_events_path)
    foreign_video_ids = sorted(
        {event["video_id"] for event in all_events if event["video_id"] != video_id}
    )
    if foreign_video_ids:
        raise ValueError(
            "single-video manual feature build rejects manual events from other videos; "
            f"use the truth pack by-video artifact instead: {foreign_video_ids}"
        )
    selected_events = list(all_events)
    candidate_only_flags = {"event_ground_truth_missing", "provisional_rule_baseline"}
    contaminated = [
        event["event_id"]
        for event in selected_events
        if candidate_only_flags & set(event.get("quality_flags", []))
    ]
    if contaminated:
        raise ValueError(
            "accepted manual events must not inherit candidate-only quality flags: "
            f"{contaminated}"
        )
    event_ids = [event["event_id"] for event in selected_events]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("manual event_id values must be unique within a video")
    selected_events.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["event_id"]))

    known_primary_ids = {
        int(item["primary_player_id"])
        for item in timeline
        if isinstance(item.get("primary_player_id"), int)
    }
    for event in selected_events:
        if event["person_track_id"] not in known_primary_ids:
            raise ValueError(
                "manual event person_track_id is not represented by the primary-player timeline: "
                f"{event['event_id']}:{event['person_track_id']}"
            )

    indicators_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    required_by_event: dict[str, set[str]] = defaultdict(set)
    for indicator in registry["indicators"]:
        event_code = indicator["indicator_id"].split("-M", 1)[0]
        indicators_by_event[event_code].append(indicator)
        required_by_event[event_code].update(measurement_feature_names(indicator))

    source_files = {
        "frames": {"path": str(frames_path), "sha256": file_sha256(frames_path)},
        "primary_timeline": {
            "path": str(primary_timeline_path),
            "sha256": file_sha256(primary_timeline_path),
        },
        "manual_events": {
            "path": str(manual_events_path),
            "sha256": file_sha256(manual_events_path),
        },
        "feasibility_registry": {
            "path": str(registry_path),
            "sha256": file_sha256(registry_path),
        },
    }
    lineage_root_sha = canonical_sha256(
        {key: value["sha256"] for key, value in sorted(source_files.items())}
    )
    versions = _pose_versions(
        pose_model, registry["registry_version"], primary_player_version
    )
    base_provenance = {
        "build_version": MANUAL_EVENT_FEATURE_BUILD_VERSION,
        "event_source": "accepted_manual_truth",
        "candidate_event_detector_used": False,
        "lineage_root_sha256": lineage_root_sha,
        "source_files": source_files,
        "model_versions": versions,
        **(source_provenance or {}),
    }
    sequence = pose_sequence_from_records(records, timeline)
    emitted_events: list[dict[str, Any]] = []
    feature_records: list[dict[str, Any]] = []
    feature_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for event in selected_events:
        if event["event_code"] not in indicators_by_event:
            continue
        manual_event_sha = canonical_sha256(event)
        diagnostics = diagnose_primary_timeline(
            records,
            timeline,
            start_ms=int(event["start_ms"]),
            end_ms=int(event["end_ms"]),
        )
        emitted_event = {
            **event,
            "quality_flags": sorted(
                set(event.get("quality_flags", [])) | set(diagnostics["quality_flags"])
            ),
            "track_diagnostics": diagnostics,
            "measurement_lineage": {
                "manual_event_sha256": manual_event_sha,
                "lineage_root_sha256": lineage_root_sha,
                "candidate_event_detector_used": False,
            },
        }
        emitted_events.append(emitted_event)
        interval = EventInterval(
            event_id=event["event_id"],
            event_code=event["event_code"],
            start_ms=event["start_ms"],
            end_ms=event["end_ms"],
            person_track_id=event["person_track_id"],
            key_phases=event["key_phases_ms"],
        )
        for result in compute_event_features(
            sequence, interval, sorted(required_by_event[event["event_code"]])
        ):
            payload = result.to_dict()
            payload.update(
                {
                    "schema_version": "1.0.0",
                    "video_id": video_id,
                    "event_id": event["event_id"],
                    "event_code": event["event_code"],
                    "person_track_id": event["person_track_id"],
                }
            )
            payload["provenance"].update(
                {
                    **base_provenance,
                    "manual_event_sha256": manual_event_sha,
                    "manual_boundary_ms": {
                        "start_ms": event["start_ms"],
                        "end_ms": event["end_ms"],
                    },
                    "manual_key_phases_ms": event["key_phases_ms"],
                }
            )
            feature_records.append(payload)
            feature_lookup[(event["event_id"], result.feature_name)] = payload

    indicator_records: list[dict[str, Any]] = []
    score_records: list[dict[str, Any]] = []
    for event in emitted_events:
        manual_event_sha = event["measurement_lineage"]["manual_event_sha256"]
        for indicator in indicators_by_event[event["event_code"]]:
            compact_features = [
                _compact_feature(feature_lookup[(event["event_id"], name)])
                for name in measurement_feature_names(indicator)
            ]
            scoring_feature_by_name = {
                item["feature_name"]: item for item in compact_features
            }
            if indicator["indicator_id"] == TARGET_DIRECTION_INDICATOR_ID:
                scoring_feature_by_name[TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME] = (
                    _compact_feature(
                        build_target_direction_alignment_feature(
                            launch_direction_feature=feature_lookup.get(
                                (event["event_id"], "launch_direction_deg")
                            ),
                            target_direction_deg=None,
                            reference_confidence=None,
                            unavailable_reason="manual_target_direction_required",
                        )
                    )
                )
            scoring_features = [
                scoring_feature_by_name[name]
                for name in indicator["required_features"]
            ]
            source_frames = sorted(
                {
                    frame
                    for item in compact_features
                    for frame in item.get("source_frames", [])
                }
            )
            indicator_quality_flags = indicator_event_quality_flags(
                event, indicator
            )
            score = score_indicator(
                indicator_id=indicator["indicator_id"],
                features=scoring_features,
                calibration=None,
                model_versions=versions,
                evidence=[
                    {
                        "video_id": video_id,
                        "event_id": event["event_id"],
                        "person_track_id": event["person_track_id"],
                        "source_frames": source_frames,
                        "manual_event_sha256": manual_event_sha,
                    }
                ],
                feasibility_level=indicator["feasibility_level"],
                event_quality_flags=indicator_quality_flags,
            )
            measurement_allowed = (
                all(item["valid"] for item in compact_features)
                and score["quality_gate"]["measurement_allowed"]
            )
            common = {
                "video_id": video_id,
                "indicator_id": indicator["indicator_id"],
                "feasibility_level": indicator["feasibility_level"],
                "event_id": event["event_id"],
                "event_code": event["event_code"],
                "person_track_id": event["person_track_id"],
            }
            indicator_records.append(
                {
                    "schema_version": "1.0.0",
                    **common,
                    "feature_status": "measured" if measurement_allowed else "unavailable",
                    "features": compact_features,
                    "scoring_feature_status": (
                        "measured"
                        if all(item["valid"] for item in scoring_features)
                        else "unavailable"
                    ),
                    "scoring_features": scoring_features,
                    "supplemental_features": [],
                    "supplemental_feature_status": "not_applicable",
                    "scoring_status": score["status"],
                    "grade": None,
                    "reason_codes": score["reason_codes"],
                    "quality_gate": score["quality_gate"],
                    "provenance": {
                        **base_provenance,
                        "manual_event_sha256": manual_event_sha,
                        "manual_event_id": event["event_id"],
                        "feature_record_sha256_by_name": {
                            item["feature_name"]: canonical_sha256(
                                feature_lookup[(event["event_id"], item["feature_name"])]
                            )
                            for item in compact_features
                        },
                    },
                }
            )
            score_records.append({**score, **common})

    events_path = output_dir / "events.jsonl"
    features_path = output_dir / "features.jsonl"
    indicators_path = output_dir / "indicator-features.jsonl"
    scores_path = output_dir / "scores.jsonl"
    _write_jsonl(events_path, emitted_events)
    _write_jsonl(features_path, feature_records)
    _write_jsonl(indicators_path, indicator_records)
    _write_jsonl(scores_path, score_records)
    summary = {
        "schema_version": "1.0.0",
        "build_version": MANUAL_EVENT_FEATURE_BUILD_VERSION,
        "video_id": video_id,
        "status": (
            "manual_event_features_measured_calibration_required"
            if emitted_events
            else "annotation_required_no_accepted_manual_events"
        ),
        "registry_version": registry["registry_version"],
        "indicator_count": len(registry["indicators"]),
        "event_counts": dict(Counter(item["event_code"] for item in emitted_events)),
        "indicator_record_count": len(indicator_records),
        "feature_record_count": len(feature_records),
        "score_status_counts": dict(Counter(item["status"] for item in score_records)),
        "grade_counts": {},
        "lineage_root_sha256": lineage_root_sha,
        "source_files": source_files,
        "model_versions": versions,
        "artifacts": {
            "events_jsonl": str(events_path),
            "features_jsonl": str(features_path),
            "indicator_features_jsonl": str(indicators_path),
            "scores_jsonl": str(scores_path),
        },
        "safety": {
            "candidate_event_detector_used": False,
            "candidate_boundaries_promoted_to_truth": False,
            "generated_thresholds": False,
            "any_non_null_grade": any(item.get("grade") is not None for item in score_records),
            "automatic_F3_or_F4_promotion": False,
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    clear_feature_cache(sequence)
    return {
        "summary": summary,
        "events": emitted_events,
        "features": feature_records,
        "indicator_records": indicator_records,
        "scores": score_records,
    }


__all__ = ["MANUAL_EVENT_FEATURE_BUILD_VERSION", "build_manual_event_features"]
