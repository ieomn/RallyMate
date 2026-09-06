from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rallymate_events import (
    EVENT_DETECTOR_VERSION,
    PHASE_CANDIDATE_VERSION,
    detect_pose_events,
)
from rallymate_evaluation.feature_errors import evaluate_feature_errors
from rallymate_features import (
    FEATURE_LIBRARY_VERSION,
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_features.fs01_fs02_features import FS01_FS02_FEATURE_VERSION
from rallymate_features.fs09_features import FS09_FEATURE_VERSION
from rallymate_scoring.feasibility import (
    load_feasibility_registry,
    measurement_feature_names,
    validate_feasibility_registry,
)
from rallymate_scoring.quality_policy import (
    QUALITY_POLICY_VERSION,
    indicator_event_quality_flags,
)
from rallymate_scoring.scoring_context import (
    SCORING_REFERENCE_CONTEXT_VERSION,
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
    TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION,
    TARGET_DIRECTION_EVENT_CODE,
    TARGET_DIRECTION_INDICATOR_ID,
    TARGET_DIRECTION_MISSING_FLAG,
    bind_scoring_context_evidence_to_video,
    compact_target_direction_alignment_feature,
    load_scoring_reference_context,
    resolve_target_direction_context,
)
from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.runtime_profile_binding import (
    bind_production_calibrations_for_runtime,
)
from rallymate_tracking import (
    PRIMARY_PLAYER_ALGORITHM_VERSION,
    diagnose_primary_timeline,
    primary_timeline_algorithm_version,
    registry_required_primary_player_version,
)


SCORING_LOOP_VERSION = "minimum-scoring-loop-v0.7.0"
EVENT_CONTRACT_VERSION = "events.1.0.0"
PRIMARY_PLAYER_VERSION = PRIMARY_PLAYER_ALGORITHM_VERSION
SUPPLEMENTAL_FINE_FOOT_SEMANTICS = (
    "fine_foot_keypoint_evidence_not_A_to_E_threshold"
)
SUPPLEMENTAL_FEATURES_BY_INDICATOR = {
    "FS01-M02": [
        "left_ankle_shank_foot_angle_deg",
        "right_ankle_shank_foot_angle_deg",
    ],
    "FS09-M03": [
        "left_ankle_shank_foot_angle_deg",
        "right_ankle_shank_foot_angle_deg",
    ],
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _implemented_feature_contract(indicator: dict[str, Any]) -> str:
    contracts: set[str] = set()
    measured_names = measurement_feature_names(indicator)
    for name in measured_names:
        definition = FEATURE_DEFINITIONS.get(name)
        if definition is None:
            raise ValueError(f"implemented feature definition is missing: {name}")
        version = definition["version"]
        if version == FS01_FS02_FEATURE_VERSION:
            contracts.add(FS01_FS02_FEATURE_VERSION)
        elif version == FS09_FEATURE_VERSION:
            contracts.add(FS09_FEATURE_VERSION)
        else:
            contracts.add(FEATURE_LIBRARY_VERSION)
    context_names = set(indicator["required_features"]) - set(measured_names)
    if context_names:
        if context_names != {TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME}:
            raise ValueError(
                f"unsupported context feature contract: {sorted(context_names)}"
            )
        if (
            indicator.get("versions", {}).get("scoring_context_feature")
            != TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION
        ):
            raise ValueError("target-direction context feature version mismatch")
    expected = indicator["versions"]["feature_contract"]
    allowed = {FEATURE_LIBRARY_VERSION, expected}
    if not contracts.issubset(allowed) or expected not in contracts:
        raise ValueError(
            f"indicator {indicator['indicator_id']} implemented feature families "
            f"{sorted(contracts)} do not realize registry contract {expected!r}"
        )
    # Composite FS09 metrics intentionally reuse stable base geometry and the
    # provisional stability envelope.  Their metric-level contract is the
    # event proxy family, not a claim that every pure function has one version.
    return expected


def _model_versions(
    pose_model: dict[str, Any] | None,
    feasibility_version: str,
    primary_player_version: str,
    indicators: list[dict[str, Any]],
    calibrations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pose_model = pose_model or {}
    return {
        "pose_backend": str(pose_model.get("backend", "unknown")),
        "pose_runtime": str(pose_model.get("runtime", "unknown")),
        "pose_profile": str(pose_model.get("profile", "unknown")),
        "pose_model_sha256": pose_model.get("model_sha256"),
        "native_keypoint_format": pose_model.get("native_keypoint_format"),
        "native_keypoint_count": pose_model.get("native_keypoint_count"),
        "keypoint_schema_version": pose_model.get("keypoint_schema_version"),
        "indicator_definition": {
            item["indicator_id"]: item["versions"]["indicator_definition"]
            for item in indicators
        },
        "event_contract": EVENT_CONTRACT_VERSION,
        "primary_player": primary_player_version,
        "event": EVENT_DETECTOR_VERSION,
        "phase_contract": PHASE_CANDIDATE_VERSION,
        "feature": FEATURE_LIBRARY_VERSION,
        "feature_contract": {
            item["indicator_id"]: _implemented_feature_contract(item)
            for item in indicators
        },
        "quality_policy": QUALITY_POLICY_VERSION,
        "feasibility_registry": feasibility_version,
        "calibration": {
            indicator_id: payload.get("threshold_version")
            or payload.get("model_version")
            for indicator_id, payload in sorted((calibrations or {}).items())
        }
        or None,
    }


def run_minimum_scoring_loop(
    *,
    frames_path: str | Path,
    primary_timeline_path: str | Path,
    output_dir: str | Path,
    feasibility_registry_path: str | Path,
    feasibility_registry: dict[str, Any] | None = None,
    source_id: str,
    video_id: str | None = None,
    pose_model: dict[str, Any] | None = None,
    source_provenance: dict[str, Any] | None = None,
    calibrations: dict[str, Any] | None = None,
    runtime_binding_registry: dict[str, Any] | None = None,
    runtime_view_evidence: dict[str, Any] | None = None,
    scoring_reference_context_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the fixed-camera, single-primary-player Pose-only maturity loop.

    ``calibrations`` is intentionally optional. When it is absent, every valid
    indicator is returned as ``calibration_required`` and no grade is emitted.
    A grade is an event-level, single-indicator result only; this loop does not
    define an aggregate score or aggregate grade.
    """

    frames_path = Path(frames_path)
    primary_timeline_path = Path(primary_timeline_path)
    output_dir = Path(output_dir)
    if feasibility_registry is None:
        registry = load_feasibility_registry(feasibility_registry_path)
    else:
        registry = feasibility_registry
        validate_feasibility_registry(registry)
    indicators = registry["indicators"]
    required_by_event: dict[str, set[str]] = defaultdict(set)
    indicators_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for indicator in indicators:
        event_code = indicator["indicator_id"].split("-")[0]
        required_by_event[event_code].update(measurement_feature_names(indicator))
        required_by_event[event_code].update(
            SUPPLEMENTAL_FEATURES_BY_INDICATOR.get(indicator["indicator_id"], [])
        )
        indicators_by_event[event_code].append(indicator)
    if scoring_reference_context_path is not None and any(
        indicator["indicator_id"] == TARGET_DIRECTION_INDICATOR_ID
        for indicator in indicators
    ):
        # This is the observed image-plane direction used only to compare with
        # an independently supplied target.  It is not inferred as the target.
        required_by_event[TARGET_DIRECTION_EVENT_CODE].add("launch_direction_deg")

    records = _load_jsonl(frames_path)
    timeline = _load_jsonl(primary_timeline_path)
    required_primary_version = registry_required_primary_player_version(indicators)
    primary_player_version = primary_timeline_algorithm_version(
        timeline,
        expected_version=required_primary_version,
        require_declared=required_primary_version is not None,
    )
    sequence = pose_sequence_from_records(records, timeline)
    canonical_video_id = video_id or source_id
    provenance = {
        "scoring_loop_version": SCORING_LOOP_VERSION,
        **(source_provenance or {}),
    }
    versions = _model_versions(
        pose_model,
        registry["registry_version"],
        primary_player_version,
        indicators,
        calibrations,
    )
    effective_calibrations = dict(calibrations or {})
    production_calibrations = {
        indicator_id: calibration
        for indicator_id, calibration in effective_calibrations.items()
        if calibration.get("artifact_scope") == "production"
    }
    if production_calibrations:
        if runtime_binding_registry is None or runtime_view_evidence is None:
            raise ValueError(
                "production scoring requires trusted runtime-profile bindings and "
                "accepted per-video view evidence"
            )
        video_sha256 = provenance.get("video_sha256")
        if not isinstance(video_sha256, str) or len(video_sha256) != 64:
            raise ValueError(
                "production scoring source_provenance.video_sha256 is required"
            )
        if runtime_view_evidence.get("video_id") != canonical_video_id:
            raise ValueError(
                "runtime view evidence video_id does not match the scoring video"
            )
        if str(runtime_view_evidence.get("video_sha256", "")).lower() != (
            video_sha256.lower()
        ):
            raise ValueError(
                "runtime view evidence video_sha256 does not match source provenance"
            )
        video_path = provenance.get("video_path")
        if isinstance(video_path, str) and Path(video_path).is_file():
            if _sha256(Path(video_path)).lower() != video_sha256.lower():
                raise ValueError(
                    "source video content changed or provenance SHA-256 is invalid"
                )
        effective_calibrations.update(
            bind_production_calibrations_for_runtime(
                production_calibrations,
                binding_registry=runtime_binding_registry,
                view_evidence=runtime_view_evidence,
                feasibility_registry=registry,
                model_versions=versions,
            )
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    events = detect_pose_events(
        sequence, source_id=source_id, video_id=canonical_video_id
    )
    for event in events:
        diagnostics = diagnose_primary_timeline(
            records,
            timeline,
            start_ms=int(event["start_ms"]),
            end_ms=int(event["end_ms"]),
        )
        event["track_diagnostics"] = diagnostics
        event["quality_flags"] = sorted(
            set(event["quality_flags"]) | set(diagnostics["quality_flags"])
        )
    _write_jsonl(output_dir / "events.jsonl", events)

    reference_context_payload: dict[str, Any] | None = None
    reference_observations: dict[str, dict[str, Any]] = {}
    reference_context_sha256: str | None = None
    if scoring_reference_context_path is not None:
        video_sha256 = provenance.get("video_sha256")
        if not isinstance(video_sha256, str) or len(video_sha256) != 64:
            raise ValueError(
                "scoring reference context requires source_provenance.video_sha256"
            )
        (
            reference_context_payload,
            reference_observations,
            reference_context_sha256,
        ) = load_scoring_reference_context(
            scoring_reference_context_path,
            expected_video_id=canonical_video_id,
            expected_video_sha256=video_sha256,
        )
        events_by_id = {event["event_id"]: event for event in events}
        observations_by_current_event: dict[str, dict[str, Any]] = {}
        window_start_ms = int(sequence.timestamp_ms[0]) if sequence.timestamp_ms.size else 0
        window_end_ms = int(sequence.timestamp_ms[-1]) if sequence.timestamp_ms.size else 0
        for event_id, observation in reference_observations.items():
            event = events_by_id.get(event_id)
            if event is None:
                boundary_matches = [
                    candidate
                    for candidate in events
                    if candidate.get("event_code") == observation.get("event_code")
                    and candidate.get("start_ms") == observation.get("start_ms")
                    and candidate.get("end_ms") == observation.get("end_ms")
                ]
                if len(boundary_matches) != 1:
                    if observation.get("status") != "accepted":
                        # Draft/unobservable rows cannot change a score.  They
                        # may safely be stale after a fresh detector run; the
                        # current events will remain explicitly missing and a
                        # new worklist can be generated from this run.
                        continue
                    if (
                        int(observation["end_ms"]) < window_start_ms
                        or int(observation["start_ms"]) > window_end_ms
                    ):
                        # A full-video context may safely accompany a cropped
                        # run.  Only observations completely outside the
                        # analyzed timestamp window are ignored.
                        continue
                    raise ValueError(
                        "scoring reference context event cannot be uniquely remapped "
                        f"by exact boundaries: {event_id}"
                    )
                event = boundary_matches[0]
            if (
                event.get("event_code") != observation.get("event_code")
                or event.get("start_ms") != observation.get("start_ms")
                or event.get("end_ms") != observation.get("end_ms")
            ):
                raise ValueError(
                    f"scoring reference context event interval changed: {event_id}"
                )
            current_event_id = event["event_id"]
            if current_event_id in observations_by_current_event:
                raise ValueError(
                    "multiple scoring reference observations map to one current event: "
                    f"{current_event_id}"
                )
            observations_by_current_event[current_event_id] = observation
        reference_observations = observations_by_current_event
        provenance["scoring_reference_context"] = {
            "path": str(Path(scoring_reference_context_path).resolve()),
            "sha256": reference_context_sha256,
            "artifact_version": SCORING_REFERENCE_CONTEXT_VERSION,
            "accepted_observation_count": sum(
                observation["status"] == "accepted"
                for observation in reference_observations.values()
            ),
            "semantics": (
                "operator_or_coach_declared_reference_not_grade_not_inferred_from_motion"
            ),
        }
        versions["scoring_reference_context"] = SCORING_REFERENCE_CONTEXT_VERSION

    feature_records: list[dict[str, Any]] = []
    feature_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        interval = EventInterval(
            event_id=event["event_id"],
            event_code=event["event_code"],
            start_ms=event["start_ms"],
            end_ms=event["end_ms"],
            person_track_id=event["person_track_id"],
            key_phases=event["key_phases_ms"],
        )
        names = sorted(required_by_event[event["event_code"]])
        for result in compute_event_features(sequence, interval, names):
            payload = result.to_dict()
            payload.update(
                {
                    "schema_version": "1.0.0",
                    "video_id": canonical_video_id,
                    "event_id": event["event_id"],
                    "event_code": event["event_code"],
                    "person_track_id": event["person_track_id"],
                }
            )
            payload["provenance"].update(
                {
                    **provenance,
                    "model_versions": versions,
                    "event_detector_version": event["provenance"]["detector_version"],
                }
            )
            feature_records.append(payload)
            feature_lookup[(event["event_id"], result.feature_name)] = payload
    _write_jsonl(output_dir / "features.jsonl", feature_records)

    indicator_records: list[dict[str, Any]] = []
    score_records: list[dict[str, Any]] = []
    resolved_scoring_contexts: list[dict[str, Any]] = []
    for event in events:
        for indicator in indicators_by_event[event["event_code"]]:
            features = [
                feature_lookup[(event["event_id"], name)]
                for name in measurement_feature_names(indicator)
            ]
            compact_features = [
                {
                    "feature_name": item["feature_name"],
                    "feature_version": item["feature_version"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "valid": item["valid"],
                    "confidence": item["confidence"],
                    "reason": item["reason"],
                    "source_frames": item["source_frames"],
                }
                for item in features
            ]
            supplemental_features = [
                feature_lookup[(event["event_id"], name)]
                for name in SUPPLEMENTAL_FEATURES_BY_INDICATOR.get(
                    indicator["indicator_id"], []
                )
            ]
            compact_supplemental = [
                {
                    "feature_name": item["feature_name"],
                    "feature_version": item["feature_version"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "valid": item["valid"],
                    "confidence": item["confidence"],
                    "reason": item["reason"],
                    "source_frames": item["source_frames"],
                    "semantics": SUPPLEMENTAL_FINE_FOOT_SEMANTICS,
                }
                for item in supplemental_features
            ]
            all_valid = all(item["valid"] for item in compact_features)
            source_frames = sorted(
                {
                    int(frame)
                    for item in compact_features
                    for frame in item["source_frames"]
                }
            )
            calibration = effective_calibrations.get(indicator["indicator_id"])
            indicator_quality_flags = indicator_event_quality_flags(
                event, indicator
            )
            scoring_context: dict[str, Any] | None = None
            if indicator["indicator_id"] == TARGET_DIRECTION_INDICATOR_ID:
                launch_direction_feature = feature_lookup.get(
                    (event["event_id"], "launch_direction_deg")
                )
                scoring_context = resolve_target_direction_context(
                    event=event,
                    launch_direction_feature=launch_direction_feature,
                    observation=reference_observations.get(event["event_id"]),
                    source_sha256=reference_context_sha256,
                )
                resolved_scoring_contexts.append(scoring_context)
                if scoring_context["status"] == "available":
                    indicator_quality_flags = [
                        flag
                        for flag in indicator_quality_flags
                        if flag != TARGET_DIRECTION_MISSING_FLAG
                    ]
            scoring_feature_by_name = {
                item["feature_name"]: item for item in compact_features
            }
            if indicator["indicator_id"] == TARGET_DIRECTION_INDICATOR_ID:
                scoring_feature_by_name[TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME] = (
                    compact_target_direction_alignment_feature(
                        scoring_context,
                        launch_direction_feature=launch_direction_feature,
                    )
                )
            scoring_features = [
                scoring_feature_by_name[name]
                for name in indicator["required_features"]
            ]
            scoring_features_valid = all(item["valid"] for item in scoring_features)
            score_evidence = [
                {
                    "video_id": canonical_video_id,
                    **(
                        {"video_sha256": provenance["video_sha256"]}
                        if provenance.get("video_sha256") is not None
                        else {}
                    ),
                    "person_track_id": event["person_track_id"],
                    "event_id": event["event_id"],
                    "source_frames": source_frames,
                }
            ]
            if scoring_context is not None:
                score_evidence.append(
                    bind_scoring_context_evidence_to_video(
                        scoring_context,
                        video_id=canonical_video_id,
                        video_sha256=provenance.get("video_sha256"),
                    )
                )
            score = score_indicator(
                indicator_id=indicator["indicator_id"],
                features=scoring_features,
                calibration=calibration,
                model_versions=versions,
                evidence=score_evidence,
                feasibility_level=indicator["feasibility_level"],
                event_quality_flags=indicator_quality_flags,
            )
            if scoring_context is not None:
                score["scoring_context"] = scoring_context
            quality_gate = score["quality_gate"]
            measurement_allowed = all_valid and quality_gate["measurement_allowed"]
            common = {
                "video_id": canonical_video_id,
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
                        "measured" if scoring_features_valid else "unavailable"
                    ),
                    "scoring_features": scoring_features,
                    "supplemental_features": compact_supplemental,
                    "supplemental_feature_status": (
                        "measured"
                        if compact_supplemental and all(item["valid"] for item in compact_supplemental)
                        else "unavailable"
                        if compact_supplemental
                        else "not_applicable"
                    ),
                    "scoring_status": score["status"],
                    "grade": score["grade"],
                    "reason_codes": score["reason_codes"],
                    "quality_gate": quality_gate,
                    **(
                        {"scoring_context": scoring_context}
                        if scoring_context is not None
                        else {}
                    ),
                    "provenance": {**provenance, "model_versions": versions},
                }
            )
            score_records.append({**score, **common})
    _write_jsonl(output_dir / "indicator-features.jsonl", indicator_records)
    _write_jsonl(output_dir / "scores.jsonl", score_records)

    # Truth interfaces are executable, but absent truth must never appear as a
    # perfect evaluation. A separate evaluator can replace this artifact after
    # manual event/keypoint annotations are imported.
    feature_names_by_event = {
        code: sorted(names) for code, names in required_by_event.items()
    }
    truth_evaluation = evaluate_feature_errors(
        sequence, None, events, None, feature_names_by_event
    )
    (output_dir / "event-feature-errors.json").write_text(
        json.dumps(truth_evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    event_counts = Counter(event["event_code"] for event in events)
    status_counts = Counter(item["status"] for item in score_records)
    indicator_ids = [item["indicator_id"] for item in indicators]
    status_counts_by_indicator: dict[str, Counter[str]] = defaultdict(Counter)
    grade_counts_by_indicator: dict[str, Counter[str]] = defaultdict(Counter)
    reason_counts_by_indicator: dict[str, Counter[str]] = defaultdict(Counter)
    for item in score_records:
        status_counts_by_indicator[item["indicator_id"]][item["status"]] += 1
        if item["grade"] is not None:
            grade_counts_by_indicator[item["indicator_id"]][item["grade"]] += 1
        reason_counts_by_indicator[item["indicator_id"]].update(item["reason_codes"])
    quality_gate_counts = Counter(
        item["quality_gate"]["status"] for item in score_records
    )
    valid_counts = Counter(
        item["indicator_id"]
        for item in indicator_records
        if item["feature_status"] == "measured"
    )
    total_counts = Counter(item["indicator_id"] for item in indicator_records)
    measured_indicator_count = sum(
        valid_counts[indicator_id] > 0 for indicator_id in total_counts
    )
    target_label_zh = f"{len(indicators)}项 Pose 指标"
    scoring_status = (
        "scored"
        if status_counts.get("scored", 0) > 0
        else "calibration_required"
        if status_counts.get("calibration_required", 0) > 0
        else "unavailable"
    )
    loop_status = (
        "unavailable_no_pose_motion_event_candidate"
        if not events
        else "event_indicators_scored_with_versioned_calibration"
        if scoring_status == "scored"
        else "candidate_events_features_measured_calibration_required"
        if scoring_status == "calibration_required"
        else "candidate_events_features_unavailable"
    )
    grade_counts = Counter(
        item["grade"] for item in score_records if item["grade"] is not None
    )
    summary = {
        "schema_version": "1.0.0",
        "loop_version": SCORING_LOOP_VERSION,
        "video_id": canonical_video_id,
        "status": loop_status,
        "scope": registry["scope"],
        "event_counts": dict(event_counts),
        "event_evaluation": truth_evaluation["event_metrics"],
        "feature_error_evaluation": {
            "status": truth_evaluation["status"],
            "feature_metrics": truth_evaluation["feature_metrics"],
            "error_budget": truth_evaluation["error_budget"],
        },
        "score_status_counts": dict(status_counts),
        "indicator_score_status_counts": {
            indicator_id: dict(status_counts_by_indicator[indicator_id])
            for indicator_id in indicator_ids
        },
        "quality_gate_status_counts": dict(quality_gate_counts),
        "grade_counts": dict(grade_counts),
        "indicator_grade_counts": {
            indicator_id: dict(grade_counts_by_indicator[indicator_id])
            for indicator_id in indicator_ids
        },
        "result_state": {
            "measurement_status": (
                "measured" if measured_indicator_count > 0 else "unavailable"
            ),
            "measured_indicator_count": measured_indicator_count,
            "target_indicator_count": len(indicators),
            "scoring_status": scoring_status,
            "grade": None,
            "grade_counts": dict(grade_counts),
            "aggregate_grade_status": "not_designed",
            "semantics": (
                "measured means at least one valid event-level feature set exists; "
                "A-E may be emitted only per event and indicator after F4 plus trusted "
                "promotion-ledger authorization; no aggregate grade is defined"
            ),
            "ui_message_zh": (
                f"{target_label_zh}特征已测量，正式 A～E 待教练标定"
                if scoring_status == "calibration_required"
                else f"当前观测不足，{target_label_zh}不可评价"
                if scoring_status == "unavailable"
                else f"{target_label_zh}已通过版本化标定输出等级"
            ),
        },
        "indicator_feature_validity": {
            indicator_id: {
                "valid": valid_counts[indicator_id],
                "total": total_counts[indicator_id],
                "valid_rate": round(
                    valid_counts[indicator_id] / max(total_counts[indicator_id], 1), 6
                ),
                "feasibility_level": next(
                    item["feasibility_level"]
                    for item in indicators
                    if item["indicator_id"] == indicator_id
                ),
                "score_status_counts": dict(
                    status_counts_by_indicator[indicator_id]
                ),
                "grade_counts": dict(grade_counts_by_indicator[indicator_id]),
                "blocking_reason_code_counts": dict(
                    reason_counts_by_indicator[indicator_id]
                ),
            }
            for indicator_id in sorted(indicator_ids)
        },
        "supplemental_granularity": {
            "status": "diagnostic_not_calibrated_scoring_standard",
            "indicator_features": SUPPLEMENTAL_FEATURES_BY_INDICATOR,
            "reason": (
                "COCO17 ankle point alone cannot define shank-foot angle; "
                "registered fine-foot big-toe and small-toe points are required "
                "(available in Halpe26 and COCO-WholeBody133)"
            ),
        },
        "scoring_reference_context": {
            "status": (
                "not_provided"
                if reference_context_payload is None
                else "all_FS02_references_available"
                if resolved_scoring_contexts
                and all(
                    context["status"] == "available"
                    for context in resolved_scoring_contexts
                )
                else "partially_available"
                if any(
                    context["status"] == "available"
                    for context in resolved_scoring_contexts
                )
                else "provided_without_available_reference"
            ),
            "artifact_version": SCORING_REFERENCE_CONTEXT_VERSION,
            "source_sha256": reference_context_sha256,
            "target_event_count": len(resolved_scoring_contexts),
            "input_observation_count": len(
                reference_context_payload.get("observations", [])
                if reference_context_payload is not None
                else []
            ),
            "applied_observation_count": len(reference_observations),
            "input_status_counts": dict(
                Counter(
                    observation["status"]
                    for observation in (
                        reference_context_payload.get("observations", [])
                        if reference_context_payload is not None
                        else []
                    )
                )
            ),
            "available_alignment_count": sum(
                context["status"] == "available"
                for context in resolved_scoring_contexts
            ),
            "pending_count": sum(
                context["status"] == "pending"
                for context in resolved_scoring_contexts
            ),
            "unobservable_count": sum(
                context["status"] == "unobservable"
                for context in resolved_scoring_contexts
            ),
            "missing_or_unavailable_count": sum(
                context["status"] in {"missing", "unavailable"}
                for context in resolved_scoring_contexts
            ),
            "movement_direction_inferred_as_target": False,
            "grades_or_thresholds_generated": False,
        },
        "safety_assertions": {
            "any_non_null_grade_without_calibration": bool(
                not effective_calibrations
                and any(item["grade"] is not None for item in score_records)
            ),
            "fake_thresholds_generated": False,
        },
        "model_versions": versions,
        "provenance": provenance,
        "artifacts": {
            "events_jsonl": "events.jsonl",
            "features_jsonl": "features.jsonl",
            "indicator_features_jsonl": "indicator-features.jsonl",
            "scores_jsonl": "scores.jsonl",
            "event_feature_errors_json": "event-feature-errors.json",
        },
        "artifact_sha256": {
            "events_jsonl": _sha256(output_dir / "events.jsonl"),
            "features_jsonl": _sha256(output_dir / "features.jsonl"),
            "indicator_features_jsonl": _sha256(
                output_dir / "indicator-features.jsonl"
            ),
            "scores_jsonl": _sha256(output_dir / "scores.jsonl"),
            "event_feature_errors_json": _sha256(
                output_dir / "event-feature-errors.json"
            ),
        },
    }
    (output_dir / "scoring-loop-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    clear_feature_cache(sequence)
    return {
        "summary": summary,
        "events": events,
        "indicator_records": indicator_records,
        "scores": score_records,
        "feasibility": registry,
    }
