from __future__ import annotations

"""Content-bound metadata for indicator-feature calibration sources.

The persisted snapshot in a calibration sample is deliberately separate from
the process-local object defined here.  The snapshot makes the eligibility
calculation repeatable; the process-local object establishes that the JSONL
bytes came from the exact scoring-summary/run-bundle entry supplied by the
operator for this compiler invocation.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from rallymate_scoring.quality_policy import (
    QUALITY_POLICY_VERSION,
    evaluate_indicator_event_quality,
)
from rallymate_scoring.run_bundle_binding import (
    ScoringRunBundleBindingError,
    authorize_scoring_run_bundle,
)
from rallymate_scoring.scoring_context import (
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
    TARGET_DIRECTION_INDICATOR_ID,
    TARGET_DIRECTION_MISSING_FLAG,
)


INDICATOR_FEATURE_SOURCE_METADATA_VERSION = (
    "indicator-feature-source-metadata-v1.0.0"
)
QUALIFICATION_SNAPSHOT_VERSION = "calibration-feature-qualification-v1.0.0"
VERIFIED_SOURCE_STATUS = "verified_scoring_run_bundle_source"
SYNTHETIC_SOURCE_STATUS = "synthetic_test_only_source"
DIAGNOSTIC_SOURCE_STATUS = "unverified_diagnostic_source"
SOURCE_STATUSES = frozenset(
    {VERIFIED_SOURCE_STATUS, SYNTHETIC_SOURCE_STATUS, DIAGNOSTIC_SOURCE_STATUS}
)
CANONICALIZATION = "rallymate-canonical-json-v1"

_HEX = frozenset("0123456789abcdefABCDEF")
_QUALITY_GATE_FIELDS = {
    "schema_version",
    "policy_version",
    "indicator_id",
    "status",
    "hard_fail",
    "measurement_allowed",
    "scoring_allowed",
    "input_quality_flags",
    "hard_fail_flags",
    "scoring_block_flags",
    "advisory_flags",
    "non_blocking_flags",
    "unclassified_flags",
    "semantics",
}
_SOURCE_METADATA_FIELDS = {
    "metadata_version",
    "canonicalization",
    "video_id",
    "indicator_features_raw_sha256",
    "indicator_feature_record_count",
    "scoring_summary_raw_sha256",
    "run_bundle_root_sha256",
    "run_bundle_entry_id",
    "run_bundle_ledger_id",
    "run_bundle_ledger_version",
    "run_bundle_ledger_canonical_sha256",
    "run_bundle_authority_id",
}
_SNAPSHOT_FIELDS = {
    "snapshot_version",
    "source_status",
    "feature_record_present",
    "feature_status",
    "quality_policy_version",
    "quality_gate",
    "resolved_target_direction",
    "source_feature_canonical_sha256",
    "source_metadata",
}


class IndicatorFeatureQualificationError(ValueError):
    """Raised when feature-source metadata or a qualification snapshot differs."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IndicatorFeatureQualificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(raw: bytes, *, field: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IndicatorFeatureQualificationError(
            f"{field} must be strict UTF-8 JSON"
        ) from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(
                IndicatorFeatureQualificationError(
                    f"{field} contains non-finite JSON number: {item}"
                )
            ),
        )
    except json.JSONDecodeError as exc:
        raise IndicatorFeatureQualificationError(f"invalid {field}: {exc}") from exc
    if not isinstance(value, dict):
        raise IndicatorFeatureQualificationError(f"{field} must be an object")
    return value


def _parse_jsonl(raw: bytes, *, field: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IndicatorFeatureQualificationError(
            f"{field} must be strict UTF-8 JSONL"
        ) from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    IndicatorFeatureQualificationError(
                        f"{field}:{line_number} contains non-finite JSON number: {item}"
                    )
                ),
            )
        except json.JSONDecodeError as exc:
            raise IndicatorFeatureQualificationError(
                f"invalid {field}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise IndicatorFeatureQualificationError(
                f"{field}:{line_number} must be an object"
            )
        records.append(value)
    return records


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(raw)


def _sha256(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise IndicatorFeatureQualificationError(f"{field} must be SHA-256")
    return value.lower()


def _string(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise IndicatorFeatureQualificationError(f"{field} must be non-empty")
    return value


def _plain_file(path: str | Path, field: str) -> tuple[Path, bytes]:
    requested = Path(path)
    if requested.is_symlink():
        raise IndicatorFeatureQualificationError(f"{field} must be a plain file")
    resolved = requested.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise IndicatorFeatureQualificationError(f"{field} must be a plain file")
    return resolved, resolved.read_bytes()


def _declared_indicator_feature_path(summary_path: Path, summary: dict[str, Any]) -> Path:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        raise IndicatorFeatureQualificationError(
            "scoring_summary.artifacts must be an object"
        )
    declared = artifacts.get("indicator_features_jsonl")
    if not isinstance(declared, str) or not declared:
        raise IndicatorFeatureQualificationError(
            "scoring_summary.artifacts.indicator_features_jsonl is required"
        )
    candidate = Path(declared)
    if not candidate.is_absolute():
        candidate = summary_path.parent / candidate
    return candidate.resolve()


@dataclass(frozen=True, slots=True)
class VerifiedIndicatorFeatureSourceMetadata:
    """Non-serializable same-process result of one source-metadata check."""

    _token: object
    _summary_path: Path
    _summary_raw: bytes
    _indicator_features_path: Path
    _indicator_features_raw: bytes
    _metadata: dict[str, Any]

    def __reduce__(self) -> Any:  # pragma: no cover - defensive serialization guard
        raise TypeError("verified indicator-feature source metadata is process-local")

    @property
    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._metadata)


_VERIFICATION_TOKEN = object()


def verify_indicator_feature_source_metadata(
    *,
    scoring_summary_path: str | Path,
    indicator_features_path: str | Path,
    run_bundle_ledger: dict[str, Any],
) -> VerifiedIndicatorFeatureSourceMetadata:
    """Read each source once and bind the exact JSONL bytes to one ledger entry."""

    summary_path, summary_raw = _plain_file(
        scoring_summary_path, "scoring_summary_path"
    )
    features_path, features_raw = _plain_file(
        indicator_features_path, "indicator_features_path"
    )
    summary = _parse_json(summary_raw, field="scoring summary")
    records = _parse_jsonl(features_raw, field="indicator features")
    if _declared_indicator_feature_path(summary_path, summary) != features_path:
        raise IndicatorFeatureQualificationError(
            "scoring summary indicator-features path differs from the supplied JSONL"
        )
    artifact_hashes = summary.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        raise IndicatorFeatureQualificationError(
            "scoring_summary.artifact_sha256 must be an object"
        )
    features_sha256 = _sha256_bytes(features_raw)
    declared_sha256 = _sha256(
        artifact_hashes.get("indicator_features_jsonl"),
        "scoring_summary.artifact_sha256.indicator_features_jsonl",
    )
    if declared_sha256 != features_sha256:
        raise IndicatorFeatureQualificationError(
            "scoring summary indicator-features SHA differs from the supplied JSONL"
        )
    try:
        authorization = authorize_scoring_run_bundle(
            summary=summary,
            scoring_summary_sha256=_sha256_bytes(summary_raw),
            ledger=run_bundle_ledger,
        )
    except ScoringRunBundleBindingError as exc:
        raise IndicatorFeatureQualificationError(str(exc)) from exc
    video_id = _string(authorization.get("video_id"), "run_bundle.video_id")
    for index, record in enumerate(records):
        record_video_id = record.get("video_id")
        if not isinstance(record_video_id, str) or not (
            record_video_id == video_id or record_video_id.startswith(video_id + ":")
        ):
            raise IndicatorFeatureQualificationError(
                "indicator feature record video_id differs from scoring summary: "
                f"records[{index}]"
            )
    metadata = {
        "metadata_version": INDICATOR_FEATURE_SOURCE_METADATA_VERSION,
        "canonicalization": CANONICALIZATION,
        "video_id": video_id,
        "indicator_features_raw_sha256": features_sha256,
        "indicator_feature_record_count": len(records),
        "scoring_summary_raw_sha256": _sha256_bytes(summary_raw),
        "run_bundle_root_sha256": _sha256(
            authorization.get("bundle_root_sha256"), "run_bundle.bundle_root_sha256"
        ),
        "run_bundle_entry_id": _string(
            authorization.get("entry_id"), "run_bundle.entry_id"
        ),
        "run_bundle_ledger_id": _string(
            authorization.get("ledger_id"), "run_bundle.ledger_id"
        ),
        "run_bundle_ledger_version": _string(
            authorization.get("ledger_version"), "run_bundle.ledger_version"
        ),
        "run_bundle_ledger_canonical_sha256": _canonical_sha256(
            run_bundle_ledger
        ),
        "run_bundle_authority_id": _string(
            authorization.get("ledger_authority_id"), "run_bundle.ledger_authority_id"
        ),
    }
    validate_source_metadata(metadata, require_verified=True)
    return VerifiedIndicatorFeatureSourceMetadata(
        _VERIFICATION_TOKEN,
        summary_path,
        summary_raw,
        features_path,
        features_raw,
        metadata,
    )


def require_verified_indicator_feature_source_metadata(
    value: Any,
    *,
    expected_indicator_features_path: str | Path,
    expected_indicator_features_raw: bytes,
) -> dict[str, Any]:
    if (
        not isinstance(value, VerifiedIndicatorFeatureSourceMetadata)
        or value._token is not _VERIFICATION_TOKEN
    ):
        raise IndicatorFeatureQualificationError(
            "same-process verified indicator-feature source metadata is required"
        )
    expected_path = Path(expected_indicator_features_path).resolve()
    if value._indicator_features_path != expected_path:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source metadata path does not match compiler input"
        )
    if value._indicator_features_raw != expected_indicator_features_raw:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source metadata bytes do not match compiler input"
        )
    metadata = value.metadata
    validate_source_metadata(metadata, require_verified=True)
    return metadata


def assert_indicator_feature_source_metadata_unchanged(
    value: VerifiedIndicatorFeatureSourceMetadata,
) -> None:
    if value._token is not _VERIFICATION_TOKEN:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source metadata object is invalid"
        )
    for field, path, raw in (
        ("scoring summary", value._summary_path, value._summary_raw),
        ("indicator features", value._indicator_features_path, value._indicator_features_raw),
    ):
        if not path.is_file() or path.is_symlink() or path.read_bytes() != raw:
            raise IndicatorFeatureQualificationError(
                f"{field} changed before calibration dataset commit"
            )


def nonproduction_source_metadata(
    *,
    indicator_features_raw_sha256: str,
    record_count: int,
    video_id: str | None,
) -> dict[str, Any]:
    metadata = {
        "metadata_version": INDICATOR_FEATURE_SOURCE_METADATA_VERSION,
        "canonicalization": CANONICALIZATION,
        "video_id": video_id,
        "indicator_features_raw_sha256": indicator_features_raw_sha256,
        "indicator_feature_record_count": record_count,
        "scoring_summary_raw_sha256": None,
        "run_bundle_root_sha256": None,
        "run_bundle_entry_id": None,
        "run_bundle_ledger_id": None,
        "run_bundle_ledger_version": None,
        "run_bundle_ledger_canonical_sha256": None,
        "run_bundle_authority_id": None,
    }
    validate_source_metadata(metadata, require_verified=False)
    return metadata


def validate_source_metadata(
    value: Any, *, require_verified: bool
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SOURCE_METADATA_FIELDS:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source metadata fields are invalid"
        )
    if value.get("metadata_version") != INDICATOR_FEATURE_SOURCE_METADATA_VERSION:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source metadata version is invalid"
        )
    if value.get("canonicalization") != CANONICALIZATION:
        raise IndicatorFeatureQualificationError(
            "indicator-feature source canonicalization is invalid"
        )
    video_id = _string(value.get("video_id"), "source_metadata.video_id", nullable=True)
    raw_sha256 = _sha256(
        value.get("indicator_features_raw_sha256"),
        "source_metadata.indicator_features_raw_sha256",
    )
    count = value.get("indicator_feature_record_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise IndicatorFeatureQualificationError(
            "source_metadata.indicator_feature_record_count must be non-negative"
        )
    result = {
        "metadata_version": INDICATOR_FEATURE_SOURCE_METADATA_VERSION,
        "canonicalization": CANONICALIZATION,
        "video_id": video_id,
        "indicator_features_raw_sha256": raw_sha256,
        "indicator_feature_record_count": count,
    }
    for field in (
        "scoring_summary_raw_sha256",
        "run_bundle_root_sha256",
        "run_bundle_ledger_canonical_sha256",
    ):
        result[field] = _sha256(
            value.get(field), f"source_metadata.{field}", nullable=not require_verified
        )
    for field in (
        "run_bundle_entry_id",
        "run_bundle_ledger_id",
        "run_bundle_ledger_version",
        "run_bundle_authority_id",
    ):
        result[field] = _string(
            value.get(field), f"source_metadata.{field}", nullable=not require_verified
        )
    if require_verified and video_id is None:
        raise IndicatorFeatureQualificationError(
            "verified source metadata requires video_id"
        )
    return result


def validate_quality_gate(value: Any, *, indicator_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _QUALITY_GATE_FIELDS:
        raise IndicatorFeatureQualificationError(
            "qualification quality_gate fields are incomplete"
        )
    flags = value.get("input_quality_flags")
    if not isinstance(flags, list) or any(
        not isinstance(flag, str) or not flag for flag in flags
    ):
        raise IndicatorFeatureQualificationError(
            "qualification quality_gate.input_quality_flags must be a string array"
        )
    expected = evaluate_indicator_event_quality(indicator_id, flags)
    if value != expected:
        raise IndicatorFeatureQualificationError(
            "qualification quality_gate differs from the versioned quality policy"
        )
    return deepcopy(expected)


def build_qualification_snapshot(
    *,
    indicator_id: str,
    feature_record: dict[str, Any] | None,
    source_feature_canonical_sha256: str | None,
    source_status: str,
    source_metadata: dict[str, Any],
    resolved_target_direction: bool,
) -> dict[str, Any]:
    if source_status not in SOURCE_STATUSES:
        raise IndicatorFeatureQualificationError(
            "qualification source_status is invalid"
        )
    require_verified = source_status == VERIFIED_SOURCE_STATUS
    metadata = validate_source_metadata(
        source_metadata, require_verified=require_verified
    )
    present = feature_record is not None
    feature_status: str | None = None
    quality_gate: dict[str, Any] | None = None
    feature_sha256: str | None = None
    if present:
        raw_status = feature_record.get("feature_status")
        if raw_status is not None and (
            not isinstance(raw_status, str) or not raw_status
        ):
            raise IndicatorFeatureQualificationError(
                "indicator feature_status must be null or non-empty"
            )
        feature_status = raw_status
        supplied_gate = feature_record.get("quality_gate")
        if supplied_gate is not None:
            quality_gate = validate_quality_gate(
                supplied_gate, indicator_id=indicator_id
            )
        feature_sha256 = _sha256(
            source_feature_canonical_sha256,
            "source_feature_canonical_sha256",
        )
        if feature_sha256 != _canonical_sha256(feature_record):
            raise IndicatorFeatureQualificationError(
                "source feature canonical SHA does not match feature record"
            )
    elif source_feature_canonical_sha256 is not None:
        raise IndicatorFeatureQualificationError(
            "missing feature record cannot carry a canonical SHA"
        )
    snapshot = {
        "snapshot_version": QUALIFICATION_SNAPSHOT_VERSION,
        "source_status": source_status,
        "feature_record_present": present,
        "feature_status": feature_status,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "quality_gate": quality_gate,
        "resolved_target_direction": bool(resolved_target_direction),
        "source_feature_canonical_sha256": feature_sha256,
        "source_metadata": metadata,
    }
    validate_qualification_snapshot(
        snapshot, indicator_id=indicator_id, require_verified_source=require_verified
    )
    return snapshot


def _feature_values_complete(feature_vector: Any) -> bool:
    if not isinstance(feature_vector, list) or not feature_vector:
        return False
    for item in feature_vector:
        if not isinstance(item, Mapping):
            return False
        value = item.get("value")
        if (
            item.get("valid") is not True
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not isinstance(item.get("unit"), str)
            or not item["unit"]
            or not isinstance(item.get("feature_version"), str)
            or not item["feature_version"]
        ):
            return False
    return True


def _resolved_target_direction_from_vector(
    indicator_id: str, feature_vector: list[dict[str, Any]]
) -> bool:
    if indicator_id != TARGET_DIRECTION_INDICATOR_ID:
        return False
    return any(
        item.get("feature_name") == TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME
        and item.get("valid") is True
        and isinstance(item.get("value"), (int, float))
        and not isinstance(item.get("value"), bool)
        and math.isfinite(float(item["value"]))
        for item in feature_vector
    )


def validate_qualification_snapshot(
    value: Any,
    *,
    indicator_id: str,
    require_verified_source: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_FIELDS:
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot fields are invalid"
        )
    if value.get("snapshot_version") != QUALIFICATION_SNAPSHOT_VERSION:
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot version is invalid"
        )
    source_status = value.get("source_status")
    if source_status not in SOURCE_STATUSES:
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot source_status is invalid"
        )
    if require_verified_source and source_status != VERIFIED_SOURCE_STATUS:
        raise IndicatorFeatureQualificationError(
            "real independent-test sample requires verified feature source metadata"
        )
    present = value.get("feature_record_present")
    if not isinstance(present, bool):
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot.feature_record_present must be boolean"
        )
    feature_status = value.get("feature_status")
    if feature_status is not None and (
        not isinstance(feature_status, str) or not feature_status
    ):
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot.feature_status must be null or non-empty"
        )
    if value.get("quality_policy_version") != QUALITY_POLICY_VERSION:
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot quality policy version is unsupported"
        )
    quality_gate = value.get("quality_gate")
    if quality_gate is not None:
        quality_gate = validate_quality_gate(quality_gate, indicator_id=indicator_id)
    resolved = value.get("resolved_target_direction")
    if not isinstance(resolved, bool):
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot.resolved_target_direction must be boolean"
        )
    feature_sha256 = _sha256(
        value.get("source_feature_canonical_sha256"),
        "qualification_snapshot.source_feature_canonical_sha256",
        nullable=not present,
    )
    if present and feature_sha256 is None:
        raise IndicatorFeatureQualificationError(
            "present feature record requires source_feature_canonical_sha256"
        )
    if not present and (
        feature_status is not None or quality_gate is not None or feature_sha256 is not None
    ):
        raise IndicatorFeatureQualificationError(
            "missing feature record cannot carry feature status, gate, or record SHA"
        )
    metadata = validate_source_metadata(
        value.get("source_metadata"),
        require_verified=(source_status == VERIFIED_SOURCE_STATUS),
    )
    return {
        "snapshot_version": QUALIFICATION_SNAPSHOT_VERSION,
        "source_status": source_status,
        "feature_record_present": present,
        "feature_status": feature_status,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "quality_gate": quality_gate,
        "resolved_target_direction": resolved,
        "source_feature_canonical_sha256": feature_sha256,
        "source_metadata": metadata,
    }


def derive_feature_qualification(
    *,
    snapshot: dict[str, Any],
    indicator_id: str,
    feature_vector: list[dict[str, Any]],
    require_verified_source: bool,
) -> tuple[bool, list[str]]:
    normalized = validate_qualification_snapshot(
        snapshot,
        indicator_id=indicator_id,
        require_verified_source=require_verified_source,
    )
    expected_resolved = _resolved_target_direction_from_vector(
        indicator_id, feature_vector
    )
    if normalized["resolved_target_direction"] is not expected_resolved:
        raise IndicatorFeatureQualificationError(
            "qualification_snapshot.resolved_target_direction differs from feature vector"
        )
    reasons: list[str] = []
    if not normalized["feature_record_present"]:
        reasons.append("indicator_feature_join_missing")
    elif normalized["feature_status"] != "measured":
        reasons.append("indicator_feature_status_unavailable")
    gate = normalized["quality_gate"]
    if gate is None:
        if require_verified_source:
            reasons.append("quality_gate_missing")
    else:
        if gate["hard_fail"] is True:
            reasons.append("quality_gate_hard_fail")
        if gate["measurement_allowed"] is not True:
            reasons.append("quality_gate_measurement_disallowed")
        if gate["scoring_allowed"] is not True:
            scoring_flags = set(gate["scoring_block_flags"])
            if normalized["resolved_target_direction"]:
                scoring_flags.discard(TARGET_DIRECTION_MISSING_FLAG)
            if scoring_flags:
                reasons.append("quality_gate_scoring_disallowed")
    complete = _feature_values_complete(feature_vector) and not reasons
    return complete, sorted(set(reasons))


__all__ = [
    "DIAGNOSTIC_SOURCE_STATUS",
    "INDICATOR_FEATURE_SOURCE_METADATA_VERSION",
    "IndicatorFeatureQualificationError",
    "QUALIFICATION_SNAPSHOT_VERSION",
    "SYNTHETIC_SOURCE_STATUS",
    "VERIFIED_SOURCE_STATUS",
    "VerifiedIndicatorFeatureSourceMetadata",
    "assert_indicator_feature_source_metadata_unchanged",
    "build_qualification_snapshot",
    "derive_feature_qualification",
    "nonproduction_source_metadata",
    "require_verified_indicator_feature_source_metadata",
    "validate_qualification_snapshot",
    "verify_indicator_feature_source_metadata",
]
