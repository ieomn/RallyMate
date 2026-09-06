from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_scoring.calibration import (
    GRADES,
    _quadratic_weighted_kappa,
    validate_coach_label,
)
from rallymate_scoring.calibration_fitting import (
    CANONICALIZATION,
    CalibrationFitError,
    canonical_json_bytes,
    canonical_sha256,
    independent_test_seal_sha256,
    predict_candidate,
    validate_calibration_candidate,
)
from rallymate_scoring.indicator_requirements import (
    canonical_sha256 as requirements_canonical_sha256,
    validate_indicator_requirements_snapshot,
)
from rallymate_scoring.indicator_feature_qualification import (
    IndicatorFeatureQualificationError,
    derive_feature_qualification,
    validate_qualification_snapshot,
)


INDEPENDENT_TEST_PROTOCOL_SCHEMA_VERSION = "1.0.0"
INDEPENDENT_TEST_REPORT_SCHEMA_VERSION = "1.2.0"


class IndependentTestError(ValueError):
    """Raised when a sealed independent calibration test cannot be trusted."""


def _require_string(payload: dict[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise IndependentTestError(f"{context}.{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise IndependentTestError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndependentTestError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise IndependentTestError(f"{field} must be a finite number")
    return result


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IndependentTestError(f"{field} must be a non-negative integer")
    return value


def _parse_time(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IndependentTestError(f"{field} must be an ISO-8601 timestamp") from exc
    if result.utcoffset() is None:
        raise IndependentTestError(f"{field} must include a timezone")
    return result


def validate_independent_test_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IndependentTestError("independent-test protocol must be an object")
    if payload.get("schema_version") != INDEPENDENT_TEST_PROTOCOL_SCHEMA_VERSION:
        raise IndependentTestError("unsupported independent-test protocol schema_version")
    if payload.get("artifact_scope") not in {
        "independent_test_protocol",
        "synthetic_test_only_independent_test_protocol",
    }:
        raise IndependentTestError("independent-test protocol scope is invalid")
    protocol_id = _require_string(payload, "protocol_id", "protocol")
    protocol_version = _require_string(payload, "protocol_version", "protocol")
    indicator_id = _require_string(payload, "indicator_id", "protocol")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise IndependentTestError("protocol.source is required")
    expected_kind = (
        "synthetic_test_fixture"
        if payload["artifact_scope"].startswith("synthetic_test_only")
        else "preregistered_independent_test_protocol"
    )
    if source.get("kind") != expected_kind:
        raise IndependentTestError(
            f"protocol scope requires source.kind={expected_kind}"
        )
    _require_sha256(source.get("source_sha256"), "protocol.source.source_sha256")
    registered_at = _require_string(source, "registered_at", "protocol.source")
    _parse_time(registered_at, "protocol.source.registered_at")
    if payload.get("label_scale") != list(GRADES):
        raise IndependentTestError("protocol.label_scale must be [E,D,C,B,A]")
    if payload.get("label_resolution_policy") not in {
        "unanimous_multi_coach_only_v1",
        "external_adjudicated_v1",
    }:
        raise IndependentTestError("unsupported protocol label_resolution_policy")
    metrics = payload.get("acceptance_metrics")
    if not isinstance(metrics, dict):
        raise IndependentTestError("protocol.acceptance_metrics is required")
    required_metric_names = {
        "minimum_record_count",
        "minimum_leakage_group_count",
        "minimum_valid_rate",
        "maximum_mean_absolute_grade_error",
        "maximum_absolute_bias_grade_steps",
        "minimum_quadratic_weighted_kappa",
        "required_grade_coverage",
        "required_view_groups",
    }
    missing = required_metric_names - set(metrics)
    if missing:
        raise IndependentTestError(
            f"protocol acceptance_metrics missing fields: {sorted(missing)}"
        )
    _non_negative_integer(metrics["minimum_record_count"], "minimum_record_count")
    _non_negative_integer(
        metrics["minimum_leakage_group_count"], "minimum_leakage_group_count"
    )
    for name in (
        "minimum_valid_rate",
        "maximum_mean_absolute_grade_error",
        "maximum_absolute_bias_grade_steps",
        "minimum_quadratic_weighted_kappa",
    ):
        value = _finite(metrics[name], f"acceptance_metrics.{name}")
        if name == "minimum_valid_rate" and not 0 <= value <= 1:
            raise IndependentTestError("minimum_valid_rate must be in 0..1")
        if name == "minimum_quadratic_weighted_kappa" and not -1 <= value <= 1:
            raise IndependentTestError(
                "minimum_quadratic_weighted_kappa must be in -1..1"
            )
        if name.startswith("maximum_") and value < 0:
            raise IndependentTestError(f"{name} must be non-negative")
    grade_coverage = metrics["required_grade_coverage"]
    if (
        not isinstance(grade_coverage, list)
        or not grade_coverage
        or any(grade not in GRADES for grade in grade_coverage)
        or len(set(grade_coverage)) != len(grade_coverage)
    ):
        raise IndependentTestError(
            "required_grade_coverage must be a non-empty unique A-E subset"
        )
    view_groups = metrics["required_view_groups"]
    if (
        not isinstance(view_groups, list)
        or any(not isinstance(value, str) or not value for value in view_groups)
        or len(view_groups) != len(set(view_groups))
    ):
        raise IndependentTestError("required_view_groups must be a unique string array")
    return {
        "protocol_id": protocol_id,
        "protocol_version": protocol_version,
        "indicator_id": indicator_id,
        "registered_at": registered_at,
        "scope": payload["artifact_scope"],
    }


def _string_array(
    value: Any, field: str, *, allow_empty: bool = True
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "non-empty " if not allow_empty else ""
        raise IndependentTestError(f"{field} must be a {qualifier}string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise IndependentTestError(f"{field} values must be non-empty strings")
    if len(value) != len(set(value)):
        raise IndependentTestError(f"{field} must not contain duplicates")
    return list(value)


def _require_exact_keys(
    payload: Any,
    *,
    required: set[str],
    allowed: set[str] | None,
    context: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IndependentTestError(f"{context} must be an object")
    missing = required - set(payload)
    if missing:
        raise IndependentTestError(f"{context} missing fields: {sorted(missing)}")
    if allowed is not None:
        unexpected = set(payload) - allowed
        if unexpected:
            raise IndependentTestError(
                f"{context} contains unexpected fields: {sorted(unexpected)}"
            )
    return payload


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IndependentTestError(f"{field} must be a positive integer")
    return value


def _validate_feature_vector(sample: dict[str, Any]) -> None:
    vector = sample.get("feature_vector")
    if not isinstance(vector, list) or not vector:
        raise IndependentTestError("sample.feature_vector must be a non-empty array")
    names: set[str] = set()
    all_complete = True
    required_fields = {
        "feature_name",
        "feature_version",
        "value",
        "unit",
        "confidence",
        "valid",
        "reason",
        "source_frames",
    }
    for index, item in enumerate(vector):
        context = f"sample.feature_vector[{index}]"
        item = _require_exact_keys(
            item,
            required=required_fields,
            allowed=required_fields,
            context=context,
        )
        name = _require_string(item, "feature_name", context)
        if name in names:
            raise IndependentTestError(f"duplicate sample feature_name: {name}")
        names.add(name)
        if item.get("feature_version") is not None:
            _require_string(item, "feature_version", context)
        if item.get("unit") is not None:
            _require_string(item, "unit", context)
        if item.get("value") is not None:
            _finite(item["value"], f"{context}.value")
        confidence = _finite(item.get("confidence"), f"{context}.confidence")
        if not 0 <= confidence <= 1:
            raise IndependentTestError(f"{context}.confidence must be in 0..1")
        if not isinstance(item.get("valid"), bool):
            raise IndependentTestError(f"{context}.valid must be boolean")
        _require_string(item, "reason", context)
        frames = item.get("source_frames")
        if (
            not isinstance(frames, list)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in frames
            )
            or len(frames) != len(set(frames))
        ):
            raise IndependentTestError(
                f"{context}.source_frames must be unique non-negative integers"
            )
        item_complete = (
            item["valid"] is True
            and item["value"] is not None
            and isinstance(item["unit"], str)
            and bool(item["unit"])
            and isinstance(item["feature_version"], str)
            and bool(item["feature_version"])
        )
        all_complete = all_complete and item_complete
    if not isinstance(sample.get("feature_vector_complete"), bool):
        raise IndependentTestError("sample.feature_vector_complete must be boolean")
    if sample["feature_vector_complete"] and not all_complete:
        raise IndependentTestError(
            "sample.feature_vector_complete cannot be true with incomplete features"
        )


def _validate_manual_event(sample: dict[str, Any]) -> None:
    fields = {
        "start_ms",
        "end_ms",
        "key_phases_ms",
        "required_phase_keys",
        "missing_required_phase_keys",
        "required_phases_complete",
        "boundary_uncertainty_ms",
        "annotator_id",
        "reviewer_id",
        "adjudication_status",
    }
    event = _require_exact_keys(
        sample.get("manual_event"), required=fields, allowed=fields, context="sample.manual_event"
    )
    start_ms = _non_negative_integer(event.get("start_ms"), "sample.manual_event.start_ms")
    end_ms = _positive_integer(event.get("end_ms"), "sample.manual_event.end_ms")
    if end_ms <= start_ms:
        raise IndependentTestError("sample.manual_event end_ms must be after start_ms")
    phases = event.get("key_phases_ms")
    if not isinstance(phases, dict):
        raise IndependentTestError("sample.manual_event.key_phases_ms must be an object")
    for name, value in phases.items():
        if not isinstance(name, str) or not name:
            raise IndependentTestError("manual event phase names must be non-empty strings")
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not start_ms <= value <= end_ms
        ):
            raise IndependentTestError(
                f"sample.manual_event.key_phases_ms.{name} must be null or inside event"
            )
    required_phases = _string_array(
        event.get("required_phase_keys"), "sample.manual_event.required_phase_keys"
    )
    missing_phases = _string_array(
        event.get("missing_required_phase_keys"),
        "sample.manual_event.missing_required_phase_keys",
    )
    expected_missing = sorted(
        name for name in required_phases if name not in phases or phases[name] is None
    )
    if sorted(missing_phases) != expected_missing:
        raise IndependentTestError(
            "sample.manual_event missing_required_phase_keys does not match key phases"
        )
    if event.get("required_phases_complete") is not (not expected_missing):
        raise IndependentTestError(
            "sample.manual_event.required_phases_complete is inconsistent"
        )
    _non_negative_integer(
        event.get("boundary_uncertainty_ms"),
        "sample.manual_event.boundary_uncertainty_ms",
    )
    _require_string(event, "annotator_id", "sample.manual_event")
    _require_string(event, "reviewer_id", "sample.manual_event")
    if event.get("adjudication_status") != "accepted":
        raise IndependentTestError("sample.manual_event must be accepted")


def _validate_semantics(sample: dict[str, Any]) -> None:
    fields = {
        "requirements_contract_status",
        "required_keys",
        "records",
        "missing_keys",
        "unobservable_keys",
        "complete",
        "fully_observable",
    }
    semantics = _require_exact_keys(
        sample.get("semantics"), required=fields, allowed=fields, context="sample.semantics"
    )
    if semantics.get("requirements_contract_status") not in {"declared", "missing"}:
        raise IndependentTestError(
            "sample.semantics.requirements_contract_status is invalid"
        )
    required_keys = _string_array(
        semantics.get("required_keys"), "sample.semantics.required_keys"
    )
    missing_keys = _string_array(
        semantics.get("missing_keys"), "sample.semantics.missing_keys"
    )
    unobservable_keys = _string_array(
        semantics.get("unobservable_keys"), "sample.semantics.unobservable_keys"
    )
    records = semantics.get("records")
    if not isinstance(records, list):
        raise IndependentTestError("sample.semantics.records must be an array")
    by_key: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        context = f"sample.semantics.records[{index}]"
        required = {
            "annotation_id",
            "video_id",
            "event_id",
            "indicator_id",
            "semantic_key",
            "observable",
            "adjudication_status",
        }
        record = _require_exact_keys(
            record, required=required, allowed=None, context=context
        )
        for field in ("annotation_id", "video_id", "event_id", "indicator_id", "semantic_key"):
            _require_string(record, field, context)
        for field in ("video_id", "event_id", "indicator_id"):
            if record[field] != sample[field]:
                raise IndependentTestError(f"{context}.{field} does not match sample")
        if record["semantic_key"] in by_key:
            raise IndependentTestError(
                f"duplicate semantic_key: {record['semantic_key']}"
            )
        if not isinstance(record.get("observable"), bool):
            raise IndependentTestError(f"{context}.observable must be boolean")
        if record.get("adjudication_status") != "accepted":
            raise IndependentTestError(f"{context} must be accepted")
        if record["observable"]:
            if not isinstance(record.get("value"), dict):
                raise IndependentTestError(f"{context} observable record requires value")
        elif (
            record.get("value") is not None
            or not isinstance(record.get("null_reason"), str)
            or not record["null_reason"]
        ):
            raise IndependentTestError(
                f"{context} unobservable record requires null value and null_reason"
            )
        by_key[record["semantic_key"]] = record
    expected_missing = sorted(set(required_keys) - set(by_key))
    expected_unobservable = sorted(
        name
        for name in required_keys
        if name in by_key and by_key[name]["observable"] is False
    )
    if sorted(missing_keys) != expected_missing:
        raise IndependentTestError("sample.semantics.missing_keys is inconsistent")
    if sorted(unobservable_keys) != expected_unobservable:
        raise IndependentTestError("sample.semantics.unobservable_keys is inconsistent")
    if semantics.get("complete") is not (not expected_missing):
        raise IndependentTestError("sample.semantics.complete is inconsistent")
    if semantics.get("fully_observable") is not (
        not expected_missing and not expected_unobservable
    ):
        raise IndependentTestError("sample.semantics.fully_observable is inconsistent")


def _validate_raw_labels(
    sample: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    labels = _require_exact_keys(
        sample.get("labels"),
        required={"grades", "rankings"},
        allowed={"grades", "rankings"},
        context="sample.labels",
    )
    grades = labels.get("grades")
    rankings = labels.get("rankings")
    if not isinstance(grades, list) or not isinstance(rankings, list):
        raise IndependentTestError("sample label collections must be arrays")
    seen_annotation_ids: set[str] = set()
    grade_annotators: set[str] = set()
    for collection_name, records, expected_type in (
        ("grades", grades, "grade"),
        ("rankings", rankings, "ranking"),
    ):
        for index, record in enumerate(records):
            context = f"sample.labels.{collection_name}[{index}]"
            if not isinstance(record, dict):
                raise IndependentTestError(f"{context} must be an object")
            try:
                validate_coach_label(record)
            except ValueError as exc:
                raise IndependentTestError(f"{context} is invalid: {exc}") from exc
            if record["label_type"] != expected_type:
                raise IndependentTestError(f"{context}.label_type is in the wrong collection")
            for field in ("video_id", "event_id", "indicator_id"):
                if record[field] != sample[field]:
                    raise IndependentTestError(f"{context}.{field} does not match sample")
            annotation_id = record["annotation_id"]
            if annotation_id in seen_annotation_ids:
                raise IndependentTestError(
                    f"duplicate raw coach annotation_id: {annotation_id}"
                )
            seen_annotation_ids.add(annotation_id)
            if expected_type == "grade":
                annotator_id = record["annotator_id"]
                if annotator_id in grade_annotators:
                    raise IndependentTestError(
                        f"coach {annotator_id} supplied multiple grades for one sample"
                    )
                grade_annotators.add(annotator_id)
    return list(grades), list(rankings)


def _validate_label_summary_shape(sample: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "resolution_policy",
        "resolution_semantics",
        "grade_status",
        "resolved_grade",
        "contributing_annotation_ids",
        "contributing_annotator_ids",
        "conflicting_grades",
        "grade_label_count",
        "ranking_label_count",
        "ranking_annotator_count",
    }
    summary = _require_exact_keys(
        sample.get("label_summary"),
        required=fields,
        allowed=fields,
        context="sample.label_summary",
    )
    if summary.get("resolution_policy") not in {
        "unanimous_multi_coach_only_v1",
        "external_adjudicated_v1",
    }:
        raise IndependentTestError("sample.label_summary resolution_policy is invalid")
    _require_string(summary, "resolution_semantics", "sample.label_summary")
    if summary.get("grade_status") not in {
        "missing",
        "single_annotator",
        "unanimous",
        "conflict",
        "adjudicated",
    }:
        raise IndependentTestError("sample.label_summary grade_status is invalid")
    if summary.get("resolved_grade") is not None and summary["resolved_grade"] not in GRADES:
        raise IndependentTestError("sample.label_summary resolved_grade is invalid")
    _string_array(
        summary.get("contributing_annotation_ids"),
        "sample.label_summary.contributing_annotation_ids",
    )
    _string_array(
        summary.get("contributing_annotator_ids"),
        "sample.label_summary.contributing_annotator_ids",
    )
    conflicting = summary.get("conflicting_grades")
    if (
        not isinstance(conflicting, list)
        or any(grade not in GRADES for grade in conflicting)
        or len(conflicting) != len(set(conflicting))
    ):
        raise IndependentTestError(
            "sample.label_summary.conflicting_grades must be a unique A-E array"
        )
    for field in ("grade_label_count", "ranking_label_count", "ranking_annotator_count"):
        _non_negative_integer(summary.get(field), f"sample.label_summary.{field}")
    return summary


def _validate_groups_readiness_lineage(
    sample: dict[str, Any], grades: list[dict[str, Any]], rankings: list[dict[str, Any]]
) -> None:
    _sample_groups(sample)
    readiness_fields = {
        "eligible_for_grade_modeling",
        "eligible_for_grade_evaluation",
        "ranking_labels_available",
        "reason_codes",
    }
    readiness = _require_exact_keys(
        sample.get("readiness"),
        required=readiness_fields,
        allowed=readiness_fields,
        context="sample.readiness",
    )
    for field in (
        "eligible_for_grade_modeling",
        "eligible_for_grade_evaluation",
        "ranking_labels_available",
    ):
        if not isinstance(readiness.get(field), bool):
            raise IndependentTestError(f"sample.readiness.{field} must be boolean")
    _string_array(readiness.get("reason_codes"), "sample.readiness.reason_codes")
    if readiness["ranking_labels_available"] is bool(rankings):
        pass
    else:
        raise IndependentTestError(
            "sample.readiness.ranking_labels_available is inconsistent"
        )
    lineage_fields = {
        "manual_event_sha256",
        "indicator_feature_sha256",
        "semantic_truth_sha256",
        "coach_labels_sha256",
        "registry_indicator_sha256",
        "join_key",
        "join_policy",
    }
    lineage = _require_exact_keys(
        sample.get("lineage"),
        required=lineage_fields,
        allowed=lineage_fields,
        context="sample.lineage",
    )
    for field in (
        "manual_event_sha256",
        "semantic_truth_sha256",
        "coach_labels_sha256",
        "registry_indicator_sha256",
    ):
        _require_sha256(lineage.get(field), f"sample.lineage.{field}")
    if lineage.get("indicator_feature_sha256") is not None:
        _require_sha256(
            lineage["indicator_feature_sha256"],
            "sample.lineage.indicator_feature_sha256",
        )
    if lineage.get("join_policy") != "exact_manual_event_id_only_v1":
        raise IndependentTestError("sample.lineage.join_policy is invalid")
    join_key = _require_exact_keys(
        lineage.get("join_key"),
        required={"video_id", "event_id", "indicator_id"},
        allowed={"video_id", "event_id", "indicator_id"},
        context="sample.lineage.join_key",
    )
    expected_join_key = {
        "video_id": sample["video_id"],
        "event_id": sample["event_id"],
        "indicator_id": sample["indicator_id"],
    }
    if join_key != expected_join_key:
        raise IndependentTestError("sample.lineage.join_key does not match sample")
    expected_labels_hash = canonical_sha256(
        sorted([*grades, *rankings], key=lambda item: item["annotation_id"])
    )
    if lineage["coach_labels_sha256"].lower() != expected_labels_hash:
        raise IndependentTestError(
            "sample.lineage.coach_labels_sha256 does not match raw coach labels"
        )


def validate_independent_test_sample(
    sample: dict[str, Any],
    *,
    expected_indicator_id: str,
    expected_dataset_version: str,
    expected_requirements: dict[str, Any],
    require_verified_qualification: bool = False,
) -> None:
    """Validate the complete sealed sample contract before using its labels/features."""

    required = {
        "schema_version",
        "sample_id",
        "dataset_version",
        "video_id",
        "event_id",
        "event_code",
        "indicator_id",
        "person_track_id",
        "split",
        "feature_vector",
        "feature_vector_complete",
        "manual_event",
        "semantics",
        "labels",
        "label_summary",
        "groups",
        "readiness",
        "lineage",
    }
    schema_version = sample.get("schema_version")
    if schema_version not in {"1.0.0", "1.1.0"}:
        raise IndependentTestError(
            "unsupported independent-test sample schema_version"
        )
    if require_verified_qualification and schema_version != "1.1.0":
        raise IndependentTestError(
            "real independent-test sample requires schema_version 1.1.0"
        )
    if schema_version == "1.1.0":
        required.add("qualification_snapshot")
    sample = _require_exact_keys(
        sample,
        required=required,
        allowed=required | {"adjudication"},
        context="independent-test sample",
    )
    sample_id = _require_string(sample, "sample_id", "sample")
    if (
        not sample_id.startswith("sample-")
        or len(sample_id) != 27
        or any(character not in "0123456789abcdef" for character in sample_id[7:])
    ):
        raise IndependentTestError("sample.sample_id must match sample-[0-9a-f]{20}")
    dataset_version = _require_string(sample, "dataset_version", "sample")
    if dataset_version != expected_dataset_version:
        raise IndependentTestError("sample.dataset_version does not match candidate")
    for field in ("video_id", "event_id", "indicator_id"):
        _require_string(sample, field, "sample")
    if sample["indicator_id"] != expected_indicator_id:
        raise IndependentTestError("sample.indicator_id does not match candidate")
    if sample.get("event_code") not in {"FS01", "FS02", "FS09"}:
        raise IndependentTestError("sample.event_code is invalid")
    indicator_suffix = sample["indicator_id"][6:]
    if (
        len(sample["indicator_id"]) != 8
        or sample["indicator_id"][:4] not in {"FS01", "FS02", "FS09"}
        or sample["indicator_id"][4:6] != "-M"
        or len(indicator_suffix) != 2
        or not indicator_suffix.isdigit()
    ):
        raise IndependentTestError("sample.indicator_id has an invalid format")
    if not sample["indicator_id"].startswith(f"{sample['event_code']}-M"):
        raise IndependentTestError("sample.event_code does not match indicator_id")
    _positive_integer(sample.get("person_track_id"), "sample.person_track_id")
    if sample.get("split") != "independent_test":
        raise IndependentTestError("sample.split must be independent_test")
    expected_sample_id = "sample-" + canonical_sha256(
        {
            "video_id": sample["video_id"],
            "event_id": sample["event_id"],
            "indicator_id": sample["indicator_id"],
            "person_track_id": sample["person_track_id"],
        }
    )[:20]
    if sample_id != expected_sample_id:
        raise IndependentTestError("sample.sample_id does not match sample identity")
    _validate_feature_vector(sample)
    _validate_manual_event(sample)
    _validate_semantics(sample)
    actual_feature_names = [
        item["feature_name"] for item in sample["feature_vector"]
    ]
    if actual_feature_names != expected_requirements["required_feature_names"]:
        raise IndependentTestError(
            "sample.feature_vector does not exactly match authoritative required features"
        )
    if (
        sample["manual_event"]["required_phase_keys"]
        != expected_requirements["required_phase_keys"]
    ):
        raise IndependentTestError(
            "sample.manual_event.required_phase_keys does not match authoritative requirements"
        )
    if (
        sample["semantics"]["required_keys"]
        != expected_requirements["required_semantic_keys"]
    ):
        raise IndependentTestError(
            "sample.semantics.required_keys does not match authoritative requirements"
        )
    grades, rankings = _validate_raw_labels(sample)
    _validate_label_summary_shape(sample)
    _validate_groups_readiness_lineage(sample, grades, rankings)
    if sample["lineage"]["registry_indicator_sha256"].lower() != str(
        expected_requirements["registry_indicator_sha256"]
    ).lower():
        raise IndependentTestError(
            "sample.lineage.registry_indicator_sha256 does not match authoritative requirements"
        )
    feature_qualification_reasons: list[str] = []
    recomputed_feature_complete = sample["feature_vector_complete"]
    if schema_version == "1.1.0":
        try:
            qualification_snapshot = validate_qualification_snapshot(
                sample["qualification_snapshot"],
                indicator_id=sample["indicator_id"],
                require_verified_source=require_verified_qualification,
            )
            recomputed_feature_complete, feature_qualification_reasons = (
                derive_feature_qualification(
                    snapshot=qualification_snapshot,
                    indicator_id=sample["indicator_id"],
                    feature_vector=sample["feature_vector"],
                    require_verified_source=require_verified_qualification,
                )
            )
        except IndicatorFeatureQualificationError as exc:
            raise IndependentTestError(
                f"invalid sample qualification_snapshot: {exc}"
            ) from exc
        snapshot_feature_sha256 = qualification_snapshot[
            "source_feature_canonical_sha256"
        ]
        lineage_feature_sha256 = sample["lineage"]["indicator_feature_sha256"]
        if (
            snapshot_feature_sha256 is None
            and lineage_feature_sha256 is not None
        ) or (
            snapshot_feature_sha256 is not None
            and (
                lineage_feature_sha256 is None
                or snapshot_feature_sha256.lower()
                != str(lineage_feature_sha256).lower()
            )
        ):
            raise IndependentTestError(
                "qualification feature-record SHA does not match sample lineage"
            )
        if (
            require_verified_qualification
            and qualification_snapshot["source_metadata"]["video_id"]
            != sample["video_id"]
        ):
            raise IndependentTestError(
                "qualification source video_id does not match sample"
            )
        if sample["feature_vector_complete"] is not recomputed_feature_complete:
            raise IndependentTestError(
                "sample.feature_vector_complete does not match recomputed qualification"
            )
    resolved_grade, grade_reason = _resolved_grade(
        sample, sample["label_summary"]["resolution_policy"]
    )
    readiness = sample["readiness"]
    manual_event = sample["manual_event"]
    semantics = sample["semantics"]
    expected_eligible = (
        recomputed_feature_complete
        and manual_event["required_phases_complete"]
        and semantics["requirements_contract_status"] == "declared"
        and semantics["complete"]
        and semantics["fully_observable"]
        and resolved_grade is not None
    )
    if (
        readiness["eligible_for_grade_modeling"] is not expected_eligible
        or readiness["eligible_for_grade_evaluation"] is not expected_eligible
    ):
        raise IndependentTestError(
            "sample readiness eligibility does not match feature/event/semantic/raw-label gates"
        )
    required_reasons: set[str] = set()
    if not recomputed_feature_complete:
        required_reasons.add("required_feature_vector_incomplete")
    required_reasons.update(feature_qualification_reasons)
    if not manual_event["required_phases_complete"]:
        required_reasons.add("required_manual_event_phase_missing")
    if semantics["requirements_contract_status"] != "declared":
        required_reasons.add("semantic_requirements_contract_missing")
    if semantics["missing_keys"]:
        required_reasons.add("required_semantic_truth_missing")
    if semantics["unobservable_keys"]:
        required_reasons.add("required_semantic_truth_unobservable")
    if resolved_grade is None:
        required_reasons.add(grade_reason)
    actual_reasons = set(readiness["reason_codes"])
    if not required_reasons.issubset(actual_reasons):
        raise IndependentTestError(
            "sample.readiness.reason_codes omit recomputed blocking reasons"
        )
    if expected_eligible and actual_reasons:
        raise IndependentTestError(
            "eligible sample.readiness.reason_codes must be empty"
        )


def _sample_groups(sample: dict[str, Any]) -> dict[str, str]:
    required = ("player_id", "session_id", "view_group", "leakage_group_id")
    groups = _require_exact_keys(
        sample.get("groups"),
        required=set(required),
        allowed=set(required),
        context="sample.groups",
    )
    for field in required:
        _require_string(groups, field, "sample.groups")
    leakage_group_id = groups["leakage_group_id"]
    if (
        not leakage_group_id.startswith("lg-")
        or len(leakage_group_id) != 19
        or any(
            character not in "0123456789abcdef"
            for character in leakage_group_id[3:]
        )
    ):
        raise IndependentTestError(
            "sample.groups.leakage_group_id must match lg-[0-9a-f]{16}"
        )
    return {field: str(groups[field]) for field in required}


def _resolved_grade(sample: dict[str, Any], policy: str) -> tuple[str | None, str]:
    grades, rankings = _validate_raw_labels(sample)
    summary = _validate_label_summary_shape(sample)
    grade_records = sorted(
        grades, key=lambda item: (item["annotator_id"], item["annotation_id"])
    )
    expected_annotation_ids = [item["annotation_id"] for item in grade_records]
    expected_annotator_ids = sorted({item["annotator_id"] for item in grade_records})
    unique_grades = sorted({item["grade"] for item in grade_records})
    expected_counts = {
        "grade_label_count": len(grade_records),
        "ranking_label_count": len(rankings),
        "ranking_annotator_count": len({item["annotator_id"] for item in rankings}),
    }
    if summary["contributing_annotation_ids"] != expected_annotation_ids:
        raise IndependentTestError(
            "sample.label_summary contributing_annotation_ids do not match raw labels"
        )
    if summary["contributing_annotator_ids"] != expected_annotator_ids:
        raise IndependentTestError(
            "sample.label_summary contributing_annotator_ids do not match unique coaches"
        )
    for field, expected in expected_counts.items():
        if summary[field] != expected:
            raise IndependentTestError(
                f"sample.label_summary.{field} does not match raw labels"
            )
    if summary["resolution_policy"] != policy:
        raise IndependentTestError(
            "sample label_summary resolution_policy does not match candidate"
        )
    if policy == "unanimous_multi_coach_only_v1":
        if not grade_records:
            status, resolved = "missing", None
        elif len(expected_annotator_ids) < 2:
            status, resolved = "single_annotator", None
        elif len(unique_grades) == 1:
            status, resolved = "unanimous", unique_grades[0]
        else:
            status, resolved = "conflict", None
        expected_conflicts = unique_grades if status == "conflict" else []
        if (
            summary["grade_status"] != status
            or summary["resolved_grade"] != resolved
            or summary["conflicting_grades"] != expected_conflicts
        ):
            raise IndependentTestError(
                "sample unanimous label_summary does not match raw coach labels"
            )
        if "adjudication" in sample:
            raise IndependentTestError(
                "unanimous sample cannot contain external adjudication"
            )
        return (resolved, "valid") if resolved is not None else (None, f"grade_status_{status}")
    if policy != "external_adjudicated_v1":
        raise IndependentTestError("unsupported sample label resolution policy")
    if len(grade_records) < 2 or len(expected_annotator_ids) < 2:
        raise IndependentTestError(
            "external adjudication requires raw grades from at least two unique coaches"
        )
    adjudication_fields = {
        "decision_id",
        "adjudicator_id",
        "decided_at",
        "source_label_ids",
        "source_sha256",
        "grade",
    }
    adjudication = _require_exact_keys(
        sample.get("adjudication"),
        required=adjudication_fields,
        allowed=adjudication_fields,
        context="sample.adjudication",
    )
    _require_string(adjudication, "decision_id", "sample.adjudication")
    adjudicator_id = _require_string(
        adjudication, "adjudicator_id", "sample.adjudication"
    )
    if adjudicator_id in expected_annotator_ids:
        raise IndependentTestError(
            "external adjudicator must be distinct from contributing coaches"
        )
    decided_at = _require_string(adjudication, "decided_at", "sample.adjudication")
    decided_timestamp = _parse_time(decided_at, "sample.adjudication.decided_at")
    if decided_timestamp.utcoffset() is None:
        raise IndependentTestError(
            "sample.adjudication.decided_at must include a timezone"
        )
    source_ids = _string_array(
        adjudication.get("source_label_ids"),
        "sample.adjudication.source_label_ids",
        allow_empty=False,
    )
    if sorted(source_ids) != sorted(expected_annotation_ids):
        raise IndependentTestError(
            "sample.adjudication.source_label_ids must exactly bind raw grade labels"
        )
    _require_sha256(
        adjudication.get("source_sha256"), "sample.adjudication.source_sha256"
    )
    expected_source_sha256 = canonical_sha256(
        sorted(grade_records, key=lambda item: item["annotation_id"])
    )
    if adjudication["source_sha256"].lower() != expected_source_sha256:
        raise IndependentTestError(
            "sample.adjudication.source_sha256 does not bind raw grade labels"
        )
    adjudicated_grade = adjudication.get("grade")
    if adjudicated_grade not in GRADES:
        raise IndependentTestError("sample.adjudication.grade must be A-E")
    expected_conflicts = unique_grades if len(unique_grades) > 1 else []
    if (
        summary["grade_status"] != "adjudicated"
        or summary["resolved_grade"] != adjudicated_grade
        or summary["conflicting_grades"] != expected_conflicts
    ):
        raise IndependentTestError(
            "sample adjudicated label_summary does not match raw labels/adjudication"
        )
    return str(adjudicated_grade), "valid"


def _feature_values(
    sample: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, float] | None, str]:
    if sample.get("feature_vector_complete") is not True:
        return None, "feature_vector_incomplete_or_quality_blocked"
    readiness = sample.get("readiness")
    if not isinstance(readiness, dict):
        return None, "sample_readiness_missing"
    if readiness.get("eligible_for_grade_evaluation") is not True:
        return None, "sample_not_eligible_for_grade_evaluation"
    vector = sample.get("feature_vector")
    if not isinstance(vector, list):
        return None, "feature_vector_missing"
    values: dict[str, float] = {}
    units: dict[str, str] = {}
    versions: dict[str, str] = {}
    for item in vector:
        if not isinstance(item, dict) or not isinstance(item.get("feature_name"), str):
            return None, "feature_record_invalid"
        name = item["feature_name"]
        if name in values:
            return None, "duplicate_feature_name"
        if item.get("valid") is not True or item.get("value") is None:
            return None, f"feature_unavailable:{name}"
        try:
            values[name] = _finite(item["value"], f"feature.{name}.value")
        except IndependentTestError:
            return None, f"feature_non_finite:{name}"
        units[name] = str(item.get("unit", ""))
        versions[name] = str(item.get("feature_version", ""))
    if candidate["backend"] == "threshold_rule":
        required = [candidate["primary_feature"]]
        expected_units = {required[0]: candidate["unit"]}
        expected_versions = {required[0]: candidate["primary_feature_version"]}
    else:
        required = list(candidate["feature_order"])
        expected_units = candidate["unit_by_feature"]
        expected_versions = candidate["feature_version_by_feature"]
    for name in required:
        if name not in values:
            return None, f"required_feature_missing:{name}"
        if units[name] != expected_units[name]:
            return None, f"feature_unit_mismatch:{name}"
        if versions[name] != expected_versions[name]:
            return None, f"feature_version_mismatch:{name}"
    return {name: values[name] for name in required}, "valid"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "record_count": 0,
            "accuracy": None,
            "mean_absolute_grade_error": None,
            "bias_grade_steps": None,
            "quadratic_weighted_kappa": None,
            "confusion_matrix": {
                expected: {predicted: 0 for predicted in GRADES} for expected in GRADES
            },
        }
    expected = np.asarray([GRADES.index(row["expected_grade"]) for row in rows])
    predicted = np.asarray([GRADES.index(row["predicted_grade"]) for row in rows])
    error = predicted - expected
    return {
        "record_count": len(rows),
        "accuracy": round(float(np.mean(error == 0)), 10),
        "mean_absolute_grade_error": round(float(np.mean(np.abs(error))), 10),
        "bias_grade_steps": round(float(np.mean(error)), 10),
        "quadratic_weighted_kappa": (
            round(float(value), 10)
            if (value := _quadratic_weighted_kappa(expected.tolist(), predicted.tolist()))
            is not None
            else None
        ),
        "confusion_matrix": {
            expected_grade: {
                predicted_grade: int(
                    np.sum(
                        (expected == expected_index)
                        & (predicted == predicted_index)
                    )
                )
                for predicted_index, predicted_grade in enumerate(GRADES)
            }
            for expected_index, expected_grade in enumerate(GRADES)
        },
    }


def _grouped_metrics(
    rows: list[dict[str, Any]], group_field: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["groups"][group_field]].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _acceptance(
    *,
    metrics: dict[str, Any],
    valid_rate: float,
    rows: list[dict[str, Any]],
    protocol: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    requirements = protocol["acceptance_metrics"]
    observed_grades = sorted({row["expected_grade"] for row in rows})
    observed_views = sorted({row["groups"]["view_group"] for row in rows})
    group_count = len({row["groups"]["leakage_group_id"] for row in rows})
    checks = [
        (
            "minimum_record_count",
            metrics["record_count"] >= requirements["minimum_record_count"],
            metrics["record_count"],
            requirements["minimum_record_count"],
        ),
        (
            "minimum_leakage_group_count",
            group_count >= requirements["minimum_leakage_group_count"],
            group_count,
            requirements["minimum_leakage_group_count"],
        ),
        (
            "minimum_valid_rate",
            valid_rate >= requirements["minimum_valid_rate"],
            valid_rate,
            requirements["minimum_valid_rate"],
        ),
        (
            "maximum_mean_absolute_grade_error",
            metrics["mean_absolute_grade_error"] is not None
            and metrics["mean_absolute_grade_error"]
            <= requirements["maximum_mean_absolute_grade_error"],
            metrics["mean_absolute_grade_error"],
            requirements["maximum_mean_absolute_grade_error"],
        ),
        (
            "maximum_absolute_bias_grade_steps",
            metrics["bias_grade_steps"] is not None
            and abs(metrics["bias_grade_steps"])
            <= requirements["maximum_absolute_bias_grade_steps"],
            abs(metrics["bias_grade_steps"])
            if metrics["bias_grade_steps"] is not None
            else None,
            requirements["maximum_absolute_bias_grade_steps"],
        ),
        (
            "minimum_quadratic_weighted_kappa",
            metrics["quadratic_weighted_kappa"] is not None
            and metrics["quadratic_weighted_kappa"]
            >= requirements["minimum_quadratic_weighted_kappa"],
            metrics["quadratic_weighted_kappa"],
            requirements["minimum_quadratic_weighted_kappa"],
        ),
        (
            "required_grade_coverage",
            set(requirements["required_grade_coverage"]).issubset(observed_grades),
            observed_grades,
            requirements["required_grade_coverage"],
        ),
        (
            "required_view_groups",
            set(requirements["required_view_groups"]).issubset(observed_views),
            observed_views,
            requirements["required_view_groups"],
        ),
    ]
    results = [
        {
            "metric": name,
            "passed": bool(passed),
            "observed": observed,
            "required": required,
            "source": "preregistered_protocol_no_internal_default",
        }
        for name, passed, observed, required in checks
    ]
    return all(item["passed"] for item in results), results


def _validate_report_groups(value: Any, field: str) -> dict[str, str]:
    required = {"player_id", "session_id", "view_group", "leakage_group_id"}
    groups = _require_exact_keys(
        value,
        required=required,
        allowed=required,
        context=field,
    )
    for name in sorted(required):
        _require_string(groups, name, field)
    return {name: str(groups[name]) for name in sorted(required)}


def _report_canonical_bytes(value: Any, field: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise IndependentTestError(
            f"{field} must be finite canonical JSON"
        ) from exc


def _validate_report_prediction(
    value: Any,
    *,
    index: int,
    candidate_backend: str | None,
) -> dict[str, Any]:
    field = f"report.predictions[{index}]"
    required = {
        "sample_id",
        "video_id",
        "event_id",
        "expected_grade",
        "predicted_grade",
        "grade_probabilities",
        "groups",
    }
    row = _require_exact_keys(
        value,
        required=required,
        allowed=required,
        context=field,
    )
    for name in ("sample_id", "video_id", "event_id"):
        _require_string(row, name, field)
    if row.get("expected_grade") not in GRADES:
        raise IndependentTestError(f"{field}.expected_grade must be A-E")
    if row.get("predicted_grade") not in GRADES:
        raise IndependentTestError(f"{field}.predicted_grade must be A-E")
    probabilities = row.get("grade_probabilities")
    if probabilities is None:
        if candidate_backend == "ordinal_regression":
            raise IndependentTestError(
                f"{field}.grade_probabilities are required for ordinal regression"
            )
    else:
        probabilities = _require_exact_keys(
            probabilities,
            required=set(GRADES),
            allowed=set(GRADES),
            context=f"{field}.grade_probabilities",
        )
        parsed: dict[str, float] = {}
        for grade in GRADES:
            probability = _finite(
                probabilities[grade],
                f"{field}.grade_probabilities.{grade}",
            )
            if not 0 <= probability <= 1:
                raise IndependentTestError(
                    f"{field}.grade_probabilities.{grade} must be in 0..1"
                )
            parsed[grade] = probability
        if not math.isclose(sum(parsed.values()), 1.0, abs_tol=1e-8):
            raise IndependentTestError(
                f"{field}.grade_probabilities must sum to one"
            )
        expected_prediction = max(GRADES, key=lambda grade: parsed[grade])
        if row["predicted_grade"] != expected_prediction:
            raise IndependentTestError(
                f"{field}.predicted_grade disagrees with grade_probabilities"
            )
        if candidate_backend == "threshold_rule":
            raise IndependentTestError(
                f"{field}.grade_probabilities must be null for threshold rules"
            )
    _validate_report_groups(row.get("groups"), f"{field}.groups")
    return row


def validate_independent_test_report(
    report: dict[str, Any],
    *,
    protocol: dict[str, Any],
    candidate: dict[str, Any] | None = None,
    require_nonempty_evaluation: bool = False,
) -> dict[str, Any]:
    """Reconcile a full evaluator report with its exact sealed inputs.

    The protocol payload is deliberately required instead of trusting the
    protocol summary embedded in the report.  When a candidate is supplied, the
    report's dataset coverage must partition the candidate's exact sealed sample
    IDs into predictions and explicit exclusions.
    """

    if not isinstance(report, dict):
        raise IndependentTestError("independent-test report must be an object")
    if report.get("schema_version") != INDEPENDENT_TEST_REPORT_SCHEMA_VERSION:
        raise IndependentTestError(
            "full independent-test evidence requires report schema_version "
            f"{INDEPENDENT_TEST_REPORT_SCHEMA_VERSION}"
        )
    protocol_audit = validate_independent_test_protocol(protocol)
    protocol_is_test = protocol_audit["scope"].startswith("synthetic_test_only")
    expected_scope = (
        "synthetic_test_only_independent_test_report"
        if protocol_is_test
        else "independent_test_report"
    )
    if report.get("artifact_scope") != expected_scope:
        raise IndependentTestError("report/protocol real-vs-test scope mismatch")
    _require_string(report, "report_version", "report")
    if report.get("status") not in {"passed", "failed"}:
        raise IndependentTestError("report.status must be passed or failed")
    if report.get("approved_for_scoring") is not False:
        raise IndependentTestError("report must remain unapproved before promotion")
    if report.get("indicator_id") != protocol_audit["indicator_id"]:
        raise IndependentTestError("report/protocol indicator_id mismatch")

    candidate_backend: str | None = None
    seal_sample_ids: list[str] | None = None
    if candidate is not None:
        try:
            validate_calibration_candidate(candidate)
        except CalibrationFitError as exc:
            raise IndependentTestError(
                f"invalid calibration candidate: {exc}"
            ) from exc
        candidate_is_test = candidate["artifact_scope"].startswith(
            "synthetic_test_only"
        )
        if candidate_is_test != protocol_is_test:
            raise IndependentTestError(
                "candidate/protocol real-vs-test scope mismatch"
            )
        if candidate["indicator_id"] != protocol_audit["indicator_id"]:
            raise IndependentTestError("candidate/protocol indicator_id mismatch")
        if (
            candidate["target_semantics"]["label_resolution_policy"]
            != protocol["label_resolution_policy"]
        ):
            raise IndependentTestError(
                "candidate/protocol label_resolution_policy mismatch"
            )
        candidate_backend = str(candidate["backend"])
        report_candidate = _require_exact_keys(
            report.get("candidate"),
            required={
                "candidate_id",
                "candidate_version",
                "backend",
                "content_sha256",
            },
            allowed={
                "candidate_id",
                "candidate_version",
                "backend",
                "content_sha256",
            },
            context="report.candidate",
        )
        expected_candidate = {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": canonical_sha256(candidate),
        }
        if _report_canonical_bytes(
            report_candidate, "report.candidate"
        ) != _report_canonical_bytes(expected_candidate, "expected candidate"):
            raise IndependentTestError("report.candidate lineage mismatch")
        seal = candidate["independent_test"]
        seal_sample_ids = _string_array(
            seal.get("sample_ids"),
            "candidate.independent_test.sample_ids",
            allow_empty=False,
        )
        if len(seal_sample_ids) != seal["record_count"]:
            raise IndependentTestError(
                "candidate independent-test sample_ids must match record_count"
            )

    protocol_summary = _require_exact_keys(
        report.get("protocol"),
        required={
            "protocol_id",
            "protocol_version",
            "content_sha256",
            "source_sha256",
            "registered_at",
        },
        allowed={
            "protocol_id",
            "protocol_version",
            "content_sha256",
            "source_sha256",
            "registered_at",
        },
        context="report.protocol",
    )
    expected_protocol_summary = {
        "protocol_id": protocol_audit["protocol_id"],
        "protocol_version": protocol_audit["protocol_version"],
        "content_sha256": canonical_sha256(protocol),
        "source_sha256": protocol["source"]["source_sha256"].lower(),
        "registered_at": protocol_audit["registered_at"],
    }
    if _report_canonical_bytes(
        protocol_summary, "report.protocol"
    ) != _report_canonical_bytes(
        expected_protocol_summary, "expected protocol summary"
    ):
        raise IndependentTestError(
            "report.protocol does not bind the supplied independent-test protocol"
        )
    evaluated_at = _require_string(report, "evaluated_at", "report")
    evaluated_timestamp = _parse_time(evaluated_at, "report.evaluated_at")
    if evaluated_timestamp < _parse_time(
        protocol_audit["registered_at"], "protocol.source.registered_at"
    ):
        raise IndependentTestError(
            "independent-test protocol must be registered before evaluation"
        )

    dataset_fields = {
        "dataset_id",
        "dataset_version",
        "seal_id",
        "content_sha256",
        "record_count",
        "leakage_groups",
        "canonicalization",
    }
    dataset = _require_exact_keys(
        report.get("dataset"),
        required=dataset_fields,
        allowed=dataset_fields,
        context="report.dataset",
    )
    for name in ("dataset_id", "dataset_version", "seal_id"):
        _require_string(dataset, name, "report.dataset")
    _require_sha256(dataset.get("content_sha256"), "report.dataset.content_sha256")
    record_count = _non_negative_integer(
        dataset.get("record_count"), "report.dataset.record_count"
    )
    leakage_groups = _string_array(
        dataset.get("leakage_groups"),
        "report.dataset.leakage_groups",
        allow_empty=not require_nonempty_evaluation,
    )
    if dataset.get("canonicalization") != CANONICALIZATION:
        raise IndependentTestError("report.dataset canonicalization is unsupported")
    if candidate is not None:
        seal = candidate["independent_test"]
        expected_dataset = {
            "dataset_id": candidate["prepared_dataset"]["dataset_id"],
            "dataset_version": candidate["prepared_dataset"]["dataset_version"],
            "seal_id": seal["seal_id"],
            "content_sha256": seal["content_sha256"].lower(),
            "record_count": seal["record_count"],
            "leakage_groups": list(seal["groups"]),
            "canonicalization": CANONICALIZATION,
        }
        if _report_canonical_bytes(
            dataset, "report.dataset"
        ) != _report_canonical_bytes(expected_dataset, "expected dataset"):
            raise IndependentTestError("report.dataset seal lineage mismatch")

    coverage_fields = {
        "sealed_record_count",
        "evaluation_eligible_record_count",
        "evaluated_record_count",
        "valid_rate",
        "evaluation_eligible_rate",
        "eligible_evaluation_completion_rate",
        "excluded_record_count",
        "exclusion_reason_counts",
    }
    coverage = _require_exact_keys(
        report.get("coverage"),
        required=coverage_fields,
        allowed=coverage_fields,
        context="report.coverage",
    )
    sealed_count = _non_negative_integer(
        coverage.get("sealed_record_count"),
        "report.coverage.sealed_record_count",
    )
    evaluation_eligible_count = _non_negative_integer(
        coverage.get("evaluation_eligible_record_count"),
        "report.coverage.evaluation_eligible_record_count",
    )
    evaluated_count = _non_negative_integer(
        coverage.get("evaluated_record_count"),
        "report.coverage.evaluated_record_count",
    )
    excluded_count = _non_negative_integer(
        coverage.get("excluded_record_count"),
        "report.coverage.excluded_record_count",
    )
    rates = {
        "valid_rate": _finite(coverage.get("valid_rate"), "report.coverage.valid_rate"),
        "evaluation_eligible_rate": _finite(
            coverage.get("evaluation_eligible_rate"),
            "report.coverage.evaluation_eligible_rate",
        ),
        "eligible_evaluation_completion_rate": _finite(
            coverage.get("eligible_evaluation_completion_rate"),
            "report.coverage.eligible_evaluation_completion_rate",
        ),
    }
    if any(not 0 <= value <= 1 for value in rates.values()):
        raise IndependentTestError("report coverage rates must be in 0..1")
    if sealed_count != record_count:
        raise IndependentTestError(
            "report coverage sealed_record_count must equal sealed record_count"
        )
    if evaluated_count + excluded_count != sealed_count:
        raise IndependentTestError(
            "report coverage evaluated/excluded counts do not partition the seal"
        )
    if not evaluated_count <= evaluation_eligible_count <= sealed_count:
        raise IndependentTestError(
            "report coverage evaluated/evaluation-eligible counts are inconsistent"
        )
    expected_rates = {
        "valid_rate": round(evaluated_count / sealed_count, 10)
        if sealed_count
        else 0.0,
        "evaluation_eligible_rate": round(
            evaluation_eligible_count / sealed_count, 10
        )
        if sealed_count
        else 0.0,
        "eligible_evaluation_completion_rate": round(
            evaluated_count / evaluation_eligible_count, 10
        )
        if evaluation_eligible_count
        else 0.0,
    }
    if rates != expected_rates:
        raise IndependentTestError("report coverage rates are inconsistent")
    if require_nonempty_evaluation and (
        sealed_count < 1
        or evaluation_eligible_count < 1
        or evaluated_count < 1
        or evaluated_count != evaluation_eligible_count
        or rates["eligible_evaluation_completion_rate"] != 1.0
    ):
        raise IndependentTestError(
            "production promotion requires complete non-empty evaluation-eligible coverage"
        )

    predictions = report.get("predictions")
    if not isinstance(predictions, list):
        raise IndependentTestError("report.predictions must be an array")
    parsed_predictions = [
        _validate_report_prediction(
            row,
            index=index,
            candidate_backend=candidate_backend,
        )
        for index, row in enumerate(predictions)
    ]
    if len(parsed_predictions) != evaluated_count:
        raise IndependentTestError(
            "report predictions do not match evaluated_record_count"
        )

    exclusions = report.get("exclusions")
    if not isinstance(exclusions, list):
        raise IndependentTestError("report.exclusions must be an array")
    parsed_exclusions: list[dict[str, Any]] = []
    exclusion_fields = {"sample_id", "classification", "reason_codes", "groups"}
    for index, value in enumerate(exclusions):
        field = f"report.exclusions[{index}]"
        item = _require_exact_keys(
            value,
            required=exclusion_fields,
            allowed=exclusion_fields,
            context=field,
        )
        _require_string(item, "sample_id", field)
        if item.get("classification") != "not_evaluation_eligible":
            raise IndependentTestError(
                f"{field}.classification must be not_evaluation_eligible"
            )
        _string_array(
            item.get("reason_codes"), f"{field}.reason_codes", allow_empty=False
        )
        _validate_report_groups(item.get("groups"), f"{field}.groups")
        parsed_exclusions.append(item)
    if len(parsed_exclusions) != excluded_count:
        raise IndependentTestError(
            "report exclusions do not match excluded_record_count"
        )

    prediction_ids = [str(row["sample_id"]) for row in parsed_predictions]
    exclusion_ids = [str(item["sample_id"]) for item in parsed_exclusions]
    all_ids = prediction_ids + exclusion_ids
    if len(all_ids) != len(set(all_ids)):
        raise IndependentTestError(
            "report predictions/exclusions contain duplicate sample_ids"
        )
    if len(all_ids) != sealed_count:
        raise IndependentTestError(
            "report predictions/exclusions do not cover the sealed record count"
        )
    if seal_sample_ids is not None and sorted(all_ids) != sorted(seal_sample_ids):
        raise IndependentTestError(
            "report predictions/exclusions do not exactly cover sealed sample_ids"
        )
    leakage_group_set = set(leakage_groups)
    for row in [*parsed_predictions, *parsed_exclusions]:
        if row["groups"]["leakage_group_id"] not in leakage_group_set:
            raise IndependentTestError(
                "report row leakage_group_id is absent from the sealed dataset"
            )

    reason_counts = coverage.get("exclusion_reason_counts")
    if not isinstance(reason_counts, dict):
        raise IndependentTestError(
            "report.coverage.exclusion_reason_counts must be an object"
        )
    for reason, count in reason_counts.items():
        if not isinstance(reason, str) or not reason:
            raise IndependentTestError(
                "report exclusion reason names must be non-empty strings"
            )
        _non_negative_integer(
            count, f"report.coverage.exclusion_reason_counts.{reason}"
        )
    expected_reason_counts = dict(
        sorted(
            Counter(
                reason_code
                for item in parsed_exclusions
                for reason_code in item["reason_codes"]
            ).items()
        )
    )
    if reason_counts != expected_reason_counts:
        raise IndependentTestError(
            "report exclusion_reason_counts do not reconcile with exclusions"
        )

    expected_metrics = {
        "overall": _metrics(parsed_predictions),
        "by_view_group": _grouped_metrics(parsed_predictions, "view_group"),
        "by_player": _grouped_metrics(parsed_predictions, "player_id"),
        "by_session": _grouped_metrics(parsed_predictions, "session_id"),
    }
    metrics = report.get("metrics")
    if not isinstance(metrics, dict) or _report_canonical_bytes(
        metrics, "report.metrics"
    ) != _report_canonical_bytes(expected_metrics, "expected report metrics"):
        raise IndependentTestError(
            "report metrics do not exactly reconcile with prediction rows"
        )

    acceptance = _require_exact_keys(
        report.get("acceptance"),
        required={"passed", "checks", "semantics"},
        allowed={"passed", "checks", "semantics"},
        context="report.acceptance",
    )
    if not isinstance(acceptance.get("passed"), bool):
        raise IndependentTestError("report.acceptance.passed must be boolean")
    _require_string(acceptance, "semantics", "report.acceptance")
    expected_passed, expected_checks = _acceptance(
        metrics=expected_metrics["overall"],
        valid_rate=rates["valid_rate"],
        rows=parsed_predictions,
        protocol=protocol,
    )
    if acceptance["passed"] is not expected_passed or _report_canonical_bytes(
        acceptance.get("checks"), "report.acceptance.checks"
    ) != _report_canonical_bytes(expected_checks, "expected acceptance checks"):
        raise IndependentTestError(
            "report acceptance does not exactly reconcile with the supplied protocol"
        )
    expected_status = "passed" if expected_passed else "failed"
    if report["status"] != expected_status:
        raise IndependentTestError(
            "report.status disagrees with recomputed protocol acceptance"
        )

    promotion = _require_exact_keys(
        report.get("promotion"),
        required={
            "production_asset_created",
            "registry_F4_promoted",
            "explicit_human_approval_required",
        },
        allowed={
            "production_asset_created",
            "registry_F4_promoted",
            "explicit_human_approval_required",
        },
        context="report.promotion",
    )
    if (
        promotion["production_asset_created"] is not False
        or promotion["registry_F4_promoted"] is not False
        or promotion["explicit_human_approval_required"] is not True
    ):
        raise IndependentTestError("report promotion state is not pre-promotion")
    return {
        "is_test": protocol_is_test,
        "evaluated_at": evaluated_timestamp,
        "protocol_registered_at": _parse_time(
            protocol_audit["registered_at"], "protocol.source.registered_at"
        ),
        "evaluated_record_count": evaluated_count,
        "evaluation_eligible_record_count": evaluation_eligible_count,
        "sealed_record_count": sealed_count,
        "excluded_record_count": excluded_count,
        "acceptance_passed": expected_passed,
    }


def evaluate_independent_test(
    *,
    candidate: dict[str, Any],
    samples: list[dict[str, Any]],
    protocol: dict[str, Any],
    indicator_requirements: dict[str, Any] | None = None,
    evaluated_at: str,
) -> dict[str, Any]:
    try:
        validate_calibration_candidate(candidate)
    except CalibrationFitError as exc:
        raise IndependentTestError(f"invalid calibration candidate: {exc}") from exc
    protocol_audit = validate_independent_test_protocol(protocol)
    if candidate["indicator_id"] != protocol_audit["indicator_id"]:
        raise IndependentTestError("candidate/protocol indicator_id mismatch")
    candidate_is_test = candidate["artifact_scope"].startswith("synthetic_test_only")
    if indicator_requirements is None:
        if not candidate_is_test:
            raise IndependentTestError(
                "production independent test requires an authoritative indicator "
                "requirements snapshot"
            )
        # Backward-compatible convenience for existing synthetic unit fixtures.
        # Production and the CLI never use this path. New adversarial tests pass
        # an explicit test-only snapshot so requirement shrinking is exercised.
        matching = [
            sample
            for sample in samples
            if sample.get("indicator_id") == candidate["indicator_id"]
        ]
        if not matching:
            raise IndependentTestError(
                "synthetic requirements cannot be inferred without a sample"
            )
        fixture = matching[0]
        indicator_requirements = {
            "schema_version": "1.0.0",
            "artifact_scope": (
                "synthetic_test_only_indicator_scoring_requirements"
            ),
            "requirements_version": "legacy-synthetic-fixture-v1",
            "source": {
                "kind": "synthetic_test_fixture",
                "registry_version": "legacy-synthetic-registry-v1",
                "registry_content_sha256": "0" * 64,
                "semantic_contract_version": "legacy-synthetic-semantics-v1",
                "semantic_contract_sha256": "0" * 64,
                "canonicalization": "rallymate-canonical-json-v1",
            },
            "indicators": [
                {
                    "indicator_id": candidate["indicator_id"],
                    "required_feature_names": [
                        item["feature_name"] for item in fixture["feature_vector"]
                    ],
                    "required_phase_keys": list(
                        fixture["manual_event"]["required_phase_keys"]
                    ),
                    "required_semantic_keys": list(
                        fixture["semantics"]["required_keys"]
                    ),
                    "registry_indicator_sha256": fixture["lineage"][
                        "registry_indicator_sha256"
                    ],
                }
            ],
        }
    try:
        requirements_by_indicator = validate_indicator_requirements_snapshot(
            indicator_requirements
        )
    except ValueError as exc:
        raise IndependentTestError(
            f"invalid indicator requirements snapshot: {exc}"
        ) from exc
    if candidate["indicator_id"] not in requirements_by_indicator:
        raise IndependentTestError(
            "indicator requirements snapshot does not contain candidate indicator"
        )
    expected_requirements = requirements_by_indicator[candidate["indicator_id"]]
    protocol_is_test = protocol_audit["scope"].startswith("synthetic_test_only")
    requirements_is_test = str(
        indicator_requirements.get("artifact_scope", "")
    ).startswith("synthetic_test_only")
    if candidate_is_test != protocol_is_test:
        raise IndependentTestError("candidate/protocol real-vs-test scope mismatch")
    if candidate_is_test != requirements_is_test:
        raise IndependentTestError(
            "candidate/indicator-requirements real-vs-test scope mismatch"
        )
    if (
        protocol.get("label_resolution_policy")
        != candidate["target_semantics"]["label_resolution_policy"]
    ):
        raise IndependentTestError(
            "candidate/protocol label_resolution_policy mismatch"
        )
    evaluated_timestamp = _parse_time(evaluated_at, "evaluated_at")
    if evaluated_timestamp < _parse_time(
        protocol_audit["registered_at"], "protocol.source.registered_at"
    ):
        raise IndependentTestError("protocol must be registered before evaluation")
    if not isinstance(samples, list):
        raise IndependentTestError("independent-test samples must be an array")
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise IndependentTestError(
                f"independent-test samples[{index}] must be an object"
            )
    indicator_samples = sorted(
        (
            sample
            for sample in samples
            if sample.get("indicator_id") == candidate["indicator_id"]
            and sample.get("split") == "independent_test"
        ),
        key=lambda sample: str(sample.get("sample_id", "")),
    )
    seal = candidate["independent_test"]
    sample_ids = [str(sample.get("sample_id", "")) for sample in indicator_samples]
    if any(not value for value in sample_ids) or len(sample_ids) != len(set(sample_ids)):
        raise IndependentTestError("independent-test sample_ids must be non-empty/unique")
    groups = sorted(
        {_sample_groups(sample)["leakage_group_id"] for sample in indicator_samples}
    )
    try:
        content_sha256 = independent_test_seal_sha256(indicator_samples)
    except CalibrationFitError as exc:
        raise IndependentTestError(str(exc)) from exc
    if seal.get("canonicalization") != CANONICALIZATION:
        raise IndependentTestError("candidate test seal canonicalization is unsupported")
    if int(seal.get("record_count", -1)) != len(indicator_samples):
        raise IndependentTestError("independent-test seal record_count mismatch")
    if sorted(seal.get("groups", [])) != groups:
        raise IndependentTestError("independent-test seal groups mismatch")
    if seal.get("sample_ids") is not None and sorted(seal["sample_ids"]) != sample_ids:
        raise IndependentTestError("independent-test seal sample_ids mismatch")
    if _require_sha256(
        seal.get("content_sha256"), "candidate.independent_test.content_sha256"
    ) != content_sha256:
        raise IndependentTestError("independent-test seal content hash mismatch")

    for sample in indicator_samples:
        validate_independent_test_sample(
            sample,
            expected_indicator_id=candidate["indicator_id"],
            expected_dataset_version=candidate["prepared_dataset"]["dataset_version"],
            expected_requirements=expected_requirements,
            require_verified_qualification=not candidate_is_test,
        )

    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    evaluation_eligible_count = 0
    for sample in indicator_samples:
        groups_payload = _sample_groups(sample)
        readiness = sample["readiness"]
        if readiness["eligible_for_grade_evaluation"] is not True:
            reason_codes = list(readiness["reason_codes"])
            exclusion_counts.update(reason_codes)
            exclusions.append(
                {
                    "sample_id": sample["sample_id"],
                    "classification": "not_evaluation_eligible",
                    "reason_codes": reason_codes,
                    "groups": groups_payload,
                }
            )
            continue
        evaluation_eligible_count += 1
        expected_grade, grade_reason = _resolved_grade(
            sample, candidate["target_semantics"]["label_resolution_policy"]
        )
        if expected_grade is None:
            raise IndependentTestError(
                "evaluation-eligible sample has no resolved grade: "
                f"{sample['sample_id']} ({grade_reason})"
            )
        features, feature_reason = _feature_values(sample, candidate)
        if features is None:
            raise IndependentTestError(
                "evaluation-eligible sample cannot produce required features: "
                f"{sample['sample_id']} ({feature_reason})"
            )
        try:
            prediction = predict_candidate(candidate, features)
        except (CalibrationFitError, KeyError) as exc:
            raise IndependentTestError(
                "candidate_prediction_error for evaluation-eligible sample: "
                f"{sample['sample_id']} ({type(exc).__name__})"
            )
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "video_id": sample.get("video_id"),
                "event_id": sample.get("event_id"),
                "expected_grade": expected_grade,
                "predicted_grade": prediction["grade"],
                "grade_probabilities": prediction["probabilities"],
                "groups": groups_payload,
            }
        )
    sealed_count = len(indicator_samples)
    valid_rate = round(len(rows) / sealed_count, 10) if sealed_count else 0.0
    evaluation_eligible_rate = (
        round(evaluation_eligible_count / sealed_count, 10) if sealed_count else 0.0
    )
    eligible_evaluation_completion_rate = (
        round(len(rows) / evaluation_eligible_count, 10)
        if evaluation_eligible_count
        else 0.0
    )
    overall = _metrics(rows)
    passed, checks = _acceptance(
        metrics=overall,
        valid_rate=valid_rate,
        rows=rows,
        protocol=protocol,
    )
    protocol_sha256 = hashlib.sha256(canonical_json_bytes(protocol)).hexdigest()
    candidate_sha256 = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
    report = {
        "schema_version": INDEPENDENT_TEST_REPORT_SCHEMA_VERSION,
        "artifact_scope": (
            "synthetic_test_only_independent_test_report"
            if candidate_is_test
            else "independent_test_report"
        ),
        "report_version": (
            f"independent-test-{candidate['candidate_version']}-"
            f"{protocol_audit['protocol_version']}"
        ),
        "status": "passed" if passed else "failed",
        "approved_for_scoring": False,
        "indicator_id": candidate["indicator_id"],
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "backend": candidate["backend"],
            "content_sha256": candidate_sha256,
        },
        "dataset": {
            "dataset_id": candidate["prepared_dataset"]["dataset_id"],
            "dataset_version": candidate["prepared_dataset"]["dataset_version"],
            "seal_id": seal["seal_id"],
            "content_sha256": content_sha256,
            "record_count": len(indicator_samples),
            "leakage_groups": groups,
            "canonicalization": CANONICALIZATION,
        },
        "protocol": {
            "protocol_id": protocol_audit["protocol_id"],
            "protocol_version": protocol_audit["protocol_version"],
            "content_sha256": protocol_sha256,
            "source_sha256": protocol["source"]["source_sha256"].lower(),
            "registered_at": protocol_audit["registered_at"],
        },
        "indicator_requirements": {
            "requirements_version": indicator_requirements["requirements_version"],
            "content_sha256": requirements_canonical_sha256(
                indicator_requirements
            ),
            "registry_version": indicator_requirements["source"][
                "registry_version"
            ],
            "registry_content_sha256": indicator_requirements["source"][
                "registry_content_sha256"
            ].lower(),
            "registry_indicator_sha256": expected_requirements[
                "registry_indicator_sha256"
            ].lower(),
            "semantic_contract_version": indicator_requirements["source"][
                "semantic_contract_version"
            ],
            "semantic_contract_sha256": indicator_requirements["source"][
                "semantic_contract_sha256"
            ].lower(),
        },
        "evaluated_at": evaluated_at,
        "coverage": {
            "sealed_record_count": sealed_count,
            "evaluation_eligible_record_count": evaluation_eligible_count,
            "evaluated_record_count": len(rows),
            "valid_rate": valid_rate,
            "evaluation_eligible_rate": evaluation_eligible_rate,
            "eligible_evaluation_completion_rate": eligible_evaluation_completion_rate,
            "excluded_record_count": sealed_count - len(rows),
            "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        },
        "metrics": {
            "overall": overall,
            "by_view_group": _grouped_metrics(rows, "view_group"),
            "by_player": _grouped_metrics(rows, "player_id"),
            "by_session": _grouped_metrics(rows, "session_id"),
        },
        "acceptance": {
            "passed": passed,
            "checks": checks,
            "semantics": (
                "all numeric limits originate in the versioned preregistered "
                "protocol; this evaluator has no experience-based defaults"
            ),
        },
        "predictions": rows,
        "exclusions": exclusions,
        "promotion": {
            "production_asset_created": False,
            "registry_F4_promoted": False,
            "explicit_human_approval_required": True,
        },
    }
    validate_independent_test_report(
        report,
        protocol=protocol,
        candidate=candidate,
    )
    return report


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IndependentTestError(
                    f"invalid JSONL at line {line_number}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise IndependentTestError(
                    f"independent-test sample line {line_number} must be an object"
                )
            records.append(payload)
    return records
