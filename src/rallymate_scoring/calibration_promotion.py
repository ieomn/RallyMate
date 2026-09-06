from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from collections.abc import Iterator, Mapping
from typing import Any

from rallymate_scoring.calibration import (
    CALIBRATION_SCHEMA_VERSION,
    validate_ordinal_model,
    validate_threshold_calibration,
)
from rallymate_scoring.calibration_fitting import (
    CANONICALIZATION,
    CalibrationFitError,
    canonical_sha256,
    validate_calibration_candidate,
)
from rallymate_scoring.calibration_independent_test import (
    INDEPENDENT_TEST_REPORT_SCHEMA_VERSION,
    IndependentTestError,
    evaluate_independent_test,
    validate_independent_test_report,
)
from rallymate_scoring.feasibility import validate_feasibility_registry
from rallymate_scoring.maturity_evidence import (
    MaturityEvidenceError,
    PRODUCTION_SCOPE as MATURITY_PRODUCTION_SCOPE,
    maturity_evidence_binding,
    validate_maturity_evidence_for_registry,
)
from rallymate_scoring.scoring_truth_calibration_authorization import (
    ScoringTruthCalibrationAuthorizationError,
    VerifiedScoringTruthCalibrationAuthorization,
    require_verified_scoring_truth_calibration_authorization,
    validate_scoring_truth_calibration_authorization_binding,
)


PROMOTION_DECISION_SCHEMA_VERSION = "1.1.0"
PROMOTION_REPORT_SCHEMA_VERSION = "1.3.0"
PROMOTION_LINEAGE_SCHEMA_VERSION = "1.3.0"
TRUSTED_PROMOTION_LEDGER_SCHEMA_VERSION = "1.2.0"
_SHA256_LENGTH = 64
_TRUSTED_CALIBRATION_TOKEN = object()
_PROMOTION_INPUT_OBJECT_FIELDS = (
    "candidate",
    "independent_test_report",
    "independent_test_protocol",
    "decision",
    "maturity_evidence",
)


class CalibrationPromotionError(ValueError):
    """Raised before any scoring asset is created when a promotion gate fails."""


class TrustedProductionCalibration(Mapping[str, Any]):
    """Runtime-only production calibration authorized by a trusted ledger.

    This deliberately is not a ``dict`` and is not JSON serializable. Serializing
    and loading it again drops authorization, forcing ledger verification again.
    The constructor token keeps ordinary callers on the verification factory.
    """

    __slots__ = ("_payload", "_authorization")

    def __init__(
        self,
        payload: dict[str, Any],
        authorization: dict[str, Any],
        *,
        _token: object,
    ) -> None:
        if _token is not _TRUSTED_CALIBRATION_TOKEN:
            raise CalibrationPromotionError(
                "TrustedProductionCalibration must be created by ledger verification"
            )
        self._payload = deepcopy(payload)
        self._authorization = deepcopy(authorization)

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def authorization(self) -> dict[str, Any]:
        return deepcopy(self._authorization)


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CalibrationPromotionError(f"{field} must be an object")
    return value


def _require_string(payload: dict[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CalibrationPromotionError(f"{context}.{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise CalibrationPromotionError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CalibrationPromotionError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationPromotionError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalibrationPromotionError(f"{field} must include a timezone")
    return parsed


def _expect_equal(actual: Any, expected: Any, field: str) -> None:
    if actual != expected:
        raise CalibrationPromotionError(f"{field} lineage mismatch")


def _verify_binding(
    binding: Any,
    expected: dict[str, Any],
    field: str,
    *,
    sha_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    payload = _require_object(binding, field)
    for name, value in expected.items():
        actual = payload.get(name)
        if name in sha_fields:
            actual = _require_sha256(actual, f"{field}.{name}")
            value = _require_sha256(value, f"expected.{field}.{name}")
        _expect_equal(actual, value, f"{field}.{name}")
    return payload


def _validate_maturity_binding(
    value: Any,
    *,
    field: str,
    indicator_id: str,
    registry_version: str,
    registry_sha256: str,
    expected_scope: str = MATURITY_PRODUCTION_SCOPE,
) -> dict[str, Any]:
    """Validate the immutable subset persisted in decisions/assets/ledgers."""

    binding = _require_object(value, field)
    expected_fields = {
        "bundle_id",
        "bundle_version",
        "artifact_scope",
        "indicator_id",
        "content_sha256",
        "final_transition_sha256",
        "final_registry",
        "canonicalization",
    }
    if set(binding) != expected_fields:
        raise CalibrationPromotionError(
            f"{field} must contain the exact maturity-evidence binding fields"
        )
    for name in ("bundle_id", "bundle_version", "artifact_scope", "indicator_id"):
        _require_string(binding, name, field)
    if binding["artifact_scope"] != expected_scope:
        raise CalibrationPromotionError(
            f"{field}.artifact_scope must be {expected_scope}"
        )
    if binding["indicator_id"] != indicator_id:
        raise CalibrationPromotionError(f"{field}.indicator_id lineage mismatch")
    for name in ("content_sha256", "final_transition_sha256"):
        _require_sha256(binding.get(name), f"{field}.{name}")
    if binding.get("canonicalization") != CANONICALIZATION:
        raise CalibrationPromotionError(f"{field}.canonicalization is invalid")
    final_registry = _require_object(
        binding.get("final_registry"), f"{field}.final_registry"
    )
    if set(final_registry) != {
        "registry_version",
        "content_sha256",
        "indicator_id",
        "indicator_level",
    }:
        raise CalibrationPromotionError(
            f"{field}.final_registry fields are invalid"
        )
    _expect_equal(
        final_registry.get("registry_version"),
        registry_version,
        f"{field}.final_registry.registry_version",
    )
    _expect_equal(
        _require_sha256(
            final_registry.get("content_sha256"),
            f"{field}.final_registry.content_sha256",
        ),
        _require_sha256(registry_sha256, "expected.registry_sha256"),
        f"{field}.final_registry.content_sha256",
    )
    _expect_equal(
        final_registry.get("indicator_id"),
        indicator_id,
        f"{field}.final_registry.indicator_id",
    )
    if final_registry.get("indicator_level") != "F4":
        raise CalibrationPromotionError(
            f"{field}.final_registry.indicator_level must be F4"
        )
    return binding


def validate_promotion_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the human decision structure without treating it as approval proof."""

    if not isinstance(payload, dict):
        raise CalibrationPromotionError("promotion decision must be an object")
    if payload.get("schema_version") != PROMOTION_DECISION_SCHEMA_VERSION:
        raise CalibrationPromotionError("unsupported promotion decision schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {
        "calibration_promotion_decision",
        "synthetic_test_only_calibration_promotion_decision",
    }:
        raise CalibrationPromotionError("promotion decision artifact_scope is invalid")
    for field in (
        "decision_id",
        "decision_version",
        "calibration_version",
        "indicator_id",
        "decided_at",
    ):
        _require_string(payload, field, "promotion_decision")
    if payload.get("decision") != "approved":
        raise CalibrationPromotionError("promotion decision must be explicitly approved")
    requested_scope = payload.get("requested_artifact_scope")
    if requested_scope not in {"production", "test_only"}:
        raise CalibrationPromotionError("requested_artifact_scope must be production or test_only")
    is_test = scope.startswith("synthetic_test_only")
    if is_test != (requested_scope == "test_only"):
        raise CalibrationPromotionError("decision scope/requested artifact scope mismatch")

    reviewer = _require_object(payload.get("reviewer"), "promotion_decision.reviewer")
    _require_string(reviewer, "reviewer_id", "promotion_decision.reviewer")
    _require_string(reviewer, "role", "promotion_decision.reviewer")
    source = _require_object(payload.get("source"), "promotion_decision.source")
    expected_source_kind = "synthetic_test_fixture" if is_test else "human_promotion_record"
    if source.get("kind") != expected_source_kind:
        raise CalibrationPromotionError(
            f"promotion decision scope requires source.kind={expected_source_kind}"
        )
    _require_sha256(source.get("source_sha256"), "promotion_decision.source.source_sha256")
    registered_at = _timestamp(
        source.get("registered_at"), "promotion_decision.source.registered_at"
    )
    decided_at = _timestamp(payload["decided_at"], "promotion_decision.decided_at")
    if registered_at > decided_at:
        raise CalibrationPromotionError("promotion decision must be registered before it is decided")

    for field in (
        "candidate",
        "prepared_dataset",
        "fit_protocol",
        "independent_test",
        "independent_test_report",
        "independent_test_protocol",
        "feasibility_registry",
    ):
        _require_object(payload.get(field), f"promotion_decision.{field}")
    if not is_test:
        _require_object(
            payload.get("maturity_evidence"),
            "promotion_decision.maturity_evidence",
        )
    attestations = _require_object(
        payload.get("attestations"), "promotion_decision.attestations"
    )
    required_attestations = [
        "candidate_lineage_reviewed",
        "independent_test_reviewed",
        "registry_maturity_reviewed",
        "no_manual_threshold_or_coefficient_edits",
    ]
    if not is_test:
        required_attestations.append("maturity_evidence_reviewed")
    if any(attestations.get(field) is not True for field in required_attestations):
        raise CalibrationPromotionError("all promotion attestations must be explicitly true")
    return {
        "scope": scope,
        "is_test": is_test,
        "requested_artifact_scope": requested_scope,
        "decided_at": decided_at,
        "registered_at": registered_at,
    }


def _validate_report(
    report: dict[str, Any],
    candidate: dict[str, Any],
    independent_test_protocol: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise CalibrationPromotionError("independent-test report must be an object")
    candidate_is_test = candidate["artifact_scope"].startswith("synthetic_test_only")
    allowed_schema_versions = (
        {"1.0.0", "1.1.0", INDEPENDENT_TEST_REPORT_SCHEMA_VERSION}
        if candidate_is_test
        else {INDEPENDENT_TEST_REPORT_SCHEMA_VERSION}
    )
    if report.get("schema_version") not in allowed_schema_versions:
        raise CalibrationPromotionError("unsupported independent-test report schema_version")
    expected_scope = (
        "synthetic_test_only_independent_test_report"
        if candidate_is_test
        else "independent_test_report"
    )
    if report.get("artifact_scope") != expected_scope:
        raise CalibrationPromotionError("candidate/report real-vs-test scope mismatch")
    full_report_audit: dict[str, Any] | None = None
    if not candidate_is_test:
        if not isinstance(independent_test_protocol, dict):
            raise CalibrationPromotionError(
                "production promotion requires the exact independent-test protocol payload"
            )
        try:
            full_report_audit = validate_independent_test_report(
                report,
                protocol=independent_test_protocol,
                candidate=candidate,
                require_nonempty_evaluation=True,
            )
        except IndependentTestError as exc:
            raise CalibrationPromotionError(
                f"invalid production independent-test evidence: {exc}"
            ) from exc
    elif (
        independent_test_protocol is not None
        and report.get("schema_version") == INDEPENDENT_TEST_REPORT_SCHEMA_VERSION
    ):
        try:
            full_report_audit = validate_independent_test_report(
                report,
                protocol=independent_test_protocol,
                candidate=candidate,
            )
        except IndependentTestError as exc:
            raise CalibrationPromotionError(
                f"invalid synthetic independent-test evidence: {exc}"
            ) from exc
    _require_string(report, "report_version", "independent_test_report")
    if report.get("status") != "passed":
        raise CalibrationPromotionError("independent-test report status must be passed")
    if report.get("approved_for_scoring") is not False:
        raise CalibrationPromotionError(
            "evaluator report must remain unapproved before explicit promotion"
        )
    if report.get("indicator_id") != candidate["indicator_id"]:
        raise CalibrationPromotionError("candidate/report indicator_id mismatch")
    acceptance = _require_object(report.get("acceptance"), "report.acceptance")
    if acceptance.get("passed") is not True:
        raise CalibrationPromotionError("independent-test acceptance must be passed")
    promotion = _require_object(report.get("promotion"), "report.promotion")
    if (
        promotion.get("production_asset_created") is not False
        or promotion.get("registry_F4_promoted") is not False
        or promotion.get("explicit_human_approval_required") is not True
    ):
        raise CalibrationPromotionError("report promotion state is not pre-promotion")

    report_candidate = _require_object(report.get("candidate"), "report.candidate")
    _verify_binding(
        report_candidate,
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        },
        "report.candidate",
        sha_fields=("content_sha256",),
    )
    seal = candidate["independent_test"]
    dataset = _require_object(report.get("dataset"), "report.dataset")
    _verify_binding(
        dataset,
        {
            "dataset_id": candidate["prepared_dataset"]["dataset_id"],
            "dataset_version": candidate["prepared_dataset"]["dataset_version"],
            "seal_id": seal["seal_id"],
            "content_sha256": seal["content_sha256"].lower(),
            "record_count": seal["record_count"],
            "canonicalization": CANONICALIZATION,
        },
        "report.dataset",
        sha_fields=("content_sha256",),
    )
    _expect_equal(
        sorted(dataset.get("leakage_groups", [])),
        sorted(seal["groups"]),
        "report.dataset.leakage_groups",
    )
    protocol = _require_object(report.get("protocol"), "report.protocol")
    for field in ("protocol_id", "protocol_version", "registered_at"):
        _require_string(protocol, field, "report.protocol")
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(protocol.get(field), f"report.protocol.{field}")
    evaluated_at = (
        full_report_audit["evaluated_at"]
        if full_report_audit is not None
        else _timestamp(report.get("evaluated_at"), "report.evaluated_at")
    )
    protocol_registered_at = (
        full_report_audit["protocol_registered_at"]
        if full_report_audit is not None
        else _timestamp(protocol["registered_at"], "report.protocol.registered_at")
    )
    if protocol_registered_at > evaluated_at:
        raise CalibrationPromotionError(
            "independent-test protocol must be registered before evaluation"
        )
    requirements = _require_object(
        report.get("indicator_requirements"), "report.indicator_requirements"
    )
    expected_requirement_fields = {
        "requirements_version",
        "content_sha256",
        "registry_version",
        "registry_content_sha256",
        "registry_indicator_sha256",
        "semantic_contract_version",
        "semantic_contract_sha256",
    }
    if set(requirements) != expected_requirement_fields:
        raise CalibrationPromotionError(
            "report.indicator_requirements fields are invalid"
        )
    for field in (
        "requirements_version",
        "registry_version",
        "semantic_contract_version",
    ):
        _require_string(requirements, field, "report.indicator_requirements")
    for field in (
        "content_sha256",
        "registry_content_sha256",
        "registry_indicator_sha256",
        "semantic_contract_sha256",
    ):
        _require_sha256(
            requirements.get(field), f"report.indicator_requirements.{field}"
        )
    return {
        "is_test": candidate_is_test,
        "evaluated_at": evaluated_at,
        "protocol_registered_at": protocol_registered_at,
    }


def _build_source_replay_lineage(
    *,
    independent_test_samples: list[dict[str, Any]],
    indicator_requirements: dict[str, Any],
    sealed_record_count: int,
    supplied: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind the exact evaluator inputs used to replay production evidence.

    API callers without files retain canonical in-memory bindings.  The CLI passes
    file-byte bindings as well, so a later reader can distinguish source bytes
    from the parsed canonical payload without treating a path as authority.
    """

    expected = {
        "samples": {
            "canonical_sha256": canonical_sha256(independent_test_samples),
            "source_record_count": len(independent_test_samples),
            "sealed_record_count": sealed_record_count,
        },
        "indicator_requirements": {
            "canonical_sha256": canonical_sha256(indicator_requirements),
        },
    }
    if supplied is None:
        return {
            "samples": {
                "source_kind": "in_memory_canonical_json",
                **expected["samples"],
            },
            "indicator_requirements": {
                "source_kind": "in_memory_canonical_json",
                **expected["indicator_requirements"],
            },
        }
    payload = _require_object(supplied, "independent_test_source_replay")
    if set(payload) != {"samples", "indicator_requirements"}:
        raise CalibrationPromotionError(
            "independent_test_source_replay fields are invalid"
        )
    result: dict[str, Any] = {}
    for field, expected_values in expected.items():
        item = _require_object(
            payload.get(field), f"independent_test_source_replay.{field}"
        )
        source_kind = item.get("source_kind")
        if source_kind == "in_memory_canonical_json":
            allowed = {"source_kind", *expected_values}
        elif source_kind == "file_bytes":
            allowed = {"source_kind", "source_path", "raw_sha256", *expected_values}
            _require_string(
                item, "source_path", f"independent_test_source_replay.{field}"
            )
            _require_sha256(
                item.get("raw_sha256"),
                f"independent_test_source_replay.{field}.raw_sha256",
            )
        else:
            raise CalibrationPromotionError(
                f"independent_test_source_replay.{field}.source_kind is invalid"
            )
        if set(item) != allowed:
            raise CalibrationPromotionError(
                f"independent_test_source_replay.{field} fields are invalid"
            )
        for name, value in expected_values.items():
            actual = item.get(name)
            if name.endswith("sha256"):
                actual = _require_sha256(
                    actual, f"independent_test_source_replay.{field}.{name}"
                )
                value = _require_sha256(
                    value, f"expected.independent_test_source_replay.{field}.{name}"
                )
            _expect_equal(
                actual,
                value,
                f"independent_test_source_replay.{field}.{name}",
            )
        result[field] = deepcopy(item)
    return result


def _validate_promotion_input_snapshots(
    supplied: Any,
    *,
    expected_canonical_sha256: dict[str, str],
) -> dict[str, Any]:
    """Validate immutable bindings for every production promotion object input."""

    payload = _require_object(supplied, "promotion_input_snapshots")
    expected_fields = {
        *_PROMOTION_INPUT_OBJECT_FIELDS,
        "registry_lifecycle_authority",
    }
    if set(payload) != expected_fields:
        raise CalibrationPromotionError(
            "promotion_input_snapshots fields are invalid"
        )
    result: dict[str, Any] = {}
    for field in _PROMOTION_INPUT_OBJECT_FIELDS:
        context = f"promotion_input_snapshots.{field}"
        item = _require_object(payload.get(field), context)
        source_kind = item.get("source_kind")
        fields = {"source_kind", "canonical_sha256"}
        if source_kind == "file_bytes":
            fields.update({"source_path", "raw_sha256"})
            _require_string(item, "source_path", context)
            _require_sha256(item.get("raw_sha256"), f"{context}.raw_sha256")
        elif source_kind != "in_memory_canonical_json":
            raise CalibrationPromotionError(f"{context}.source_kind is invalid")
        if set(item) != fields:
            raise CalibrationPromotionError(f"{context} fields are invalid")
        actual = _require_sha256(
            item.get("canonical_sha256"), f"{context}.canonical_sha256"
        )
        expected = _require_sha256(
            expected_canonical_sha256[field],
            f"expected.{context}.canonical_sha256",
        )
        _expect_equal(actual, expected, f"{context}.canonical_sha256")
        result[field] = deepcopy(item)

    context = "promotion_input_snapshots.registry_lifecycle_authority"
    registry_source = _require_object(
        payload.get("registry_lifecycle_authority"), context
    )
    source_kind = registry_source.get("source_kind")
    if source_kind == "in_memory_canonical_json":
        fields = {"source_kind", "artifact_canonical_sha256"}
    elif source_kind == "registry_lifecycle_verified_file_bytes":
        fields = {
            "source_kind",
            "manifest_path",
            "manifest_raw_sha256",
            "authority_version",
            "authority_slot",
            "artifact_path",
            "artifact_raw_sha256",
            "artifact_canonical_sha256",
            "embedded_version",
        }
        for name in (
            "manifest_path",
            "authority_version",
            "authority_slot",
            "artifact_path",
            "embedded_version",
        ):
            _require_string(registry_source, name, context)
        if registry_source.get("authority_slot") != "roles.runtime_feasibility":
            raise CalibrationPromotionError(
                f"{context}.authority_slot must be roles.runtime_feasibility"
            )
        for name in ("manifest_raw_sha256", "artifact_raw_sha256"):
            _require_sha256(registry_source.get(name), f"{context}.{name}")
    else:
        raise CalibrationPromotionError(f"{context}.source_kind is invalid")
    if set(registry_source) != fields:
        raise CalibrationPromotionError(f"{context} fields are invalid")
    actual_registry_sha256 = _require_sha256(
        registry_source.get("artifact_canonical_sha256"),
        f"{context}.artifact_canonical_sha256",
    )
    expected_registry_sha256 = _require_sha256(
        expected_canonical_sha256["feasibility_registry"],
        f"expected.{context}.artifact_canonical_sha256",
    )
    _expect_equal(
        actual_registry_sha256,
        expected_registry_sha256,
        f"{context}.artifact_canonical_sha256",
    )
    result["registry_lifecycle_authority"] = deepcopy(registry_source)
    return result


def _build_promotion_input_snapshots(
    *,
    candidate: dict[str, Any],
    independent_test_report: dict[str, Any],
    independent_test_protocol: dict[str, Any],
    decision: dict[str, Any],
    maturity_evidence: dict[str, Any],
    feasibility_registry: dict[str, Any],
    supplied: dict[str, Any] | None,
) -> dict[str, Any]:
    objects = {
        "candidate": candidate,
        "independent_test_report": independent_test_report,
        "independent_test_protocol": independent_test_protocol,
        "decision": decision,
        "maturity_evidence": maturity_evidence,
    }
    expected = {
        field: canonical_sha256(value) for field, value in objects.items()
    }
    expected["feasibility_registry"] = canonical_sha256(feasibility_registry)
    if supplied is None:
        supplied = {
            field: {
                "source_kind": "in_memory_canonical_json",
                "canonical_sha256": expected[field],
            }
            for field in _PROMOTION_INPUT_OBJECT_FIELDS
        }
        supplied["registry_lifecycle_authority"] = {
            "source_kind": "in_memory_canonical_json",
            "artifact_canonical_sha256": expected["feasibility_registry"],
        }
    return _validate_promotion_input_snapshots(
        supplied,
        expected_canonical_sha256=expected,
    )


def _replay_production_independent_test(
    *,
    candidate: dict[str, Any],
    report: dict[str, Any],
    protocol: dict[str, Any] | None,
    independent_test_samples: list[dict[str, Any]] | None,
    indicator_requirements: dict[str, Any] | None,
    source_replay: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(protocol, dict):
        raise CalibrationPromotionError(
            "production promotion requires the exact independent-test protocol payload"
        )
    if not isinstance(independent_test_samples, list) or not independent_test_samples:
        raise CalibrationPromotionError(
            "production promotion requires non-empty independent_test_samples"
        )
    if not isinstance(indicator_requirements, dict) or not indicator_requirements:
        raise CalibrationPromotionError(
            "production promotion requires indicator_requirements"
        )
    try:
        replayed = evaluate_independent_test(
            candidate=candidate,
            samples=independent_test_samples,
            protocol=protocol,
            indicator_requirements=indicator_requirements,
            evaluated_at=report.get("evaluated_at"),
        )
    except IndependentTestError as exc:
        raise CalibrationPromotionError(
            f"production independent-test source replay failed: {exc}"
        ) from exc
    if canonical_sha256(replayed) != canonical_sha256(report):
        raise CalibrationPromotionError(
            "supplied independent-test report does not exactly match production source replay"
        )
    return _build_source_replay_lineage(
        independent_test_samples=independent_test_samples,
        indicator_requirements=indicator_requirements,
        sealed_record_count=candidate["independent_test"]["record_count"],
        supplied=source_replay,
    )


def _find_registry_indicator(
    registry: dict[str, Any], indicator_id: str
) -> dict[str, Any]:
    try:
        validate_feasibility_registry(registry)
    except ValueError as exc:
        raise CalibrationPromotionError(f"invalid feasibility registry: {exc}") from exc
    matches = [
        item for item in registry["indicators"] if item.get("indicator_id") == indicator_id
    ]
    if len(matches) != 1:
        raise CalibrationPromotionError(
            "feasibility registry must contain the candidate indicator exactly once"
        )
    return matches[0]


def _validate_report_requirements_for_maturity_transition(
    report: dict[str, Any],
    maturity_evidence: dict[str, Any],
) -> None:
    """Bind the test requirement snapshot to the pre-F4 (F3) registry.

    Independent evaluation necessarily precedes the F3->F4 decision, so binding
    it to the final F4 bytes would create an impossible circular workflow.
    """

    requirements = _require_object(
        report.get("indicator_requirements"), "report.indicator_requirements"
    )
    pre_f4_registry = maturity_evidence["transitions"][3]["registry_before"]
    expected = {
        "registry_version": pre_f4_registry["registry_version"],
        "registry_content_sha256": pre_f4_registry["content_sha256"],
    }
    for field, expected_value in expected.items():
        actual = requirements.get(field)
        if field.endswith("sha256"):
            actual = _require_sha256(actual, f"report.indicator_requirements.{field}")
            expected_value = _require_sha256(
                expected_value, f"expected.indicator_requirements.{field}"
            )
        _expect_equal(
            actual,
            expected_value,
            f"report.indicator_requirements.{field}",
        )


def _verify_decision_lineage(
    *,
    decision: dict[str, Any],
    candidate: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
    maturity_binding: dict[str, Any] | None,
) -> None:
    _expect_equal(
        decision["indicator_id"], candidate["indicator_id"], "decision.indicator_id"
    )
    _verify_binding(
        decision["candidate"],
        {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        },
        "decision.candidate",
        sha_fields=("content_sha256",),
    )
    _verify_binding(
        decision["prepared_dataset"],
        candidate["prepared_dataset"],
        "decision.prepared_dataset",
        sha_fields=("content_sha256", "source_sha256"),
    )
    _verify_binding(
        decision["fit_protocol"],
        candidate["fit_protocol"],
        "decision.fit_protocol",
        sha_fields=("content_sha256", "source_sha256"),
    )
    resolution_policy = candidate["target_semantics"]["label_resolution_policy"]
    if (
        candidate["prepared_dataset"].get("label_resolution_policy")
        != resolution_policy
        or candidate["fit_protocol"].get("label_resolution_policy")
        != resolution_policy
    ):
        raise CalibrationPromotionError(
            "candidate dataset/protocol label-resolution lineage mismatch"
        )
    seal = candidate["independent_test"]
    _verify_binding(
        decision["independent_test"],
        {
            "seal_id": seal["seal_id"],
            "content_sha256": seal["content_sha256"],
            "record_count": seal["record_count"],
            "canonicalization": seal["canonicalization"],
        },
        "decision.independent_test",
        sha_fields=("content_sha256",),
    )
    _verify_binding(
        decision["independent_test_report"],
        {
            "report_version": report["report_version"],
            "content_sha256": canonical_sha256(report),
        },
        "decision.independent_test_report",
        sha_fields=("content_sha256",),
    )
    _verify_binding(
        decision["independent_test_protocol"],
        {
            "protocol_id": report["protocol"]["protocol_id"],
            "protocol_version": report["protocol"]["protocol_version"],
            "content_sha256": report["protocol"]["content_sha256"],
            "source_sha256": report["protocol"]["source_sha256"],
            "registered_at": report["protocol"]["registered_at"],
        },
        "decision.independent_test_protocol",
        sha_fields=("content_sha256", "source_sha256"),
    )
    indicator = _find_registry_indicator(registry, candidate["indicator_id"])
    _verify_binding(
        decision["feasibility_registry"],
        {
            "registry_version": registry["registry_version"],
            "content_sha256": canonical_sha256(registry),
            "indicator_level": indicator["feasibility_level"],
        },
        "decision.feasibility_registry",
        sha_fields=("content_sha256",),
    )
    if maturity_binding is not None:
        _verify_binding(
            decision["maturity_evidence"],
            maturity_binding,
            "decision.maturity_evidence",
            sha_fields=("content_sha256", "final_transition_sha256"),
        )


def _validate_maturity_for_promotion(
    *,
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    report: dict[str, Any],
    registry: dict[str, Any],
    indicator: dict[str, Any],
    expected_scope: str,
) -> dict[str, Any]:
    """Bind F1/F2/F4 evidence semantics to this exact promotion input set."""

    try:
        validate_maturity_evidence_for_registry(
            bundle,
            registry,
            expected_scope=expected_scope,
        )
    except MaturityEvidenceError as exc:
        raise CalibrationPromotionError(
            f"invalid maturity evidence: {exc}"
        ) from exc
    if bundle.get("indicator_id") != candidate["indicator_id"]:
        raise CalibrationPromotionError(
            "maturity evidence indicator_id does not match candidate"
        )

    expected_event_codes = sorted(
        {
            str(requirement).split(".", 1)[0]
            for requirement in indicator.get("required_events", [])
        }
    )
    f1_event_codes = sorted(
        bundle["transitions"][0]["evidence"]["payload"]["event_evaluation"][
            "event_codes"
        ]
    )
    if f1_event_codes != expected_event_codes:
        raise CalibrationPromotionError(
            "maturity F1 event codes do not exactly match registry requirements"
        )

    required_features = list(indicator.get("required_features", []))
    f2_payload = bundle["transitions"][1]["evidence"]["payload"]
    evaluated_features = [
        row["feature_name"]
        for row in f2_payload["feature_evaluation"]["features"]
    ]
    if evaluated_features != required_features:
        raise CalibrationPromotionError(
            "maturity F2 evaluated features do not exactly match registry required_features"
        )

    f4_transition = bundle["transitions"][3]
    f4_payload = f4_transition["evidence"]["payload"]["independent_test"]
    dataset_reference = f4_payload["dataset"]
    if (
        dataset_reference.get("artifact_id")
        != candidate["prepared_dataset"]["dataset_id"]
        or dataset_reference.get("artifact_version")
        != candidate["prepared_dataset"]["dataset_version"]
        or _require_sha256(
            dataset_reference.get("content_sha256"),
            "maturity F4 dataset.content_sha256",
        )
        != _require_sha256(
            candidate["independent_test"]["content_sha256"],
            "candidate.independent_test.content_sha256",
        )
    ):
        raise CalibrationPromotionError(
            "maturity F4 independent dataset does not match candidate test seal"
        )
    report_reference = f4_payload["report"]
    if (
        report_reference.get("artifact_id") != report["report_version"]
        or report_reference.get("artifact_version") != report["report_version"]
        or _require_sha256(
            report_reference.get("content_sha256"),
            "maturity F4 report.content_sha256",
        )
        != canonical_sha256(report)
    ):
        raise CalibrationPromotionError(
            "maturity F4 report does not match independent-test report"
        )
    protocol_reference = f4_payload["protocol"]
    expected_protocol = {
        "artifact_id": report["protocol"]["protocol_id"],
        "artifact_version": report["protocol"]["protocol_version"],
        "content_sha256": report["protocol"]["content_sha256"].lower(),
        "source_sha256": report["protocol"]["source_sha256"].lower(),
        "source_kind": (
            "synthetic_test_fixture"
            if expected_scope != MATURITY_PRODUCTION_SCOPE
            else "external_preregistered_protocol"
        ),
        "registered_at": report["protocol"]["registered_at"],
    }
    if protocol_reference != expected_protocol:
        raise CalibrationPromotionError(
            "maturity F4 protocol does not match independent-test protocol"
        )
    if f4_transition["evidence"].get("evaluated_at") != report.get("evaluated_at"):
        raise CalibrationPromotionError(
            "maturity F4 evaluation timestamp does not match independent-test report"
        )
    return maturity_evidence_binding(bundle)


def _asset_from_candidate(
    candidate: dict[str, Any],
    report: dict[str, Any],
    decision: dict[str, Any],
    promotion_lineage: dict[str, Any],
    *,
    production: bool,
) -> dict[str, Any]:
    evidence = (
        {
            "status": "passed",
            "approved_for_scoring": True,
            "dataset_version": report["dataset"]["dataset_version"],
            "report_version": report["report_version"],
            "report_sha256": canonical_sha256(report),
            "acceptance_protocol_version": report["protocol"]["protocol_version"],
            "evaluated_at": report["evaluated_at"],
        }
        if production
        else {
            "status": "test_only",
            "approved_for_scoring": False,
            "dataset_version": report["dataset"]["dataset_version"],
            "report_version": report["report_version"],
            "report_sha256": canonical_sha256(report),
            "acceptance_protocol_version": report["protocol"]["protocol_version"],
            "evaluated_at": report["evaluated_at"],
        }
    )
    common = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "backend": candidate["backend"],
        "indicator_id": candidate["indicator_id"],
        "source": "coach_ground_truth_calibration",
        "ground_truth_dataset_version": candidate["prepared_dataset"]["dataset_version"],
        "artifact_scope": "production" if production else "test_only",
        "independent_test": evidence,
        "promotion_lineage": promotion_lineage,
    }
    if candidate["backend"] == "threshold_rule":
        asset = {
            **common,
            "threshold_version": decision["calibration_version"],
            "primary_feature": candidate["primary_feature"],
            "primary_feature_version": candidate["primary_feature_version"],
            "unit": candidate["unit"],
            "direction": candidate["direction"],
            "thresholds": list(candidate["thresholds"]),
        }
        validate_threshold_calibration(asset)
        return asset
    asset = {
        **common,
        "model_version": decision["calibration_version"],
        "feature_order": list(candidate["feature_order"]),
        "unit_by_feature": dict(candidate["unit_by_feature"]),
        "feature_version_by_feature": dict(candidate["feature_version_by_feature"]),
        "coefficients": list(candidate["coefficients"]),
        "intercept": candidate["intercept"],
        "cutpoints": list(candidate["cutpoints"]),
    }
    validate_ordinal_model(asset)
    return asset


def _build_promotion_lineage(
    *,
    candidate: dict[str, Any],
    independent_test_report: dict[str, Any],
    decision: dict[str, Any],
    feasibility_registry: dict[str, Any],
    indicator: dict[str, Any],
    promoted_at: str,
    maturity_binding: dict[str, Any] | None,
    source_replay: dict[str, Any] | None,
    input_snapshots: dict[str, Any] | None,
) -> dict[str, Any]:
    seal = candidate["independent_test"]
    lineage = {
        "schema_version": PROMOTION_LINEAGE_SCHEMA_VERSION,
        "promotion_id": decision["decision_id"],
        "promotion_version": decision["decision_version"],
        "calibration_version": decision["calibration_version"],
        "promoted_at": promoted_at,
        "indicator_id": candidate["indicator_id"],
        "backend": candidate["backend"],
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "content_sha256": canonical_sha256(candidate),
        },
        "prepared_dataset": deepcopy(candidate["prepared_dataset"]),
        "truth_authorization": deepcopy(candidate["truth_authorization"]),
        "fit_protocol": deepcopy(candidate["fit_protocol"]),
        "independent_test": {
            "seal_id": seal["seal_id"],
            "record_count": seal["record_count"],
            "content_sha256": seal["content_sha256"].lower(),
            "canonicalization": seal["canonicalization"],
        },
        "independent_test_report": {
            "report_version": independent_test_report["report_version"],
            "content_sha256": canonical_sha256(independent_test_report),
            "evaluated_at": independent_test_report["evaluated_at"],
        },
        "independent_test_protocol": {
            "protocol_id": independent_test_report["protocol"]["protocol_id"],
            "protocol_version": independent_test_report["protocol"]["protocol_version"],
            "content_sha256": independent_test_report["protocol"]["content_sha256"].lower(),
            "source_sha256": independent_test_report["protocol"]["source_sha256"].lower(),
            "registered_at": independent_test_report["protocol"]["registered_at"],
        },
        "decision": {
            "decision_id": decision["decision_id"],
            "decision_version": decision["decision_version"],
            "content_sha256": canonical_sha256(decision),
            "source_sha256": decision["source"]["source_sha256"].lower(),
            "registered_at": decision["source"]["registered_at"],
            "decided_at": decision["decided_at"],
        },
        "feasibility_registry": {
            "registry_version": feasibility_registry["registry_version"],
            "content_sha256": canonical_sha256(feasibility_registry),
            "indicator_id": candidate["indicator_id"],
            "indicator_level": indicator["feasibility_level"],
            "required_events": list(indicator["required_events"]),
            "required_features": list(indicator["required_features"]),
        },
    }
    if maturity_binding is not None:
        lineage["maturity_evidence"] = deepcopy(maturity_binding)
    if source_replay is not None:
        lineage["independent_test_source_replay"] = deepcopy(source_replay)
    if input_snapshots is not None:
        lineage["promotion_input_snapshots"] = deepcopy(input_snapshots)
    return lineage


def validate_promotion_lineage(
    lineage: dict[str, Any], *, asset: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(lineage, dict):
        raise CalibrationPromotionError("production asset promotion_lineage is required")
    if lineage.get("schema_version") != PROMOTION_LINEAGE_SCHEMA_VERSION:
        raise CalibrationPromotionError("unsupported promotion_lineage schema_version")
    for field in (
        "promotion_id",
        "promotion_version",
        "calibration_version",
        "promoted_at",
        "indicator_id",
        "backend",
    ):
        _require_string(lineage, field, "promotion_lineage")
    _timestamp(lineage["promoted_at"], "promotion_lineage.promoted_at")
    if lineage["backend"] not in {"threshold_rule", "ordinal_regression"}:
        raise CalibrationPromotionError("promotion_lineage.backend is invalid")

    candidate = _require_object(lineage.get("candidate"), "promotion_lineage.candidate")
    for field in ("candidate_id", "candidate_version"):
        _require_string(candidate, field, "promotion_lineage.candidate")
    _require_sha256(candidate.get("content_sha256"), "promotion_lineage.candidate.content_sha256")

    prepared = _require_object(
        lineage.get("prepared_dataset"), "promotion_lineage.prepared_dataset"
    )
    for field in ("dataset_id", "dataset_version", "artifact_scope", "label_resolution_policy"):
        _require_string(prepared, field, "promotion_lineage.prepared_dataset")
    if prepared["artifact_scope"] != "calibration_input":
        raise CalibrationPromotionError("production lineage requires real calibration_input")
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(prepared.get(field), f"promotion_lineage.prepared_dataset.{field}")

    truth_authorization = _require_object(
        lineage.get("truth_authorization"), "promotion_lineage.truth_authorization"
    )
    try:
        validate_scoring_truth_calibration_authorization_binding(truth_authorization)
    except ScoringTruthCalibrationAuthorizationError as exc:
        raise CalibrationPromotionError(
            f"invalid production truth authorization lineage: {exc}"
        ) from exc
    authorization_sha256 = _require_sha256(
        truth_authorization.get("binding_sha256"),
        "promotion_lineage.truth_authorization.binding_sha256",
    )
    _expect_equal(
        _require_sha256(
            prepared.get("truth_authorization_binding_sha256"),
            "promotion_lineage.prepared_dataset.truth_authorization_binding_sha256",
        ),
        authorization_sha256,
        "promotion_lineage.prepared_dataset.truth_authorization_binding_sha256",
    )

    fit_protocol = _require_object(
        lineage.get("fit_protocol"), "promotion_lineage.fit_protocol"
    )
    for field in ("protocol_id", "protocol_version", "artifact_scope", "label_resolution_policy"):
        _require_string(fit_protocol, field, "promotion_lineage.fit_protocol")
    if fit_protocol["artifact_scope"] != "calibration_fit_protocol":
        raise CalibrationPromotionError("production lineage requires real calibration_fit_protocol")
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(fit_protocol.get(field), f"promotion_lineage.fit_protocol.{field}")
    if prepared["label_resolution_policy"] != fit_protocol["label_resolution_policy"]:
        raise CalibrationPromotionError("promotion lineage label-resolution policy mismatch")

    independent = _require_object(
        lineage.get("independent_test"), "promotion_lineage.independent_test"
    )
    _require_string(independent, "seal_id", "promotion_lineage.independent_test")
    if not isinstance(independent.get("record_count"), int) or independent["record_count"] < 1:
        raise CalibrationPromotionError("promotion_lineage independent-test record_count must be positive")
    _require_sha256(
        independent.get("content_sha256"),
        "promotion_lineage.independent_test.content_sha256",
    )
    if independent.get("canonicalization") != CANONICALIZATION:
        raise CalibrationPromotionError("promotion_lineage canonicalization is invalid")

    test_report = _require_object(
        lineage.get("independent_test_report"),
        "promotion_lineage.independent_test_report",
    )
    _require_string(test_report, "report_version", "promotion_lineage.independent_test_report")
    _require_sha256(
        test_report.get("content_sha256"),
        "promotion_lineage.independent_test_report.content_sha256",
    )
    evaluated_at = _timestamp(
        test_report.get("evaluated_at"),
        "promotion_lineage.independent_test_report.evaluated_at",
    )

    independent_protocol = _require_object(
        lineage.get("independent_test_protocol"),
        "promotion_lineage.independent_test_protocol",
    )
    for field in ("protocol_id", "protocol_version", "registered_at"):
        _require_string(
            independent_protocol, field, "promotion_lineage.independent_test_protocol"
        )
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(
            independent_protocol.get(field),
            f"promotion_lineage.independent_test_protocol.{field}",
        )
    if _timestamp(
        independent_protocol["registered_at"],
        "promotion_lineage.independent_test_protocol.registered_at",
    ) > evaluated_at:
        raise CalibrationPromotionError("independent-test protocol registration follows evaluation")

    source_replay = _require_object(
        lineage.get("independent_test_source_replay"),
        "promotion_lineage.independent_test_source_replay",
    )
    if set(source_replay) != {"samples", "indicator_requirements"}:
        raise CalibrationPromotionError(
            "promotion_lineage.independent_test_source_replay fields are invalid"
        )
    sample_source = _require_object(
        source_replay.get("samples"),
        "promotion_lineage.independent_test_source_replay.samples",
    )
    requirements_source = _require_object(
        source_replay.get("indicator_requirements"),
        "promotion_lineage.independent_test_source_replay.indicator_requirements",
    )
    for field, item, record_count in (
        ("samples", sample_source, independent["record_count"]),
        ("indicator_requirements", requirements_source, None),
    ):
        context = f"promotion_lineage.independent_test_source_replay.{field}"
        source_kind = item.get("source_kind")
        expected_fields = {"source_kind", "canonical_sha256"}
        if field == "samples":
            expected_fields.update({"source_record_count", "sealed_record_count"})
        if source_kind == "file_bytes":
            expected_fields.update({"source_path", "raw_sha256"})
            _require_string(item, "source_path", context)
            _require_sha256(item.get("raw_sha256"), f"{context}.raw_sha256")
        elif source_kind != "in_memory_canonical_json":
            raise CalibrationPromotionError(f"{context}.source_kind is invalid")
        if set(item) != expected_fields:
            raise CalibrationPromotionError(f"{context} fields are invalid")
        _require_sha256(item.get("canonical_sha256"), f"{context}.canonical_sha256")
        if (
            field == "samples"
            and (
                not isinstance(item.get("source_record_count"), int)
                or item["source_record_count"] < record_count
                or item.get("sealed_record_count") != record_count
            )
        ):
            raise CalibrationPromotionError(
                f"{context} source/sealed record counts are inconsistent"
            )

    decision = _require_object(lineage.get("decision"), "promotion_lineage.decision")
    for field in ("decision_id", "decision_version", "registered_at", "decided_at"):
        _require_string(decision, field, "promotion_lineage.decision")
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(decision.get(field), f"promotion_lineage.decision.{field}")
    decision_registered = _timestamp(
        decision["registered_at"], "promotion_lineage.decision.registered_at"
    )
    decided_at = _timestamp(decision["decided_at"], "promotion_lineage.decision.decided_at")
    promoted_at = _timestamp(lineage["promoted_at"], "promotion_lineage.promoted_at")
    if decision_registered > decided_at or evaluated_at > decided_at or decided_at > promoted_at:
        raise CalibrationPromotionError("promotion_lineage timestamp order is invalid")

    registry = _require_object(
        lineage.get("feasibility_registry"), "promotion_lineage.feasibility_registry"
    )
    for field in ("registry_version", "indicator_id", "indicator_level"):
        _require_string(registry, field, "promotion_lineage.feasibility_registry")
    _require_sha256(
        registry.get("content_sha256"),
        "promotion_lineage.feasibility_registry.content_sha256",
    )
    if registry["indicator_level"] != "F4":
        raise CalibrationPromotionError("production promotion lineage requires F4")
    for field in ("required_events", "required_features"):
        values = registry.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
        ):
            raise CalibrationPromotionError(
                f"promotion_lineage.feasibility_registry.{field} must be a "
                "unique non-empty string list"
            )

    maturity = _validate_maturity_binding(
        lineage.get("maturity_evidence"),
        field="promotion_lineage.maturity_evidence",
        indicator_id=lineage["indicator_id"],
        registry_version=registry["registry_version"],
        registry_sha256=registry["content_sha256"],
    )
    _validate_promotion_input_snapshots(
        lineage.get("promotion_input_snapshots"),
        expected_canonical_sha256={
            "candidate": candidate["content_sha256"],
            "independent_test_report": test_report["content_sha256"],
            "independent_test_protocol": independent_protocol["content_sha256"],
            "decision": decision["content_sha256"],
            "maturity_evidence": maturity["content_sha256"],
            "feasibility_registry": registry["content_sha256"],
        },
    )

    if asset is not None:
        if asset.get("artifact_scope") != "production":
            raise CalibrationPromotionError("promotion lineage may authorize production assets only")
        version = asset.get("threshold_version") or asset.get("model_version")
        expected = {
            "indicator_id": asset.get("indicator_id"),
            "backend": asset.get("backend"),
            "calibration_version": version,
        }
        for field, value in expected.items():
            _expect_equal(lineage.get(field), value, f"promotion_lineage.{field}")
        _expect_equal(
            prepared["dataset_version"],
            asset.get("ground_truth_dataset_version"),
            "promotion_lineage.prepared_dataset.dataset_version",
        )
        evidence = _require_object(asset.get("independent_test"), "asset.independent_test")
        _expect_equal(test_report["report_version"], evidence.get("report_version"), "promotion_lineage.independent_test_report.report_version")
        _expect_equal(test_report["content_sha256"], evidence.get("report_sha256"), "promotion_lineage.independent_test_report.content_sha256")
        _expect_equal(test_report["evaluated_at"], evidence.get("evaluated_at"), "promotion_lineage.independent_test_report.evaluated_at")
        _expect_equal(independent_protocol["protocol_version"], evidence.get("acceptance_protocol_version"), "promotion_lineage.independent_test_protocol.protocol_version")
        _expect_equal(registry["indicator_id"], asset.get("indicator_id"), "promotion_lineage.feasibility_registry.indicator_id")
    return {
        "promotion_id": lineage["promotion_id"],
        "promotion_version": lineage["promotion_version"],
        "calibration_version": lineage["calibration_version"],
        "indicator_id": lineage["indicator_id"],
        "backend": lineage["backend"],
        "lineage_sha256": canonical_sha256(lineage),
        "promoted_registry_version": registry["registry_version"],
        "promoted_registry_content_sha256": registry["content_sha256"].lower(),
        "promoted_registry_indicator_id": registry["indicator_id"],
        "promoted_registry_indicator_level": registry["indicator_level"],
        "promoted_registry_required_events": list(registry["required_events"]),
        "promoted_registry_required_features": list(registry["required_features"]),
        "maturity_evidence_bundle_id": maturity["bundle_id"],
        "maturity_evidence_content_sha256": maturity["content_sha256"].lower(),
        "maturity_evidence_final_transition_sha256": maturity[
            "final_transition_sha256"
        ].lower(),
        "truth_authorization_id": truth_authorization["authorization_id"],
        "truth_authorization_binding_sha256": authorization_sha256,
    }


def promote_calibration_candidate(
    *,
    candidate: dict[str, Any],
    independent_test_report: dict[str, Any],
    independent_test_protocol: dict[str, Any] | None = None,
    independent_test_samples: list[dict[str, Any]] | None = None,
    indicator_requirements: dict[str, Any] | None = None,
    independent_test_source_replay: dict[str, Any] | None = None,
    promotion_input_snapshots: dict[str, Any] | None = None,
    decision: dict[str, Any],
    feasibility_registry: dict[str, Any],
    promoted_at: str,
    maturity_evidence: dict[str, Any] | None = None,
    verified_truth_authorization: VerifiedScoringTruthCalibrationAuthorization
    | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create an immutable scoring asset only after all external gates agree.

    The function copies fitted parameters verbatim.  It never estimates, adjusts,
    or supplies numeric thresholds, coefficients, cutpoints, or acceptance limits.
    It also never mutates the supplied feasibility registry.
    """

    try:
        validate_calibration_candidate(candidate)
    except CalibrationFitError as exc:
        raise CalibrationPromotionError(f"invalid calibration candidate: {exc}") from exc
    prepared_dataset = _require_object(
        candidate.get("prepared_dataset"), "candidate.prepared_dataset"
    )
    for field in ("dataset_id", "dataset_version"):
        _require_string(prepared_dataset, field, "candidate.prepared_dataset")
    fit_protocol = _require_object(candidate.get("fit_protocol"), "candidate.fit_protocol")
    for field in ("protocol_id", "protocol_version"):
        _require_string(fit_protocol, field, "candidate.fit_protocol")
    decision_audit = validate_promotion_decision(decision)
    report_audit = _validate_report(
        independent_test_report,
        candidate,
        independent_test_protocol,
    )
    candidate_is_test = candidate["artifact_scope"].startswith("synthetic_test_only")
    if candidate_is_test != decision_audit["is_test"] or candidate_is_test != report_audit["is_test"]:
        raise CalibrationPromotionError("candidate/report/decision real-vs-test scope mismatch")
    if candidate_is_test:
        if verified_truth_authorization is not None:
            raise CalibrationPromotionError(
                "synthetic promotion cannot accept production truth authority"
            )
    else:
        try:
            live_truth_authorization = (
                require_verified_scoring_truth_calibration_authorization(
                    verified_truth_authorization
                )
            )
        except ScoringTruthCalibrationAuthorizationError as exc:
            raise CalibrationPromotionError(str(exc)) from exc
        if live_truth_authorization != candidate.get("truth_authorization"):
            raise CalibrationPromotionError(
                "candidate truth authorization differs from live verification"
            )
    promoted_timestamp = _timestamp(promoted_at, "promoted_at")
    if report_audit["evaluated_at"] > decision_audit["decided_at"]:
        raise CalibrationPromotionError("promotion decision cannot precede test evaluation")
    if decision_audit["decided_at"] > promoted_timestamp:
        raise CalibrationPromotionError("asset promotion cannot precede the decision")

    indicator = _find_registry_indicator(feasibility_registry, candidate["indicator_id"])
    production = decision_audit["requested_artifact_scope"] == "production"
    if candidate_is_test and production:
        raise CalibrationPromotionError("synthetic candidates cannot create production assets")
    if production and indicator["feasibility_level"] != "F4":
        raise CalibrationPromotionError(
            "production scoring asset requires the registry indicator to already be F4"
        )
    maturity_binding: dict[str, Any] | None = None
    if production and maturity_evidence is None:
        raise CalibrationPromotionError(
            "production scoring asset requires complete F0-to-F4 maturity evidence"
        )
    if maturity_evidence is not None:
        maturity_binding = _validate_maturity_for_promotion(
            bundle=maturity_evidence,
            candidate=candidate,
            report=independent_test_report,
            registry=feasibility_registry,
            indicator=indicator,
            expected_scope=(
                MATURITY_PRODUCTION_SCOPE
                if production
                else "synthetic_test_only_maturity_evidence"
            ),
        )
        if production:
            _validate_report_requirements_for_maturity_transition(
                independent_test_report,
                maturity_evidence,
            )
        if _timestamp(
            maturity_evidence["created_at"], "maturity_evidence.created_at"
        ) > decision_audit["decided_at"]:
            raise CalibrationPromotionError(
                "promotion decision cannot predate maturity evidence creation"
            )

    _verify_decision_lineage(
        decision=decision,
        candidate=candidate,
        report=independent_test_report,
        registry=feasibility_registry,
        maturity_binding=maturity_binding,
    )

    source_replay: dict[str, Any] | None = None
    input_snapshots: dict[str, Any] | None = None
    if production:
        source_replay = _replay_production_independent_test(
            candidate=candidate,
            report=independent_test_report,
            protocol=independent_test_protocol,
            independent_test_samples=independent_test_samples,
            indicator_requirements=indicator_requirements,
            source_replay=independent_test_source_replay,
        )
        input_snapshots = _build_promotion_input_snapshots(
            candidate=candidate,
            independent_test_report=independent_test_report,
            independent_test_protocol=_require_object(
                independent_test_protocol, "independent_test_protocol"
            ),
            decision=decision,
            maturity_evidence=_require_object(
                maturity_evidence, "maturity_evidence"
            ),
            feasibility_registry=feasibility_registry,
            supplied=promotion_input_snapshots,
        )
    else:
        supplied_production_only_inputs = [
            name
            for name, value in (
                ("independent_test_samples", independent_test_samples),
                ("indicator_requirements", indicator_requirements),
                ("independent_test_source_replay", independent_test_source_replay),
                ("promotion_input_snapshots", promotion_input_snapshots),
            )
            if value is not None
        ]
        if supplied_production_only_inputs:
            raise CalibrationPromotionError(
                "synthetic/test-only promotion cannot accept production-only "
                f"inputs: {', '.join(supplied_production_only_inputs)}"
            )

    promotion_lineage = _build_promotion_lineage(
        candidate=candidate,
        independent_test_report=independent_test_report,
        decision=decision,
        feasibility_registry=feasibility_registry,
        indicator=indicator,
        promoted_at=promoted_at,
        maturity_binding=maturity_binding,
        source_replay=source_replay,
        input_snapshots=input_snapshots,
    )
    if production:
        validate_promotion_lineage(promotion_lineage)
    asset = _asset_from_candidate(
        candidate,
        independent_test_report,
        decision,
        promotion_lineage,
        production=production,
    )
    report = {
        "schema_version": PROMOTION_REPORT_SCHEMA_VERSION,
        "artifact_scope": (
            "calibration_promotion_report"
            if production
            else "synthetic_test_only_calibration_promotion_report"
        ),
        "promotion_id": decision["decision_id"],
        "promotion_version": decision["decision_version"],
        "status": "promoted",
        "indicator_id": candidate["indicator_id"],
        "promoted_at": promoted_at,
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        },
        "truth_authorization": deepcopy(candidate["truth_authorization"]),
        "independent_test_report": {
            "report_version": independent_test_report["report_version"],
            "content_sha256": canonical_sha256(independent_test_report),
            "status": "passed",
            "approved_before_promotion": False,
            "acceptance_protocol_id": independent_test_report["protocol"]["protocol_id"],
            "acceptance_protocol_version": independent_test_report["protocol"][
                "protocol_version"
            ],
            "acceptance_protocol_sha256": independent_test_report["protocol"][
                "content_sha256"
            ],
            "evaluated_at": independent_test_report["evaluated_at"],
        },
        "decision": {
            "decision_id": decision["decision_id"],
            "decision_version": decision["decision_version"],
            "content_sha256": canonical_sha256(decision),
            "reviewer_id": decision["reviewer"]["reviewer_id"],
            "decided_at": decision["decided_at"],
            "source_kind": decision["source"]["kind"],
            "source_sha256": decision["source"]["source_sha256"].lower(),
            "registered_at": decision["source"]["registered_at"],
        },
        "feasibility_registry": {
            "registry_version": feasibility_registry["registry_version"],
            "content_sha256": canonical_sha256(feasibility_registry),
            "indicator_level": indicator["feasibility_level"],
            "registry_mutated": False,
        },
        "output_asset": {
            "artifact_scope": asset["artifact_scope"],
            "backend": asset["backend"],
            "calibration_version": decision["calibration_version"],
            "content_sha256": canonical_sha256(asset),
        },
        "production_asset_created": production,
        "test_only_asset_created": not production,
        "semantics": (
            "parameters copied verbatim from the fitted candidate; no numeric "
            "acceptance limits or scoring parameters are created during promotion"
        ),
    }
    if maturity_binding is not None:
        report["maturity_evidence"] = deepcopy(maturity_binding)
    if source_replay is not None:
        report["independent_test_source_replay"] = deepcopy(source_replay)
    if input_snapshots is not None:
        report["promotion_input_snapshots"] = deepcopy(input_snapshots)
    return asset, report


def _calibration_version(asset: dict[str, Any]) -> str:
    field = "threshold_version" if asset.get("backend") == "threshold_rule" else "model_version"
    return _require_string(asset, field, "calibration_asset")


def _validate_promotion_report_against_asset(
    promotion_report: dict[str, Any], asset: dict[str, Any]
) -> None:
    if not isinstance(promotion_report, dict):
        raise CalibrationPromotionError("promotion report must be an object")
    if promotion_report.get("schema_version") != PROMOTION_REPORT_SCHEMA_VERSION:
        raise CalibrationPromotionError("unsupported promotion report schema_version")
    if promotion_report.get("artifact_scope") != "calibration_promotion_report":
        raise CalibrationPromotionError("trusted ledger accepts real promotion reports only")
    if (
        promotion_report.get("status") != "promoted"
        or promotion_report.get("production_asset_created") is not True
        or promotion_report.get("test_only_asset_created") is not False
    ):
        raise CalibrationPromotionError("promotion report did not create a production asset")
    lineage = _require_object(asset.get("promotion_lineage"), "asset.promotion_lineage")
    validate_promotion_lineage(lineage, asset=asset)
    for field in ("promotion_id", "promotion_version", "indicator_id", "promoted_at"):
        _expect_equal(
            promotion_report.get(field), lineage.get(field), f"promotion_report.{field}"
        )
    report_candidate = _require_object(
        promotion_report.get("candidate"), "promotion_report.candidate"
    )
    for field in ("candidate_id", "candidate_version", "content_sha256"):
        _expect_equal(
            report_candidate.get(field), lineage["candidate"].get(field),
            f"promotion_report.candidate.{field}",
        )
    _expect_equal(report_candidate.get("backend"), asset.get("backend"), "promotion_report.candidate.backend")
    test_report = _require_object(
        promotion_report.get("independent_test_report"),
        "promotion_report.independent_test_report",
    )
    for field in ("report_version", "content_sha256", "evaluated_at"):
        _expect_equal(
            test_report.get(field), lineage["independent_test_report"].get(field),
            f"promotion_report.independent_test_report.{field}",
        )
    _expect_equal(
        test_report.get("acceptance_protocol_version"),
        lineage["independent_test_protocol"]["protocol_version"],
        "promotion_report.independent_test_report.acceptance_protocol_version",
    )
    _expect_equal(
        test_report.get("acceptance_protocol_sha256"),
        lineage["independent_test_protocol"]["content_sha256"],
        "promotion_report.independent_test_report.acceptance_protocol_sha256",
    )
    report_source_replay = _require_object(
        promotion_report.get("independent_test_source_replay"),
        "promotion_report.independent_test_source_replay",
    )
    if report_source_replay != lineage["independent_test_source_replay"]:
        raise CalibrationPromotionError(
            "promotion_report.independent_test_source_replay lineage mismatch"
        )
    report_input_snapshots = _require_object(
        promotion_report.get("promotion_input_snapshots"),
        "promotion_report.promotion_input_snapshots",
    )
    if report_input_snapshots != lineage["promotion_input_snapshots"]:
        raise CalibrationPromotionError(
            "promotion_report.promotion_input_snapshots lineage mismatch"
        )
    report_decision = _require_object(
        promotion_report.get("decision"), "promotion_report.decision"
    )
    for field in ("decision_id", "decision_version", "content_sha256", "decided_at"):
        _expect_equal(
            report_decision.get(field), lineage["decision"].get(field),
            f"promotion_report.decision.{field}",
        )
    report_registry = _require_object(
        promotion_report.get("feasibility_registry"),
        "promotion_report.feasibility_registry",
    )
    for field in ("registry_version", "content_sha256", "indicator_level"):
        _expect_equal(
            report_registry.get(field), lineage["feasibility_registry"].get(field),
            f"promotion_report.feasibility_registry.{field}",
        )
    if report_registry.get("registry_mutated") is not False:
        raise CalibrationPromotionError("promotion report cannot claim registry mutation")
    report_maturity = _require_object(
        promotion_report.get("maturity_evidence"),
        "promotion_report.maturity_evidence",
    )
    if report_maturity != lineage["maturity_evidence"]:
        raise CalibrationPromotionError(
            "promotion_report.maturity_evidence lineage mismatch"
        )
    report_truth_authorization = _require_object(
        promotion_report.get("truth_authorization"),
        "promotion_report.truth_authorization",
    )
    if report_truth_authorization != lineage["truth_authorization"]:
        raise CalibrationPromotionError(
            "promotion_report.truth_authorization lineage mismatch"
        )
    output = _require_object(promotion_report.get("output_asset"), "promotion_report.output_asset")
    _verify_binding(
        output,
        {
            "artifact_scope": "production",
            "backend": asset["backend"],
            "calibration_version": _calibration_version(asset),
            "content_sha256": canonical_sha256(asset),
        },
        "promotion_report.output_asset",
        sha_fields=("content_sha256",),
    )


def build_trusted_promotion_ledger(
    *,
    promotions: list[tuple[dict[str, Any], dict[str, Any]]],
    ledger_id: str,
    ledger_version: str,
    authority_id: str,
    registered_at: str,
    verified_truth_authorizations: Mapping[
        str, VerifiedScoringTruthCalibrationAuthorization
    ]
    | None = None,
) -> dict[str, Any]:
    """Build an operator-controlled allow-list from already promoted outputs.

    The ledger is not a signature. Its authority comes from deployment ACLs and
    from configuring its path outside ordinary job requests. Every entry binds the
    canonical asset hash to the exact promotion lineage and promotion-report hash.
    """

    for name, value in (
        ("ledger_id", ledger_id),
        ("ledger_version", ledger_version),
        ("authority_id", authority_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise CalibrationPromotionError(f"{name} must be a non-empty string")
    ledger_time = _timestamp(registered_at, "trusted_ledger.registered_at")
    if not isinstance(promotions, list) or not promotions:
        raise CalibrationPromotionError("trusted ledger requires at least one promotion")
    if not isinstance(verified_truth_authorizations, Mapping):
        raise CalibrationPromotionError(
            "trusted ledger construction requires same-process verified truth "
            "authorization for every production indicator"
        )
    entries: list[dict[str, Any]] = []
    indicator_ids: set[str] = set()
    for index, pair in enumerate(promotions):
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise CalibrationPromotionError(f"promotions[{index}] must be an asset/report pair")
        asset, report = pair
        backend = asset.get("backend") if isinstance(asset, dict) else None
        if backend == "threshold_rule":
            validate_threshold_calibration(asset)
        elif backend == "ordinal_regression":
            validate_ordinal_model(asset)
        else:
            raise CalibrationPromotionError(f"promotions[{index}] backend is invalid")
        if asset.get("artifact_scope") != "production":
            raise CalibrationPromotionError("trusted ledger cannot authorize test-only assets")
        _validate_promotion_report_against_asset(report, asset)
        lineage = asset["promotion_lineage"]
        indicator_id = str(asset["indicator_id"])
        try:
            live_truth_authorization = (
                require_verified_scoring_truth_calibration_authorization(
                    verified_truth_authorizations.get(indicator_id)
                )
            )
        except ScoringTruthCalibrationAuthorizationError as exc:
            raise CalibrationPromotionError(
                f"trusted ledger {indicator_id} truth authorization is not live: {exc}"
            ) from exc
        if live_truth_authorization != lineage.get("truth_authorization"):
            raise CalibrationPromotionError(
                f"trusted ledger {indicator_id} truth authorization differs from "
                "live verification"
            )
        if _timestamp(lineage["promoted_at"], "promotion_lineage.promoted_at") > ledger_time:
            raise CalibrationPromotionError("trusted ledger registration precedes promotion")
        if indicator_id in indicator_ids:
            raise CalibrationPromotionError(
                f"trusted ledger has multiple active assets for {indicator_id}"
            )
        indicator_ids.add(indicator_id)
        asset_sha256 = canonical_sha256(asset)
        lineage_sha256 = canonical_sha256(lineage)
        entries.append(
            {
                "entry_id": f"{indicator_id}:{_calibration_version(asset)}:{asset_sha256[:16]}",
                "status": "active",
                "indicator_id": indicator_id,
                "backend": asset["backend"],
                "calibration_version": _calibration_version(asset),
                "asset_content_sha256": asset_sha256,
                "promotion_lineage_sha256": lineage_sha256,
                "promotion_lineage": deepcopy(lineage),
                "promotion_report": {
                    "promotion_id": report["promotion_id"],
                    "promotion_version": report["promotion_version"],
                    "content_sha256": canonical_sha256(report),
                    "promoted_at": report["promoted_at"],
                },
            }
        )
    ledger = {
        "schema_version": TRUSTED_PROMOTION_LEDGER_SCHEMA_VERSION,
        "artifact_scope": "trusted_calibration_promotion_ledger",
        "ledger_id": ledger_id,
        "ledger_version": ledger_version,
        "registered_at": registered_at,
        "source": {
            "kind": "operator_controlled_local_ledger",
            "authority_id": authority_id,
        },
        "entries": entries,
        "trust_boundary": (
            "local ledger path and file ACL are controlled by the deployment operator; "
            "ordinary pipeline job requests cannot select or modify this ledger"
        ),
    }
    validate_trusted_promotion_ledger(ledger)
    return ledger


def validate_trusted_promotion_ledger(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise CalibrationPromotionError("trusted promotion ledger must be an object")
    if payload.get("schema_version") != TRUSTED_PROMOTION_LEDGER_SCHEMA_VERSION:
        raise CalibrationPromotionError("unsupported trusted promotion ledger schema_version")
    if payload.get("artifact_scope") != "trusted_calibration_promotion_ledger":
        raise CalibrationPromotionError("trusted promotion ledger artifact_scope is invalid")
    for field in ("ledger_id", "ledger_version", "registered_at", "trust_boundary"):
        _require_string(payload, field, "trusted_ledger")
    ledger_time = _timestamp(payload["registered_at"], "trusted_ledger.registered_at")
    source = _require_object(payload.get("source"), "trusted_ledger.source")
    if source.get("kind") != "operator_controlled_local_ledger":
        raise CalibrationPromotionError("trusted ledger source.kind is invalid")
    _require_string(source, "authority_id", "trusted_ledger.source")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CalibrationPromotionError("trusted ledger entries must be non-empty")
    entry_ids: set[str] = set()
    active_indicators: set[str] = set()
    for index, entry in enumerate(entries):
        context = f"trusted_ledger.entries[{index}]"
        if not isinstance(entry, dict):
            raise CalibrationPromotionError(f"{context} must be an object")
        for field in ("entry_id", "indicator_id", "backend", "calibration_version"):
            _require_string(entry, field, context)
        if entry["entry_id"] in entry_ids:
            raise CalibrationPromotionError("trusted ledger entry_id values must be unique")
        entry_ids.add(entry["entry_id"])
        if entry.get("status") != "active":
            raise CalibrationPromotionError("trusted ledger supports active entries only")
        if entry["indicator_id"] in active_indicators:
            raise CalibrationPromotionError(
                f"trusted ledger has multiple active assets for {entry['indicator_id']}"
            )
        active_indicators.add(entry["indicator_id"])
        if entry["backend"] not in {"threshold_rule", "ordinal_regression"}:
            raise CalibrationPromotionError(f"{context}.backend is invalid")
        for field in ("asset_content_sha256", "promotion_lineage_sha256"):
            _require_sha256(entry.get(field), f"{context}.{field}")
        lineage = _require_object(entry.get("promotion_lineage"), f"{context}.promotion_lineage")
        audit = validate_promotion_lineage(lineage)
        _expect_equal(entry["indicator_id"], audit["indicator_id"], f"{context}.indicator_id")
        _expect_equal(entry["backend"], audit["backend"], f"{context}.backend")
        _expect_equal(
            entry["calibration_version"], audit["calibration_version"],
            f"{context}.calibration_version",
        )
        _expect_equal(
            entry["promotion_lineage_sha256"], audit["lineage_sha256"],
            f"{context}.promotion_lineage_sha256",
        )
        report = _require_object(entry.get("promotion_report"), f"{context}.promotion_report")
        for field in ("promotion_id", "promotion_version", "promoted_at"):
            _require_string(report, field, f"{context}.promotion_report")
        _require_sha256(report.get("content_sha256"), f"{context}.promotion_report.content_sha256")
        _expect_equal(report["promotion_id"], lineage["promotion_id"], f"{context}.promotion_report.promotion_id")
        _expect_equal(report["promotion_version"], lineage["promotion_version"], f"{context}.promotion_report.promotion_version")
        _expect_equal(report["promoted_at"], lineage["promoted_at"], f"{context}.promotion_report.promoted_at")
        if _timestamp(report["promoted_at"], f"{context}.promotion_report.promoted_at") > ledger_time:
            raise CalibrationPromotionError("trusted ledger registration precedes an entry promotion")


def verify_production_asset_with_trusted_ledger(
    asset: dict[str, Any], ledger: dict[str, Any]
) -> dict[str, Any]:
    backend = asset.get("backend") if isinstance(asset, dict) else None
    if backend == "threshold_rule":
        validate_threshold_calibration(asset)
    elif backend == "ordinal_regression":
        validate_ordinal_model(asset)
    else:
        raise CalibrationPromotionError("production calibration backend is invalid")
    if asset.get("artifact_scope") != "production":
        raise CalibrationPromotionError("trusted ledger verification requires production asset")
    evidence = asset.get("independent_test", {})
    if evidence.get("status") != "passed" or evidence.get("approved_for_scoring") is not True:
        raise CalibrationPromotionError("production asset lacks passed independent-test approval")
    lineage = _require_object(asset.get("promotion_lineage"), "asset.promotion_lineage")
    lineage_audit = validate_promotion_lineage(lineage, asset=asset)
    validate_trusted_promotion_ledger(ledger)
    matches = [
        entry
        for entry in ledger["entries"]
        if entry["status"] == "active"
        and entry["indicator_id"] == asset["indicator_id"]
        and entry["backend"] == asset["backend"]
        and entry["calibration_version"] == _calibration_version(asset)
    ]
    if len(matches) != 1:
        raise CalibrationPromotionError(
            "production asset has no unique active entry in the trusted promotion ledger"
        )
    entry = matches[0]
    _expect_equal(
        entry["asset_content_sha256"], canonical_sha256(asset),
        "trusted_ledger.asset_content_sha256",
    )
    _expect_equal(
        entry["promotion_lineage_sha256"], lineage_audit["lineage_sha256"],
        "trusted_ledger.promotion_lineage_sha256",
    )
    if canonical_sha256(entry["promotion_lineage"]) != canonical_sha256(lineage):
        raise CalibrationPromotionError("trusted ledger promotion_lineage snapshot mismatch")
    return {
        "ledger_id": ledger["ledger_id"],
        "ledger_version": ledger["ledger_version"],
        "ledger_authority_id": ledger["source"]["authority_id"],
        "entry_id": entry["entry_id"],
        "promotion_report_sha256": entry["promotion_report"]["content_sha256"],
        **lineage_audit,
    }


def authorize_production_asset_with_trusted_ledger(
    asset: dict[str, Any], ledger: dict[str, Any]
) -> TrustedProductionCalibration:
    authorization = verify_production_asset_with_trusted_ledger(asset, ledger)
    return TrustedProductionCalibration(
        asset,
        authorization,
        _token=_TRUSTED_CALIBRATION_TOKEN,
    )


def bind_trusted_production_calibration_to_registry(
    calibration: TrustedProductionCalibration,
    feasibility_registry: dict[str, Any],
) -> TrustedProductionCalibration:
    """Bind a ledger-authorized asset to the exact registry used at runtime.

    Promotion proves that an asset was approved against one immutable registry
    snapshot.  It does *not* prove that a later scoring job is using that same
    snapshot.  This second gate compares the canonical registry hash, version,
    indicator identity and current F4 state before the trusted wrapper can score.
    """

    if not isinstance(calibration, TrustedProductionCalibration):
        raise CalibrationPromotionError(
            "runtime registry binding requires ledger-authorized production calibration"
        )
    try:
        validate_feasibility_registry(feasibility_registry)
    except ValueError as exc:
        raise CalibrationPromotionError(
            f"invalid runtime feasibility registry: {exc}"
        ) from exc

    authorization = calibration.authorization
    indicator_id = str(calibration["indicator_id"])
    promoted_indicator_id = authorization.get("promoted_registry_indicator_id")
    if promoted_indicator_id != indicator_id:
        raise CalibrationPromotionError(
            "promoted registry indicator does not match production calibration"
        )

    current_version = str(feasibility_registry["registry_version"])
    promoted_version = authorization.get("promoted_registry_version")
    if current_version != promoted_version:
        raise CalibrationPromotionError(
            "runtime feasibility registry version does not match promoted registry: "
            f"runtime={current_version!r}, promoted={promoted_version!r}"
        )

    indicator = _find_registry_indicator(feasibility_registry, indicator_id)
    if indicator["feasibility_level"] != "F4":
        raise CalibrationPromotionError(
            f"runtime feasibility registry indicator {indicator_id} is not F4"
        )

    current_sha256 = canonical_sha256(feasibility_registry)
    promoted_sha256 = authorization.get("promoted_registry_content_sha256")
    if current_sha256 != promoted_sha256:
        raise CalibrationPromotionError(
            "runtime feasibility registry content does not match promoted registry "
            "despite equal registry_version"
        )

    if authorization.get("promoted_registry_indicator_level") != "F4":
        raise CalibrationPromotionError(
            "production calibration was not promoted against an F4 indicator"
        )
    if authorization.get("promoted_registry_required_events") != indicator.get(
        "required_events"
    ):
        raise CalibrationPromotionError(
            "runtime indicator event contract does not match promoted registry"
        )
    if authorization.get("promoted_registry_required_features") != indicator.get(
        "required_features"
    ):
        raise CalibrationPromotionError(
            "runtime indicator feature contract does not match promoted registry"
        )

    bound_authorization = {
        **authorization,
        "runtime_registry_binding_verified": True,
        "runtime_registry_version": current_version,
        "runtime_registry_content_sha256": current_sha256,
        "runtime_registry_indicator_id": indicator_id,
        "runtime_registry_indicator_level": indicator["feasibility_level"],
        "runtime_registry_required_events": list(indicator["required_events"]),
        "runtime_registry_required_features": list(indicator["required_features"]),
    }
    return TrustedProductionCalibration(
        deepcopy(dict(calibration)),
        bound_authorization,
        _token=_TRUSTED_CALIBRATION_TOKEN,
    )


def is_trusted_production_calibration(value: Any) -> bool:
    return isinstance(value, TrustedProductionCalibration)
