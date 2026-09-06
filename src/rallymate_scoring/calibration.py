from __future__ import annotations

import itertools
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


GRADES = ("E", "D", "C", "B", "A")
GRADE_VALUE = {grade: index for index, grade in enumerate(GRADES)}
CALIBRATION_SCHEMA_VERSION = "1.2.0"
INDEPENDENT_TEST_STATUSES = {"pending", "passed", "failed", "test_only"}
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_coach_label(record: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "annotation_id",
        "video_id",
        "event_id",
        "indicator_id",
        "annotator_id",
        "label_type",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"coach label missing fields: {sorted(missing)}")
    if record["schema_version"] != "1.0.0":
        raise ValueError("unsupported coach label schema_version")
    for field in required - {"schema_version"}:
        if field == "label_type":
            continue
        if not isinstance(record[field], str) or not record[field]:
            raise ValueError(f"{field} must be a non-empty string")
    if record["label_type"] == "grade":
        if record.get("grade") not in GRADES:
            raise ValueError("grade label must be A, B, C, D or E")
    elif record["label_type"] == "ranking":
        if not isinstance(record.get("rank_group_id"), str) or not record["rank_group_id"]:
            raise ValueError("ranking label requires rank_group_id")
        if not isinstance(record.get("rank"), int) or record["rank"] < 1:
            raise ValueError("ranking label requires positive integer rank")
    else:
        raise ValueError("label_type must be grade or ranking")


def load_coach_labels(path: str | Path) -> list[dict[str, Any]]:
    labels = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid coach label line {line_number}: {exc}") from exc
            validate_coach_label(record)
            labels.append(record)
    return labels


def _quadratic_weighted_kappa(first: list[int], second: list[int]) -> float | None:
    if len(first) != len(second) or len(first) < 2:
        return None
    categories = len(GRADES)
    observed = np.zeros((categories, categories), dtype=np.float64)
    for left, right in zip(first, second):
        observed[left, right] += 1
    observed /= observed.sum()
    first_hist = observed.sum(axis=1)
    second_hist = observed.sum(axis=0)
    expected = np.outer(first_hist, second_hist)
    weights = np.fromfunction(
        lambda i, j: ((i - j) / (categories - 1)) ** 2,
        (categories, categories),
    )
    expected_disagreement = float(np.sum(weights * expected))
    if expected_disagreement <= 1e-12:
        return 1.0 if float(np.sum(weights * observed)) <= 1e-12 else None
    return 1.0 - float(np.sum(weights * observed)) / expected_disagreement


def _kendall_tau(first: dict[str, int], second: dict[str, int]) -> float | None:
    items = sorted(set(first) & set(second))
    if len(items) < 2:
        return None
    concordant = discordant = 0
    for left, right in itertools.combinations(items, 2):
        first_delta = first[left] - first[right]
        second_delta = second[left] - second[right]
        product = first_delta * second_delta
        if product > 0:
            concordant += 1
        elif product < 0:
            discordant += 1
    denominator = concordant + discordant
    return (concordant - discordant) / denominator if denominator else None


def compute_annotator_agreement(labels: list[dict[str, Any]]) -> dict[str, Any]:
    grade_by_annotator: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    ranking_by_group: dict[tuple[str, str], dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for label in labels:
        validate_coach_label(label)
        item = (label["event_id"], label["indicator_id"])
        if label["label_type"] == "grade":
            grade_by_annotator[label["annotator_id"]][item] = GRADE_VALUE[label["grade"]]
        else:
            key = (label["indicator_id"], label["rank_group_id"])
            ranking_by_group[key][label["annotator_id"]][label["event_id"]] = label["rank"]
    grade_pairs = []
    for first_id, second_id in itertools.combinations(sorted(grade_by_annotator), 2):
        shared = sorted(set(grade_by_annotator[first_id]) & set(grade_by_annotator[second_id]))
        kappa = _quadratic_weighted_kappa(
            [grade_by_annotator[first_id][item] for item in shared],
            [grade_by_annotator[second_id][item] for item in shared],
        )
        grade_pairs.append(
            {
                "annotator_a": first_id,
                "annotator_b": second_id,
                "shared_items": len(shared),
                "quadratic_weighted_kappa": (
                    round(float(kappa), 8) if kappa is not None else None
                ),
            }
        )
    ranking_pairs = []
    for (indicator_id, group_id), annotations in sorted(ranking_by_group.items()):
        for first_id, second_id in itertools.combinations(sorted(annotations), 2):
            tau = _kendall_tau(annotations[first_id], annotations[second_id])
            ranking_pairs.append(
                {
                    "indicator_id": indicator_id,
                    "rank_group_id": group_id,
                    "annotator_a": first_id,
                    "annotator_b": second_id,
                    "shared_items": len(
                        set(annotations[first_id]) & set(annotations[second_id])
                    ),
                    "kendall_tau": round(float(tau), 8) if tau is not None else None,
                }
            )
    kappas = [
        item["quadratic_weighted_kappa"]
        for item in grade_pairs
        if item["quadratic_weighted_kappa"] is not None
    ]
    taus = [
        item["kendall_tau"]
        for item in ranking_pairs
        if item["kendall_tau"] is not None
    ]
    if not kappas and not taus:
        status = "insufficient_multi_annotator_overlap"
    else:
        status = "evaluated"
    return {
        "schema_version": "1.0.0",
        "status": status,
        "label_count": len(labels),
        "annotator_count": len({label["annotator_id"] for label in labels}),
        "grade_agreement": {
            "pairwise": grade_pairs,
            "mean_quadratic_weighted_kappa": (
                round(float(np.mean(kappas)), 8) if kappas else None
            ),
        },
        "ranking_agreement": {
            "pairwise": ranking_pairs,
            "mean_kendall_tau": round(float(np.mean(taus)), 8) if taus else None,
        },
    }


def _validate_independent_test_evidence(payload: dict[str, Any]) -> None:
    if payload.get("artifact_scope") not in {"production", "test_only"}:
        raise ValueError("calibration artifact_scope must be production or test_only")
    evidence = payload.get("independent_test")
    if not isinstance(evidence, dict):
        raise ValueError("calibration independent_test evidence is required")
    required = {
        "status",
        "approved_for_scoring",
        "dataset_version",
        "report_version",
        "report_sha256",
        "acceptance_protocol_version",
        "evaluated_at",
    }
    missing = required - set(evidence)
    if missing:
        raise ValueError(
            f"independent_test evidence missing fields: {sorted(missing)}"
        )
    status = evidence["status"]
    if status not in INDEPENDENT_TEST_STATUSES:
        raise ValueError("independent_test status is invalid")
    if not isinstance(evidence["approved_for_scoring"], bool):
        raise ValueError("independent_test approved_for_scoring must be boolean")
    for field in (
        "dataset_version",
        "report_version",
        "acceptance_protocol_version",
        "evaluated_at",
    ):
        value = evidence[field]
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"independent_test {field} must be null or non-empty string")
    report_sha256 = evidence["report_sha256"]
    if report_sha256 is not None and (
        not isinstance(report_sha256, str)
        or not _SHA256_PATTERN.fullmatch(report_sha256)
    ):
        raise ValueError("independent_test report_sha256 must be null or 64 hex characters")
    if payload["artifact_scope"] == "test_only":
        if status != "test_only":
            raise ValueError("test_only calibration requires test_only independent_test status")
        if evidence["approved_for_scoring"]:
            raise ValueError("test_only calibration cannot be approved for production scoring")
        return
    if status == "test_only":
        raise ValueError("production calibration cannot use test_only evidence")
    if status != "passed":
        if evidence["approved_for_scoring"]:
            raise ValueError("unpassed independent_test cannot approve scoring")
        return
    if not evidence["approved_for_scoring"]:
        raise ValueError("passed independent_test must explicitly approve scoring")
    for field in (
        "dataset_version",
        "report_version",
        "acceptance_protocol_version",
        "evaluated_at",
    ):
        if not isinstance(evidence[field], str) or not evidence[field]:
            raise ValueError(f"passed independent_test requires non-empty {field}")
    if evidence["report_sha256"] is None:
        raise ValueError("passed independent_test requires a 64-character report_sha256")


def _require_non_empty_strings(payload: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise ValueError(f"calibration {field} must be a non-empty string")


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def calibration_scoring_readiness(
    payload: dict[str, Any], *, allow_test_only: bool = False
) -> tuple[bool, str]:
    """Return whether a structurally valid calibration may emit A-E.

    Test-only assets require an explicit caller override. Production assets require
    independently tested, traceable and explicitly approved evidence.
    """

    if payload["artifact_scope"] == "test_only":
        return (
            (True, "test_only_override")
            if allow_test_only
            else (False, "test_only_calibration_not_allowed")
        )
    evidence = payload["independent_test"]
    if evidence["status"] == "failed":
        return False, "independent_test_failed"
    if evidence["status"] != "passed" or not evidence["approved_for_scoring"]:
        return False, "independent_test_not_passed"
    return True, "independent_test_passed"


def validate_threshold_calibration(payload: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "backend",
        "threshold_version",
        "indicator_id",
        "primary_feature",
        "primary_feature_version",
        "unit",
        "direction",
        "thresholds",
        "source",
        "ground_truth_dataset_version",
        "artifact_scope",
        "independent_test",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"threshold calibration missing fields: {sorted(missing)}")
    if (
        payload["schema_version"] != CALIBRATION_SCHEMA_VERSION
        or payload["backend"] != "threshold_rule"
    ):
        raise ValueError("invalid threshold calibration contract")
    if payload["source"] != "coach_ground_truth_calibration":
        raise ValueError("threshold source must be coach_ground_truth_calibration")
    _require_non_empty_strings(
        payload,
        (
            "threshold_version",
            "indicator_id",
            "primary_feature",
            "primary_feature_version",
            "unit",
            "ground_truth_dataset_version",
        ),
    )
    if payload["direction"] not in {"higher_is_better", "lower_is_better"}:
        raise ValueError("threshold direction is invalid")
    thresholds = payload["thresholds"]
    if not isinstance(thresholds, list) or len(thresholds) != 4:
        raise ValueError("A-E threshold rule requires exactly four cut points")
    if not all(_finite_number(value) for value in thresholds):
        raise ValueError("thresholds must be finite numbers")
    if any(right <= left for left, right in zip(thresholds, thresholds[1:])):
        raise ValueError("thresholds must be strictly increasing")
    _validate_independent_test_evidence(payload)


def validate_ordinal_model(payload: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "backend",
        "model_version",
        "indicator_id",
        "feature_order",
        "unit_by_feature",
        "feature_version_by_feature",
        "coefficients",
        "cutpoints",
        "source",
        "ground_truth_dataset_version",
        "artifact_scope",
        "independent_test",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"ordinal model missing fields: {sorted(missing)}")
    if (
        payload["schema_version"] != CALIBRATION_SCHEMA_VERSION
        or payload["backend"] != "ordinal_regression"
    ):
        raise ValueError("invalid ordinal model contract")
    if payload["source"] != "coach_ground_truth_calibration":
        raise ValueError("ordinal model source must be coach_ground_truth_calibration")
    _require_non_empty_strings(
        payload,
        ("model_version", "indicator_id", "ground_truth_dataset_version"),
    )
    features = payload["feature_order"]
    coefficients = payload["coefficients"]
    cutpoints = payload["cutpoints"]
    if not isinstance(features, list) or not features:
        raise ValueError("ordinal feature_order must be non-empty")
    if any(not isinstance(name, str) or not name for name in features):
        raise ValueError("ordinal feature_order values must be non-empty strings")
    if len(set(features)) != len(features):
        raise ValueError("ordinal feature_order must not contain duplicates")
    for field in ("unit_by_feature", "feature_version_by_feature"):
        mapping = payload[field]
        if not isinstance(mapping, dict) or set(mapping) != set(features):
            raise ValueError(f"ordinal {field} must exactly match feature_order")
        if any(not isinstance(value, str) or not value for value in mapping.values()):
            raise ValueError(f"ordinal {field} values must be non-empty strings")
    if not isinstance(coefficients, list) or len(coefficients) != len(features):
        raise ValueError("ordinal coefficients must match feature_order")
    if not all(_finite_number(value) for value in coefficients):
        raise ValueError("ordinal coefficients must be finite numbers")
    if not _finite_number(payload.get("intercept", 0.0)):
        raise ValueError("ordinal intercept must be a finite number")
    if not isinstance(cutpoints, list) or len(cutpoints) != 4:
        raise ValueError("ordinal model requires four cutpoints")
    if not all(_finite_number(value) for value in cutpoints):
        raise ValueError("ordinal cutpoints must be finite numbers")
    if any(right <= left for left, right in zip(cutpoints, cutpoints[1:])):
        raise ValueError("ordinal cutpoints must be strictly increasing")
    _validate_independent_test_evidence(payload)
