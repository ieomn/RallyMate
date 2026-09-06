from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_evaluation.pose_diagnostic_truth import (
    DIAGNOSTIC_TYPES,
    EVALUATION_VERSION,
    MATCHING_PROTOCOL,
    PoseDiagnosticTruthError,
    evaluate_pose_diagnostic_truth,
    validate_pose_diagnostic_evaluation,
    validate_pose_diagnostic_truth_pack_sources,
)
from rallymate_scoring.quality_policy import QUALITY_POLICY_VERSION


PROTOCOL_SCHEMA_VERSION = "1.0.0"
PROTOCOL_VERSION = "pose-diagnostic-gate-acceptance-protocol-v1.0.0"
REVIEW_SCHEMA_VERSION = "1.0.0"
REVIEW_VERSION = "pose-diagnostic-quality-gate-review-v1.0.0"
PROTOCOL_SCOPE = "external_preregistered_diagnostic_gate_protocol"
CANONICALIZATION = "rallymate-canonical-json-v1"


class DiagnosticPolicyReviewError(ValueError):
    """Raised when diagnostic policy evidence is incomplete or untrusted."""


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _exact_keys(value: Mapping[str, Any], required: set[str], field: str) -> None:
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        raise DiagnosticPolicyReviewError(
            f"{field} missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise DiagnosticPolicyReviewError(
            f"{field} unsupported fields: {', '.join(sorted(extra))}"
        )


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiagnosticPolicyReviewError(f"{field} must be a non-empty string")
    return value


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise DiagnosticPolicyReviewError(f"{field} must be a SHA-256")
    return value.upper()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DiagnosticPolicyReviewError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiagnosticPolicyReviewError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DiagnosticPolicyReviewError(f"{field} must include a timezone")
    return parsed


def _rate(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise DiagnosticPolicyReviewError(f"{field} must be within [0,1]")
    return float(value)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DiagnosticPolicyReviewError(f"{field} must be a positive integer")
    return value


def validate_diagnostic_gate_acceptance_protocol(
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(protocol, dict):
        raise DiagnosticPolicyReviewError("protocol must be an object")
    _exact_keys(
        protocol,
        {
            "schema_version",
            "protocol_version",
            "protocol_id",
            "artifact_scope",
            "created_at",
            "registered_at",
            "registered_by",
            "registration",
            "quality_policy_version_under_review",
            "evaluation_version",
            "matching_protocol_version",
            "diagnostic_types",
            "expected_scopes",
            "requirements",
            "safety",
        },
        "protocol",
    )
    if protocol.get("schema_version") != PROTOCOL_SCHEMA_VERSION:
        raise DiagnosticPolicyReviewError("unsupported protocol schema_version")
    if protocol.get("protocol_version") != PROTOCOL_VERSION:
        raise DiagnosticPolicyReviewError("unsupported protocol version")
    if protocol.get("artifact_scope") != PROTOCOL_SCOPE:
        raise DiagnosticPolicyReviewError("protocol is not externally preregistered")
    _string(protocol.get("protocol_id"), "protocol.protocol_id")
    created_at = _timestamp(protocol.get("created_at"), "protocol.created_at")
    registered_at = _timestamp(protocol.get("registered_at"), "protocol.registered_at")
    if registered_at < created_at:
        raise DiagnosticPolicyReviewError("protocol registration predates creation")
    reviewer = protocol.get("registered_by")
    if not isinstance(reviewer, Mapping):
        raise DiagnosticPolicyReviewError("protocol.registered_by must be an object")
    _exact_keys(reviewer, {"reviewer_id", "role"}, "protocol.registered_by")
    _string(reviewer.get("reviewer_id"), "protocol.registered_by.reviewer_id")
    _string(reviewer.get("role"), "protocol.registered_by.role")
    registration = protocol.get("registration")
    if not isinstance(registration, Mapping):
        raise DiagnosticPolicyReviewError("protocol.registration must be an object")
    _exact_keys(
        registration,
        {"registry_id", "source_kind", "source_sha256"},
        "protocol.registration",
    )
    _string(registration.get("registry_id"), "protocol.registration.registry_id")
    if registration.get("source_kind") != "external_protocol_registry":
        raise DiagnosticPolicyReviewError("protocol registration source is not external")
    _sha(registration.get("source_sha256"), "protocol.registration.source_sha256")
    if protocol.get("quality_policy_version_under_review") != QUALITY_POLICY_VERSION:
        raise DiagnosticPolicyReviewError("protocol targets a different quality policy")
    if protocol.get("evaluation_version") != EVALUATION_VERSION:
        raise DiagnosticPolicyReviewError("protocol targets a different evaluation version")
    if protocol.get("matching_protocol_version") != MATCHING_PROTOCOL:
        raise DiagnosticPolicyReviewError("protocol targets a different matching protocol")
    if protocol.get("diagnostic_types") != list(DIAGNOSTIC_TYPES):
        raise DiagnosticPolicyReviewError("protocol diagnostic type set mismatch")
    scopes = protocol.get("expected_scopes")
    if not isinstance(scopes, list) or not scopes:
        raise DiagnosticPolicyReviewError("protocol.expected_scopes must be non-empty")
    scope_ids: set[str] = set()
    manifest_hashes: set[str] = set()
    for index, scope in enumerate(scopes):
        field = f"protocol.expected_scopes[{index}]"
        if not isinstance(scope, Mapping):
            raise DiagnosticPolicyReviewError(f"{field} must be an object")
        _exact_keys(
            scope,
            {
                "scope_id",
                "video_id",
                "truth_pack_manifest_content_sha256",
                "truth_pack_source_queue_sha256",
                "queue_artifact_binding_sha256",
            },
            field,
        )
        scope_id = _string(scope.get("scope_id"), f"{field}.scope_id")
        manifest_hash = _sha(
            scope.get("truth_pack_manifest_content_sha256"),
            f"{field}.truth_pack_manifest_content_sha256",
        )
        if scope_id in scope_ids or manifest_hash in manifest_hashes:
            raise DiagnosticPolicyReviewError("protocol scope IDs/hashes must be unique")
        scope_ids.add(scope_id)
        manifest_hashes.add(manifest_hash)
        _string(scope.get("video_id"), f"{field}.video_id")
        _sha(
            scope.get("truth_pack_source_queue_sha256"),
            f"{field}.truth_pack_source_queue_sha256",
        )
        _sha(
            scope.get("queue_artifact_binding_sha256"),
            f"{field}.queue_artifact_binding_sha256",
        )
    requirements = protocol.get("requirements")
    if not isinstance(requirements, Mapping):
        raise DiagnosticPolicyReviewError("protocol.requirements must be an object")
    _exact_keys(
        requirements,
        {"require_all_expected_scopes", "per_diagnostic_type"},
        "protocol.requirements",
    )
    if requirements.get("require_all_expected_scopes") is not True:
        raise DiagnosticPolicyReviewError("protocol must require all expected scopes")
    rows = requirements.get("per_diagnostic_type")
    if not isinstance(rows, list) or len(rows) != len(DIAGNOSTIC_TYPES):
        raise DiagnosticPolicyReviewError("protocol criteria must cover every diagnostic type")
    names: set[str] = set()
    for index, row in enumerate(rows):
        field = f"protocol.requirements.per_diagnostic_type[{index}]"
        if not isinstance(row, Mapping):
            raise DiagnosticPolicyReviewError(f"{field} must be an object")
        _exact_keys(
            row,
            {
                "diagnostic_type",
                "require_full_timeline_per_scope",
                "minimum_evaluated_scopes",
                "minimum_total_truth_positives",
                "minimum_precision",
                "minimum_recall",
                "minimum_f1",
            },
            field,
        )
        diagnostic_type = row.get("diagnostic_type")
        if diagnostic_type not in DIAGNOSTIC_TYPES or diagnostic_type in names:
            raise DiagnosticPolicyReviewError("protocol criteria type invalid or duplicate")
        names.add(diagnostic_type)
        if row.get("require_full_timeline_per_scope") is not True:
            raise DiagnosticPolicyReviewError("protocol must require full-timeline scope")
        minimum_scopes = _positive_int(
            row.get("minimum_evaluated_scopes"), f"{field}.minimum_evaluated_scopes"
        )
        if minimum_scopes > len(scopes):
            raise DiagnosticPolicyReviewError("minimum scopes exceed registered scopes")
        _positive_int(
            row.get("minimum_total_truth_positives"),
            f"{field}.minimum_total_truth_positives",
        )
        for metric_name in ("minimum_precision", "minimum_recall", "minimum_f1"):
            _rate(row.get(metric_name), f"{field}.{metric_name}")
    if names != set(DIAGNOSTIC_TYPES):
        raise DiagnosticPolicyReviewError("protocol criteria type set mismatch")
    safety = protocol.get("safety")
    if not isinstance(safety, Mapping):
        raise DiagnosticPolicyReviewError("protocol.safety must be an object")
    _exact_keys(
        safety,
        {
            "thresholds_are_A_to_E_scoring_thresholds",
            "automatic_quality_policy_change_allowed",
            "result_revealed_before_registration",
        },
        "protocol.safety",
    )
    if any(safety.get(field) is not False for field in safety):
        raise DiagnosticPolicyReviewError("protocol contains unsafe claims")
    return dict(protocol)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 8)


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 8)


def _load_and_recompute_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, Mapping):
        raise DiagnosticPolicyReviewError("evaluation bundle must be an object")
    _exact_keys(
        bundle,
        {"manifest", "evaluation", "manifest_path", "evaluation_path"},
        "evaluation_bundle",
    )
    manifest = bundle.get("manifest")
    evaluation = bundle.get("evaluation")
    if not isinstance(manifest, Mapping) or not isinstance(evaluation, Mapping):
        raise DiagnosticPolicyReviewError("evaluation bundle payloads must be objects")
    manifest_path = Path(str(bundle.get("manifest_path", ""))).resolve()
    evaluation_path = Path(str(bundle.get("evaluation_path", ""))).resolve()
    if not manifest_path.is_file() or not evaluation_path.is_file():
        raise DiagnosticPolicyReviewError("evaluation bundle path is missing")
    if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise DiagnosticPolicyReviewError("manifest file differs from supplied payload")
    if json.loads(evaluation_path.read_text(encoding="utf-8")) != evaluation:
        raise DiagnosticPolicyReviewError("evaluation file differs from supplied payload")
    try:
        validate_pose_diagnostic_truth_pack_sources(manifest)
        validate_pose_diagnostic_evaluation(evaluation)
    except PoseDiagnosticTruthError as exc:
        raise DiagnosticPolicyReviewError(str(exc)) from exc
    manifest_hash = _canonical_hash(manifest)
    if evaluation.get("source", {}).get("truth_pack_manifest_content_sha256") != manifest_hash:
        raise DiagnosticPolicyReviewError("evaluation does not bind the supplied manifest")
    coverage_path = Path(str(evaluation["source"]["coverage"]["path"])).resolve()
    positives_path = Path(str(evaluation["source"]["positives"]["path"])).resolve()
    try:
        recomputed = evaluate_pose_diagnostic_truth(
            manifest=manifest,
            coverage_csv_text=coverage_path.read_text(encoding="utf-8"),
            positives_csv_text=positives_path.read_text(encoding="utf-8"),
            coverage_path=coverage_path,
            positives_path=positives_path,
            generated_at=evaluation["generated_at"],
        )
    except (OSError, PoseDiagnosticTruthError) as exc:
        raise DiagnosticPolicyReviewError(str(exc)) from exc
    if recomputed != evaluation:
        raise DiagnosticPolicyReviewError(
            "evaluation report does not exactly match recomputed truth inputs"
        )
    return {
        "manifest": dict(manifest),
        "evaluation": dict(evaluation),
        "manifest_path": manifest_path,
        "evaluation_path": evaluation_path,
        "manifest_content_sha256": manifest_hash,
        "manifest_file_sha256": _sha256(manifest_path),
        "evaluation_file_sha256": _sha256(evaluation_path),
    }


def review_pose_diagnostic_quality_gate(
    *,
    evaluation_bundles: list[Mapping[str, Any]],
    protocol: Mapping[str, Any] | None = None,
    protocol_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not evaluation_bundles:
        raise DiagnosticPolicyReviewError("at least one evaluation bundle is required")
    bundles = [_load_and_recompute_bundle(bundle) for bundle in evaluation_bundles]
    manifest_hashes = [bundle["manifest_content_sha256"] for bundle in bundles]
    if len(manifest_hashes) != len(set(manifest_hashes)):
        raise DiagnosticPolicyReviewError("evaluation bundles must be unique")

    validated_protocol = None
    protocol_binding = None
    missing_scope_hashes: list[str] = []
    if protocol is not None:
        validated_protocol = validate_diagnostic_gate_acceptance_protocol(protocol)
        if protocol_path is None:
            raise DiagnosticPolicyReviewError("protocol_path is required with protocol")
        protocol_path = protocol_path.resolve()
        if not protocol_path.is_file() or json.loads(
            protocol_path.read_text(encoding="utf-8")
        ) != protocol:
            raise DiagnosticPolicyReviewError("protocol path/payload mismatch")
        registered_at = _timestamp(protocol["registered_at"], "protocol.registered_at")
        expected_by_hash = {
            item["truth_pack_manifest_content_sha256"].upper(): item
            for item in protocol["expected_scopes"]
        }
        extra = set(manifest_hashes) - set(expected_by_hash)
        if extra:
            raise DiagnosticPolicyReviewError("unregistered evaluation scope supplied")
        missing_scope_hashes = sorted(set(expected_by_hash) - set(manifest_hashes))
        for bundle in bundles:
            evaluation = bundle["evaluation"]
            manifest = bundle["manifest"]
            expected = expected_by_hash[bundle["manifest_content_sha256"]]
            if any(
                (
                    str(expected[field]).upper() != str(actual).upper()
                    if field.endswith("sha256")
                    else expected[field] != actual
                )
                for field, actual in (
                    ("video_id", manifest["video_id"]),
                    (
                        "truth_pack_source_queue_sha256",
                        manifest["source_queue"]["sha256"],
                    ),
                    (
                        "queue_artifact_binding_sha256",
                        manifest["source_queue"]["artifact_binding_sha256"],
                    ),
                )
            ):
                raise DiagnosticPolicyReviewError("registered evaluation scope binding mismatch")
            if registered_at > _timestamp(
                evaluation["generated_at"], "evaluation.generated_at"
            ):
                raise DiagnosticPolicyReviewError(
                    "protocol must be registered before evaluation result generation"
                )
        protocol_binding = {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "path": str(protocol_path),
            "sha256": _sha256(protocol_path),
            "content_sha256": _canonical_hash(protocol),
            "registered_at": protocol["registered_at"],
            "registration": dict(protocol["registration"]),
        }

    aggregated: dict[str, dict[str, Any]] = {}
    criteria_by_type = (
        {
            row["diagnostic_type"]: row
            for row in validated_protocol["requirements"]["per_diagnostic_type"]
        }
        if validated_protocol is not None
        else {}
    )
    any_annotation_missing = False
    any_insufficient = False
    any_failed = False
    for diagnostic_type in DIAGNOSTIC_TYPES:
        rows = [
            bundle["evaluation"]["by_diagnostic_type"][diagnostic_type]
            for bundle in bundles
        ]
        tp = sum(row["true_positive"] for row in rows)
        fp = sum(row["false_positive"] for row in rows)
        fn = sum(row["false_negative"] for row in rows)
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = _f1(precision, recall)
        full_scopes = sum(row["coverage_status"] == "full_timeline" for row in rows)
        truth_positives = tp + fn
        criteria = criteria_by_type.get(diagnostic_type)
        criteria_results = None
        if criteria is None:
            decision_status = "acceptance_protocol_required"
        else:
            criteria_results = {
                "full_timeline_scopes": {
                    "observed": full_scopes,
                    "required": criteria["minimum_evaluated_scopes"],
                    "passed": full_scopes >= criteria["minimum_evaluated_scopes"],
                },
                "truth_positives": {
                    "observed": truth_positives,
                    "required": criteria["minimum_total_truth_positives"],
                    "passed": truth_positives
                    >= criteria["minimum_total_truth_positives"],
                },
                "precision": {
                    "observed": precision,
                    "required": criteria["minimum_precision"],
                    "passed": precision is not None
                    and precision >= criteria["minimum_precision"],
                },
                "recall": {
                    "observed": recall,
                    "required": criteria["minimum_recall"],
                    "passed": recall is not None
                    and recall >= criteria["minimum_recall"],
                },
                "f1": {
                    "observed": f1,
                    "required": criteria["minimum_f1"],
                    "passed": f1 is not None and f1 >= criteria["minimum_f1"],
                },
            }
            if not criteria_results["full_timeline_scopes"]["passed"]:
                any_annotation_missing = True
                decision_status = "annotation_required"
            elif not criteria_results["truth_positives"]["passed"]:
                any_insufficient = True
                decision_status = "insufficient_positive_truth"
            elif all(
                criteria_results[name]["passed"]
                for name in ("precision", "recall", "f1")
            ):
                decision_status = "criteria_passed_pending_human_policy_review"
            else:
                any_failed = True
                decision_status = "acceptance_criteria_failed"
        aggregated[diagnostic_type] = {
            "evaluation_scope_count": len(rows),
            "full_timeline_scope_count": full_scopes,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "truth_positive_count": truth_positives,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "criteria": dict(criteria) if criteria is not None else None,
            "criteria_results": criteria_results,
            "status": decision_status,
        }

    blockers: list[str] = []
    if validated_protocol is None:
        blockers.append("external_preregistered_acceptance_protocol_missing")
    if missing_scope_hashes:
        blockers.append("registered_evaluation_scopes_missing")
    if any(bundle["evaluation"]["status"] != "evaluated_full_timeline" for bundle in bundles):
        blockers.append("full_timeline_diagnostic_truth_missing")
    if any_insufficient:
        blockers.append("minimum_positive_truth_not_met")
    if any_failed:
        blockers.append("diagnostic_acceptance_metrics_not_met")
    if validated_protocol is None:
        status = (
            "annotation_and_protocol_required"
            if "full_timeline_diagnostic_truth_missing" in blockers
            else "acceptance_protocol_required"
        )
    elif missing_scope_hashes:
        status = "registered_scope_incomplete"
    elif any_annotation_missing or "full_timeline_diagnostic_truth_missing" in blockers:
        status = "annotation_required"
    elif any_insufficient:
        status = "insufficient_evidence"
    elif any_failed:
        status = "acceptance_criteria_failed"
    else:
        status = "eligible_for_human_policy_review"

    report = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_version": REVIEW_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "quality_policy_version_under_review": QUALITY_POLICY_VERSION,
        "protocol": protocol_binding,
        "sources": [
            {
                "video_id": bundle["manifest"]["video_id"],
                "manifest_path": str(bundle["manifest_path"]),
                "manifest_file_sha256": bundle["manifest_file_sha256"],
                "manifest_content_sha256": bundle["manifest_content_sha256"],
                "evaluation_path": str(bundle["evaluation_path"]),
                "evaluation_sha256": bundle["evaluation_file_sha256"],
                "evaluation_status": bundle["evaluation"]["status"],
                "truth_pack_source_queue_sha256": bundle["manifest"]["source_queue"][
                    "sha256"
                ],
                "queue_artifact_binding_sha256": bundle["manifest"]["source_queue"][
                    "artifact_binding_sha256"
                ],
            }
            for bundle in bundles
        ],
        "missing_registered_scope_manifest_hashes": missing_scope_hashes,
        "by_diagnostic_type": aggregated,
        "blockers": blockers,
        "policy_decision": {
            "automatic_policy_change_allowed": False,
            "quality_policy_change_applied": False,
            "next_quality_policy_version": None,
            "human_review_required": True,
            "semantics": (
                "diagnostic evidence review only; passing criteria is not a policy release"
            ),
        },
        "safety": {
            "A_to_E_thresholds_generated": False,
            "grades_generated": False,
            "quality_gate_modified": False,
            "maturity_promoted": False,
            "self_declared_protocol_identity_is_trusted": False,
        },
    }
    validate_diagnostic_policy_review(report)
    return report


def validate_diagnostic_policy_review(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise DiagnosticPolicyReviewError("unsupported review schema_version")
    if report.get("review_version") != REVIEW_VERSION:
        raise DiagnosticPolicyReviewError("unsupported review version")
    if report.get("status") not in {
        "annotation_and_protocol_required",
        "acceptance_protocol_required",
        "registered_scope_incomplete",
        "annotation_required",
        "insufficient_evidence",
        "acceptance_criteria_failed",
        "eligible_for_human_policy_review",
    }:
        raise DiagnosticPolicyReviewError("review status invalid")
    if report.get("quality_policy_version_under_review") != QUALITY_POLICY_VERSION:
        raise DiagnosticPolicyReviewError("review quality policy version mismatch")
    sources = report.get("sources")
    if not isinstance(sources, list) or not sources:
        raise DiagnosticPolicyReviewError("review sources must be non-empty")
    metrics = report.get("by_diagnostic_type")
    if not isinstance(metrics, Mapping) or set(metrics) != set(DIAGNOSTIC_TYPES):
        raise DiagnosticPolicyReviewError("review diagnostic type set mismatch")
    decision = report.get("policy_decision")
    if not isinstance(decision, Mapping) or any(
        decision.get(field) is not False
        for field in ("automatic_policy_change_allowed", "quality_policy_change_applied")
    ) or decision.get("next_quality_policy_version") is not None or decision.get(
        "human_review_required"
    ) is not True:
        raise DiagnosticPolicyReviewError("review cannot apply a quality policy change")
    safety = report.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in (
            "A_to_E_thresholds_generated",
            "grades_generated",
            "quality_gate_modified",
            "maturity_promoted",
            "self_declared_protocol_identity_is_trusted",
        )
    ):
        raise DiagnosticPolicyReviewError("review contains unsafe claims")
