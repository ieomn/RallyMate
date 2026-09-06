from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

from rallymate_scoring.calibration_fitting import (
    CANONICALIZATION,
    canonical_sha256,
)
from rallymate_scoring.feasibility import validate_feasibility_registry


MATURITY_EVIDENCE_SCHEMA_VERSION = "1.0.0"
MATURITY_EVIDENCE_BUNDLE_VERSION = "maturity-evidence-bundle-v1"
PRODUCTION_SCOPE = "maturity_evidence"
TEST_ONLY_SCOPE = "synthetic_test_only_maturity_evidence"
MATURITY_TRANSITIONS = (
    ("F0", "F1"),
    ("F1", "F2"),
    ("F2", "F3"),
    ("F3", "F4"),
)
_SHA256_LENGTH = 64


class MaturityEvidenceError(ValueError):
    """Raised when a maturity bundle is incomplete, unbound, or inconsistent."""


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaturityEvidenceError(f"{field} must be an object")
    return value


def _require_array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise MaturityEvidenceError(f"{field} must be an array")
    return value


def _exact_keys(
    payload: Mapping[str, Any],
    required: set[str],
    field: str,
) -> None:
    missing = required - set(payload)
    extra = set(payload) - required
    if missing:
        raise MaturityEvidenceError(
            f"{field} missing required fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise MaturityEvidenceError(
            f"{field} contains unsupported fields: {', '.join(sorted(extra))}"
        )


def _require_string(payload: Mapping[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MaturityEvidenceError(f"{context}.{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise MaturityEvidenceError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MaturityEvidenceError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MaturityEvidenceError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaturityEvidenceError(f"{field} must include a timezone")
    return parsed


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaturityEvidenceError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MaturityEvidenceError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise MaturityEvidenceError(f"{field} must be >= {minimum}")
    return result


def _ratio(value: Any, field: str) -> float:
    result = _number(value, field, minimum=0.0)
    if result > 1.0:
        raise MaturityEvidenceError(f"{field} must be <= 1")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MaturityEvidenceError(f"{field} must be a positive integer")
    return value


def _source_kind(scope: str, production_kind: str) -> str:
    return "synthetic_test_fixture" if scope == TEST_ONLY_SCOPE else production_kind


def _validate_reference(
    value: Any,
    field: str,
    *,
    scope: str,
    production_kind: str,
) -> tuple[dict[str, Any], datetime]:
    reference = _require_object(value, field)
    _exact_keys(
        reference,
        {
            "artifact_id",
            "artifact_version",
            "content_sha256",
            "source_sha256",
            "source_kind",
            "registered_at",
        },
        field,
    )
    _require_string(reference, "artifact_id", field)
    _require_string(reference, "artifact_version", field)
    _require_sha256(reference.get("content_sha256"), f"{field}.content_sha256")
    _require_sha256(reference.get("source_sha256"), f"{field}.source_sha256")
    expected_kind = _source_kind(scope, production_kind)
    if reference.get("source_kind") != expected_kind:
        raise MaturityEvidenceError(
            f"{field}.source_kind must be {expected_kind} for {scope}"
        )
    return reference, _timestamp(reference.get("registered_at"), f"{field}.registered_at")


def _validate_registry_binding(
    value: Any,
    field: str,
    *,
    indicator_id: str,
    expected_level: str,
) -> dict[str, Any]:
    binding = _require_object(value, field)
    _exact_keys(
        binding,
        {"registry_version", "content_sha256", "indicator_id", "indicator_level"},
        field,
    )
    _require_string(binding, "registry_version", field)
    _require_sha256(binding.get("content_sha256"), f"{field}.content_sha256")
    if binding.get("indicator_id") != indicator_id:
        raise MaturityEvidenceError(f"{field}.indicator_id bundle mismatch")
    if binding.get("indicator_level") != expected_level:
        raise MaturityEvidenceError(
            f"{field}.indicator_level must be {expected_level}"
        )
    return binding


def _validate_reviewer(value: Any, field: str) -> dict[str, Any]:
    reviewer = _require_object(value, field)
    _exact_keys(reviewer, {"reviewer_id", "role"}, field)
    _require_string(reviewer, "reviewer_id", field)
    _require_string(reviewer, "role", field)
    return reviewer


def _validate_error_stats(value: Any, field: str) -> dict[str, Any]:
    stats = _require_object(value, field)
    _exact_keys(
        stats,
        {"mae", "p95", "bias", "valid_rate", "eligible_count", "valid_count"},
        field,
    )
    _number(stats.get("mae"), f"{field}.mae", minimum=0.0)
    _number(stats.get("p95"), f"{field}.p95", minimum=0.0)
    _number(stats.get("bias"), f"{field}.bias")
    valid_rate = _ratio(stats.get("valid_rate"), f"{field}.valid_rate")
    eligible_count = _positive_int(stats.get("eligible_count"), f"{field}.eligible_count")
    valid_count = _positive_int(stats.get("valid_count"), f"{field}.valid_count")
    if valid_count > eligible_count:
        raise MaturityEvidenceError(f"{field}.valid_count cannot exceed eligible_count")
    expected_rate = valid_count / eligible_count
    if not math.isclose(valid_rate, expected_rate, rel_tol=0.0, abs_tol=1e-6):
        raise MaturityEvidenceError(
            f"{field}.valid_rate must equal valid_count / eligible_count"
        )
    return stats


def _validate_metric_summary(value: Any, field: str) -> None:
    metrics = _require_array(value, field)
    if not metrics:
        raise MaturityEvidenceError(f"{field} must not be empty")
    names: set[str] = set()
    for index, item in enumerate(metrics):
        context = f"{field}[{index}]"
        metric = _require_object(item, context)
        _exact_keys(metric, {"metric_name", "value", "unit"}, context)
        name = _require_string(metric, "metric_name", context)
        if name in names:
            raise MaturityEvidenceError(f"{field} metric_name values must be unique")
        names.add(name)
        _number(metric.get("value"), f"{context}.value")
        _require_string(metric, "unit", context)


def _validate_event_payload(
    payload: dict[str, Any],
    *,
    scope: str,
    evaluated_at: datetime,
) -> None:
    _exact_keys(payload, {"event_evaluation", "manual_event_truth"}, "F1.payload")
    evaluation = _require_object(payload.get("event_evaluation"), "F1.payload.event_evaluation")
    _exact_keys(
        evaluation,
        {
            "status",
            "event_codes",
            "event_f1",
            "mean_segment_iou",
            "boundary_mae_ms",
            "evaluated_event_count",
        },
        "F1.payload.event_evaluation",
    )
    if evaluation.get("status") != "evaluated":
        raise MaturityEvidenceError("F1 event evaluation status must be evaluated")
    codes = _require_array(evaluation.get("event_codes"), "F1.payload.event_evaluation.event_codes")
    if not codes or any(not isinstance(code, str) or not code.strip() for code in codes):
        raise MaturityEvidenceError("F1 event_codes must be a non-empty string array")
    if len(codes) != len(set(codes)):
        raise MaturityEvidenceError("F1 event_codes must be unique")
    _ratio(evaluation.get("event_f1"), "F1.payload.event_evaluation.event_f1")
    _ratio(
        evaluation.get("mean_segment_iou"),
        "F1.payload.event_evaluation.mean_segment_iou",
    )
    _number(
        evaluation.get("boundary_mae_ms"),
        "F1.payload.event_evaluation.boundary_mae_ms",
        minimum=0.0,
    )
    _positive_int(
        evaluation.get("evaluated_event_count"),
        "F1.payload.event_evaluation.evaluated_event_count",
    )
    _, registered_at = _validate_reference(
        payload.get("manual_event_truth"),
        "F1.payload.manual_event_truth",
        scope=scope,
        production_kind="manual_event_annotations",
    )
    if registered_at > evaluated_at:
        raise MaturityEvidenceError("F1 manual event truth must predate evaluation")


def _validate_feature_metric(value: Any, field: str) -> str:
    metric = _require_object(value, field)
    _exact_keys(
        metric,
        {
            "feature_name",
            "unit",
            "mae",
            "p95",
            "bias",
            "valid_rate",
            "eligible_count",
            "valid_count",
            "by_view",
        },
        field,
    )
    feature_name = _require_string(metric, "feature_name", field)
    _require_string(metric, "unit", field)
    _validate_error_stats(
        {key: metric[key] for key in ("mae", "p95", "bias", "valid_rate", "eligible_count", "valid_count")},
        f"{field}.overall",
    )
    by_view = _require_array(metric.get("by_view"), f"{field}.by_view")
    if not by_view:
        raise MaturityEvidenceError(f"{field}.by_view must not be empty")
    view_names: set[str] = set()
    for index, row in enumerate(by_view):
        context = f"{field}.by_view[{index}]"
        view = _require_object(row, context)
        _exact_keys(
            view,
            {"view_group", "mae", "p95", "bias", "valid_rate", "eligible_count", "valid_count"},
            context,
        )
        view_name = _require_string(view, "view_group", context)
        if view_name in view_names:
            raise MaturityEvidenceError(f"{field}.by_view view_group values must be unique")
        view_names.add(view_name)
        _validate_error_stats(
            {key: view[key] for key in ("mae", "p95", "bias", "valid_rate", "eligible_count", "valid_count")},
            context,
        )
    return feature_name


def _validate_feature_payload(
    payload: dict[str, Any],
    *,
    scope: str,
    evaluated_at: datetime,
) -> None:
    _exact_keys(
        payload,
        {
            "feature_evaluation",
            "error_budget",
            "manual_keypoint_truth",
            "manual_event_truth",
            "grade_gap_assessment",
        },
        "F2.payload",
    )
    evaluation = _require_object(
        payload.get("feature_evaluation"), "F2.payload.feature_evaluation"
    )
    _exact_keys(evaluation, {"status", "features"}, "F2.payload.feature_evaluation")
    if evaluation.get("status") != "evaluated":
        raise MaturityEvidenceError("F2 feature evaluation status must be evaluated")
    features = _require_array(evaluation.get("features"), "F2.payload.feature_evaluation.features")
    if not features:
        raise MaturityEvidenceError("F2 feature evaluation must contain features")
    feature_names = [
        _validate_feature_metric(item, f"F2.payload.feature_evaluation.features[{index}]")
        for index, item in enumerate(features)
    ]
    if len(feature_names) != len(set(feature_names)):
        raise MaturityEvidenceError("F2 feature names must be unique")
    feature_name_set = set(feature_names)

    budget = _require_object(payload.get("error_budget"), "F2.payload.error_budget")
    _exact_keys(budget, {"method", "features"}, "F2.payload.error_budget")
    if budget.get("method") != "one_factor_counterfactual_differences_not_additive_shapley_decomposition":
        raise MaturityEvidenceError("F2 error budget method is unsupported")
    budget_rows = _require_array(budget.get("features"), "F2.payload.error_budget.features")
    budget_names: list[str] = []
    for index, item in enumerate(budget_rows):
        context = f"F2.payload.error_budget.features[{index}]"
        row = _require_object(item, context)
        _exact_keys(
            row,
            {
                "feature_name",
                "pose_error",
                "event_boundary_error",
                "smoothing_error",
                "missing_value_impact",
            },
            context,
        )
        name = _require_string(row, "feature_name", context)
        budget_names.append(name)
        for component in ("pose_error", "event_boundary_error", "smoothing_error"):
            _validate_error_stats(row.get(component), f"{context}.{component}")
        missing = _require_object(
            row.get("missing_value_impact"), f"{context}.missing_value_impact"
        )
        _exact_keys(
            missing,
            {"mean_valid_fraction_loss", "p95_valid_fraction_loss"},
            f"{context}.missing_value_impact",
        )
        _ratio(
            missing.get("mean_valid_fraction_loss"),
            f"{context}.missing_value_impact.mean_valid_fraction_loss",
        )
        _ratio(
            missing.get("p95_valid_fraction_loss"),
            f"{context}.missing_value_impact.p95_valid_fraction_loss",
        )
    if len(budget_names) != len(set(budget_names)) or set(budget_names) != feature_name_set:
        raise MaturityEvidenceError(
            "F2 error budget features must exactly match feature evaluation"
        )

    for name, kind in (
        ("manual_keypoint_truth", "manual_corrected_keypoints"),
        ("manual_event_truth", "manual_event_annotations"),
    ):
        _, registered_at = _validate_reference(
            payload.get(name),
            f"F2.payload.{name}",
            scope=scope,
            production_kind=kind,
        )
        if registered_at > evaluated_at:
            raise MaturityEvidenceError(f"F2 {name} must predate evaluation")

    gap = _require_object(
        payload.get("grade_gap_assessment"), "F2.payload.grade_gap_assessment"
    )
    _exact_keys(
        gap,
        {"status", "method", "feature_decisions", "report", "semantics"},
        "F2.payload.grade_gap_assessment",
    )
    if gap.get("status") != "passed":
        raise MaturityEvidenceError("F2 external grade-gap assessment must be passed")
    if gap.get("method") != "external_preregistered_protocol":
        raise MaturityEvidenceError("F2 grade-gap assessment must use an external protocol")
    if gap.get("semantics") != "feature_error_materially_smaller_than_potential_grade_separation":
        raise MaturityEvidenceError("F2 grade-gap assessment semantics are invalid")
    decisions = _require_array(
        gap.get("feature_decisions"), "F2.payload.grade_gap_assessment.feature_decisions"
    )
    decision_names: list[str] = []
    for index, item in enumerate(decisions):
        context = f"F2.payload.grade_gap_assessment.feature_decisions[{index}]"
        decision = _require_object(item, context)
        _exact_keys(decision, {"feature_name", "status"}, context)
        decision_names.append(_require_string(decision, "feature_name", context))
        if decision.get("status") != "passed":
            raise MaturityEvidenceError(f"{context}.status must be passed")
    if len(decision_names) != len(set(decision_names)) or set(decision_names) != feature_name_set:
        raise MaturityEvidenceError(
            "F2 grade-gap decisions must exactly match evaluated features"
        )
    _, gap_registered_at = _validate_reference(
        gap.get("report"),
        "F2.payload.grade_gap_assessment.report",
        scope=scope,
        production_kind="coach_ground_truth_grade_gap_analysis",
    )
    if gap_registered_at > evaluated_at:
        raise MaturityEvidenceError("F2 grade-gap report must predate evaluation")


def _validate_f3_payload(
    payload: dict[str, Any],
    *,
    scope: str,
    evaluated_at: datetime,
) -> None:
    _exact_keys(
        payload,
        {"grade_separation", "multi_coach_agreement", "internal_validation"},
        "F3.payload",
    )
    separation = _require_object(
        payload.get("grade_separation"), "F3.payload.grade_separation"
    )
    _exact_keys(
        separation,
        {"status", "method", "metric_summary", "report"},
        "F3.payload.grade_separation",
    )
    if separation.get("status") != "passed":
        raise MaturityEvidenceError("F3 grade separation must be passed")
    _require_string(separation, "method", "F3.payload.grade_separation")
    _validate_metric_summary(
        separation.get("metric_summary"), "F3.payload.grade_separation.metric_summary"
    )
    _, registered_at = _validate_reference(
        separation.get("report"),
        "F3.payload.grade_separation.report",
        scope=scope,
        production_kind="evaluation_report",
    )
    if registered_at > evaluated_at:
        raise MaturityEvidenceError("F3 grade separation report must predate evaluation")

    agreement = _require_object(
        payload.get("multi_coach_agreement"), "F3.payload.multi_coach_agreement"
    )
    _exact_keys(
        agreement,
        {"status", "coach_count", "metric_summary", "report", "coach_label_source"},
        "F3.payload.multi_coach_agreement",
    )
    if agreement.get("status") != "evaluated":
        raise MaturityEvidenceError("F3 multi-coach agreement status must be evaluated")
    coach_count = _positive_int(
        agreement.get("coach_count"), "F3.payload.multi_coach_agreement.coach_count"
    )
    if coach_count < 2:
        raise MaturityEvidenceError("F3 multi-coach agreement requires at least two coaches")
    _validate_metric_summary(
        agreement.get("metric_summary"), "F3.payload.multi_coach_agreement.metric_summary"
    )
    for name, kind in (
        ("report", "evaluation_report"),
        ("coach_label_source", "multi_coach_human_labels"),
    ):
        _, registered_at = _validate_reference(
            agreement.get(name),
            f"F3.payload.multi_coach_agreement.{name}",
            scope=scope,
            production_kind=kind,
        )
        if registered_at > evaluated_at:
            raise MaturityEvidenceError(f"F3 multi-coach {name} must predate evaluation")

    validation = _require_object(
        payload.get("internal_validation"), "F3.payload.internal_validation"
    )
    _exact_keys(
        validation,
        {"status", "validation_scheme", "metric_summary", "report"},
        "F3.payload.internal_validation",
    )
    if validation.get("status") != "validated":
        raise MaturityEvidenceError("F3 internal validation status must be validated")
    _require_string(validation, "validation_scheme", "F3.payload.internal_validation")
    _validate_metric_summary(
        validation.get("metric_summary"), "F3.payload.internal_validation.metric_summary"
    )
    _, registered_at = _validate_reference(
        validation.get("report"),
        "F3.payload.internal_validation.report",
        scope=scope,
        production_kind="evaluation_report",
    )
    if registered_at > evaluated_at:
        raise MaturityEvidenceError("F3 internal validation report must predate evaluation")


def _validate_f4_payload(
    payload: dict[str, Any],
    *,
    scope: str,
    evaluated_at: datetime,
    acceptance_protocol: dict[str, Any],
) -> None:
    _exact_keys(payload, {"independent_test"}, "F4.payload")
    test = _require_object(payload.get("independent_test"), "F4.payload.independent_test")
    _exact_keys(
        test,
        {
            "status",
            "approved_for_scoring",
            "dataset",
            "report",
            "protocol",
            "metric_summary",
            "independence_attestation",
        },
        "F4.payload.independent_test",
    )
    if test.get("status") != "passed":
        raise MaturityEvidenceError("F4 independent test status must be passed")
    if test.get("approved_for_scoring") is not True:
        raise MaturityEvidenceError("F4 independent test must approve scoring")
    references = (
        ("dataset", "independent_human_test_set"),
        ("report", "evaluation_report"),
        ("protocol", "external_preregistered_protocol"),
    )
    validated_references: dict[str, dict[str, Any]] = {}
    for name, kind in references:
        reference, registered_at = _validate_reference(
            test.get(name),
            f"F4.payload.independent_test.{name}",
            scope=scope,
            production_kind=kind,
        )
        validated_references[name] = reference
        if registered_at > evaluated_at:
            raise MaturityEvidenceError(f"F4 independent-test {name} must predate evaluation")
    if validated_references["protocol"] != acceptance_protocol:
        raise MaturityEvidenceError(
            "F4 independent-test protocol must equal the transition acceptance protocol"
        )
    _validate_metric_summary(
        test.get("metric_summary"), "F4.payload.independent_test.metric_summary"
    )
    attestation = _require_object(
        test.get("independence_attestation"),
        "F4.payload.independent_test.independence_attestation",
    )
    _exact_keys(
        attestation,
        {"train_test_disjoint", "held_out_until_final_evaluation", "source"},
        "F4.payload.independent_test.independence_attestation",
    )
    if attestation.get("train_test_disjoint") is not True:
        raise MaturityEvidenceError("F4 train/test disjoint attestation is required")
    if attestation.get("held_out_until_final_evaluation") is not True:
        raise MaturityEvidenceError("F4 held-out test attestation is required")
    _, registered_at = _validate_reference(
        attestation.get("source"),
        "F4.payload.independent_test.independence_attestation.source",
        scope=scope,
        production_kind="human_review_record",
    )
    if registered_at > evaluated_at:
        raise MaturityEvidenceError("F4 independence attestation must predate evaluation")


def canonical_transition_sha256(transition: Mapping[str, Any]) -> str:
    """Hash a transition, including its previous hash, but excluding its own hash."""

    if not isinstance(transition, Mapping):
        raise MaturityEvidenceError("transition must be an object")
    return canonical_sha256(
        {key: value for key, value in transition.items() if key != "transition_sha256"}
    )


def _validate_transition(
    value: Any,
    *,
    index: int,
    expected_levels: tuple[str, str],
    expected_previous_sha256: str | None,
    indicator_id: str,
    scope: str,
) -> tuple[dict[str, Any], datetime]:
    field = f"transitions[{index}]"
    transition = _require_object(value, field)
    _exact_keys(
        transition,
        {
            "transition_id",
            "from_level",
            "to_level",
            "previous_transition_sha256",
            "transition_sha256",
            "registry_before",
            "registry_after",
            "evidence",
            "acceptance",
        },
        field,
    )
    _require_string(transition, "transition_id", field)
    from_level, to_level = expected_levels
    if transition.get("from_level") != from_level or transition.get("to_level") != to_level:
        raise MaturityEvidenceError(f"{field} must represent {from_level}->{to_level}")
    previous = transition.get("previous_transition_sha256")
    if expected_previous_sha256 is None:
        if previous is not None:
            raise MaturityEvidenceError("first transition previous_transition_sha256 must be null")
    elif _require_sha256(previous, f"{field}.previous_transition_sha256") != expected_previous_sha256:
        raise MaturityEvidenceError(f"{field} previous transition hash mismatch")

    before = _validate_registry_binding(
        transition.get("registry_before"),
        f"{field}.registry_before",
        indicator_id=indicator_id,
        expected_level=from_level,
    )
    after = _validate_registry_binding(
        transition.get("registry_after"),
        f"{field}.registry_after",
        indicator_id=indicator_id,
        expected_level=to_level,
    )
    if before["content_sha256"].lower() == after["content_sha256"].lower():
        raise MaturityEvidenceError(f"{field} registry hash must change on promotion")
    if before["registry_version"] == after["registry_version"]:
        raise MaturityEvidenceError(f"{field} registry_version must change on promotion")

    evidence = _require_object(transition.get("evidence"), f"{field}.evidence")
    _exact_keys(
        evidence,
        {"evidence_id", "evidence_version", "evaluated_at", "source", "payload", "payload_sha256"},
        f"{field}.evidence",
    )
    _require_string(evidence, "evidence_id", f"{field}.evidence")
    _require_string(evidence, "evidence_version", f"{field}.evidence")
    evaluated_at = _timestamp(evidence.get("evaluated_at"), f"{field}.evidence.evaluated_at")
    _, evidence_registered_at = _validate_reference(
        evidence.get("source"),
        f"{field}.evidence.source",
        scope=scope,
        production_kind="evaluation_report",
    )
    if evidence_registered_at > evaluated_at:
        raise MaturityEvidenceError(f"{field} evidence source must predate evaluation")
    payload = _require_object(evidence.get("payload"), f"{field}.evidence.payload")
    payload_sha = _require_sha256(evidence.get("payload_sha256"), f"{field}.evidence.payload_sha256")
    if canonical_sha256(payload) != payload_sha:
        raise MaturityEvidenceError(f"{field} evidence payload hash mismatch")

    acceptance = _require_object(transition.get("acceptance"), f"{field}.acceptance")
    _exact_keys(
        acceptance,
        {"decision", "protocol", "reviewer", "reviewed_at", "source"},
        f"{field}.acceptance",
    )
    if acceptance.get("decision") != "passed":
        raise MaturityEvidenceError(f"{field} acceptance decision must be passed")
    protocol, protocol_registered_at = _validate_reference(
        acceptance.get("protocol"),
        f"{field}.acceptance.protocol",
        scope=scope,
        production_kind="external_preregistered_protocol",
    )
    if protocol_registered_at > evaluated_at:
        raise MaturityEvidenceError(f"{field} protocol must be registered before evaluation")
    _validate_reviewer(acceptance.get("reviewer"), f"{field}.acceptance.reviewer")
    reviewed_at = _timestamp(acceptance.get("reviewed_at"), f"{field}.acceptance.reviewed_at")
    _, review_registered_at = _validate_reference(
        acceptance.get("source"),
        f"{field}.acceptance.source",
        scope=scope,
        production_kind="human_review_record",
    )
    if reviewed_at < evaluated_at:
        raise MaturityEvidenceError(f"{field} review must not predate evaluation")
    if review_registered_at > reviewed_at:
        raise MaturityEvidenceError(f"{field} review source must predate the review")

    if to_level == "F1":
        _validate_event_payload(payload, scope=scope, evaluated_at=evaluated_at)
    elif to_level == "F2":
        _validate_feature_payload(payload, scope=scope, evaluated_at=evaluated_at)
    elif to_level == "F3":
        _validate_f3_payload(payload, scope=scope, evaluated_at=evaluated_at)
    else:
        _validate_f4_payload(
            payload,
            scope=scope,
            evaluated_at=evaluated_at,
            acceptance_protocol=protocol,
        )

    expected_hash = canonical_transition_sha256(transition)
    actual_hash = _require_sha256(transition.get("transition_sha256"), f"{field}.transition_sha256")
    if actual_hash != expected_hash:
        raise MaturityEvidenceError(f"{field} canonical transition hash mismatch")
    return transition, reviewed_at


def validate_maturity_evidence_bundle(
    payload: dict[str, Any],
    *,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """Validate a complete F0->F4 maturity history without inventing thresholds.

    The validator is deliberately fail-closed: all four sequential transitions,
    all human/external source bindings for production, and every canonical hash
    are mandatory. Numerical acceptance criteria remain in referenced external,
    pre-registered protocols and are never interpreted here.
    """

    bundle = _require_object(payload, "maturity evidence bundle")
    _exact_keys(
        bundle,
        {
            "schema_version",
            "bundle_version",
            "artifact_scope",
            "bundle_id",
            "indicator_id",
            "canonicalization",
            "created_at",
            "source",
            "transitions",
            "final_registry",
        },
        "maturity evidence bundle",
    )
    if bundle.get("schema_version") != MATURITY_EVIDENCE_SCHEMA_VERSION:
        raise MaturityEvidenceError("unsupported maturity evidence schema_version")
    if bundle.get("bundle_version") != MATURITY_EVIDENCE_BUNDLE_VERSION:
        raise MaturityEvidenceError("unsupported maturity evidence bundle_version")
    scope = bundle.get("artifact_scope")
    if scope not in {PRODUCTION_SCOPE, TEST_ONLY_SCOPE}:
        raise MaturityEvidenceError("maturity evidence artifact_scope is invalid")
    if expected_scope is not None and scope != expected_scope:
        raise MaturityEvidenceError("maturity evidence artifact_scope does not match caller")
    _require_string(bundle, "bundle_id", "maturity evidence bundle")
    indicator_id = _require_string(bundle, "indicator_id", "maturity evidence bundle")
    if bundle.get("canonicalization") != CANONICALIZATION:
        raise MaturityEvidenceError(
            f"maturity evidence canonicalization must be {CANONICALIZATION}"
        )
    created_at = _timestamp(bundle.get("created_at"), "maturity evidence bundle.created_at")
    _, bundle_registered_at = _validate_reference(
        bundle.get("source"),
        "maturity evidence bundle.source",
        scope=scope,
        production_kind="human_bundle_record",
    )
    if bundle_registered_at > created_at:
        raise MaturityEvidenceError("bundle source must be registered before bundle creation")

    transitions = _require_array(bundle.get("transitions"), "maturity evidence bundle.transitions")
    if len(transitions) != len(MATURITY_TRANSITIONS):
        raise MaturityEvidenceError("maturity evidence must contain exactly four transitions")
    previous_hash: str | None = None
    previous_after: dict[str, Any] | None = None
    previous_reviewed_at: datetime | None = None
    transition_ids: set[str] = set()
    transition_hashes: list[str] = []
    for index, expected_levels in enumerate(MATURITY_TRANSITIONS):
        transition, reviewed_at = _validate_transition(
            transitions[index],
            index=index,
            expected_levels=expected_levels,
            expected_previous_sha256=previous_hash,
            indicator_id=indicator_id,
            scope=scope,
        )
        transition_id = transition["transition_id"]
        if transition_id in transition_ids:
            raise MaturityEvidenceError("transition_id values must be unique")
        transition_ids.add(transition_id)
        if previous_after is not None and transition["registry_before"] != previous_after:
            raise MaturityEvidenceError(
                f"transitions[{index}] registry_before must equal prior registry_after"
            )
        if previous_reviewed_at is not None and reviewed_at < previous_reviewed_at:
            raise MaturityEvidenceError("transition review timestamps must be sequential")
        previous_after = transition["registry_after"]
        previous_reviewed_at = reviewed_at
        previous_hash = transition["transition_sha256"].lower()
        transition_hashes.append(previous_hash)

    final_registry = _validate_registry_binding(
        bundle.get("final_registry"),
        "maturity evidence bundle.final_registry",
        indicator_id=indicator_id,
        expected_level="F4",
    )
    if final_registry != previous_after:
        raise MaturityEvidenceError("final_registry must equal the F3->F4 registry_after")
    if previous_reviewed_at is not None and created_at < previous_reviewed_at:
        raise MaturityEvidenceError("bundle creation must not predate the final review")
    return {
        "valid": True,
        "schema_version": MATURITY_EVIDENCE_SCHEMA_VERSION,
        "bundle_id": bundle["bundle_id"],
        "bundle_version": bundle["bundle_version"],
        "artifact_scope": scope,
        "indicator_id": indicator_id,
        "content_sha256": canonical_sha256(bundle),
        "transition_sha256s": transition_hashes,
        "final_registry": dict(final_registry),
        "semantics": "external_protocol_evidence_gate_no_numeric_thresholds_embedded",
    }


def validate_maturity_evidence_for_registry(
    bundle: dict[str, Any],
    registry: dict[str, Any],
    *,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """Bind a validated bundle to the exact current feasibility registry bytes."""

    audit = validate_maturity_evidence_bundle(bundle, expected_scope=expected_scope)
    try:
        validate_feasibility_registry(registry)
    except ValueError as exc:
        raise MaturityEvidenceError(f"invalid feasibility registry: {exc}") from exc
    indicator = next(
        (
            item
            for item in registry["indicators"]
            if item.get("indicator_id") == audit["indicator_id"]
        ),
        None,
    )
    if indicator is None:
        raise MaturityEvidenceError("bundle indicator is absent from feasibility registry")
    expected = audit["final_registry"]
    if registry.get("registry_version") != expected["registry_version"]:
        raise MaturityEvidenceError("final registry_version bundle mismatch")
    if canonical_sha256(registry) != expected["content_sha256"].lower():
        raise MaturityEvidenceError("final registry content SHA-256 bundle mismatch")
    if indicator.get("feasibility_level") != "F4":
        raise MaturityEvidenceError("bound feasibility indicator must be F4")
    blockers = indicator.get("current_blockers")
    if not isinstance(blockers, list) or blockers:
        raise MaturityEvidenceError(
            "bound F4 feasibility indicator must have no unresolved blockers"
        )
    return {**audit, "registry_bound": True}


def maturity_evidence_binding(bundle: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable fields a later promotion/ledger should persist."""

    audit = validate_maturity_evidence_bundle(bundle)
    return {
        "bundle_id": audit["bundle_id"],
        "bundle_version": audit["bundle_version"],
        "artifact_scope": audit["artifact_scope"],
        "indicator_id": audit["indicator_id"],
        "content_sha256": audit["content_sha256"],
        "final_transition_sha256": audit["transition_sha256s"][-1],
        "final_registry": audit["final_registry"],
        "canonicalization": CANONICALIZATION,
    }


__all__ = [
    "MATURITY_EVIDENCE_BUNDLE_VERSION",
    "MATURITY_EVIDENCE_SCHEMA_VERSION",
    "MATURITY_TRANSITIONS",
    "MaturityEvidenceError",
    "PRODUCTION_SCOPE",
    "TEST_ONLY_SCOPE",
    "canonical_transition_sha256",
    "maturity_evidence_binding",
    "validate_maturity_evidence_bundle",
    "validate_maturity_evidence_for_registry",
]
