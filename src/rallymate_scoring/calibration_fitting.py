from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter
from typing import Any, Iterable

import numpy as np

from rallymate_scoring.calibration import GRADES
from rallymate_scoring.scoring_truth_calibration_authorization import (
    DIAGNOSTIC_STATUS,
    SYNTHETIC_STATUS,
    ScoringTruthCalibrationAuthorizationError,
    VerifiedScoringTruthCalibrationAuthorization,
    build_nonproduction_truth_authorization_marker,
    require_verified_scoring_truth_calibration_authorization,
    validate_scoring_truth_calibration_authorization_binding,
)


PREPARED_DATASET_SCHEMA_VERSION = "1.0.0"
FIT_PROTOCOL_SCHEMA_VERSION = "1.1.0"
CALIBRATION_CANDIDATE_SCHEMA_VERSION = "1.0.0"
CANONICALIZATION = "rallymate-canonical-json-v1"
_SHA256_LENGTH = 64
_REAL_DATASET_SCOPE = "calibration_input"
_TEST_DATASET_SCOPE = "synthetic_test_only_calibration_input"
_DIAGNOSTIC_DATASET_SCOPE = "unverified_truth_diagnostic_input"
_REAL_PROTOCOL_SCOPE = "calibration_fit_protocol"
_TEST_PROTOCOL_SCOPE = "synthetic_test_only_fit_protocol"


class CalibrationFitError(ValueError):
    """Raised before an asset is written when a fitting gate is not satisfied."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _legacy_nonproduction_truth_marker(
    payload: dict[str, Any], *, status: str
) -> dict[str, Any]:
    """Normalize an old explicit non-production fixture without granting authority."""

    digest = canonical_sha256(payload)
    names = (
        "intake_manifest",
        "truth_manifest",
        "truth_validation_report",
        "manual_events",
        "manual_semantics",
        "coach_labels",
    )
    return build_nonproduction_truth_authorization_marker(
        status=status,
        input_files={
            name: canonical_sha256(
                {"legacy_nonproduction_dataset_sha256": digest, "slot": name}
            ).upper()
            for name in names
        },
    )


def independent_test_seal_sha256(samples: list[dict[str, Any]]) -> str:
    """Hash complete test sample objects using the public seal canonicalization.

    The payload is the complete (unprojected) sample object array sorted by the
    unique ``sample_id``. Object keys are sorted, UTF-8 is used, whitespace is
    omitted, non-ASCII text is preserved, and NaN/Infinity are forbidden.
    """

    if not isinstance(samples, list):
        raise CalibrationFitError("independent test samples must be an array")
    sample_ids: set[str] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise CalibrationFitError(
                f"independent test samples[{index}] must be an object"
            )
        sample_id = _require_string(sample, "sample_id", f"samples[{index}]")
        if sample_id in sample_ids:
            raise CalibrationFitError(f"duplicate independent-test sample_id: {sample_id}")
        sample_ids.add(sample_id)
    try:
        return canonical_sha256(sorted(samples, key=lambda item: item["sample_id"]))
    except (TypeError, ValueError) as exc:
        raise CalibrationFitError(
            "independent test samples must be finite canonical JSON"
        ) from exc


def _require_string(payload: dict[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CalibrationFitError(f"{context}.{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdefABCDEF" for char in value)
    ):
        raise CalibrationFitError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationFitError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationFitError(f"{field} must be a finite number")
    return result


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CalibrationFitError(f"{field} must be a non-negative integer")
    return value


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise CalibrationFitError(f"{field} must be a non-empty string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise CalibrationFitError(f"{field} values must be non-empty strings")
    if len(value) != len(set(value)):
        raise CalibrationFitError(f"{field} must not contain duplicates")
    return list(value)


def _validate_feature_maps(
    payload: dict[str, Any], feature_order: list[str], *, allow_null: bool = False
) -> None:
    for field in ("unit_by_feature", "feature_version_by_feature"):
        mapping = payload.get(field)
        if not isinstance(mapping, dict) or set(mapping) != set(feature_order):
            raise CalibrationFitError(f"{field} must exactly cover feature_order")
        for value in mapping.values():
            if allow_null and value is None:
                continue
            if not isinstance(value, str) or not value:
                raise CalibrationFitError(
                    f"{field} values must be non-empty strings"
                    + (" or null before readiness" if allow_null else "")
                )


def _validate_label_resolution(record: dict[str, Any], policy: str, index: int) -> None:
    label_ids = _string_list(
        record.get("label_source_ids"), f"records[{index}].label_source_ids"
    )
    if len(label_ids) < 2:
        raise CalibrationFitError(
            f"records[{index}].label_source_ids requires at least two source labels"
        )
    if policy == "unanimous_multi_coach_only_v1":
        annotators = _string_list(
            record.get("annotator_ids"), f"records[{index}].annotator_ids"
        )
        if len(annotators) < 2:
            raise CalibrationFitError(
                f"records[{index}] requires at least two distinct annotators"
            )
        if record.get("consensus_status") != "unanimous":
            raise CalibrationFitError(
                f"records[{index}] is not an explicit unanimous coach label"
            )
        if record.get("adjudication") is not None:
            raise CalibrationFitError(
                f"records[{index}] cannot mix unanimous and adjudicated semantics"
            )
        return
    if policy != "external_adjudicated_v1":
        raise CalibrationFitError("unsupported label_resolution_policy")
    adjudication = record.get("adjudication")
    if not isinstance(adjudication, dict):
        raise CalibrationFitError(f"records[{index}].adjudication is required")
    for field in ("adjudicator_id", "decision_id", "decided_at"):
        _require_string(adjudication, field, f"records[{index}].adjudication")
    adjudicated_sources = _string_list(
        adjudication.get("source_label_ids"),
        f"records[{index}].adjudication.source_label_ids",
    )
    if len(adjudicated_sources) < 2 or not set(adjudicated_sources).issubset(label_ids):
        raise CalibrationFitError(
            f"records[{index}] adjudication must reference at least two source labels"
        )


def validate_prepared_dataset(
    payload: dict[str, Any], *, require_fit_ready: bool = False
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CalibrationFitError("prepared dataset must be an object")
    if payload.get("schema_version") != PREPARED_DATASET_SCHEMA_VERSION:
        raise CalibrationFitError("unsupported prepared dataset schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {
        _REAL_DATASET_SCOPE,
        _TEST_DATASET_SCOPE,
        _DIAGNOSTIC_DATASET_SCOPE,
    }:
        raise CalibrationFitError("prepared dataset artifact_scope is invalid")
    dataset_id = _require_string(payload, "dataset_id", "prepared_dataset")
    dataset_version = _require_string(payload, "dataset_version", "prepared_dataset")
    indicator_id = _require_string(payload, "indicator_id", "prepared_dataset")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise CalibrationFitError("prepared_dataset.source is required")
    expected_kind = {
        _REAL_DATASET_SCOPE: "human_coach_ground_truth",
        _TEST_DATASET_SCOPE: "synthetic_test_fixture",
        _DIAGNOSTIC_DATASET_SCOPE: "unverified_private_truth",
    }[scope]
    if source.get("kind") != expected_kind:
        raise CalibrationFitError(
            f"prepared dataset scope requires source.kind={expected_kind}"
        )
    _require_string(source, "manifest_id", "prepared_dataset.source")
    _require_string(source, "prepared_at", "prepared_dataset.source")
    _require_sha256(source.get("source_sha256"), "prepared_dataset.source.source_sha256")

    truth_authorization = payload.get("truth_authorization")
    legacy_unverified_truth = truth_authorization is None
    if legacy_unverified_truth:
        truth_authorization = _legacy_nonproduction_truth_marker(
            payload,
            status=(
                SYNTHETIC_STATUS
                if scope == _TEST_DATASET_SCOPE
                else DIAGNOSTIC_STATUS
            ),
        )
    try:
        validate_scoring_truth_calibration_authorization_binding(
            truth_authorization,
            allow_nonproduction=(
                scope != _REAL_DATASET_SCOPE or legacy_unverified_truth
            ),
        )
    except ScoringTruthCalibrationAuthorizationError as exc:
        raise CalibrationFitError(
            f"invalid prepared-dataset truth authorization lineage: {exc}"
        ) from exc
    expected_authorization_status = {
        _REAL_DATASET_SCOPE: "verified_authorized_intake_for_calibration",
        _TEST_DATASET_SCOPE: SYNTHETIC_STATUS,
        _DIAGNOSTIC_DATASET_SCOPE: DIAGNOSTIC_STATUS,
    }[scope]
    if legacy_unverified_truth and scope == _REAL_DATASET_SCOPE:
        expected_authorization_status = DIAGNOSTIC_STATUS
    if truth_authorization.get("status") != expected_authorization_status:
        raise CalibrationFitError(
            "prepared dataset scope/truth-authorization status mismatch"
        )

    feature_order = _string_list(payload.get("feature_order"), "feature_order")
    _validate_feature_maps(payload, feature_order, allow_null=True)
    if payload.get("label_scale") != list(GRADES):
        raise CalibrationFitError("label_scale must be exactly [E,D,C,B,A]")
    policy = payload.get("label_resolution_policy")
    if policy not in {"unanimous_multi_coach_only_v1", "external_adjudicated_v1"}:
        raise CalibrationFitError("unsupported label_resolution_policy")

    split_policy = payload.get("split_policy")
    if not isinstance(split_policy, dict) or split_policy.get("strategy") != "group_holdout":
        raise CalibrationFitError("split_policy.strategy must be group_holdout")
    _require_string(split_policy, "group_key", "split_policy")
    groups_by_split: dict[str, list[str]] = {}
    for split in ("train", "validation", "independent_test"):
        groups_by_split[split] = _string_list(
            split_policy.get(f"{split}_groups"),
            f"split_policy.{split}_groups",
            allow_empty=True,
        )
    for first, second in itertools.combinations(groups_by_split, 2):
        overlap = set(groups_by_split[first]) & set(groups_by_split[second])
        if overlap:
            raise CalibrationFitError(
                f"split groups overlap between {first} and {second}: {sorted(overlap)}"
            )

    agreement = payload.get("agreement")
    if not isinstance(agreement, dict):
        raise CalibrationFitError("agreement is required")
    if agreement.get("status") not in {"evaluated", "not_evaluated"}:
        raise CalibrationFitError("agreement.status is invalid")
    annotator_count = _non_negative_integer(
        agreement.get("annotator_count"), "agreement.annotator_count"
    )
    shared_item_count = _non_negative_integer(
        agreement.get("shared_item_count"), "agreement.shared_item_count"
    )
    agreement_value = agreement.get("value")
    if agreement_value is not None:
        agreement_value = _finite_number(agreement_value, "agreement.value")
        if agreement_value < -1.0 or agreement_value > 1.0:
            raise CalibrationFitError("agreement.value must be between -1 and 1")

    records = payload.get("records")
    if not isinstance(records, list):
        raise CalibrationFitError("records must be an array")
    record_ids: set[str] = set()
    components: dict[str, str] = {}
    grade_counts: dict[str, Counter[str]] = {
        "train": Counter(),
        "validation": Counter(),
    }
    record_counts = Counter()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CalibrationFitError(f"records[{index}] must be an object")
        record_id = _require_string(record, "record_id", f"records[{index}]")
        if record_id in record_ids:
            raise CalibrationFitError(f"duplicate record_id: {record_id}")
        record_ids.add(record_id)
        for field in ("video_id", "event_id", "group_id"):
            _require_string(record, field, f"records[{index}]")
        split = record.get("split")
        if split not in {"train", "validation"}:
            raise CalibrationFitError(
                "prepared records may contain only train/validation; independent test must remain sealed"
            )
        if record["group_id"] not in groups_by_split[split]:
            raise CalibrationFitError(
                f"records[{index}].group_id is not assigned to its split"
            )
        component = record.get("leakage_component_id")
        if component is not None:
            if not isinstance(component, str) or not component:
                raise CalibrationFitError(
                    f"records[{index}].leakage_component_id must be a non-empty string"
                )
            previous = components.setdefault(component, split)
            if previous != split:
                raise CalibrationFitError(
                    f"leakage component {component} crosses {previous}/{split}"
                )
        features = record.get("features")
        if not isinstance(features, dict) or set(features) != set(feature_order):
            raise CalibrationFitError(
                f"records[{index}].features must exactly cover feature_order"
            )
        for name in feature_order:
            _finite_number(features[name], f"records[{index}].features.{name}")
        grade = record.get("grade")
        if grade not in GRADES:
            raise CalibrationFitError(f"records[{index}].grade must be A, B, C, D or E")
        _validate_label_resolution(record, policy, index)
        record_counts[split] += 1
        grade_counts[split][grade] += 1

    seal = payload.get("independent_test_seal")
    if not isinstance(seal, dict):
        raise CalibrationFitError("independent_test_seal is required")
    if seal.get("indicator_id") != indicator_id:
        raise CalibrationFitError(
            "independent_test_seal.indicator_id must match prepared dataset"
        )
    _require_string(seal, "seal_id", "independent_test_seal")
    seal_count = _non_negative_integer(
        seal.get("record_count"), "independent_test_seal.record_count"
    )
    seal_groups = _string_list(
        seal.get("groups"),
        "independent_test_seal.groups",
        allow_empty=True,
    )
    if set(seal_groups) != set(groups_by_split["independent_test"]):
        raise CalibrationFitError(
            "independent_test_seal.groups must match split_policy.independent_test_groups"
        )
    seal_sample_ids = seal.get("sample_ids")
    if seal_sample_ids is not None:
        seal_sample_ids = _string_list(
            seal_sample_ids,
            "independent_test_seal.sample_ids",
            allow_empty=True,
        )
        if len(seal_sample_ids) != seal_count:
            raise CalibrationFitError(
                "independent_test_seal.sample_ids must match record_count"
            )
    _require_sha256(
        seal.get("content_sha256"), "independent_test_seal.content_sha256"
    )
    if seal.get("canonicalization") != CANONICALIZATION:
        raise CalibrationFitError(
            f"independent_test_seal.canonicalization must be {CANONICALIZATION}"
        )
    if seal.get("labels_withheld") is not True:
        raise CalibrationFitError("independent test labels must be withheld")

    readiness = payload.get("readiness")
    if not isinstance(readiness, dict):
        raise CalibrationFitError("prepared_dataset.readiness is required")
    if readiness.get("status") not in {
        "insufficient",
        "prepared_for_external_protocol_review",
    }:
        raise CalibrationFitError("prepared_dataset.readiness.status is invalid")
    blockers = readiness.get("blockers")
    if (
        not isinstance(blockers, list)
        or any(not isinstance(item, str) or not item for item in blockers)
        or len(blockers) != len(set(blockers))
    ):
        raise CalibrationFitError(
            "prepared_dataset.readiness.blockers must be a unique string array"
        )
    if readiness.get("F3_claimed") is not False:
        raise CalibrationFitError("prepared dataset cannot claim F3")

    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise CalibrationFitError("prepared_dataset.integrity is required")
    if integrity.get("record_count") != len(records):
        raise CalibrationFitError("prepared dataset integrity.record_count mismatch")
    if integrity.get("split_counts") != dict(sorted(record_counts.items())):
        raise CalibrationFitError("prepared dataset integrity.split_counts mismatch")
    expected_grade_counts = {
        split: dict(sorted(counts.items())) for split, counts in grade_counts.items()
    }
    if integrity.get("grade_counts_by_split") != expected_grade_counts:
        raise CalibrationFitError(
            "prepared dataset integrity.grade_counts_by_split mismatch"
        )
    if integrity.get("source_dataset_sha256", "").lower() != source[
        "source_sha256"
    ].lower():
        raise CalibrationFitError("prepared dataset integrity source hash mismatch")
    if integrity.get("canonicalization") != CANONICALIZATION:
        raise CalibrationFitError("prepared dataset integrity canonicalization is invalid")

    if require_fit_ready:
        if scope == _DIAGNOSTIC_DATASET_SCOPE:
            raise CalibrationFitError(
                "verified authorized truth intake is required before fitting"
            )
        if readiness["status"] != "prepared_for_external_protocol_review" or blockers:
            raise CalibrationFitError(
                "prepared dataset upstream readiness blockers must be resolved before fitting"
            )
        if legacy_unverified_truth and scope == _REAL_DATASET_SCOPE:
            raise CalibrationFitError(
                "verified authorized truth intake is required before fitting"
            )
        _validate_feature_maps(payload, feature_order, allow_null=False)
        if any(not groups for groups in groups_by_split.values()):
            raise CalibrationFitError(
                "train, validation and independent-test group assignments are required"
            )
        if not records:
            raise CalibrationFitError("prepared dataset has no train/validation records")
        if record_counts["train"] == 0 or record_counts["validation"] == 0:
            raise CalibrationFitError("both train and validation records are required")
        if seal_count == 0:
            raise CalibrationFitError("a non-empty sealed independent test is required")
        if annotator_count < 2 or shared_item_count < 1:
            raise CalibrationFitError("at least two annotators with shared labels are required")
        if agreement.get("status") != "evaluated" or agreement_value is None:
            raise CalibrationFitError("evaluated multi-annotator agreement is required")
        if set(grade_counts["train"]) != set(GRADES):
            raise CalibrationFitError(
                "train split must contain all five grades to identify four ordered cutpoints"
            )
    return {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "indicator_id": indicator_id,
        "scope": (
            _DIAGNOSTIC_DATASET_SCOPE
            if legacy_unverified_truth and scope == _REAL_DATASET_SCOPE
            else scope
        ),
        "truth_authorization": truth_authorization,
        "legacy_unverified_truth": legacy_unverified_truth,
        "record_counts": dict(record_counts),
        "grade_counts": {
            split: dict(counts) for split, counts in grade_counts.items()
        },
        "annotator_count": annotator_count,
        "shared_item_count": shared_item_count,
        "agreement_value": agreement_value,
        "independent_test_count": seal_count,
    }


def validate_fit_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CalibrationFitError("fit protocol must be an object")
    if payload.get("schema_version") != FIT_PROTOCOL_SCHEMA_VERSION:
        raise CalibrationFitError("unsupported fit protocol schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {_REAL_PROTOCOL_SCOPE, _TEST_PROTOCOL_SCOPE}:
        raise CalibrationFitError("fit protocol artifact_scope is invalid")
    indicator_id = _require_string(payload, "indicator_id", "fit_protocol")
    protocol_id = _require_string(payload, "protocol_id", "fit_protocol")
    protocol_version = _require_string(payload, "protocol_version", "fit_protocol")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise CalibrationFitError("fit_protocol.source is required")
    expected_kind = (
        "preregistered_calibration_protocol"
        if scope == _REAL_PROTOCOL_SCOPE
        else "synthetic_test_fixture"
    )
    if source.get("kind") != expected_kind:
        raise CalibrationFitError(f"fit protocol scope requires source.kind={expected_kind}")
    _require_sha256(source.get("source_sha256"), "fit_protocol.source.source_sha256")
    _require_string(source, "registered_at", "fit_protocol.source")
    policy = payload.get("label_resolution_policy")
    if policy not in {"unanimous_multi_coach_only_v1", "external_adjudicated_v1"}:
        raise CalibrationFitError("unsupported fit protocol label_resolution_policy")
    if payload.get("label_scale") != list(GRADES):
        raise CalibrationFitError("fit protocol label_scale must be [E,D,C,B,A]")

    requirements = payload.get("requirements")
    if not isinstance(requirements, dict):
        raise CalibrationFitError("fit_protocol.requirements is required")
    for split in ("train", "validation", "independent_test"):
        _non_negative_integer(
            requirements.get("min_records_by_split", {}).get(split),
            f"requirements.min_records_by_split.{split}",
        )
    per_grade = requirements.get("min_records_per_grade_by_split")
    if not isinstance(per_grade, dict):
        raise CalibrationFitError(
            "requirements.min_records_per_grade_by_split is required"
        )
    for split in ("train", "validation"):
        grade_map = per_grade.get(split)
        if not isinstance(grade_map, dict) or set(grade_map) != set(GRADES):
            raise CalibrationFitError(
                f"requirements.min_records_per_grade_by_split.{split} must cover E-A"
            )
        for grade in GRADES:
            _non_negative_integer(
                grade_map[grade],
                f"requirements.min_records_per_grade_by_split.{split}.{grade}",
            )
    min_annotators = _non_negative_integer(
        requirements.get("min_annotators"), "requirements.min_annotators"
    )
    if min_annotators < 2:
        raise CalibrationFitError("fit protocol min_annotators cannot be below 2")
    _non_negative_integer(
        requirements.get("min_shared_items"), "requirements.min_shared_items"
    )
    if requirements.get("agreement_metric") != "quadratic_weighted_kappa":
        raise CalibrationFitError(
            "requirements.agreement_metric must be quadratic_weighted_kappa"
        )
    min_agreement = _finite_number(
        requirements.get("min_agreement_value"),
        "requirements.min_agreement_value",
    )
    if min_agreement < -1.0 or min_agreement > 1.0:
        raise CalibrationFitError("min_agreement_value must be between -1 and 1")

    backends = payload.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise CalibrationFitError("fit_protocol.backends is required")
    enabled: list[str] = []
    threshold = backends.get("threshold_rule")
    if isinstance(threshold, dict) and threshold.get("enabled") is True:
        _require_string(threshold, "primary_feature", "backends.threshold_rule")
        if threshold.get("direction") not in {"higher_is_better", "lower_is_better"}:
            raise CalibrationFitError("threshold_rule.direction is invalid")
        if threshold.get("objective") != "mean_absolute_grade_error":
            raise CalibrationFitError(
                "threshold_rule.objective must be mean_absolute_grade_error"
            )
        enabled.append("threshold_rule")
    ordinal = backends.get("ordinal_regression")
    if isinstance(ordinal, dict) and ordinal.get("enabled") is True:
        optimizer = ordinal.get("optimizer")
        if not isinstance(optimizer, dict):
            raise CalibrationFitError("ordinal_regression.optimizer is required")
        if optimizer.get("algorithm") != "projected_gradient_descent_v1":
            raise CalibrationFitError("unsupported ordinal optimizer algorithm")
        max_iterations = _non_negative_integer(
            optimizer.get("max_iterations"), "ordinal.optimizer.max_iterations"
        )
        if max_iterations < 1:
            raise CalibrationFitError("ordinal max_iterations must be positive")
        for field in ("learning_rate", "l2", "tolerance", "min_cutpoint_gap"):
            value = _finite_number(optimizer.get(field), f"ordinal.optimizer.{field}")
            if value < 0 or (field in {"learning_rate", "min_cutpoint_gap"} and value == 0):
                raise CalibrationFitError(f"ordinal.optimizer.{field} is invalid")
        enabled.append("ordinal_regression")
    if not enabled:
        raise CalibrationFitError("fit protocol must enable at least one backend")
    return {
        "scope": scope,
        "indicator_id": indicator_id,
        "protocol_id": protocol_id,
        "protocol_version": protocol_version,
        "enabled_backends": enabled,
    }


def _apply_protocol_gates(
    dataset: dict[str, Any], dataset_audit: dict[str, Any], protocol: dict[str, Any]
) -> None:
    if dataset["label_resolution_policy"] != protocol["label_resolution_policy"]:
        raise CalibrationFitError("dataset/protocol label_resolution_policy mismatch")
    requirements = protocol["requirements"]
    actual_counts = {
        **dataset_audit["record_counts"],
        "independent_test": dataset_audit["independent_test_count"],
    }
    for split, minimum in requirements["min_records_by_split"].items():
        if actual_counts.get(split, 0) < minimum:
            raise CalibrationFitError(
                f"protocol gate failed: {split} records {actual_counts.get(split, 0)} < {minimum}"
            )
    for split, grade_requirements in requirements[
        "min_records_per_grade_by_split"
    ].items():
        actual = dataset_audit["grade_counts"].get(split, {})
        for grade, minimum in grade_requirements.items():
            if actual.get(grade, 0) < minimum:
                raise CalibrationFitError(
                    f"protocol gate failed: {split}/{grade} records {actual.get(grade, 0)} < {minimum}"
                )
    if dataset_audit["annotator_count"] < requirements["min_annotators"]:
        raise CalibrationFitError("protocol gate failed: annotator count")
    if dataset_audit["shared_item_count"] < requirements["min_shared_items"]:
        raise CalibrationFitError("protocol gate failed: shared item count")
    if dataset_audit["agreement_value"] < requirements["min_agreement_value"]:
        raise CalibrationFitError("protocol gate failed: annotator agreement")


def _records_matrix(
    dataset: dict[str, Any], split: str
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    records = [record for record in dataset["records"] if record["split"] == split]
    order = dataset["feature_order"]
    x = np.asarray([[record["features"][name] for name in order] for record in records])
    y = np.asarray([GRADES.index(record["grade"]) for record in records], dtype=np.int64)
    return x.astype(np.float64), y, records


def _metrics(expected: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    errors = predicted.astype(np.float64) - expected.astype(np.float64)
    confusion = {
        expected_grade: {
            predicted_grade: int(
                np.sum(
                    (expected == expected_index) & (predicted == predicted_index)
                )
            )
            for predicted_index, predicted_grade in enumerate(GRADES)
        }
        for expected_index, expected_grade in enumerate(GRADES)
    }
    return {
        "record_count": int(expected.size),
        "accuracy": round(float(np.mean(errors == 0)), 10),
        "mean_absolute_grade_error": round(float(np.mean(np.abs(errors))), 10),
        "bias_grade_steps": round(float(np.mean(errors)), 10),
        "confusion_matrix": confusion,
    }


def _threshold_predictions(
    values: np.ndarray, thresholds: Iterable[float], direction: str
) -> np.ndarray:
    result = np.searchsorted(np.asarray(list(thresholds)), values, side="right")
    return result if direction == "higher_is_better" else 4 - result


def _fit_thresholds(
    values: np.ndarray, labels: np.ndarray, direction: str
) -> list[float]:
    unique = np.unique(values)
    if unique.size < 5:
        raise CalibrationFitError(
            "threshold fitting requires at least five distinct primary-feature values"
        )
    ordered_labels = labels if direction == "higher_is_better" else 4 - labels
    grouped = [ordered_labels[values == value] for value in unique]
    segment_cost = np.zeros((5, unique.size, unique.size + 1), dtype=np.float64)
    for grade_index in range(5):
        costs = np.asarray(
            [np.sum(np.abs(items - grade_index)) for items in grouped],
            dtype=np.float64,
        )
        cumulative = np.concatenate(([0.0], np.cumsum(costs)))
        for start in range(unique.size):
            segment_cost[grade_index, start, start + 1 :] = (
                cumulative[start + 1 :] - cumulative[start]
            )
    infinity = float("inf")
    dp = np.full((6, unique.size + 1), infinity)
    previous = np.full((6, unique.size + 1), -1, dtype=np.int64)
    dp[0, 0] = 0.0
    for segments in range(1, 6):
        for end in range(segments, unique.size + 1):
            candidates = [
                (dp[segments - 1, start] + segment_cost[segments - 1, start, end], start)
                for start in range(segments - 1, end)
            ]
            cost, start = min(candidates, key=lambda item: (item[0], item[1]))
            dp[segments, end] = cost
            previous[segments, end] = start
    cuts: list[int] = []
    end = unique.size
    for segments in range(5, 0, -1):
        start = int(previous[segments, end])
        if segments > 1:
            cuts.append(start)
        end = start
    cuts.reverse()
    return [float((unique[index - 1] + unique[index]) / 2.0) for index in cuts]


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return np.where(
        value >= 0,
        1.0 / (1.0 + np.exp(-value)),
        np.exp(value) / (1.0 + np.exp(value)),
    )


def _ordinal_probabilities(
    x: np.ndarray, coefficients: np.ndarray, intercept: float, cutpoints: np.ndarray
) -> np.ndarray:
    eta = intercept + x @ coefficients
    cumulative = _sigmoid(cutpoints[None, :] - eta[:, None])
    probabilities = np.column_stack(
        (
            cumulative[:, 0],
            cumulative[:, 1] - cumulative[:, 0],
            cumulative[:, 2] - cumulative[:, 1],
            cumulative[:, 3] - cumulative[:, 2],
            1.0 - cumulative[:, 3],
        )
    )
    return np.clip(probabilities, 1e-12, 1.0)


def _ordinal_loss_and_gradient(
    x: np.ndarray,
    y: np.ndarray,
    coefficients: np.ndarray,
    intercept: float,
    cutpoints: np.ndarray,
    l2: float,
) -> tuple[float, np.ndarray, float, np.ndarray]:
    eta = intercept + x @ coefficients
    cumulative = _sigmoid(cutpoints[None, :] - eta[:, None])
    derivatives = cumulative * (1.0 - cumulative)
    probabilities = _ordinal_probabilities(x, coefficients, intercept, cutpoints)
    selected = probabilities[np.arange(y.size), y]
    loss = float(-np.mean(np.log(selected)) + 0.5 * l2 * np.sum(coefficients**2))
    grad_eta = np.zeros(y.size, dtype=np.float64)
    grad_cut = np.zeros((y.size, 4), dtype=np.float64)
    for row, grade in enumerate(y):
        probability = selected[row]
        if grade == 0:
            grad_eta[row] = derivatives[row, 0] / probability
            grad_cut[row, 0] = -derivatives[row, 0] / probability
        elif grade == 4:
            grad_eta[row] = -derivatives[row, 3] / probability
            grad_cut[row, 3] = derivatives[row, 3] / probability
        else:
            lower = grade - 1
            upper = grade
            grad_eta[row] = (
                derivatives[row, upper] - derivatives[row, lower]
            ) / probability
            grad_cut[row, upper] = -derivatives[row, upper] / probability
            grad_cut[row, lower] = derivatives[row, lower] / probability
    grad_coefficients = x.T @ grad_eta / y.size + l2 * coefficients
    grad_intercept = float(np.mean(grad_eta))
    grad_cutpoints = np.mean(grad_cut, axis=0)
    return loss, grad_coefficients, grad_intercept, grad_cutpoints


def _project_cutpoints(cutpoints: np.ndarray, minimum_gap: float) -> np.ndarray:
    result = np.sort(cutpoints.copy())
    for index in range(1, result.size):
        result[index] = max(result[index], result[index - 1] + minimum_gap)
    result -= float(np.mean(result))
    return result


def _fit_ordinal(
    x: np.ndarray, y: np.ndarray, optimizer: dict[str, Any]
) -> tuple[np.ndarray, float, np.ndarray, dict[str, Any]]:
    mean = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale = np.where(scale < 1e-12, 1.0, scale)
    standardized = (x - mean) / scale
    coefficients = np.zeros(x.shape[1], dtype=np.float64)
    intercept = 0.0
    cutpoints = np.asarray([-1.5, -0.5, 0.5, 1.5], dtype=np.float64)
    learning_rate = float(optimizer["learning_rate"])
    l2 = float(optimizer["l2"])
    tolerance = float(optimizer["tolerance"])
    minimum_gap = float(optimizer["min_cutpoint_gap"])
    previous_loss = float("inf")
    converged = False
    iterations = 0
    for iterations in range(1, int(optimizer["max_iterations"]) + 1):
        loss, grad_beta, grad_intercept, grad_cutpoints = _ordinal_loss_and_gradient(
            standardized, y, coefficients, intercept, cutpoints, l2
        )
        if not math.isfinite(loss):
            raise CalibrationFitError("ordinal optimizer produced a non-finite loss")
        coefficients -= learning_rate * grad_beta
        intercept -= learning_rate * grad_intercept
        cutpoints -= learning_rate * grad_cutpoints
        cutpoints = _project_cutpoints(cutpoints, minimum_gap)
        if abs(previous_loss - loss) <= tolerance:
            converged = True
            break
        previous_loss = loss
    raw_coefficients = coefficients / scale
    raw_intercept = float(intercept - np.dot(coefficients, mean / scale))
    final_loss, _, _, _ = _ordinal_loss_and_gradient(
        standardized, y, coefficients, intercept, cutpoints, l2
    )
    return raw_coefficients, raw_intercept, cutpoints, {
        "iterations": iterations,
        "converged": converged,
        "training_negative_log_likelihood": round(final_loss, 10),
    }


def _candidate_base(
    dataset: dict[str, Any], protocol: dict[str, Any], backend: str
) -> dict[str, Any]:
    test_only = dataset["artifact_scope"] == _TEST_DATASET_SCOPE
    prefix = protocol.get("candidate_version_prefix", "calibration-candidate")
    digest = canonical_sha256(
        {
            "dataset": canonical_sha256(dataset),
            "protocol": canonical_sha256(protocol),
            "backend": backend,
        }
    )[:16]
    candidate_version = f"{prefix}-{dataset['indicator_id']}-{backend}-{digest}"
    seal = dataset["independent_test_seal"]
    return {
        "schema_version": CALIBRATION_CANDIDATE_SCHEMA_VERSION,
        "artifact_scope": (
            "synthetic_test_only_calibration_candidate"
            if test_only
            else "calibration_candidate"
        ),
        "candidate_id": candidate_version,
        "candidate_version": candidate_version,
        "backend": backend,
        "indicator_id": dataset["indicator_id"],
        "source": "coach_ground_truth_calibration_candidate",
        "truth_authorization": dict(dataset["truth_authorization"]),
        "target_semantics": {
            "label_scale_order": list(GRADES),
            "grade_numeric_mapping": {grade: index for index, grade in enumerate(GRADES)},
            "label_resolution_policy": dataset["label_resolution_policy"],
        },
        "prepared_dataset": {
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["dataset_version"],
            "artifact_scope": dataset["artifact_scope"],
            "label_resolution_policy": dataset["label_resolution_policy"],
            "content_sha256": canonical_sha256(dataset),
            "source_sha256": dataset["source"]["source_sha256"].lower(),
            "truth_authorization_binding_sha256": dataset["truth_authorization"][
                "binding_sha256"
            ].lower(),
        },
        "fit_protocol": {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "artifact_scope": protocol["artifact_scope"],
            "label_resolution_policy": protocol["label_resolution_policy"],
            "content_sha256": canonical_sha256(protocol),
            "source_sha256": protocol["source"]["source_sha256"].lower(),
        },
        "independent_test": {
            "status": "sealed_not_evaluated",
            "approved_for_scoring": False,
            "accessed_during_fit": False,
            "seal_id": seal["seal_id"],
            "record_count": seal["record_count"],
            "groups": seal["groups"],
            "content_sha256": seal["content_sha256"].lower(),
            "canonicalization": seal["canonicalization"],
            **(
                {"sample_ids": list(seal["sample_ids"])}
                if seal.get("sample_ids") is not None
                else {}
            ),
        },
        "promotion": {
            "scoring_allowed": False,
            "F3_promoted": False,
            "F4_promoted": False,
            "reason": "independent_test_and_explicit_promotion_required",
        },
    }


def fit_calibration_candidates(
    dataset: dict[str, Any],
    protocol: dict[str, Any],
    *,
    verified_truth_authorization: VerifiedScoringTruthCalibrationAuthorization
    | None = None,
) -> list[dict[str, Any]]:
    if (
        isinstance(dataset, dict)
        and dataset.get("artifact_scope") == _TEST_DATASET_SCOPE
        and dataset.get("truth_authorization") is None
    ):
        legacy_dataset = dict(dataset)
        legacy_dataset["truth_authorization"] = _legacy_nonproduction_truth_marker(
            dataset, status=SYNTHETIC_STATUS
        )
        dataset = legacy_dataset
    dataset_audit = validate_prepared_dataset(dataset, require_fit_ready=True)
    protocol_audit = validate_fit_protocol(protocol)
    real_pair = (
        dataset_audit["scope"] == _REAL_DATASET_SCOPE
        and protocol_audit["scope"] == _REAL_PROTOCOL_SCOPE
    )
    test_pair = (
        dataset_audit["scope"] == _TEST_DATASET_SCOPE
        and protocol_audit["scope"] == _TEST_PROTOCOL_SCOPE
    )
    if not (real_pair or test_pair):
        raise CalibrationFitError("dataset/protocol real-vs-test scope mismatch")
    if real_pair:
        try:
            live_binding = require_verified_scoring_truth_calibration_authorization(
                verified_truth_authorization
            )
        except ScoringTruthCalibrationAuthorizationError as exc:
            raise CalibrationFitError(str(exc)) from exc
        if live_binding != dataset_audit["truth_authorization"]:
            raise CalibrationFitError(
                "prepared dataset truth authorization differs from live verification"
            )
    elif verified_truth_authorization is not None:
        raise CalibrationFitError(
            "synthetic calibration cannot accept production truth authority"
        )
    if dataset_audit["indicator_id"] != protocol_audit["indicator_id"]:
        raise CalibrationFitError(
            "dataset/protocol indicator_id mismatch: "
            f"{dataset_audit['indicator_id']} != {protocol_audit['indicator_id']}"
        )
    _apply_protocol_gates(dataset, dataset_audit, protocol)
    x_train, y_train, _ = _records_matrix(dataset, "train")
    x_validation, y_validation, _ = _records_matrix(dataset, "validation")
    candidates: list[dict[str, Any]] = []
    for backend in protocol_audit["enabled_backends"]:
        candidate = _candidate_base(dataset, protocol, backend)
        if backend == "threshold_rule":
            configuration = protocol["backends"][backend]
            feature = configuration["primary_feature"]
            if feature not in dataset["feature_order"]:
                raise CalibrationFitError(
                    f"threshold primary_feature {feature} is absent from dataset"
                )
            feature_index = dataset["feature_order"].index(feature)
            direction = configuration["direction"]
            thresholds = _fit_thresholds(x_train[:, feature_index], y_train, direction)
            train_predictions = _threshold_predictions(
                x_train[:, feature_index], thresholds, direction
            )
            validation_predictions = _threshold_predictions(
                x_validation[:, feature_index], thresholds, direction
            )
            candidate.update(
                {
                    "primary_feature": feature,
                    "primary_feature_version": dataset["feature_version_by_feature"][feature],
                    "unit": dataset["unit_by_feature"][feature],
                    "direction": direction,
                    "thresholds": thresholds,
                    "fit": {
                        "method": "ordered_dynamic_programming_v1",
                        "objective": configuration["objective"],
                        "train_metrics": _metrics(y_train, train_predictions),
                        "validation_metrics": _metrics(
                            y_validation, validation_predictions
                        ),
                        "independent_test_metrics": None,
                    },
                }
            )
        else:
            configuration = protocol["backends"][backend]
            coefficients, intercept, cutpoints, optimizer_result = _fit_ordinal(
                x_train, y_train, configuration["optimizer"]
            )
            train_probabilities = _ordinal_probabilities(
                x_train, coefficients, intercept, cutpoints
            )
            validation_probabilities = _ordinal_probabilities(
                x_validation, coefficients, intercept, cutpoints
            )
            candidate.update(
                {
                    "feature_order": list(dataset["feature_order"]),
                    "unit_by_feature": dict(dataset["unit_by_feature"]),
                    "feature_version_by_feature": dict(
                        dataset["feature_version_by_feature"]
                    ),
                    "coefficients": [float(value) for value in coefficients],
                    "intercept": float(intercept),
                    "cutpoints": [float(value) for value in cutpoints],
                    "fit": {
                        "method": configuration["optimizer"]["algorithm"],
                        "objective": "ordinal_cumulative_logit_negative_log_likelihood",
                        "optimizer": optimizer_result,
                        "train_metrics": _metrics(
                            y_train, np.argmax(train_probabilities, axis=1)
                        ),
                        "validation_metrics": _metrics(
                            y_validation,
                            np.argmax(validation_probabilities, axis=1),
                        ),
                        "independent_test_metrics": None,
                    },
                }
            )
        validate_calibration_candidate(candidate)
        candidates.append(candidate)
    return candidates


def validate_calibration_candidate(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise CalibrationFitError("calibration candidate must be an object")
    if payload.get("schema_version") != CALIBRATION_CANDIDATE_SCHEMA_VERSION:
        raise CalibrationFitError("unsupported calibration candidate schema_version")
    if payload.get("artifact_scope") not in {
        "calibration_candidate",
        "synthetic_test_only_calibration_candidate",
    }:
        raise CalibrationFitError("candidate artifact_scope is invalid")
    for field in ("candidate_id", "candidate_version", "indicator_id"):
        _require_string(payload, field, "candidate")
    if payload.get("source") != "coach_ground_truth_calibration_candidate":
        raise CalibrationFitError("candidate source is invalid")
    real_candidate = payload["artifact_scope"] == "calibration_candidate"
    truth_authorization = payload.get("truth_authorization")
    try:
        validate_scoring_truth_calibration_authorization_binding(
            truth_authorization,
            allow_nonproduction=not real_candidate,
        )
    except ScoringTruthCalibrationAuthorizationError as exc:
        raise CalibrationFitError(
            f"invalid candidate truth authorization lineage: {exc}"
        ) from exc
    expected_truth_status = (
        "verified_authorized_intake_for_calibration"
        if real_candidate
        else SYNTHETIC_STATUS
    )
    if truth_authorization.get("status") != expected_truth_status:
        raise CalibrationFitError(
            "candidate scope/truth-authorization status mismatch"
        )
    semantics = payload.get("target_semantics")
    if not isinstance(semantics, dict) or semantics.get("label_scale_order") != list(GRADES):
        raise CalibrationFitError("candidate target semantics are invalid")
    if semantics.get("grade_numeric_mapping") != {
        grade: index for index, grade in enumerate(GRADES)
    }:
        raise CalibrationFitError("candidate grade mapping is invalid")
    resolution_policy = semantics.get("label_resolution_policy")
    if resolution_policy not in {
        "unanimous_multi_coach_only_v1",
        "external_adjudicated_v1",
    }:
        raise CalibrationFitError("candidate label resolution policy is invalid")
    for provenance_name in ("prepared_dataset", "fit_protocol"):
        provenance = payload.get(provenance_name)
        if not isinstance(provenance, dict):
            raise CalibrationFitError(f"candidate.{provenance_name} is required")
        provenance_scope = provenance.get("artifact_scope")
        if provenance_scope in {_REAL_DATASET_SCOPE, _TEST_DATASET_SCOPE}:
            _require_string(
                provenance, "dataset_id", f"candidate.{provenance_name}"
            )
            _require_string(
                provenance, "dataset_version", f"candidate.{provenance_name}"
            )
        elif provenance_scope in {_REAL_PROTOCOL_SCOPE, _TEST_PROTOCOL_SCOPE}:
            _require_string(
                provenance, "protocol_id", f"candidate.{provenance_name}"
            )
            _require_string(
                provenance, "protocol_version", f"candidate.{provenance_name}"
            )
        else:
            raise CalibrationFitError(
                f"candidate.{provenance_name}.artifact_scope is invalid"
            )
        _require_sha256(
            provenance.get("content_sha256"),
            f"candidate.{provenance_name}.content_sha256",
        )
        _require_sha256(
            provenance.get("source_sha256"),
            f"candidate.{provenance_name}.source_sha256",
        )
        if provenance_scope in {_REAL_DATASET_SCOPE, _TEST_DATASET_SCOPE}:
            binding_sha = _require_sha256(
                provenance.get("truth_authorization_binding_sha256"),
                f"candidate.{provenance_name}.truth_authorization_binding_sha256",
            )
            if binding_sha != truth_authorization["binding_sha256"].lower():
                raise CalibrationFitError(
                    "candidate prepared-dataset truth authorization hash mismatch"
                )
        if provenance.get("label_resolution_policy") != resolution_policy:
            raise CalibrationFitError(
                f"candidate.{provenance_name} label resolution policy mismatch"
            )
    expected_dataset_scope = (
        _REAL_DATASET_SCOPE if real_candidate else _TEST_DATASET_SCOPE
    )
    expected_protocol_scope = (
        _REAL_PROTOCOL_SCOPE if real_candidate else _TEST_PROTOCOL_SCOPE
    )
    if payload["prepared_dataset"].get("artifact_scope") != expected_dataset_scope:
        raise CalibrationFitError("candidate/prepared-dataset scope mismatch")
    if payload["fit_protocol"].get("artifact_scope") != expected_protocol_scope:
        raise CalibrationFitError("candidate/fit-protocol scope mismatch")
    independent = payload.get("independent_test")
    if not isinstance(independent, dict):
        raise CalibrationFitError("candidate.independent_test is required")
    if (
        independent.get("status") != "sealed_not_evaluated"
        or independent.get("approved_for_scoring") is not False
        or independent.get("accessed_during_fit") is not False
    ):
        raise CalibrationFitError("candidate cannot claim independent-test approval")
    _require_string(independent, "seal_id", "candidate.independent_test")
    if _non_negative_integer(
        independent.get("record_count"), "candidate.independent_test.record_count"
    ) < 1:
        raise CalibrationFitError("candidate independent test must be non-empty")
    _string_list(independent.get("groups"), "candidate.independent_test.groups")
    if independent.get("sample_ids") is not None:
        sample_ids = _string_list(
            independent["sample_ids"], "candidate.independent_test.sample_ids"
        )
        if len(sample_ids) != independent["record_count"]:
            raise CalibrationFitError(
                "candidate independent-test sample_ids must match record_count"
            )
    _require_sha256(
        independent.get("content_sha256"),
        "candidate.independent_test.content_sha256",
    )
    if independent.get("canonicalization") != CANONICALIZATION:
        raise CalibrationFitError("candidate independent-test canonicalization is invalid")
    fit = payload.get("fit")
    if not isinstance(fit, dict):
        raise CalibrationFitError("candidate.fit is required")
    for field in ("method", "objective"):
        _require_string(fit, field, "candidate.fit")
    for field in ("train_metrics", "validation_metrics"):
        if not isinstance(fit.get(field), dict):
            raise CalibrationFitError(f"candidate.fit.{field} must be an object")
    if "independent_test_metrics" not in fit:
        raise CalibrationFitError(
            "candidate.fit.independent_test_metrics must be explicit null"
        )
    if fit.get("independent_test_metrics") is not None:
        raise CalibrationFitError("candidate must not contain independent-test metrics")
    promotion = payload.get("promotion")
    if not isinstance(promotion, dict) or any(
        promotion.get(field) is not False
        for field in ("scoring_allowed", "F3_promoted", "F4_promoted")
    ):
        raise CalibrationFitError("candidate cannot claim scoring or F3/F4 promotion")
    backend = payload.get("backend")
    if backend == "threshold_rule":
        for field in ("primary_feature", "primary_feature_version", "unit"):
            _require_string(payload, field, "candidate")
        if payload.get("direction") not in {"higher_is_better", "lower_is_better"}:
            raise CalibrationFitError("candidate threshold direction is invalid")
        thresholds = payload.get("thresholds")
        if not isinstance(thresholds, list) or len(thresholds) != 4:
            raise CalibrationFitError("candidate threshold rule requires four cutpoints")
        values = [_finite_number(value, "candidate.thresholds") for value in thresholds]
        if any(right <= left for left, right in zip(values, values[1:])):
            raise CalibrationFitError("candidate thresholds must be strictly increasing")
    elif backend == "ordinal_regression":
        order = _string_list(payload.get("feature_order"), "candidate.feature_order")
        _validate_feature_maps(payload, order)
        coefficients = payload.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != len(order):
            raise CalibrationFitError("candidate coefficients must match feature_order")
        for value in coefficients:
            _finite_number(value, "candidate.coefficients")
        _finite_number(payload.get("intercept"), "candidate.intercept")
        cutpoints = payload.get("cutpoints")
        if not isinstance(cutpoints, list) or len(cutpoints) != 4:
            raise CalibrationFitError("candidate ordinal model requires four cutpoints")
        values = [_finite_number(value, "candidate.cutpoints") for value in cutpoints]
        if any(right <= left for left, right in zip(values, values[1:])):
            raise CalibrationFitError("candidate cutpoints must be strictly increasing")
    else:
        raise CalibrationFitError("candidate backend is invalid")


def predict_candidate(
    candidate: dict[str, Any], features: dict[str, Any]
) -> dict[str, Any]:
    """Predict for calibration evaluation only; never emits a production score status."""

    validate_calibration_candidate(candidate)
    if not isinstance(features, dict):
        raise CalibrationFitError("features must be an object")
    if candidate["backend"] == "threshold_rule":
        name = candidate["primary_feature"]
        if name not in features:
            raise CalibrationFitError(f"candidate requires missing feature {name}")
        prediction = int(
            _threshold_predictions(
                np.asarray([_finite_number(features[name], f"features.{name}")]),
                candidate["thresholds"],
                candidate["direction"],
            )[0]
        )
        return {
            "status": "candidate_prediction_not_scored",
            "grade": GRADES[prediction],
            "grade_index": prediction,
            "probabilities": None,
        }
    order = candidate["feature_order"]
    missing = [name for name in order if name not in features]
    if missing:
        raise CalibrationFitError(f"candidate requires missing features: {missing}")
    vector = np.asarray(
        [[_finite_number(features[name], f"features.{name}") for name in order]],
        dtype=np.float64,
    )
    probabilities = _ordinal_probabilities(
        vector,
        np.asarray(candidate["coefficients"], dtype=np.float64),
        float(candidate["intercept"]),
        np.asarray(candidate["cutpoints"], dtype=np.float64),
    )[0]
    prediction = int(np.argmax(probabilities))
    return {
        "status": "candidate_prediction_not_scored",
        "grade": GRADES[prediction],
        "grade_index": prediction,
        "probabilities": {
            grade: round(float(value), 10)
            for grade, value in zip(GRADES, probabilities)
        },
    }
