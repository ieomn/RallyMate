from __future__ import annotations

"""Runtime verification object for scoring-truth calibration inputs.

The event/phase annotation workflow release allows only the reviewed annotation
workflow to begin.  It does not, by itself, authorize later CSV/JSONL bytes as
coach ground truth.  This module defines the second, content-bound contract that
downstream calibration code requires.

There is intentionally no public constructor for a production verification
object yet.  A future authorized-intake verifier must validate the A/B/C
revision chain and issue the process-local object through ``_issue_verified``.
Persisted JSON, a self-consistent hash, or a copied release record can never
recreate the runtime verification token.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


CALIBRATION_AUTHORIZATION_BINDING_VERSION = (
    "scoring-truth-calibration-authorization-binding-v1.0.0"
)
CANONICALIZATION = "rallymate-canonical-json-v1"
VERIFIED_STATUS = "verified_authorized_intake_for_calibration"
DIAGNOSTIC_STATUS = "unverified_private_truth_diagnostic"
SYNTHETIC_STATUS = "synthetic_test_only_no_production_authority"


class ScoringTruthCalibrationAuthorizationError(ValueError):
    """Raised when calibration truth is not content-bound to real authority."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def scoring_truth_calibration_authorization_binding_sha256(
    binding: Mapping[str, Any],
) -> str:
    payload = dict(binding)
    payload.pop("binding_sha256", None)
    return _sha256(_canonical_json_bytes(payload))


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise ScoringTruthCalibrationAuthorizationError(f"{field} must be SHA-256")
    return value.upper()


_INPUT_KEYS = {
    "intake_manifest",
    "truth_manifest",
    "truth_validation_report",
    "manual_events",
    "manual_semantics",
    "coach_labels",
}


def validate_scoring_truth_calibration_authorization_binding(
    binding: Mapping[str, Any],
    *,
    allow_nonproduction: bool = False,
) -> None:
    if not isinstance(binding, Mapping):
        raise ScoringTruthCalibrationAuthorizationError(
            "calibration truth authorization binding must be an object"
        )
    expected = {
        "binding_version",
        "status",
        "canonicalization",
        "authorization_id",
        "event_protocol_authorization",
        "intake",
        "revision_lineage",
        "input_files",
        "binding_sha256",
    }
    if set(binding) != expected:
        raise ScoringTruthCalibrationAuthorizationError(
            "calibration truth authorization binding fields are not exact"
        )
    if binding.get("binding_version") != CALIBRATION_AUTHORIZATION_BINDING_VERSION:
        raise ScoringTruthCalibrationAuthorizationError(
            "unsupported calibration truth authorization binding version"
        )
    status = binding.get("status")
    allowed_statuses = {VERIFIED_STATUS}
    if allow_nonproduction:
        allowed_statuses.update({DIAGNOSTIC_STATUS, SYNTHETIC_STATUS})
    if status not in allowed_statuses:
        raise ScoringTruthCalibrationAuthorizationError(
            "calibration truth authorization is not verified for production"
        )
    if binding.get("canonicalization") != CANONICALIZATION:
        raise ScoringTruthCalibrationAuthorizationError(
            "calibration truth authorization canonicalization is invalid"
        )
    if not isinstance(binding.get("authorization_id"), str) or not binding[
        "authorization_id"
    ]:
        raise ScoringTruthCalibrationAuthorizationError("authorization_id is required")

    inputs = binding.get("input_files")
    if not isinstance(inputs, Mapping) or set(inputs) != _INPUT_KEYS:
        raise ScoringTruthCalibrationAuthorizationError(
            "authorization input_files must bind the exact calibration truth set"
        )
    for name, digest in inputs.items():
        _require_sha256(digest, f"input_files.{name}")

    intake = binding.get("intake")
    if not isinstance(intake, Mapping) or set(intake) != {
        "intake_id",
        "intake_version",
        "content_root_sha256",
    }:
        raise ScoringTruthCalibrationAuthorizationError(
            "authorization intake binding fields are invalid"
        )
    for field in ("intake_id", "intake_version"):
        if not isinstance(intake.get(field), str) or not intake[field]:
            raise ScoringTruthCalibrationAuthorizationError(f"intake.{field} is required")
    _require_sha256(intake.get("content_root_sha256"), "intake.content_root_sha256")

    revision = binding.get("revision_lineage")
    revision_fields = {
        "revision_id",
        "annotator_a_id",
        "annotator_b_id",
        "reviewer_c_id",
        "annotator_a_export_root_sha256",
        "annotator_b_export_root_sha256",
        "reviewer_c_adjudication_root_sha256",
        "roles_distinct",
        "revision_finalized",
    }
    if not isinstance(revision, Mapping) or set(revision) != revision_fields:
        raise ScoringTruthCalibrationAuthorizationError(
            "authorization A/B/C revision lineage fields are invalid"
        )
    identities = []
    for field in ("revision_id", "annotator_a_id", "annotator_b_id", "reviewer_c_id"):
        value = revision.get(field)
        if not isinstance(value, str) or not value:
            raise ScoringTruthCalibrationAuthorizationError(
                f"revision_lineage.{field} is required"
            )
        if field != "revision_id":
            identities.append(value)
    if status == VERIFIED_STATUS:
        if len(set(identities)) != 3 or revision.get("roles_distinct") is not True:
            raise ScoringTruthCalibrationAuthorizationError(
                "annotator A, annotator B, and reviewer C must be distinct"
            )
        if revision.get("revision_finalized") is not True:
            raise ScoringTruthCalibrationAuthorizationError(
                "calibration truth revision must be finalized"
            )
    elif (
        revision.get("roles_distinct") is not False
        or revision.get("revision_finalized") is not False
    ):
        raise ScoringTruthCalibrationAuthorizationError(
            "non-production truth marker cannot claim finalized A/B/C lineage"
        )
    for field in (
        "annotator_a_export_root_sha256",
        "annotator_b_export_root_sha256",
        "reviewer_c_adjudication_root_sha256",
    ):
        _require_sha256(revision.get(field), f"revision_lineage.{field}")

    event_binding = binding.get("event_protocol_authorization")
    if status == VERIFIED_STATUS:
        try:
            from rallymate_annotation.scoring_truth_authorization import (
                validate_scoring_truth_authorization_binding,
            )

            validate_scoring_truth_authorization_binding(event_binding)
        except (ImportError, ValueError) as exc:
            raise ScoringTruthCalibrationAuthorizationError(
                f"invalid event-protocol authorization binding: {exc}"
            ) from exc
    elif not isinstance(event_binding, Mapping):
        raise ScoringTruthCalibrationAuthorizationError(
            "non-production authorization marker must retain an event binding object"
        )

    expected_sha = scoring_truth_calibration_authorization_binding_sha256(binding)
    actual_sha = _require_sha256(binding.get("binding_sha256"), "binding_sha256")
    if actual_sha != expected_sha:
        raise ScoringTruthCalibrationAuthorizationError(
            "calibration truth authorization binding hash mismatch"
        )


_VERIFICATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class VerifiedScoringTruthCalibrationAuthorization:
    """Non-serializable process-local proof returned by a trusted verifier."""

    _token: object
    _binding: dict[str, Any]

    def __reduce__(self) -> Any:  # pragma: no cover - defensive serialization guard
        raise TypeError("verified calibration truth authority is process-local")

    @property
    def binding(self) -> dict[str, Any]:
        return deepcopy(self._binding)


def _issue_verified_scoring_truth_calibration_authorization(
    binding: Mapping[str, Any],
) -> VerifiedScoringTruthCalibrationAuthorization:
    """Verifier-only issuance hook; a persisted binding cannot call this implicitly."""

    validate_scoring_truth_calibration_authorization_binding(binding)
    return VerifiedScoringTruthCalibrationAuthorization(
        _VERIFICATION_TOKEN, deepcopy(dict(binding))
    )


def require_verified_scoring_truth_calibration_authorization(
    value: Any,
) -> dict[str, Any]:
    if (
        not isinstance(value, VerifiedScoringTruthCalibrationAuthorization)
        or value._token is not _VERIFICATION_TOKEN
    ):
        raise ScoringTruthCalibrationAuthorizationError(
            "a same-process verified authorized-intake object is required"
        )
    binding = value.binding
    validate_scoring_truth_calibration_authorization_binding(binding)
    return binding


def build_nonproduction_truth_authorization_marker(
    *,
    status: str,
    input_files: Mapping[str, str],
) -> dict[str, Any]:
    """Create an explicit diagnostic/test marker that can never authorize production."""

    if status not in {DIAGNOSTIC_STATUS, SYNTHETIC_STATUS}:
        raise ScoringTruthCalibrationAuthorizationError(
            "non-production truth marker status is invalid"
        )
    normalized = {name: _require_sha256(value, f"input_files.{name}") for name, value in input_files.items()}
    if set(normalized) != _INPUT_KEYS:
        raise ScoringTruthCalibrationAuthorizationError(
            "non-production marker must bind the exact calibration truth set"
        )
    marker: dict[str, Any] = {
        "binding_version": CALIBRATION_AUTHORIZATION_BINDING_VERSION,
        "status": status,
        "canonicalization": CANONICALIZATION,
        "authorization_id": f"nonproduction-{_sha256(_canonical_json_bytes(normalized))[:24]}",
        "event_protocol_authorization": {},
        "intake": {
            "intake_id": "not-verified",
            "intake_version": "not-verified",
            "content_root_sha256": "0" * 64,
        },
        "revision_lineage": {
            "revision_id": "not-verified",
            "annotator_a_id": "not-verified-a",
            "annotator_b_id": "not-verified-b",
            "reviewer_c_id": "not-verified-c",
            "annotator_a_export_root_sha256": "0" * 64,
            "annotator_b_export_root_sha256": "0" * 64,
            "reviewer_c_adjudication_root_sha256": "0" * 64,
            "roles_distinct": False,
            "revision_finalized": False,
        },
        "input_files": normalized,
    }
    marker["binding_sha256"] = scoring_truth_calibration_authorization_binding_sha256(
        marker
    )
    validate_scoring_truth_calibration_authorization_binding(
        marker, allow_nonproduction=True
    )
    return marker


__all__ = [
    "CALIBRATION_AUTHORIZATION_BINDING_VERSION",
    "DIAGNOSTIC_STATUS",
    "SYNTHETIC_STATUS",
    "ScoringTruthCalibrationAuthorizationError",
    "VerifiedScoringTruthCalibrationAuthorization",
    "build_nonproduction_truth_authorization_marker",
    "require_verified_scoring_truth_calibration_authorization",
    "scoring_truth_calibration_authorization_binding_sha256",
    "validate_scoring_truth_calibration_authorization_binding",
]
