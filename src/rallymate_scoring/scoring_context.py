from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCORING_REFERENCE_CONTEXT_SCHEMA_VERSION = "1.0.0"
SCORING_REFERENCE_CONTEXT_VERSION = "scoring-reference-context-v1.0.0"
TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION = "target-direction-alignment-v1.0.0"
TARGET_DIRECTION_INDICATOR_ID = "FS02-M02"
TARGET_DIRECTION_EVENT_CODE = "FS02"
TARGET_DIRECTION_SEMANTIC_KEY = "target_direction"
TARGET_DIRECTION_MISSING_FLAG = "tactical_target_direction_not_observed"
TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME = "target_direction_alignment_error_deg"
SCORING_CONTEXT_FEATURE_DEFINITIONS = {
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME: {
        "unit": "deg",
        "version": TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION,
        "source": "accepted_external_target_direction_plus_pose_launch_direction",
    }
}

_STATUSES = {"pending", "accepted", "unobservable"}
_SOURCE_KINDS = {
    "coach_declared_training_target",
    "manual_event_adjudication",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def bind_scoring_context_evidence_to_video(
    context: Mapping[str, Any],
    *,
    video_id: str,
    video_sha256: str | None,
) -> dict[str, Any]:
    """Attach the authoritative scoring-video identity to resolved context evidence.

    Resolved context deliberately carries event/semantic provenance rather than a
    duplicate video header.  A score evidence item, however, must be independently
    checkable against the runtime view authorization.  The caller-supplied identity
    therefore wins over any untrusted same-named fields in ``context``.
    """

    if not isinstance(context, Mapping):
        raise ValueError("resolved scoring context evidence must be an object")
    if not isinstance(video_id, str) or not video_id:
        raise ValueError("resolved scoring context evidence video_id is required")
    if video_sha256 is not None and (
        not isinstance(video_sha256, str)
        or len(video_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in video_sha256
        )
    ):
        raise ValueError(
            "resolved scoring context evidence video_sha256 must be SHA-256"
        )
    evidence = dict(context)
    evidence.pop("video_sha256", None)
    evidence.update(
        {
            "evidence_type": "scoring_reference_context",
            "video_id": video_id,
        }
    )
    if video_sha256 is not None:
        evidence["video_sha256"] = video_sha256
    return evidence


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def signed_angle_delta_deg(observed_deg: float, reference_deg: float) -> float:
    """Return observed-minus-reference in the half-open interval [-180, 180)."""

    observed = _finite_number(observed_deg, "observed_deg")
    reference = _finite_number(reference_deg, "reference_deg")
    delta = (observed - reference + 180.0) % 360.0 - 180.0
    return 0.0 if abs(delta) < 1e-12 else float(delta)


def build_target_direction_alignment_feature(
    *,
    launch_direction_feature: Mapping[str, Any] | None,
    target_direction_deg: float | None,
    reference_confidence: float | None,
    unavailable_reason: str,
) -> dict[str, Any]:
    """Build the calibration input for FS02-M02 without inventing a target.

    The function is pure and accepts either a fully observed Pose direction plus
    an independently supplied target, or returns an explicit invalid/null
    feature.  Zero is therefore a real zero-degree alignment, never a missing
    value sentinel.
    """

    source_frames = (
        list(launch_direction_feature.get("source_frames", []))
        if isinstance(launch_direction_feature, Mapping)
        else []
    )
    base = {
        "feature_name": TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
        "feature_version": TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION,
        "unit": "deg",
        "source_frames": source_frames,
    }
    if (
        not isinstance(launch_direction_feature, Mapping)
        or launch_direction_feature.get("valid") is not True
        or target_direction_deg is None
        or reference_confidence is None
    ):
        return {
            **base,
            "value": None,
            "signed_value": None,
            "confidence": 0.0,
            "valid": False,
            "reason": unavailable_reason,
            "observed_motion_direction_deg": (
                launch_direction_feature.get("value")
                if isinstance(launch_direction_feature, Mapping)
                and launch_direction_feature.get("valid") is True
                else None
            ),
            "target_direction_deg": target_direction_deg,
            "coordinate_frame": "image_plane",
            "direction_convention": "0_deg_image_right_90_deg_image_up",
        }
    observed = _finite_number(
        launch_direction_feature.get("value"), "launch_direction_feature.value"
    )
    target = _finite_number(target_direction_deg, "target_direction_deg")
    feature_confidence = _finite_number(
        launch_direction_feature.get("confidence"),
        "launch_direction_feature.confidence",
    )
    reference_confidence_value = _finite_number(
        reference_confidence, "reference_confidence"
    )
    signed_error = signed_angle_delta_deg(observed, target)
    return {
        **base,
        "value": round(abs(signed_error), 8),
        "signed_value": round(signed_error, 8),
        "confidence": round(min(feature_confidence, reference_confidence_value), 8),
        "valid": True,
        "reason": "valid_external_reference_alignment_not_grade",
        "observed_motion_direction_deg": round(observed, 8),
        "target_direction_deg": round(target, 8),
        "coordinate_frame": "image_plane",
        "direction_convention": "0_deg_image_right_90_deg_image_up",
    }


def compact_target_direction_alignment_feature(
    context: Mapping[str, Any],
    *,
    launch_direction_feature: Mapping[str, Any] | None,
) -> dict[str, Any]:
    feature = context.get("context_feature")
    if not isinstance(feature, Mapping):
        feature = build_target_direction_alignment_feature(
            launch_direction_feature=launch_direction_feature,
            target_direction_deg=None,
            reference_confidence=None,
            unavailable_reason=str(context.get("reason") or "target_direction_unavailable"),
        )
    return {
        "feature_name": feature["feature_name"],
        "feature_version": feature["feature_version"],
        "value": feature.get("value"),
        "unit": feature["unit"],
        "valid": feature.get("valid") is True,
        "confidence": float(feature.get("confidence", 0.0)),
        "reason": str(feature.get("reason") or "target_direction_unavailable"),
        "source_frames": list(feature.get("source_frames", [])),
    }


def validate_scoring_reference_context(
    payload: Mapping[str, Any],
    *,
    expected_video_id: str | None = None,
    expected_video_sha256: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("scoring reference context must be an object")
    expected_top = {
        "schema_version",
        "artifact_version",
        "artifact_scope",
        "video",
        "source",
        "coordinate_convention",
        "observations",
        "safety",
    }
    if set(payload) != expected_top:
        raise ValueError("scoring reference context fields are invalid")
    if payload.get("schema_version") != SCORING_REFERENCE_CONTEXT_SCHEMA_VERSION:
        raise ValueError("unsupported scoring reference context schema_version")
    if payload.get("artifact_version") != SCORING_REFERENCE_CONTEXT_VERSION:
        raise ValueError("unsupported scoring reference context artifact_version")
    if payload.get("artifact_scope") != "operator_declared_scoring_reference":
        raise ValueError("invalid scoring reference context artifact_scope")
    if payload.get("coordinate_convention") != "0_deg_image_right_90_deg_image_up":
        raise ValueError("unsupported scoring reference coordinate convention")

    video = payload.get("video")
    if not isinstance(video, Mapping) or set(video) != {"video_id", "video_sha256"}:
        raise ValueError("scoring reference context video binding is invalid")
    video_id = video.get("video_id")
    video_sha256 = video.get("video_sha256")
    if not isinstance(video_id, str) or not video_id:
        raise ValueError("scoring reference context video_id must be non-empty")
    if (
        not isinstance(video_sha256, str)
        or len(video_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in video_sha256)
    ):
        raise ValueError("scoring reference context video_sha256 must be SHA-256")
    if expected_video_id is not None and video_id != expected_video_id:
        raise ValueError("scoring reference context video_id mismatch")
    if (
        expected_video_sha256 is not None
        and video_sha256.lower() != expected_video_sha256.lower()
    ):
        raise ValueError("scoring reference context video_sha256 mismatch")

    source = payload.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "kind",
        "created_at",
        "created_by",
    }:
        raise ValueError("scoring reference context source is invalid")
    if source.get("kind") not in _SOURCE_KINDS:
        raise ValueError("unsupported scoring reference context source.kind")
    created_at = source.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("scoring reference context source.created_at must be non-empty")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scoring reference context created_at must be ISO-8601") from exc
    created_by = source.get("created_by")
    if created_by is not None and (not isinstance(created_by, str) or not created_by):
        raise ValueError("scoring reference context created_by must be null or non-empty")

    safety = payload.get("safety")
    if not isinstance(safety, Mapping) or set(safety) != {
        "is_grade",
        "contains_A_to_E_thresholds",
        "movement_direction_is_not_target_direction",
    }:
        raise ValueError("scoring reference context safety fields are invalid")
    if safety != {
        "is_grade": False,
        "contains_A_to_E_thresholds": False,
        "movement_direction_is_not_target_direction": True,
    }:
        raise ValueError("scoring reference context safety invariants failed")

    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("scoring reference context observations must be an array")
    result: dict[str, dict[str, Any]] = {}
    observation_ids: set[str] = set()
    for index, observation in enumerate(observations):
        prefix = f"observations[{index}]"
        if not isinstance(observation, dict):
            raise ValueError(f"{prefix} must be an object")
        expected_fields = {
            "observation_id",
            "event_id",
            "event_code",
            "start_ms",
            "end_ms",
            "indicator_id",
            "semantic_key",
            "status",
            "target_direction_deg",
            "coordinate_frame",
            "confidence",
            "observer_id",
            "reviewer_id",
            "reason",
        }
        if set(observation) != expected_fields:
            raise ValueError(f"{prefix} fields are invalid")
        observation_id = observation.get("observation_id")
        event_id = observation.get("event_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError(f"{prefix}.observation_id must be non-empty")
        if observation_id in observation_ids:
            raise ValueError(f"duplicate observation_id: {observation_id}")
        observation_ids.add(observation_id)
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"{prefix}.event_id must be non-empty")
        if event_id in result:
            raise ValueError(f"duplicate target direction event_id: {event_id}")
        if observation.get("event_code") != TARGET_DIRECTION_EVENT_CODE:
            raise ValueError(f"{prefix}.event_code must be FS02")
        if observation.get("indicator_id") != TARGET_DIRECTION_INDICATOR_ID:
            raise ValueError(f"{prefix}.indicator_id must be FS02-M02")
        if observation.get("semantic_key") != TARGET_DIRECTION_SEMANTIC_KEY:
            raise ValueError(f"{prefix}.semantic_key must be target_direction")
        start_ms = observation.get("start_ms")
        end_ms = observation.get("end_ms")
        if (
            isinstance(start_ms, bool)
            or not isinstance(start_ms, int)
            or start_ms < 0
            or isinstance(end_ms, bool)
            or not isinstance(end_ms, int)
            or end_ms <= start_ms
        ):
            raise ValueError(f"{prefix} event interval is invalid")
        status = observation.get("status")
        if status not in _STATUSES:
            raise ValueError(f"{prefix}.status is invalid")
        direction = observation.get("target_direction_deg")
        frame = observation.get("coordinate_frame")
        confidence = observation.get("confidence")
        observer_id = observation.get("observer_id")
        reviewer_id = observation.get("reviewer_id")
        reason = observation.get("reason")
        if status == "accepted":
            direction = _finite_number(direction, f"{prefix}.target_direction_deg")
            if not -180.0 <= direction <= 180.0:
                raise ValueError(f"{prefix}.target_direction_deg is outside -180..180")
            if frame != "image_plane":
                raise ValueError(f"{prefix}.coordinate_frame must be image_plane")
            confidence = _finite_number(confidence, f"{prefix}.confidence")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"{prefix}.confidence must be within 0..1")
            for field, value in (("observer_id", observer_id), ("reviewer_id", reviewer_id)):
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{prefix}.{field} must be non-empty when accepted")
            if observer_id == reviewer_id:
                raise ValueError(f"{prefix} accepted reference requires an independent reviewer")
            if reason is not None:
                raise ValueError(f"{prefix}.reason must be null when accepted")
        else:
            if any(value is not None for value in (direction, frame, confidence, observer_id, reviewer_id)):
                raise ValueError(f"{prefix} non-accepted fields must remain null")
            if not isinstance(reason, str) or not reason:
                raise ValueError(f"{prefix}.reason must explain non-accepted status")
        result[event_id] = dict(observation)
    return result


def build_scoring_reference_worklist(
    *,
    events: Sequence[Mapping[str, Any]],
    video_id: str,
    video_sha256: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(video_id, str) or not video_id:
        raise ValueError("video_id must be non-empty")
    if not isinstance(video_sha256, str) or len(video_sha256) != 64:
        raise ValueError("video_sha256 must be SHA-256")
    observations = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_code") != TARGET_DIRECTION_EVENT_CODE:
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("FS02 events must have unique non-empty event_id values")
        seen.add(event_id)
        start_ms = event.get("start_ms")
        end_ms = event.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int) or end_ms <= start_ms:
            raise ValueError(f"invalid FS02 event interval: {event_id}")
        observations.append(
            {
                "observation_id": f"target-direction-{event_id}",
                "event_id": event_id,
                "event_code": TARGET_DIRECTION_EVENT_CODE,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "indicator_id": TARGET_DIRECTION_INDICATOR_ID,
                "semantic_key": TARGET_DIRECTION_SEMANTIC_KEY,
                "status": "pending",
                "target_direction_deg": None,
                "coordinate_frame": None,
                "confidence": None,
                "observer_id": None,
                "reviewer_id": None,
                "reason": "coach_target_direction_annotation_required",
            }
        )
    payload = {
        "schema_version": SCORING_REFERENCE_CONTEXT_SCHEMA_VERSION,
        "artifact_version": SCORING_REFERENCE_CONTEXT_VERSION,
        "artifact_scope": "operator_declared_scoring_reference",
        "video": {"video_id": video_id, "video_sha256": video_sha256.upper()},
        "source": {
            "kind": "coach_declared_training_target",
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "created_by": None,
        },
        "coordinate_convention": "0_deg_image_right_90_deg_image_up",
        "observations": observations,
        "safety": {
            "is_grade": False,
            "contains_A_to_E_thresholds": False,
            "movement_direction_is_not_target_direction": True,
        },
    }
    validate_scoring_reference_context(
        payload,
        expected_video_id=video_id,
        expected_video_sha256=video_sha256,
    )
    return payload


def load_scoring_reference_context(
    path: str | Path,
    *,
    expected_video_id: str,
    expected_video_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    source_path = Path(path).resolve()
    try:
        source_bytes = source_path.read_bytes()
        payload = json.loads(source_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load scoring reference context: {source_path}") from exc
    observations = validate_scoring_reference_context(
        payload,
        expected_video_id=expected_video_id,
        expected_video_sha256=expected_video_sha256,
    )
    source_sha256 = hashlib.sha256(source_bytes).hexdigest().upper()
    return dict(payload), observations, source_sha256


def resolve_target_direction_context(
    *,
    event: Mapping[str, Any],
    launch_direction_feature: Mapping[str, Any] | None,
    observation: Mapping[str, Any] | None,
    source_sha256: str | None,
) -> dict[str, Any]:
    base = {
        "schema_version": "1.0.0",
        "context_version": SCORING_REFERENCE_CONTEXT_VERSION,
        "indicator_id": TARGET_DIRECTION_INDICATOR_ID,
        "event_id": event.get("event_id"),
        "semantic_key": TARGET_DIRECTION_SEMANTIC_KEY,
        "source_sha256": source_sha256,
        "is_grade": False,
        "threshold_version": None,
    }
    if observation is None:
        return {
            **base,
            "status": "missing",
            "observation_id": None,
            "reference_kind": None,
            "context_feature": None,
            "reason": "target_direction_observation_missing",
        }
    if (
        observation.get("event_code") != event.get("event_code")
        or observation.get("start_ms") != event.get("start_ms")
        or observation.get("end_ms") != event.get("end_ms")
    ):
        raise ValueError("target direction observation event binding mismatch")
    if observation.get("status") != "accepted":
        return {
            **base,
            "status": observation.get("status"),
            "observation_id": observation.get("observation_id"),
            "reference_kind": None,
            "context_feature": None,
            "reason": observation.get("reason"),
        }
    if not isinstance(launch_direction_feature, Mapping) or launch_direction_feature.get(
        "valid"
    ) is not True:
        return {
            **base,
            "status": "unavailable",
            "observation_id": observation.get("observation_id"),
            "reference_kind": "accepted_operator_reference",
            "context_feature": None,
            "reason": "observed_motion_direction_unavailable",
        }
    context_feature = build_target_direction_alignment_feature(
        launch_direction_feature=launch_direction_feature,
        target_direction_deg=observation.get("target_direction_deg"),
        reference_confidence=observation.get("confidence"),
        unavailable_reason="observed_motion_direction_unavailable",
    )
    context_feature["reason"] = "valid_operator_reference_alignment_not_grade"
    return {
        **base,
        "status": "available",
        "observation_id": observation.get("observation_id"),
        "observation_event_id": observation.get("event_id"),
        "reference_kind": "accepted_operator_reference",
        "observer_id": observation.get("observer_id"),
        "reviewer_id": observation.get("reviewer_id"),
        "context_feature": context_feature,
        "reason": "accepted_target_direction_and_observed_motion_direction_available",
    }


def validate_resolved_target_direction_context(
    context: Mapping[str, Any],
    *,
    event: Mapping[str, Any],
    launch_direction_feature: Mapping[str, Any] | None,
) -> None:
    if not isinstance(context, Mapping):
        raise ValueError("resolved target direction context must be an object")
    expected_context_fields = {
        "schema_version",
        "context_version",
        "indicator_id",
        "event_id",
        "semantic_key",
        "source_sha256",
        "is_grade",
        "threshold_version",
        "status",
        "observation_id",
        "reference_kind",
        "context_feature",
        "reason",
    }
    if context.get("status") == "available":
        expected_context_fields.update(
            {"observation_event_id", "observer_id", "reviewer_id"}
        )
    if set(context) != expected_context_fields:
        raise ValueError("resolved context fields are invalid")
    if context.get("schema_version") != SCORING_REFERENCE_CONTEXT_SCHEMA_VERSION:
        raise ValueError("resolved context schema_version mismatch")
    if context.get("context_version") != SCORING_REFERENCE_CONTEXT_VERSION:
        raise ValueError("resolved context context_version mismatch")
    if context.get("indicator_id") != TARGET_DIRECTION_INDICATOR_ID:
        raise ValueError("resolved context indicator_id mismatch")
    if context.get("event_id") != event.get("event_id"):
        raise ValueError("resolved context event_id mismatch")
    if context.get("semantic_key") != TARGET_DIRECTION_SEMANTIC_KEY:
        raise ValueError("resolved context semantic_key mismatch")
    if context.get("is_grade") is not False or context.get("threshold_version") is not None:
        raise ValueError("resolved context cannot contain a grade or threshold")
    status = context.get("status")
    if status not in {"missing", "pending", "unobservable", "unavailable", "available"}:
        raise ValueError("resolved context status is invalid")
    feature = context.get("context_feature")
    if status != "available":
        if feature is not None:
            raise ValueError("non-available context cannot expose a context feature")
        return
    if not isinstance(feature, Mapping) or feature.get("valid") is not True:
        raise ValueError("available context requires a valid context feature")
    expected_feature_fields = {
        "feature_name",
        "feature_version",
        "value",
        "signed_value",
        "unit",
        "confidence",
        "valid",
        "reason",
        "source_frames",
        "observed_motion_direction_deg",
        "target_direction_deg",
        "coordinate_frame",
        "direction_convention",
    }
    if set(feature) != expected_feature_fields:
        raise ValueError("resolved context feature fields are invalid")
    source_sha256 = context.get("source_sha256")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in source_sha256
        )
    ):
        raise ValueError("available context source_sha256 must be SHA-256")
    if not isinstance(context.get("observation_id"), str) or not context.get(
        "observation_id"
    ):
        raise ValueError("available context observation_id is required")
    if context.get("observation_event_id") != event.get("event_id"):
        raise ValueError("available context observation_event_id mismatch")
    if context.get("reference_kind") != "accepted_operator_reference":
        raise ValueError("available context reference_kind is invalid")
    observer_id = context.get("observer_id")
    reviewer_id = context.get("reviewer_id")
    if not isinstance(observer_id, str) or not observer_id:
        raise ValueError("available context observer_id is required")
    if not isinstance(reviewer_id, str) or not reviewer_id:
        raise ValueError("available context reviewer_id is required")
    if observer_id == reviewer_id:
        raise ValueError("available context requires an independent reviewer")
    if feature.get("feature_name") != TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME:
        raise ValueError("resolved context feature_name is invalid")
    if feature.get("feature_version") != TARGET_DIRECTION_ALIGNMENT_FEATURE_VERSION:
        raise ValueError("resolved context feature_version is invalid")
    if feature.get("unit") != "deg" or feature.get("coordinate_frame") != "image_plane":
        raise ValueError("resolved context feature unit/frame is invalid")
    if feature.get("direction_convention") != "0_deg_image_right_90_deg_image_up":
        raise ValueError("resolved context feature direction convention is invalid")
    if feature.get("reason") != "valid_operator_reference_alignment_not_grade":
        raise ValueError("resolved context feature reason is invalid")
    if context.get("reason") != (
        "accepted_target_direction_and_observed_motion_direction_available"
    ):
        raise ValueError("available context reason is invalid")
    if not isinstance(launch_direction_feature, Mapping) or launch_direction_feature.get(
        "valid"
    ) is not True:
        raise ValueError("available context requires a valid launch direction feature")
    observed = _finite_number(launch_direction_feature.get("value"), "launch direction")
    target = _finite_number(feature.get("target_direction_deg"), "target direction")
    if not -180.0 <= target <= 180.0:
        raise ValueError("resolved context target direction is outside -180..180")
    recorded_observed = _finite_number(
        feature.get("observed_motion_direction_deg"),
        "recorded observed motion direction",
    )
    if abs(recorded_observed - observed) > 1e-7:
        raise ValueError("resolved context observed motion direction mismatch")
    feature_source_frames = feature.get("source_frames")
    launch_source_frames = launch_direction_feature.get("source_frames")
    if (
        not isinstance(feature_source_frames, list)
        or any(
            isinstance(frame, bool) or not isinstance(frame, int) or frame < 0
            for frame in feature_source_frames
        )
        or not isinstance(launch_source_frames, list)
        or feature_source_frames != launch_source_frames
    ):
        raise ValueError("resolved context source_frames mismatch")
    confidence = _finite_number(feature.get("confidence"), "alignment confidence")
    launch_confidence = _finite_number(
        launch_direction_feature.get("confidence"), "launch direction confidence"
    )
    if not 0.0 <= confidence <= 1.0 or confidence > launch_confidence + 1e-7:
        raise ValueError("resolved context confidence is invalid")
    expected_signed = signed_angle_delta_deg(observed, target)
    actual_signed = _finite_number(feature.get("signed_value"), "signed alignment error")
    actual_absolute = _finite_number(feature.get("value"), "alignment error")
    if abs(actual_signed - expected_signed) > 1e-7:
        raise ValueError("resolved context signed alignment error mismatch")
    if abs(actual_absolute - abs(expected_signed)) > 1e-7:
        raise ValueError("resolved context absolute alignment error mismatch")
