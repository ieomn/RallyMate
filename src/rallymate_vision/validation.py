from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from rallymate_events.schemas import validate_event_record
from rallymate_vision.contracts import FRAME_SCHEMA_VERSION, SCHEMA_VERSION
from rallymate_vision.pose.metadata import keypoint_schema


SCORING_ARTIFACTS = {
    "primary_player": "primary-player.jsonl",
    "events": "events.jsonl",
    "features": "features.jsonl",
    "indicator_features": "indicator-features.jsonl",
    "scores": "scores.jsonl",
    "feature_errors": "event-feature-errors.json",
    "summary": "scoring-loop-summary.json",
}
COMPACT_FEATURE_FIELDS = (
    "feature_name",
    "feature_version",
    "value",
    "unit",
    "valid",
    "confidence",
    "reason",
    "source_frames",
)
LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_ARTIFACT_HASHES = {
    "minimum-scoring-loop-v0.1.0",
    "minimum-scoring-loop-v0.2.0",
}
LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_COMPLETE_BLOCK_REASONS = {
    "minimum-scoring-loop-v0.1.0",
    "minimum-scoring-loop-v0.2.0",
    "minimum-scoring-loop-v0.3.0",
}
LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_CONTEXT_EVIDENCE_IDENTITY = {
    "minimum-scoring-loop-v0.1.0",
    "minimum-scoring-loop-v0.2.0",
    "minimum-scoring-loop-v0.3.0",
    "minimum-scoring-loop-v0.4.0",
    "minimum-scoring-loop-v0.4.1",
    "minimum-scoring-loop-v0.4.23",
    "minimum-scoring-loop-v0.5.0",
    "minimum-scoring-loop-v0.6.0",
}


class OutputValidationError(ValueError):
    """Raised when a stage-1 artifact violates the handoff invariants."""


def _validate_pipeline_derived_report(
    *,
    output_dir: Path,
    summary: dict[str, Any],
    filename: str,
    summary_field: str,
    artifact_field: str,
    validator: Any,
    error_types: tuple[type[Exception], ...],
) -> str:
    path = output_dir / filename
    declared_artifact = summary.get("artifacts", {}).get(artifact_field)
    declared_summary = summary.get(summary_field)
    if declared_artifact is None and declared_summary is None:
        return "not_declared"
    _require(
        declared_artifact is not None and declared_summary is not None,
        f"derived artifact declaration is incomplete: {filename}",
    )
    if not path.exists():
        raise OutputValidationError(f"declared derived artifact is missing: {filename}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        validator(report)
    except json.JSONDecodeError as exc:
        raise OutputValidationError(f"invalid derived artifact {filename}: {exc}") from exc
    except error_types as exc:
        raise OutputValidationError(f"invalid derived artifact {filename}: {exc}") from exc
    _require(
        declared_artifact == filename,
        f"summary artifact path differs for {filename}",
    )
    _require(
        declared_summary == report.get("summary"),
        f"summary payload differs for {filename}",
    )
    for source_name, source in report.get("sources", {}).items():
        source_path = Path(source["path"])
        if not source_path.is_absolute():
            source_path = output_dir / source_path
        _require(
            source_path.exists(),
            f"derived artifact source is missing: {source_name}",
        )
        _require(
            source["sha256"] == _sha256(source_path),
            f"derived artifact source SHA-256 mismatch: {source_name}",
        )
    return str(report.get("status"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OutputValidationError(message)


def _bounded(values: list[Any], lower: float, upper: float) -> bool:
    return all(
        isinstance(value, (int, float)) and lower <= value <= upper
        for value in values
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OutputValidationError(
                    f"{path.name} line {line_number} is invalid JSON: {exc}"
                ) from exc
            _require(
                isinstance(record, dict),
                f"{path.name} line {line_number} must be a JSON object",
            )
            records.append(record)
    return records


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _validate_feature_result(record: dict[str, Any], prefix: str) -> None:
    required = {
        "feature_name",
        "feature_version",
        "value",
        "unit",
        "confidence",
        "valid",
        "reason",
        "source_frames",
    }
    _require(not (required - set(record)), f"{prefix} is missing feature fields")
    _require(
        isinstance(record["feature_name"], str) and record["feature_name"],
        f"{prefix}.feature_name must be non-empty",
    )
    _require(
        isinstance(record["feature_version"], str) and record["feature_version"],
        f"{prefix}.feature_version must be non-empty",
    )
    _require(
        isinstance(record["unit"], str) and record["unit"],
        f"{prefix}.unit must be non-empty",
    )
    confidence = record["confidence"]
    _require(
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0 <= confidence <= 1,
        f"{prefix}.confidence must be finite and in 0..1",
    )
    _require(isinstance(record["valid"], bool), f"{prefix}.valid must be boolean")
    _require(
        isinstance(record["reason"], str) and record["reason"],
        f"{prefix}.reason must be non-empty",
    )
    _require(
        isinstance(record["source_frames"], list)
        and all(isinstance(frame, int) and frame >= 0 for frame in record["source_frames"]),
        f"{prefix}.source_frames must be non-negative integers",
    )


def _compact_feature(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record[field] for field in COMPACT_FEATURE_FIELDS}


def _validate_indicator_quality_gate(
    gate: Any,
    *,
    prefix: str,
    indicator_id: str,
) -> dict[str, Any]:
    _require(isinstance(gate, dict), f"{prefix}.quality_gate must be an object")
    required = {
        "policy_version",
        "indicator_id",
        "status",
        "hard_fail",
        "measurement_allowed",
        "scoring_allowed",
        "hard_fail_flags",
    }
    _require(
        not (required - set(gate)),
        f"{prefix}.quality_gate is missing required fields",
    )
    _require(
        isinstance(gate["policy_version"], str) and gate["policy_version"],
        f"{prefix}.quality_gate.policy_version must be non-empty",
    )
    _require(
        gate["indicator_id"] == indicator_id,
        f"{prefix}.quality_gate.indicator_id mismatch",
    )
    _require(
        gate["status"] in {"pass", "advisory", "hard_fail"},
        f"{prefix}.quality_gate.status invalid",
    )
    for field in ("hard_fail", "measurement_allowed", "scoring_allowed"):
        _require(
            isinstance(gate[field], bool),
            f"{prefix}.quality_gate.{field} must be boolean",
        )
    hard_fail_flags = gate["hard_fail_flags"]
    _require(
        isinstance(hard_fail_flags, list)
        and all(isinstance(flag, str) and flag for flag in hard_fail_flags),
        f"{prefix}.quality_gate.hard_fail_flags must be a string list",
    )
    _require(
        gate["hard_fail"] == bool(hard_fail_flags),
        f"{prefix}.quality_gate hard_fail/flags invariant failed",
    )
    scoring_block_flags = gate.get("scoring_block_flags", [])
    _require(
        isinstance(scoring_block_flags, list)
        and all(isinstance(flag, str) and flag for flag in scoring_block_flags),
        f"{prefix}.quality_gate.scoring_block_flags must be a string list",
    )
    _require(
        not scoring_block_flags or not gate["scoring_allowed"],
        f"{prefix}.quality_gate scoring block flags cannot allow scoring",
    )
    _require(
        (gate["status"] == "hard_fail") == gate["hard_fail"],
        f"{prefix}.quality_gate hard_fail/status invariant failed",
    )
    if gate["hard_fail"]:
        _require(
            not gate["measurement_allowed"] and not gate["scoring_allowed"],
            f"{prefix}.quality_gate hard fail cannot allow measurement or scoring",
        )
    if not gate["measurement_allowed"]:
        _require(
            not gate["scoring_allowed"],
            f"{prefix}.quality_gate cannot allow scoring when measurement is blocked",
        )
    return {
        "measurement_blocked": bool(
            gate["hard_fail"] or not gate["measurement_allowed"]
        ),
        "scoring_blocked": bool(
            gate["hard_fail"]
            or not gate["measurement_allowed"]
            or not gate["scoring_allowed"]
        ),
        "hard_fail_flags": list(hard_fail_flags),
        "scoring_block_flags": list(scoring_block_flags),
    }


def _validate_scoring_artifacts(
    output_dir: Path,
    *,
    frame_count: int,
    frame_timestamps: dict[int, int],
    frame_processed_indexes: list[int],
    frames_path: Path,
) -> dict[str, int | str]:
    # Import lazily: scoring's diagnostic review helpers depend on this module.
    # A top-level import would make the standalone validate_run.py entry point
    # depend on whichever package happened to be imported first.
    from rallymate_scoring.scoring_context import (
        TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
        TARGET_DIRECTION_INDICATOR_ID,
        TARGET_DIRECTION_MISSING_FLAG,
        bind_scoring_context_evidence_to_video,
        compact_target_direction_alignment_feature,
        validate_resolved_target_direction_context,
    )

    paths = {name: output_dir / filename for name, filename in SCORING_ARTIFACTS.items()}
    present = {name for name, path in paths.items() if path.exists()}
    if not present:
        return {"scoring_artifacts_status": "not_present"}
    missing = set(paths) - present
    _require(
        not missing,
        f"incomplete scoring artifact bundle, missing: {sorted(missing)}",
    )
    loop_summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    _require(loop_summary.get("schema_version") == "1.0.0", "invalid scoring summary")
    video_id = loop_summary.get("video_id")
    _require(isinstance(video_id, str) and video_id, "scoring summary video_id is required")
    model_versions = loop_summary.get("model_versions")
    _require(isinstance(model_versions, dict) and model_versions, "scoring model_versions missing")
    provenance = loop_summary.get("provenance")
    _require(isinstance(provenance, dict), "scoring summary provenance missing")
    indicator_validity = loop_summary.get("indicator_feature_validity")
    _require(
        isinstance(indicator_validity, dict) and indicator_validity,
        "scoring summary indicator_feature_validity missing",
    )
    scoring_indicators = set(indicator_validity)
    _require(
        str(provenance.get("frames_sha256", "")).upper() == _sha256(frames_path),
        "scoring summary frames_sha256 does not match frames.jsonl",
    )
    video_path_value = provenance.get("video_path")
    video_sha = provenance.get("video_sha256")
    if isinstance(video_path_value, str) and video_path_value and isinstance(video_sha, str):
        video_path = Path(video_path_value)
        _require(video_path.exists(), "scoring provenance video_path is missing")
        _require(
            video_sha.upper() == _sha256(video_path),
            "scoring summary video_sha256 does not match video",
        )

    primary = _load_jsonl(paths["primary_player"])
    _require(len(primary) == frame_count, "primary-player timeline length mismatch")
    for expected_processed_index, item in zip(frame_processed_indexes, primary):
        _require(item.get("schema_version") == "1.0.0", "invalid primary-player schema")
        _require(
            item.get("processed_index") == expected_processed_index,
            "primary-player processed_index must match frames.jsonl",
        )
        source_frame = item.get("source_frame_index")
        _require(source_frame in frame_timestamps, "primary-player source frame is missing")
        _require(
            item.get("timestamp_ms") == frame_timestamps[source_frame],
            "primary-player timestamp does not match frames.jsonl",
        )
        _require(item.get("primary_player_id") == 1, "primary_player_id must remain 1")
        _require(
            item.get("selection_status") in {"selected", "missing"},
            "invalid primary-player selection_status",
        )

    events = _load_jsonl(paths["events"])
    event_by_id = {}
    max_timestamp = max(frame_timestamps.values())
    for item in events:
        try:
            validate_event_record(item)
        except ValueError as exc:
            raise OutputValidationError(f"invalid event {item.get('event_id')}: {exc}") from exc
        event_id = item["event_id"]
        _require(event_id not in event_by_id, f"duplicate event_id: {event_id}")
        _require(item["person_track_id"] == 1, "event must reference primary player 1")
        _require(item["end_ms"] <= max_timestamp, "event boundary exceeds processed video")
        diagnostics = item.get("track_diagnostics")
        _require(isinstance(diagnostics, dict), "event track_diagnostics missing")
        for field in (
            "track_coverage_fraction",
            "source_track_switch_candidate_count",
            "confirmed_id_switch_count",
            "keypoint_valid_fraction",
            "left_right_swap_candidate_frames",
            "keypoint_jump_candidate_frames",
            "longest_pose_missing_frames",
            "longest_pose_missing_ms",
        ):
            _require(field in diagnostics, f"event track_diagnostics.{field} missing")
        event_by_id[event_id] = item

    frame_indexes = set(frame_timestamps)
    features = _load_jsonl(paths["features"])
    feature_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(features):
        prefix = f"features[{index}]"
        _validate_feature_result(item, prefix)
        _require(item.get("video_id") == video_id, f"{prefix}.video_id mismatch")
        event = event_by_id.get(item.get("event_id"))
        _require(event is not None, f"{prefix} references unknown event")
        _require(item.get("event_code") == event["event_code"], f"{prefix}.event_code mismatch")
        _require(item.get("person_track_id") == 1, f"{prefix}.person_track_id mismatch")
        key = (item["event_id"], item["feature_name"])
        _require(key not in feature_by_key, f"duplicate event feature: {key}")
        feature_by_key[key] = item
        _require(
            set(item["source_frames"]).issubset(frame_indexes),
            f"{prefix} references source frame outside frames.jsonl",
        )

    indicator_records = _load_jsonl(paths["indicator_features"])
    scores = _load_jsonl(paths["scores"])
    _require(len(indicator_records) == len(scores), "indicator/scores record count mismatch")
    if not events:
        _require(
            not features and not indicator_records and not scores,
            "no-event scoring bundle must keep all derived record streams empty",
        )
        _require(
            loop_summary.get("status")
            == "unavailable_no_pose_motion_event_candidate",
            "empty events require the explicit no-event unavailable status",
        )
        _require(
            loop_summary.get("event_counts") == {}
            and loop_summary.get("score_status_counts") == {}
            and loop_summary.get("grade_counts") == {},
            "no-event summary counts must remain empty",
        )
        result_state = loop_summary.get("result_state")
        _require(
            isinstance(result_state, dict)
            and result_state.get("measurement_status") == "unavailable"
            and result_state.get("scoring_status") == "unavailable"
            and result_state.get("grade") is None,
            "no-event result_state must remain unavailable without grade",
        )
    indicator_by_key = {}
    for index, item in enumerate(indicator_records):
        prefix = f"indicator-features[{index}]"
        event = event_by_id.get(item.get("event_id"))
        _require(event is not None, f"{prefix} references unknown event")
        indicator_id = item.get("indicator_id")
        _require(
            indicator_id in scoring_indicators,
            f"{prefix} indicator is not declared by the scoring summary",
        )
        _require(item.get("video_id") == video_id, f"{prefix}.video_id mismatch")
        _require(item.get("event_code") == event["event_code"], f"{prefix}.event_code mismatch")
        _require(item.get("person_track_id") == 1, f"{prefix}.person_track_id mismatch")
        compact_features = item.get("features")
        _require(isinstance(compact_features, list) and compact_features, f"{prefix}.features empty")
        compact_feature_names: set[str] = set()
        compact_feature_order: list[str] = []
        for feature_index, feature in enumerate(compact_features):
            feature_prefix = f"{prefix}.features[{feature_index}]"
            _validate_feature_result(feature, feature_prefix)
            feature_name = feature["feature_name"]
            _require(
                feature_name not in compact_feature_names,
                f"{prefix} contains duplicate compact feature {feature_name}",
            )
            compact_feature_names.add(feature_name)
            compact_feature_order.append(feature_name)
            source_feature = feature_by_key.get((item["event_id"], feature_name))
            _require(
                source_feature is not None,
                f"{prefix} references feature absent from features.jsonl",
            )
            _require(
                feature == _compact_feature(source_feature),
                f"{feature_prefix} does not exactly match features.jsonl",
            )
        all_valid = all(feature["valid"] for feature in compact_features)
        quality_gate = item.get("quality_gate")
        quality_state = {
            "measurement_blocked": False,
            "scoring_blocked": False,
            "hard_fail_flags": [],
            "scoring_block_flags": [],
        }
        if quality_gate is not None:
            quality_state = _validate_indicator_quality_gate(
                quality_gate,
                prefix=prefix,
                indicator_id=indicator_id,
            )
        if indicator_id == TARGET_DIRECTION_INDICATOR_ID:
            scoring_context = item.get("scoring_context")
            launch_direction_feature = feature_by_key.get(
                (item["event_id"], "launch_direction_deg")
            )
            if scoring_context is not None:
                try:
                    validate_resolved_target_direction_context(
                        scoring_context,
                        event=event,
                        launch_direction_feature=launch_direction_feature,
                    )
                except ValueError as exc:
                    raise OutputValidationError(
                        f"{prefix}.scoring_context invalid: {exc}"
                    ) from exc
            context_available = (
                isinstance(scoring_context, dict)
                and scoring_context.get("status") == "available"
            )
            gate_input_flags = (
                set(quality_gate.get("input_quality_flags", []))
                if isinstance(quality_gate, dict)
                else set()
            )
            if TARGET_DIRECTION_MISSING_FLAG in event.get("quality_flags", []):
                _require(
                    context_available
                    or TARGET_DIRECTION_MISSING_FLAG in gate_input_flags,
                    f"{prefix} removed target-direction block without valid context",
                )
            if context_available:
                _require(
                    TARGET_DIRECTION_MISSING_FLAG not in gate_input_flags,
                    f"{prefix} retained target-direction block despite valid context",
                )
        scoring_contract_declared = "scoring_features" in item
        scoring_features = item.get("scoring_features", compact_features)
        _require(
            isinstance(scoring_features, list) and scoring_features,
            f"{prefix}.scoring_features empty",
        )
        scoring_feature_names: list[str] = []
        for feature_index, feature in enumerate(scoring_features):
            feature_prefix = f"{prefix}.scoring_features[{feature_index}]"
            _validate_feature_result(feature, feature_prefix)
            name = feature["feature_name"]
            _require(
                name not in scoring_feature_names,
                f"{prefix} contains duplicate scoring feature {name}",
            )
            scoring_feature_names.append(name)
        if indicator_id == TARGET_DIRECTION_INDICATOR_ID and scoring_contract_declared:
            _require(
                scoring_feature_names
                == [*compact_feature_order, TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME],
                f"{prefix}.scoring_features do not match FS02-M02 measurement/context contract",
            )
            expected_context_feature = compact_target_direction_alignment_feature(
                scoring_context or {},
                launch_direction_feature=launch_direction_feature,
            )
            _require(
                scoring_features[-1] == expected_context_feature,
                f"{prefix}.scoring_features target alignment mismatch",
            )
        else:
            _require(
                scoring_features == compact_features,
                f"{prefix}.scoring_features must equal features without a context contract",
            )
        scoring_features_valid = all(feature["valid"] for feature in scoring_features)
        if "scoring_feature_status" in item:
            _require(
                item["scoring_feature_status"]
                == ("measured" if scoring_features_valid else "unavailable"),
                f"{prefix}.scoring_feature_status disagrees with scoring features",
            )
        expected_feature_status = (
            "measured"
            if all_valid and not quality_state["measurement_blocked"]
            else "unavailable"
        )
        _require(
            item.get("feature_status") == expected_feature_status,
            f"{prefix}.feature_status disagrees with feature validity and quality gate",
        )
        scoring_status = item.get("scoring_status")
        grade = item.get("grade")
        _require(
            scoring_status in {"scored", "calibration_required", "unavailable"},
            f"{prefix}.scoring_status invalid",
        )
        _require(
            (scoring_status == "scored") == (grade in {"A", "B", "C", "D", "E"}),
            f"{prefix} grade/status invariant failed",
        )
        if quality_state["scoring_blocked"]:
            _require(
                scoring_status == "unavailable",
                f"{prefix}.scoring_status must be unavailable when quality gate blocks scoring",
            )
        if scoring_status in {"scored", "calibration_required"}:
            _require(
                item.get("feature_status") == "measured"
                and scoring_features_valid
                and not quality_state["scoring_blocked"],
                f"{prefix}.scoring_status requires measured features and an allowing quality gate",
            )
        reason_codes = item.get("reason_codes")
        _require(
            isinstance(reason_codes, list)
            and all(isinstance(reason, str) and reason for reason in reason_codes),
            f"{prefix}.reason_codes must be a string list",
        )
        if loop_summary.get("loop_version") not in (
            LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_COMPLETE_BLOCK_REASONS
        ):
            hard_fail_flags = quality_state["hard_fail_flags"]
            scoring_block_flags = quality_state["scoring_block_flags"]
            if hard_fail_flags:
                _require(
                    "event_quality_hard_fail" in reason_codes
                    and set(hard_fail_flags).issubset(reason_codes),
                    f"{prefix}.reason_codes omit active hard-fail evidence",
                )
            if scoring_block_flags:
                _require(
                    "event_scoring_quality_blocked" in reason_codes
                    and set(scoring_block_flags).issubset(reason_codes),
                    f"{prefix}.reason_codes omit active score-block evidence",
                )
        key = (item["event_id"], indicator_id)
        _require(key not in indicator_by_key, f"duplicate indicator event record: {key}")
        indicator_by_key[key] = item

    score_statuses = Counter()
    grades = Counter()
    for index, item in enumerate(scores):
        prefix = f"scores[{index}]"
        key = (item.get("event_id"), item.get("indicator_id"))
        indicator = indicator_by_key.get(key)
        _require(indicator is not None, f"{prefix} has no indicator feature record")
        _require(item.get("video_id") == video_id, f"{prefix}.video_id mismatch")
        _require(item.get("event_code") == indicator["event_code"], f"{prefix}.event_code mismatch")
        _require(item.get("person_track_id") == 1, f"{prefix}.person_track_id mismatch")
        _require(item.get("status") == indicator["scoring_status"], f"{prefix}.status mismatch")
        _require(item.get("grade") == indicator["grade"], f"{prefix}.grade mismatch")
        score_feature = item.get("feature")
        _require(
            isinstance(score_feature, dict)
            and score_feature.get("items")
            == indicator.get("scoring_features", indicator.get("features")),
            f"{prefix}.feature.items does not exactly match indicator-features.jsonl",
        )
        _require(
            item.get("reason_codes") == indicator.get("reason_codes"),
            f"{prefix}.reason_codes mismatch",
        )
        indicator_gate = indicator.get("quality_gate")
        score_gate = item.get("quality_gate")
        _require(
            score_gate == indicator_gate,
            f"{prefix}.quality_gate mismatch",
        )
        indicator_context = indicator.get("scoring_context")
        score_context = item.get("scoring_context")
        _require(
            score_context == indicator_context,
            f"{prefix}.scoring_context mismatch",
        )
        if score_context is not None:
            context_evidence = [
                evidence
                for evidence in item.get("evidence", [])
                if isinstance(evidence, dict)
                and evidence.get("evidence_type") == "scoring_reference_context"
            ]
            _require(
                len(context_evidence) == 1,
                f"{prefix} must contain exactly one scoring context evidence item",
            )
            if loop_summary.get("loop_version") in (
                LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_CONTEXT_EVIDENCE_IDENTITY
            ):
                expected_evidence = {
                    "evidence_type": "scoring_reference_context",
                    **score_context,
                }
            else:
                try:
                    expected_evidence = bind_scoring_context_evidence_to_video(
                        score_context,
                        video_id=video_id,
                        video_sha256=provenance.get("video_sha256"),
                    )
                except ValueError as exc:
                    raise OutputValidationError(
                        f"{prefix} scoring context evidence identity invalid: {exc}"
                    ) from exc
            _require(
                context_evidence[0] == expected_evidence,
                f"{prefix} scoring context evidence mismatch",
            )
        if score_gate is not None:
            quality_state = _validate_indicator_quality_gate(
                score_gate,
                prefix=prefix,
                indicator_id=item.get("indicator_id"),
            )
            if quality_state["scoring_blocked"]:
                _require(
                    item.get("status") == "unavailable",
                    f"{prefix}.status must be unavailable when quality gate blocks scoring",
                )
        _require(isinstance(item.get("model_versions"), dict), f"{prefix}.model_versions missing")
        _require(isinstance(item.get("evidence"), list) and item["evidence"], f"{prefix}.evidence missing")
        score_statuses[item["status"]] += 1
        if item.get("grade") is not None:
            grades[item["grade"]] += 1
    _require(
        dict(score_statuses) == loop_summary.get("score_status_counts"),
        "scoring summary score_status_counts mismatch",
    )
    _require(dict(grades) == loop_summary.get("grade_counts"), "scoring summary grade_counts mismatch")
    event_counts = Counter(item["event_code"] for item in events)
    _require(dict(event_counts) == loop_summary.get("event_counts"), "scoring event_counts mismatch")
    errors = json.loads(paths["feature_errors"].read_text(encoding="utf-8"))
    _require(isinstance(errors, dict) and errors.get("schema_version") == "1.0.0", "invalid feature error report")
    declared = loop_summary.get("artifacts")
    _require(isinstance(declared, dict), "scoring summary artifacts missing")
    for filename in declared.values():
        _require(
            isinstance(filename, str) and (output_dir / filename).exists(),
            f"declared scoring artifact is missing: {filename}",
        )
    artifact_hashes = loop_summary.get("artifact_sha256")
    if loop_summary.get("loop_version") not in (
        LEGACY_SCORING_LOOP_VERSIONS_WITHOUT_ARTIFACT_HASHES
    ):
        _require(
            isinstance(artifact_hashes, dict),
            "current scoring summary artifact_sha256 is required",
        )
    if artifact_hashes is not None:
        _require(
            isinstance(artifact_hashes, dict)
            and set(artifact_hashes) == set(declared),
            "scoring summary artifact_sha256 keys must match artifacts",
        )
        for artifact_name, filename in declared.items():
            expected_hash = artifact_hashes.get(artifact_name)
            _require(
                isinstance(expected_hash, str)
                and expected_hash.upper() == _sha256(output_dir / filename),
                f"scoring artifact SHA256 mismatch: {filename}",
            )
    return {
        "scoring_artifacts_status": "passed",
        "validated_primary_player_records": len(primary),
        "validated_events": len(events),
        "validated_features": len(features),
        "validated_indicator_records": len(indicator_records),
        "validated_scores": len(scores),
    }


def validate_frame_observation(record: dict[str, Any]) -> None:
    _require(
        record.get("schema_version") in {SCHEMA_VERSION, FRAME_SCHEMA_VERSION},
        "frame schema_version is missing or unsupported",
    )
    frame = record.get("frame", {})
    width = frame.get("width")
    height = frame.get("height")
    _require(
        isinstance(width, int) and width > 0,
        "frame.width must be a positive integer",
    )
    _require(
        isinstance(height, int) and height > 0,
        "frame.height must be a positive integer",
    )
    _require(
        isinstance(frame.get("timestamp_ms"), int)
        and frame["timestamp_ms"] >= 0,
        "frame.timestamp_ms must be a non-negative integer",
    )

    track_ids: set[int] = set()
    player_track_ids: set[int] = set()
    for index, detection in enumerate(record.get("detections", [])):
        prefix = f"detections[{index}]"
        track_id = detection.get("track_id")
        _require(
            isinstance(track_id, int) and track_id > 0,
            f"{prefix}.track_id must be a positive integer",
        )
        _require(
            track_id not in track_ids,
            f"{prefix}.track_id duplicates another object in the frame",
        )
        track_ids.add(track_id)
        if detection.get("class_name") == "player":
            player_track_ids.add(track_id)

        bbox = detection.get("bbox_px", [])
        _require(
            isinstance(bbox, list)
            and len(bbox) == 4
            and _bounded(bbox, 0.0, float(max(width, height))),
            f"{prefix}.bbox_px must contain four bounded numbers",
        )
        _require(
            bbox[0] <= bbox[2]
            and bbox[1] <= bbox[3]
            and bbox[2] <= width
            and bbox[3] <= height,
            f"{prefix}.bbox_px is outside the frame or inverted",
        )
        normalized = detection.get("bbox_normalized", [])
        _require(
            isinstance(normalized, list)
            and len(normalized) == 4
            and _bounded(normalized, 0.0, 1.0),
            f"{prefix}.bbox_normalized must be in the 0..1 range",
        )
        center = detection.get("center_px", [])
        center_normalized = detection.get("center_normalized", [])
        _require(
            isinstance(center, list)
            and len(center) == 2
            and _bounded(center, 0.0, float(max(width, height)))
            and center[0] <= width
            and center[1] <= height,
            f"{prefix}.center_px must be inside the frame",
        )
        _require(
            isinstance(center_normalized, list)
            and len(center_normalized) == 2
            and _bounded(center_normalized, 0.0, 1.0),
            f"{prefix}.center_normalized must be in the 0..1 range",
        )

    for pose_index, pose in enumerate(record.get("poses", [])):
        prefix = f"poses[{pose_index}]"
        person_track_id = pose.get("person_track_id")
        _require(
            person_track_id in player_track_ids,
            f"{prefix}.person_track_id has no player detection in this frame",
        )
        keypoints = pose.get("keypoints", [])
        format_name = pose.get("keypoint_format")
        _require(
            format_name in {"coco17", "halpe26", "coco_wholebody133"}
            and isinstance(keypoints, list),
            (
                f"{prefix}.keypoint_format must be coco17, halpe26 "
                "or coco_wholebody133"
            ),
        )
        definition = keypoint_schema(format_name)
        _require(
            len(keypoints) == definition["count"],
            f"{prefix} must contain {definition['count']} {format_name} keypoints",
        )
        for point_index, point in enumerate(keypoints):
            point_prefix = f"{prefix}.keypoints[{point_index}]"
            expected = definition["keypoints"][point_index]
            _require(
                point.get("index") in {None, point_index}
                and point.get("name") in {None, expected["name"]},
                f"{point_prefix} index/name does not match {format_name}",
            )
            _require(
                _bounded(
                    [point.get("x_px"), point.get("y_px")],
                    0.0,
                    float(max(width, height)),
                )
                and point["x_px"] <= width
                and point["y_px"] <= height,
                f"{point_prefix} pixel coordinates are outside the frame",
            )
            _require(
                _bounded(
                    [
                        point.get("x_normalized"),
                        point.get("y_normalized"),
                        point.get("confidence"),
                    ],
                    0.0,
                    1.0,
                ),
                f"{point_prefix} normalized values must be in 0..1",
            )
            downstream = point.get("downstream_joint_id")
            expected_downstream = expected["downstream_joint_id"]
            _require(
                downstream == expected_downstream,
                f"{point_prefix} downstream mapping does not match registry",
            )

    court = record.get("court", {})
    for index, point in enumerate(court.get("polygon_normalized", [])):
        _require(
            isinstance(point, list)
            and len(point) == 2
            and _bounded(point, 0.0, 1.0),
            f"court.polygon_normalized[{index}] must be in the 0..1 range",
        )
    _require(
        not court.get("calibration_usable")
        or court.get("status") == "calibrated",
        "only calibrated court output may set calibration_usable=true",
    )


def validate_run_artifacts(output_dir: Path) -> dict[str, int | str]:
    frames_path = output_dir / "frames.jsonl"
    summary_path = output_dir / "summary.json"
    _require(frames_path.exists(), f"missing artifact: {frames_path}")
    _require(summary_path.exists(), f"missing artifact: {summary_path}")

    frame_count = 0
    detection_count = 0
    pose_count = 0
    previous_timestamp = -1
    frame_timestamps: dict[int, int] = {}
    frame_processed_indexes: list[int] = []
    with frames_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OutputValidationError(
                    f"frames.jsonl line {line_number} is invalid JSON: {exc}"
                ) from exc
            try:
                validate_frame_observation(record)
            except OutputValidationError as exc:
                raise OutputValidationError(
                    f"frames.jsonl line {line_number}: {exc}"
                ) from exc
            timestamp = record["frame"]["timestamp_ms"]
            source_frame_index = record["frame"].get("index")
            _require(
                isinstance(source_frame_index, int) and source_frame_index >= 0,
                "frame.index must be a non-negative integer",
            )
            _require(
                source_frame_index not in frame_timestamps,
                "frame.index must be unique",
            )
            frame_timestamps[source_frame_index] = timestamp
            processed_index = record["frame"].get("processed_index")
            _require(
                isinstance(processed_index, int) and processed_index >= 0,
                "frame.processed_index must be a non-negative integer",
            )
            if frame_processed_indexes:
                _require(
                    processed_index == frame_processed_indexes[-1] + 1,
                    "frame.processed_index must be contiguous",
                )
            frame_processed_indexes.append(processed_index)
            _require(
                timestamp > previous_timestamp,
                "frame timestamps must be strictly increasing",
            )
            previous_timestamp = timestamp
            frame_count += 1
            detection_count += len(record.get("detections", []))
            pose_count += len(record.get("poses", []))

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    _require(
        summary.get("schema_version") == SCHEMA_VERSION,
        "summary schema_version is missing or unsupported",
    )
    _require(
        summary.get("processing", {}).get("processed_frames") == frame_count,
        "summary processed_frames does not match frames.jsonl",
    )
    _require(frame_count > 0, "frames.jsonl contains no records")
    result: dict[str, int | str] = {
        "status": "passed",
        "validated_frames": frame_count,
        "validated_detections": detection_count,
        "validated_poses": pose_count,
    }
    result.update(
        _validate_scoring_artifacts(
            output_dir,
            frame_count=frame_count,
            frame_timestamps=frame_timestamps,
            frame_processed_indexes=frame_processed_indexes,
            frames_path=frames_path,
        )
    )
    # Import lazily because the scoring package also exposes diagnostic modules
    # that import this run validator.  At call time this module is fully loaded,
    # so the public CLI remains usable without a package import cycle.
    from rallymate_scoring.calculation_readiness import (
        CalculationReadinessError,
        validate_indicator_calculation_readiness,
    )
    from rallymate_scoring.measurement_portfolio import (
        MeasurementPortfolioError,
        validate_indicator_measurement_portfolio,
    )
    from rallymate_scoring.cycle_measurement import (
        CycleMeasurementError,
        validate_scoring_cycle_measurement,
    )

    result["calculation_readiness_status"] = _validate_pipeline_derived_report(
        output_dir=output_dir,
        summary=summary,
        filename="calculation-readiness.json",
        summary_field="calculation_readiness",
        artifact_field="calculation_readiness_json",
        validator=validate_indicator_calculation_readiness,
        error_types=(CalculationReadinessError,),
    )
    result["measurement_portfolio_status"] = _validate_pipeline_derived_report(
        output_dir=output_dir,
        summary=summary,
        filename="indicator-measurement-portfolio.json",
        summary_field="measurement_portfolio",
        artifact_field="indicator_measurement_portfolio_json",
        validator=validate_indicator_measurement_portfolio,
        error_types=(MeasurementPortfolioError,),
    )
    result["scoring_cycle_measurement_status"] = _validate_pipeline_derived_report(
        output_dir=output_dir,
        summary=summary,
        filename="scoring-cycle-measurement.json",
        summary_field="scoring_cycle_measurement",
        artifact_field="scoring_cycle_measurement_json",
        validator=validate_scoring_cycle_measurement,
        error_types=(CycleMeasurementError,),
    )
    return result
