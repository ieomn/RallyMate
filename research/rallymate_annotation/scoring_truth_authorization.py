from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_annotation.scoring_truth_event_handoff import (
    ScoringTruthEventHandoffError,
    validate_scoring_truth_event_handoff,
)


SCHEMA_VERSION = "1.0.0"
PLAN_VERSION = "scoring-truth-event-annotation-plan-v2.0.0"
RELEASE_VERSION = "scoring-truth-operator-reviewed-local-release-v1.0.0"
BINDING_VERSION = "scoring-truth-event-authorization-binding-v2.0.0"
CANONICALIZATION = "rallymate-canonical-json-v1"
HANDOFF_VERSION = "scoring-truth-event-handoff-v1.0.0"
DECISION = "operator_released_for_independent_event_phase_annotation"

PLAN_ARTIFACT_SCOPE = "scoring_truth_event_annotation_plan"
RELEASE_ARTIFACT_SCOPE = "scoring_truth_operator_reviewed_local_release"
REVIEWER_ROLE = "annotation_release_operator"
BINDING_STATUS = "operator_reviewed_local_release_verified"

EVENT_CODES = ["FS01", "FS02", "FS09"]
PHASE_KEYS_BY_EVENT = {
    "FS01": [
        "preload_ms",
        "takeoff_proxy_ms",
        "landing_proxy_ms",
        "redistribution_ms",
        "initiation_ms",
    ],
    "FS02": [
        "direction_conversion_ms",
        "support_extension_proxy_ms",
        "lead_foot_motion_onset_proxy_ms",
        "first_step_slowdown_proxy_ms",
    ],
    "FS09": [
        "peak_speed_ms",
        "deceleration_peak_ms",
        "restabilization_onset_ms",
        "stable_control_onset_ms",
    ],
}
TASKS = [
    {
        "task_id": "m89:3ae77ee3271d67de171585a5c39ddd69:full-video-event-phase",
        "video_id": "3ae77ee3271d67de171585a5c39ddd69",
        "video_sha256": "D5194DF6070DA253DEF5DB599E4F81847F4BEF3E146AB0A5D39F7F077F9CB5D9",
        "full_video_review_required": True,
    },
    {
        "task_id": "m89:850cb0006b406c7176eeda8d711cd065:full-video-event-phase",
        "video_id": "850cb0006b406c7176eeda8d711cd065",
        "video_sha256": "71D3F59B7A8B966EF7645CAC412CB03679376F70A2F080D270391A32697D7FA9",
        "full_video_review_required": True,
    },
    {
        "task_id": "m89:8d7754d0de6d315674013d5b69a0b6ba:full-video-event-phase",
        "video_id": "8d7754d0de6d315674013d5b69a0b6ba",
        "video_sha256": "FEDA989F394818B27AE38E5BA12DF4690E6139DF53DB9CD87E6C6278B3C2E7E0",
        "full_video_review_required": True,
    },
]
ROLES = {
    "annotator_slots": ["A", "B"],
    "reviewer_slot": "C",
    "required_distinct_people": 3,
    "annotator_blinding_required": True,
    "reviewer_sees_both_only_after_both_submitted": True,
}
DECISION_CONTRACT = {
    "event_scope": "full_timeline_all_occurrences",
    "phase_observation": "observed_or_unobservable_with_reason",
    "missing_not_zero": True,
    "full_video_review_before_submit": True,
    "first_write_requires_verified_operator_release": True,
    "revision_binding_required": True,
    "adjudication_binds_both_exact_revision_sha256": True,
}
DATA_ACCESS = {
    "public_released_bundle_only": True,
    "repository_access_forbidden": True,
    "reports_access_forbidden": True,
    "model_output_access_forbidden": True,
    "private_truth_pack_access_forbidden": True,
}
SAFETY = {
    "hash_is_operator_identity_or_approval": False,
    "technical_handoff_is_annotation_authority": False,
    "plan_is_annotation_authority": False,
    "operator_release_record_required": True,
    "operator_release_record_is_digital_signature": False,
    "operator_release_record_is_trusted_timestamp": False,
    "grades_or_thresholds_supported": False,
    "calibration_authorized": False,
    "promotion_authorized": False,
    "production_enabled": False,
}
RELEASE_ATTESTATIONS = {
    "exact_plan_reviewed": True,
    "exact_handoff_reviewed": True,
    "blind_role_protocol_reviewed": True,
    "annotation_only_scope_reviewed": True,
}
RELEASE_SAFETY = {
    "annotation_workflow_release_only": True,
    "digital_signature_authority": False,
    "trusted_timestamp_authority": False,
    "calibration_authorized": False,
    "promotion_authorized": False,
    "production_scoring_authorized": False,
}

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)


class ScoringTruthAuthorizationError(ValueError):
    """The supplied evidence does not establish the narrow M90 workflow release."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ScoringTruthAuthorizationError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _reject_json_constant(value: str) -> None:
    raise ScoringTruthAuthorizationError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScoringTruthAuthorizationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag):
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _snapshot_file(path: Path, *, name: str) -> bytes:
    requested = path.expanduser().absolute()
    try:
        before_path = requested.lstat()
        before_resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthAuthorizationError(f"missing file: {name}") from exc
    if _is_link_like(requested) or not stat.S_ISREG(before_path.st_mode):
        raise ScoringTruthAuthorizationError(f"{name} must be a regular non-link file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(requested, flags)
        before_open = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
        after_path = requested.lstat()
        after_resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ScoringTruthAuthorizationError(f"could not snapshot file: {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    raw = b"".join(chunks)
    identities = {
        _path_identity(before_path),
        _path_identity(before_open),
        _path_identity(after_open),
        _path_identity(after_path),
    }
    if (
        len(identities) != 1
        or len(raw) != after_open.st_size
        or before_resolved != after_resolved
    ):
        raise ScoringTruthAuthorizationError(f"file changed while read: {name}")
    return raw


def _parse_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ScoringTruthAuthorizationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ScoringTruthAuthorizationError(
            f"{name} must be strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ScoringTruthAuthorizationError(f"{name} must be a JSON object")
    return value


def _snapshot_json(path: str | Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    raw = _snapshot_file(Path(path), name=name)
    return _parse_json_object(raw, name=name), raw


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], *, name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ScoringTruthAuthorizationError(
            f"{name} keys are invalid (missing={missing}, extra={extra})"
        )


def _require_identifier(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ScoringTruthAuthorizationError(f"{name} must be a safe identifier")
    return value


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ScoringTruthAuthorizationError(f"{name} must be uppercase SHA-256")
    return value


def _require_exact_json(value: Any, expected: Any, *, name: str) -> None:
    if _canonical_json_bytes(value) != _canonical_json_bytes(expected):
        raise ScoringTruthAuthorizationError(f"{name} is invalid")


def _parse_canonical_utc(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise ScoringTruthAuthorizationError(f"{name} must be canonical UTC with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ScoringTruthAuthorizationError(f"{name} is not a valid timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ScoringTruthAuthorizationError(f"{name} must identify UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise ScoringTruthAuthorizationError(f"{name} is not canonical UTC")
    return parsed


def _validate_technical_handoff(
    technical: Any, *, name: str, include_manifest_path: bool
) -> None:
    if not isinstance(technical, Mapping):
        raise ScoringTruthAuthorizationError(f"{name} must be an object")
    expected = {
        "manifest_raw_sha256",
        "bundle_version",
        "bundle_id",
        "content_root_sha256",
        "source_projection_sha256",
    }
    if include_manifest_path:
        expected.add("manifest_path")
    _require_exact_keys(technical, expected, name=name)
    if include_manifest_path and technical["manifest_path"] != "event-handoff-manifest.json":
        raise ScoringTruthAuthorizationError(f"{name}.manifest_path is invalid")
    if technical["bundle_version"] != HANDOFF_VERSION:
        raise ScoringTruthAuthorizationError(f"{name}.bundle_version is invalid")
    if (
        not isinstance(technical["bundle_id"], str)
        or re.fullmatch(
            r"m89-scoring-truth-event-[0-9A-F]{64}", technical["bundle_id"]
        )
        is None
    ):
        raise ScoringTruthAuthorizationError(f"{name}.bundle_id is invalid")
    for key in (
        "manifest_raw_sha256",
        "content_root_sha256",
        "source_projection_sha256",
    ):
        _require_sha(technical[key], name=f"{name}.{key}")


def _validate_plan(plan: dict[str, Any]) -> tuple[datetime, datetime]:
    _require_exact_keys(
        plan,
        {
            "schema_version",
            "plan_version",
            "plan_id",
            "created_at",
            "frozen_at",
            "artifact_scope",
            "technical_handoff",
            "scope",
            "roles",
            "decision_contract",
            "data_access",
            "safety",
        },
        name="plan",
    )
    if plan["schema_version"] != SCHEMA_VERSION:
        raise ScoringTruthAuthorizationError("plan schema_version is unsupported")
    if plan["plan_version"] != PLAN_VERSION:
        raise ScoringTruthAuthorizationError("plan_version is unsupported")
    _require_identifier(plan["plan_id"], name="plan_id")
    if plan["artifact_scope"] != PLAN_ARTIFACT_SCOPE:
        raise ScoringTruthAuthorizationError("plan artifact_scope is invalid")

    created_at = _parse_canonical_utc(plan["created_at"], name="plan.created_at")
    frozen_at = _parse_canonical_utc(plan["frozen_at"], name="plan.frozen_at")
    if frozen_at < created_at:
        raise ScoringTruthAuthorizationError("plan must be frozen after it is created")

    _validate_technical_handoff(
        plan["technical_handoff"],
        name="plan.technical_handoff",
        include_manifest_path=True,
    )
    scope = plan["scope"]
    if not isinstance(scope, Mapping):
        raise ScoringTruthAuthorizationError("plan.scope must be an object")
    _require_exact_keys(
        scope, {"event_codes", "phase_keys_by_event", "tasks"}, name="plan.scope"
    )
    _require_exact_json(
        scope,
        {
            "event_codes": EVENT_CODES,
            "phase_keys_by_event": PHASE_KEYS_BY_EVENT,
            "tasks": TASKS,
        },
        name="plan.scope (exact M89 scope)",
    )
    _require_exact_json(plan["roles"], ROLES, name="plan.roles")
    _require_exact_json(
        plan["decision_contract"], DECISION_CONTRACT, name="plan.decision_contract"
    )
    _require_exact_json(plan["data_access"], DATA_ACCESS, name="plan.data_access")
    _require_exact_json(plan["safety"], SAFETY, name="plan.safety")
    return created_at, frozen_at


def _validate_release_record(record: dict[str, Any]) -> datetime:
    _require_exact_keys(
        record,
        {
            "schema_version",
            "release_version",
            "artifact_scope",
            "release_id",
            "released_at",
            "reviewed_by",
            "decision",
            "plan",
            "technical_handoff",
            "scope_digest_sha256",
            "role_protocol_digest_sha256",
            "attestations",
            "safety",
        },
        name="operator release record",
    )
    if record["schema_version"] != SCHEMA_VERSION:
        raise ScoringTruthAuthorizationError(
            "operator release record schema_version is unsupported"
        )
    if record["release_version"] != RELEASE_VERSION:
        raise ScoringTruthAuthorizationError("operator release version is unsupported")
    if record["artifact_scope"] != RELEASE_ARTIFACT_SCOPE:
        raise ScoringTruthAuthorizationError("operator release artifact_scope is invalid")
    _require_identifier(record["release_id"], name="operator release release_id")
    released_at = _parse_canonical_utc(
        record["released_at"], name="operator release released_at"
    )

    reviewed_by = record["reviewed_by"]
    if not isinstance(reviewed_by, Mapping):
        raise ScoringTruthAuthorizationError("operator release reviewed_by must be an object")
    _require_exact_keys(
        reviewed_by, {"reviewer_id", "role"}, name="operator release reviewed_by"
    )
    _require_identifier(
        reviewed_by["reviewer_id"], name="operator release reviewer_id"
    )
    if reviewed_by["role"] != REVIEWER_ROLE:
        raise ScoringTruthAuthorizationError("operator release reviewer role is invalid")
    if record["decision"] != DECISION:
        raise ScoringTruthAuthorizationError("operator release decision is invalid")

    plan = record["plan"]
    if not isinstance(plan, Mapping):
        raise ScoringTruthAuthorizationError("operator release plan must be an object")
    _require_exact_keys(
        plan, {"plan_id", "plan_version", "raw_sha256"}, name="operator release plan"
    )
    _require_identifier(plan["plan_id"], name="operator release plan_id")
    if plan["plan_version"] != PLAN_VERSION:
        raise ScoringTruthAuthorizationError("operator release plan_version is invalid")
    _require_sha(plan["raw_sha256"], name="operator release plan raw_sha256")

    _validate_technical_handoff(
        record["technical_handoff"],
        name="operator release technical_handoff",
        include_manifest_path=False,
    )
    _require_sha(record["scope_digest_sha256"], name="operator release scope digest")
    _require_sha(
        record["role_protocol_digest_sha256"],
        name="operator release role protocol digest",
    )
    _require_exact_json(
        record["attestations"], RELEASE_ATTESTATIONS, name="operator release attestations"
    )
    _require_exact_json(
        record["safety"], RELEASE_SAFETY, name="operator release safety"
    )
    return released_at


def _assert_not_template_path(path: str | Path, *, name: str) -> None:
    if Path(path).expanduser().name.casefold().endswith(".template.json"):
        raise ScoringTruthAuthorizationError(f"template files cannot be {name}")


def _handoff_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return {
            "event_codes": manifest["scope"]["event_codes"],
            "phase_keys_by_event": manifest["scope"]["phase_keys_by_event"],
            "tasks": [
                {
                    "task_id": task["task_id"],
                    "video_id": task["video_id"],
                    "video_sha256": task["sha256"],
                    "full_video_review_required": task["full_video_review_required"],
                }
                for task in manifest["scope"]["tasks"]
            ],
        }
    except (KeyError, TypeError) as exc:
        raise ScoringTruthAuthorizationError("M89 handoff scope is incomplete") from exc


def _role_protocol(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "roles": plan["roles"],
        "decision_contract": plan["decision_contract"],
        "data_access": plan["data_access"],
        "safety": plan["safety"],
    }


def scoring_truth_authorization_binding_sha256(binding: Mapping[str, Any]) -> str:
    """Hash the complete non-recursive payload of an M90 binding.

    The hash is an integrity checksum. It is not an operator identity proof,
    signature, trusted timestamp, calibration approval, or production authority.
    """

    if not isinstance(binding, Mapping):
        raise ScoringTruthAuthorizationError("authorization binding must be an object")
    payload = dict(binding)
    payload.pop("binding_sha256", None)
    return _sha256(_canonical_json_bytes(payload))


def validate_scoring_truth_authorization_binding(binding: Mapping[str, Any]) -> None:
    """Validate a verifier-produced binding's exact shape and checksum.

    A persisted binding is replay/integrity evidence only. The combined verifier
    must read the plan, release record, and M89 handoff in the current process.
    """

    if not isinstance(binding, Mapping):
        raise ScoringTruthAuthorizationError("authorization binding must be an object")
    _require_exact_keys(
        binding,
        {
            "binding_version",
            "status",
            "canonicalization",
            "plan",
            "release_record",
            "technical_handoff",
            "scope_digest_sha256",
            "role_protocol_digest_sha256",
            "operator_release_record_verified",
            "annotation_workflow_release_only",
            "binding_sha256",
        },
        name="authorization binding",
    )
    if binding["binding_version"] != BINDING_VERSION:
        raise ScoringTruthAuthorizationError("authorization binding version is invalid")
    if binding["status"] != BINDING_STATUS:
        raise ScoringTruthAuthorizationError("authorization binding status is invalid")
    if binding["canonicalization"] != CANONICALIZATION:
        raise ScoringTruthAuthorizationError("authorization canonicalization is invalid")
    if binding["operator_release_record_verified"] is not True:
        raise ScoringTruthAuthorizationError("operator release record is not verified")
    if binding["annotation_workflow_release_only"] is not True:
        raise ScoringTruthAuthorizationError("authorization scope is not annotation-only")

    plan = binding["plan"]
    release = binding["release_record"]
    technical = binding["technical_handoff"]
    for name, value in (
        ("binding.plan", plan),
        ("binding.release_record", release),
        ("binding.technical_handoff", technical),
    ):
        if not isinstance(value, Mapping):
            raise ScoringTruthAuthorizationError(f"{name} must be an object")

    _require_exact_keys(
        plan, {"plan_id", "plan_version", "raw_sha256"}, name="binding.plan"
    )
    _require_identifier(plan["plan_id"], name="binding plan_id")
    if plan["plan_version"] != PLAN_VERSION:
        raise ScoringTruthAuthorizationError("binding plan_version is invalid")
    _require_sha(plan["raw_sha256"], name="binding plan raw_sha256")

    _require_exact_keys(
        release,
        {
            "release_id",
            "release_version",
            "raw_sha256",
            "released_at",
            "reviewer_id",
            "reviewer_role",
            "decision",
        },
        name="binding.release_record",
    )
    _require_identifier(release["release_id"], name="binding release_id")
    _require_identifier(release["reviewer_id"], name="binding reviewer_id")
    if release["release_version"] != RELEASE_VERSION:
        raise ScoringTruthAuthorizationError("binding release_version is invalid")
    if release["reviewer_role"] != REVIEWER_ROLE:
        raise ScoringTruthAuthorizationError("binding reviewer role is invalid")
    if release["decision"] != DECISION:
        raise ScoringTruthAuthorizationError("binding release decision is invalid")
    _require_sha(release["raw_sha256"], name="binding release raw_sha256")
    _parse_canonical_utc(release["released_at"], name="binding release released_at")

    _validate_technical_handoff(
        technical,
        name="binding.technical_handoff",
        include_manifest_path=False,
    )
    _require_sha(binding["scope_digest_sha256"], name="binding scope digest")
    _require_sha(
        binding["role_protocol_digest_sha256"], name="binding role protocol digest"
    )
    binding_sha = _require_sha(binding["binding_sha256"], name="binding_sha256")
    if scoring_truth_authorization_binding_sha256(binding) != binding_sha:
        raise ScoringTruthAuthorizationError("authorization binding checksum mismatch")


def verify_scoring_truth_event_authorization(
    *,
    plan_path: str | Path,
    release_record_path: str | Path,
    handoff_dir: str | Path,
) -> dict[str, Any]:
    """Verify an operator-reviewed local release for the exact M89 handoff.

    The record is a local operational gate for event/phase annotation only. It is
    not a digital signature, trusted timestamp, calibration or promotion approval,
    or production-scoring authority. Every JSON decision and emitted hash comes
    from the same stable byte snapshot used during verification.
    """

    _assert_not_template_path(plan_path, name="annotation plans")
    _assert_not_template_path(release_record_path, name="operator release records")
    plan, plan_raw = _snapshot_json(plan_path, name="annotation plan")
    release, release_raw = _snapshot_json(
        release_record_path, name="operator release record"
    )
    plan_created_at, plan_frozen_at = _validate_plan(plan)
    released_at = _validate_release_record(release)

    try:
        handoff_snapshot = validate_scoring_truth_event_handoff(handoff_dir)
    except ScoringTruthEventHandoffError as exc:
        raise ScoringTruthAuthorizationError(f"M89 handoff is invalid: {exc}") from exc
    manifest = handoff_snapshot["manifest"]
    manifest_technical = {
        "manifest_raw_sha256": handoff_snapshot["manifest_sha256"],
        "bundle_version": manifest["bundle_version"],
        "bundle_id": handoff_snapshot["bundle_id"],
        "content_root_sha256": handoff_snapshot["content_root_sha256"],
        "source_projection_sha256": manifest["source_authority"][
            "source_contract_sha256"
        ],
    }
    plan_technical = {
        key: plan["technical_handoff"][key] for key in manifest_technical
    }
    if plan_technical != manifest_technical:
        raise ScoringTruthAuthorizationError(
            "annotation plan does not bind the exact validated M89 handoff"
        )
    _require_exact_json(
        plan["scope"],
        _handoff_scope(manifest),
        name="annotation plan scope for the validated M89 handoff",
    )

    expected_record_plan = {
        "plan_id": plan["plan_id"],
        "plan_version": plan["plan_version"],
        "raw_sha256": _sha256(plan_raw),
    }
    if release["plan"] != expected_record_plan:
        raise ScoringTruthAuthorizationError(
            "operator release does not bind the exact annotation-plan bytes"
        )
    if release["technical_handoff"] != manifest_technical:
        raise ScoringTruthAuthorizationError(
            "operator release does not bind the exact validated M89 handoff"
        )
    scope_digest = _sha256(_canonical_json_bytes(plan["scope"]))
    role_protocol_digest = _sha256(_canonical_json_bytes(_role_protocol(plan)))
    if release["scope_digest_sha256"] != scope_digest:
        raise ScoringTruthAuthorizationError("operator release scope digest mismatch")
    if release["role_protocol_digest_sha256"] != role_protocol_digest:
        raise ScoringTruthAuthorizationError(
            "operator release role protocol digest mismatch"
        )

    source_created_at = _parse_canonical_utc(
        manifest["source_authority"]["manifest_created_at"],
        name="M89 source manifest_created_at",
    )
    handoff_generated_at = _parse_canonical_utc(
        manifest["generated_at"], name="M89 generated_at"
    )
    if not (
        source_created_at
        <= handoff_generated_at
        <= plan_created_at
        <= plan_frozen_at
        <= released_at
    ):
        raise ScoringTruthAuthorizationError(
            "source, handoff, plan, freeze, and claimed release chronology is invalid"
        )

    binding: dict[str, Any] = {
        "binding_version": BINDING_VERSION,
        "status": BINDING_STATUS,
        "canonicalization": CANONICALIZATION,
        "plan": expected_record_plan,
        "release_record": {
            "release_id": release["release_id"],
            "release_version": release["release_version"],
            "raw_sha256": _sha256(release_raw),
            "released_at": release["released_at"],
            "reviewer_id": release["reviewed_by"]["reviewer_id"],
            "reviewer_role": release["reviewed_by"]["role"],
            "decision": release["decision"],
        },
        "technical_handoff": manifest_technical,
        "scope_digest_sha256": scope_digest,
        "role_protocol_digest_sha256": role_protocol_digest,
        "operator_release_record_verified": True,
        "annotation_workflow_release_only": True,
    }
    binding["binding_sha256"] = scoring_truth_authorization_binding_sha256(binding)
    validate_scoring_truth_authorization_binding(binding)
    return binding


__all__ = [
    "ScoringTruthAuthorizationError",
    "scoring_truth_authorization_binding_sha256",
    "validate_scoring_truth_authorization_binding",
    "verify_scoring_truth_event_authorization",
]
