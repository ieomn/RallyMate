from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_evaluation.feature_errors import compute_smoothing_counterfactual
from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_features.schemas import FeatureResult
from rallymate_scoring.scoring_context import SCORING_CONTEXT_FEATURE_DEFINITIONS


SCHEMA_VERSION = "1.2.0"
REPORT_VERSION = "smoothing-counterfactual-coverage-v1.2.0"
COUNTERFACTUAL_METHOD = (
    "one_factor_counterfactual_differences_not_additive_shapley_decomposition"
)
CIRCULAR_DIRECTION_FEATURES = {
    "hip_center_motion_direction_deg",
    "launch_direction_deg",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must be an object")
        rows.append(value)
    if not rows:
        raise ValueError(f"feature source is empty: {path}")
    return rows


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _counterfactual_difference(
    feature_name: str,
    production_value: float,
    raw_counterfactual_value: float,
) -> float:
    difference = production_value - raw_counterfactual_value
    if feature_name in CIRCULAR_DIRECTION_FEATURES:
        return float((difference + 180.0) % 360.0 - 180.0)
    return float(difference)


def _feature_result(row: dict[str, Any]) -> FeatureResult:
    required = {
        "feature_name",
        "feature_version",
        "value",
        "unit",
        "confidence",
        "valid",
        "reason",
        "source_frames",
        "raw_value",
        "smoothed_value",
        "provenance",
    }
    missing = sorted(required - row.keys())
    if missing:
        raise ValueError(f"feature row missing fields: {missing}")
    return FeatureResult(**{key: row[key] for key in required})


def _registry_requirements(
    registry: dict[str, Any],
) -> tuple[dict[str, set[str]], list[str]]:
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("registry.indicators must be a non-empty array")
    event_codes_by_feature: dict[str, set[str]] = defaultdict(set)
    for indicator in indicators:
        if not isinstance(indicator, dict):
            raise ValueError("registry indicator must be an object")
        features = indicator.get("required_features")
        events = indicator.get("required_events")
        if not isinstance(features, list) or not features:
            raise ValueError("indicator required_features must be non-empty")
        if not isinstance(events, list) or not events:
            raise ValueError("indicator required_events must be non-empty")
        event_codes = {
            str(event).split(".", 1)[0]
            for event in events
            if isinstance(event, str) and event
        }
        if not event_codes:
            raise ValueError("indicator required_events contain no event code")
        for feature_name in features:
            if not isinstance(feature_name, str) or not feature_name:
                raise ValueError("required feature names must be non-empty")
            event_codes_by_feature[feature_name].update(event_codes)

    unknown = sorted(
        set(event_codes_by_feature)
        - set(FEATURE_DEFINITIONS)
        - set(SCORING_CONTEXT_FEATURE_DEFINITIONS)
    )
    if unknown:
        raise ValueError(f"registry contains unknown feature definitions: {unknown}")
    non_pose_context = sorted(
        set(event_codes_by_feature) & set(SCORING_CONTEXT_FEATURE_DEFINITIONS)
    )
    pose_features = {
        name: codes
        for name, codes in event_codes_by_feature.items()
        if name in FEATURE_DEFINITIONS
    }
    return pose_features, non_pose_context


def build_smoothing_counterfactual_coverage(
    *,
    registry_path: Path,
    feature_paths: list[Path],
) -> dict[str, Any]:
    if not feature_paths:
        raise ValueError("at least one features.jsonl path is required")
    registry_path = registry_path.resolve()
    registry = _read_json(registry_path)
    registry_version = registry.get("registry_version")
    if not isinstance(registry_version, str) or not registry_version:
        raise ValueError("registry_version must be non-empty")
    pose_features, non_pose_context = _registry_requirements(registry)

    metrics: dict[str, dict[str, Any]] = {}
    for feature_name, event_codes in sorted(pose_features.items()):
        metrics[feature_name] = {
            "feature_name": feature_name,
            "required_event_codes": sorted(event_codes),
            "feature_versions": set(),
            "units": set(),
            "aggregations": set(),
            "record_count": 0,
            "valid_numeric_record_count": 0,
            "computed_counterfactual_count": 0,
            "unavailable_counterfactual_count": 0,
            "invalid_or_nonnumeric_excluded_count": 0,
            "counterfactual_unavailable_reason_counts": Counter(),
            "production_exclusion_reason_counts": Counter(),
            "_computed_comparisons": [],
        }

    source_records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    total_source_rows = 0
    for raw_path in feature_paths:
        path = raw_path.resolve()
        rows = _read_jsonl(path)
        total_source_rows += len(rows)
        video_ids = {str(row.get("video_id")) for row in rows}
        if len(video_ids) != 1 or "None" in video_ids or "" in video_ids:
            raise ValueError(
                f"feature source must contain exactly one video_id: {path}"
            )
        video_id = next(iter(video_ids))
        relevant_count = 0
        relevant_events: set[str] = set()
        for row in rows:
            feature_name = row.get("feature_name")
            event_code = row.get("event_code")
            if feature_name not in pose_features:
                continue
            if event_code not in pose_features[feature_name]:
                continue
            event_id = row.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError(f"relevant feature row lacks event_id: {path}")
            identity = (video_id, event_id, str(feature_name))
            if identity in seen:
                raise ValueError(f"duplicate video/event/feature row: {identity}")
            seen.add(identity)
            relevant_count += 1
            relevant_events.add(event_id)

            provenance = row.get("provenance")
            if not isinstance(provenance, dict):
                raise ValueError(f"feature row lacks provenance: {identity}")
            model_versions = provenance.get("model_versions")
            row_registry = (
                model_versions.get("feasibility_registry")
                if isinstance(model_versions, dict)
                else None
            )
            if row_registry != registry_version:
                raise ValueError(
                    "feature registry version mismatch: "
                    f"{identity} has {row_registry!r}, expected {registry_version!r}"
                )
            metric = metrics[str(feature_name)]
            metric["record_count"] += 1
            metric["feature_versions"].add(str(row.get("feature_version")))
            metric["units"].add(str(row.get("unit")))
            metric["aggregations"].add(str(provenance.get("aggregation")))
            if row.get("valid") is not True or not _numeric(row.get("value")):
                metric["invalid_or_nonnumeric_excluded_count"] += 1
                metric["production_exclusion_reason_counts"][
                    str(row.get("reason") or "unspecified")
                ] += 1
                continue

            metric["valid_numeric_record_count"] += 1
            counterfactual = compute_smoothing_counterfactual(
                _feature_result(row)
            )
            if counterfactual.get("raw_counterfactual_value") is not None:
                metric["computed_counterfactual_count"] += 1
                production_value = float(row["value"])
                raw_counterfactual_value = float(
                    counterfactual["raw_counterfactual_value"]
                )
                metric["_computed_comparisons"].append(
                    {
                        "video_id": video_id,
                        "event_id": event_id,
                        "production_value": production_value,
                        "raw_counterfactual_value": raw_counterfactual_value,
                        "difference": _counterfactual_difference(
                            str(feature_name),
                            production_value,
                            raw_counterfactual_value,
                        ),
                        "unit": str(row.get("unit")),
                    }
                )
            else:
                metric["unavailable_counterfactual_count"] += 1
                metric["counterfactual_unavailable_reason_counts"][
                    str(counterfactual.get("reason") or "unspecified")
                ] += 1

        source_records.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "video_id": video_id,
                "source_feature_record_count": len(rows),
                "audited_required_pose_feature_record_count": relevant_count,
                "audited_event_count": len(relevant_events),
            }
        )

    feature_metrics: list[dict[str, Any]] = []
    for metric in metrics.values():
        valid_count = metric["valid_numeric_record_count"]
        computed_count = metric["computed_counterfactual_count"]
        metric["feature_versions"] = sorted(metric["feature_versions"])
        metric["units"] = sorted(metric["units"])
        metric["aggregations"] = sorted(metric["aggregations"])
        metric["counterfactual_unavailable_reason_counts"] = dict(
            sorted(metric["counterfactual_unavailable_reason_counts"].items())
        )
        metric["production_exclusion_reason_counts"] = dict(
            sorted(metric["production_exclusion_reason_counts"].items())
        )
        metric["counterfactual_coverage"] = (
            round(computed_count / valid_count, 8) if valid_count else None
        )
        metric["status"] = (
            "not_observed"
            if metric["record_count"] == 0
            else "fully_reconstructable"
            if valid_count and computed_count == valid_count
            else "partially_reconstructable"
            if computed_count
            else "not_reconstructable"
        )
        comparisons = metric.pop("_computed_comparisons")
        if metric["units"] == ["code"]:
            exact_count = sum(
                item["production_value"] == item["raw_counterfactual_value"]
                for item in comparisons
            )
            metric["counterfactual_impact"] = {
                "comparison_kind": "categorical_code_agreement",
                "computed_count": len(comparisons),
                "exact_match_count": exact_count,
                "changed_count": len(comparisons) - exact_count,
                "agreement_rate": (
                    round(exact_count / len(comparisons), 8)
                    if comparisons
                    else None
                ),
                "mean_absolute_difference": None,
                "p95_absolute_difference": None,
                "signed_mean_difference": None,
                "maximum_absolute_difference": None,
                "maximum_absolute_difference_example": None,
            }
        else:
            differences = np.asarray(
                [item["difference"] for item in comparisons],
                dtype=np.float64,
            )
            absolute = np.abs(differences)
            maximum_index = int(np.argmax(absolute)) if absolute.size else None
            maximum_example = (
                {
                    key: (
                        round(float(value), 8)
                        if key
                        in {
                            "production_value",
                            "raw_counterfactual_value",
                            "difference",
                        }
                        else value
                    )
                    for key, value in comparisons[maximum_index].items()
                }
                if maximum_index is not None
                else None
            )
            metric["counterfactual_impact"] = {
                "comparison_kind": (
                    "circular_difference_deg"
                    if metric["feature_name"] in CIRCULAR_DIRECTION_FEATURES
                    else "numeric_difference"
                ),
                "computed_count": len(comparisons),
                "exact_match_count": None,
                "changed_count": None,
                "agreement_rate": None,
                "mean_absolute_difference": (
                    round(float(np.mean(absolute)), 8) if absolute.size else None
                ),
                "p95_absolute_difference": (
                    round(float(np.percentile(absolute, 95)), 8)
                    if absolute.size
                    else None
                ),
                "signed_mean_difference": (
                    round(float(np.mean(differences)), 8)
                    if differences.size
                    else None
                ),
                "maximum_absolute_difference": (
                    round(float(np.max(absolute)), 8) if absolute.size else None
                ),
                "maximum_absolute_difference_example": maximum_example,
            }
        feature_metrics.append(metric)

    valid_total = sum(
        item["valid_numeric_record_count"] for item in feature_metrics
    )
    computed_total = sum(
        item["computed_counterfactual_count"] for item in feature_metrics
    )
    unavailable_total = sum(
        item["unavailable_counterfactual_count"] for item in feature_metrics
    )
    excluded_total = sum(
        item["invalid_or_nonnumeric_excluded_count"] for item in feature_metrics
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "audited_counterfactual_coverage_no_ground_truth",
        "registry": {
            "path": str(registry_path),
            "sha256": _sha256(registry_path),
            "registry_version": registry_version,
            "indicator_count": len(registry["indicators"]),
            "required_pose_feature_count": len(pose_features),
            "excluded_non_pose_context_features": non_pose_context,
        },
        "sources": source_records,
        "counts": {
            "source_feature_record_count": total_source_rows,
            "audited_required_pose_feature_record_count": sum(
                item["record_count"] for item in feature_metrics
            ),
            "valid_numeric_record_count": valid_total,
            "computed_counterfactual_count": computed_total,
            "unavailable_counterfactual_count": unavailable_total,
            "invalid_or_nonnumeric_excluded_count": excluded_total,
            "features_fully_reconstructable_count": sum(
                item["status"] == "fully_reconstructable"
                for item in feature_metrics
            ),
            "features_partially_reconstructable_count": sum(
                item["status"] == "partially_reconstructable"
                for item in feature_metrics
            ),
            "features_not_reconstructable_count": sum(
                item["status"] == "not_reconstructable"
                for item in feature_metrics
            ),
            "features_not_observed_count": sum(
                item["status"] == "not_observed" for item in feature_metrics
            ),
            "counterfactual_coverage": (
                round(computed_total / valid_total, 8) if valid_total else None
            ),
        },
        "feature_metrics": feature_metrics,
        "semantics": {
            "method": COUNTERFACTUAL_METHOD,
            "interpretation": (
                "coverage of replayable raw-versus-smoothed feature calculations; "
                "per-feature numeric differences or categorical agreement are "
                "smoothing sensitivity, not feature accuracy and not an additive "
                "error decomposition"
            ),
            "ground_truth_provided": False,
            "accuracy_claim": False,
            "counterfactual_coverage_is_feature_accuracy": False,
            "quality_gate_modified": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_smoothing_counterfactual_coverage(report)
    return report


def validate_smoothing_counterfactual_coverage(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported smoothing coverage schema_version")
    if report.get("report_version") != REPORT_VERSION:
        raise ValueError("unsupported smoothing coverage report_version")
    if report.get("status") != "audited_counterfactual_coverage_no_ground_truth":
        raise ValueError("unsafe smoothing coverage status")
    semantics = report.get("semantics")
    if not isinstance(semantics, dict):
        raise ValueError("semantics must be an object")
    if semantics.get("method") != COUNTERFACTUAL_METHOD:
        raise ValueError("unsupported counterfactual method")
    for key in (
        "ground_truth_provided",
        "accuracy_claim",
        "counterfactual_coverage_is_feature_accuracy",
        "quality_gate_modified",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if semantics.get(key) is not False:
            raise ValueError(f"semantics.{key} must be false")
    registry = report.get("registry")
    metrics = report.get("feature_metrics")
    counts = report.get("counts")
    if (
        not isinstance(registry, dict)
        or not isinstance(metrics, list)
        or not isinstance(counts, dict)
    ):
        raise ValueError("registry, feature_metrics, and counts are required")
    if len(metrics) != registry.get("required_pose_feature_count"):
        raise ValueError("feature metric count does not match registry")
    names = [
        item.get("feature_name") for item in metrics if isinstance(item, dict)
    ]
    if len(names) != len(set(names)) or names != sorted(names):
        raise ValueError("feature metrics must be unique and sorted")
    valid_total = computed_total = unavailable_total = excluded_total = 0
    record_total = 0
    status_counts: Counter[str] = Counter()
    for item in metrics:
        if not isinstance(item, dict):
            raise ValueError("feature metric must be an object")
        record_count = item.get("record_count")
        valid = item.get("valid_numeric_record_count")
        computed = item.get("computed_counterfactual_count")
        unavailable = item.get("unavailable_counterfactual_count")
        excluded = item.get("invalid_or_nonnumeric_excluded_count")
        count_values = (record_count, valid, computed, unavailable, excluded)
        if not all(
            isinstance(value, int) and value >= 0 for value in count_values
        ):
            raise ValueError("feature metric counts must be non-negative integers")
        if record_count != valid + excluded or valid != computed + unavailable:
            raise ValueError("feature metric count decomposition is inconsistent")
        reason_counts = item.get("counterfactual_unavailable_reason_counts")
        exclusion_counts = item.get("production_exclusion_reason_counts")
        if not isinstance(reason_counts, dict) or not isinstance(
            exclusion_counts, dict
        ):
            raise ValueError("feature reason counts must be objects")
        if sum(reason_counts.values()) != unavailable:
            raise ValueError("counterfactual unavailable reasons are inconsistent")
        if sum(exclusion_counts.values()) != excluded:
            raise ValueError("production exclusion reasons are inconsistent")
        expected_coverage = round(computed / valid, 8) if valid else None
        if item.get("counterfactual_coverage") != expected_coverage:
            raise ValueError("feature counterfactual coverage is inconsistent")
        expected_status = (
            "not_observed"
            if record_count == 0
            else "fully_reconstructable"
            if valid and computed == valid
            else "partially_reconstructable"
            if computed
            else "not_reconstructable"
        )
        if item.get("status") != expected_status:
            raise ValueError("feature counterfactual status is inconsistent")
        if record_count and (
            not item.get("feature_versions")
            or not item.get("units")
            or not item.get("aggregations")
        ):
            raise ValueError("observed feature lacks version or aggregation")
        if not item.get("required_event_codes"):
            raise ValueError("feature metric lacks required event codes")
        impact = item.get("counterfactual_impact")
        if not isinstance(impact, dict) or impact.get("computed_count") != computed:
            raise ValueError("feature counterfactual impact count is inconsistent")
        if impact.get("comparison_kind") == "categorical_code_agreement":
            exact = impact.get("exact_match_count")
            changed = impact.get("changed_count")
            if (
                not isinstance(exact, int)
                or not isinstance(changed, int)
                or exact + changed != computed
            ):
                raise ValueError("categorical counterfactual impact is inconsistent")
            expected_agreement = round(exact / computed, 8) if computed else None
            if impact.get("agreement_rate") != expected_agreement:
                raise ValueError("categorical agreement rate is inconsistent")
        elif impact.get("comparison_kind") in {
            "numeric_difference",
            "circular_difference_deg",
        }:
            if any(
                impact.get(key) is not None
                for key in ("exact_match_count", "changed_count", "agreement_rate")
            ):
                raise ValueError("numeric impact contains categorical metrics")
            if computed == 0 and any(
                impact.get(key) is not None
                for key in (
                    "mean_absolute_difference",
                    "p95_absolute_difference",
                    "signed_mean_difference",
                    "maximum_absolute_difference",
                    "maximum_absolute_difference_example",
                )
            ):
                raise ValueError("uncomputed numeric impact must remain null")
            if computed and (
                impact.get("mean_absolute_difference") is None
                or impact.get("p95_absolute_difference") is None
                or impact.get("signed_mean_difference") is None
                or impact.get("maximum_absolute_difference") is None
                or not isinstance(
                    impact.get("maximum_absolute_difference_example"), dict
                )
            ):
                raise ValueError("computed numeric impact lacks metrics")
        else:
            raise ValueError("unsupported counterfactual impact kind")
        record_total += record_count
        valid_total += valid
        computed_total += computed
        unavailable_total += unavailable
        excluded_total += excluded
        status_counts[str(item.get("status"))] += 1
    expected = {
        "audited_required_pose_feature_record_count": record_total,
        "valid_numeric_record_count": valid_total,
        "computed_counterfactual_count": computed_total,
        "unavailable_counterfactual_count": unavailable_total,
        "invalid_or_nonnumeric_excluded_count": excluded_total,
        "features_fully_reconstructable_count": status_counts[
            "fully_reconstructable"
        ],
        "features_partially_reconstructable_count": status_counts[
            "partially_reconstructable"
        ],
        "features_not_reconstructable_count": status_counts[
            "not_reconstructable"
        ],
        "features_not_observed_count": status_counts["not_observed"],
        "counterfactual_coverage": (
            round(computed_total / valid_total, 8) if valid_total else None
        ),
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            raise ValueError(f"counts.{key} is inconsistent")
    sources = report.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty array")
    source_total = 0
    audited_source_total = 0
    video_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        video_id = source.get("video_id")
        if not isinstance(video_id, str) or not video_id or video_id in video_ids:
            raise ValueError("source video_id must be unique and non-empty")
        video_ids.add(video_id)
        source_count = source.get("source_feature_record_count")
        audited_count = source.get("audited_required_pose_feature_record_count")
        if not isinstance(source_count, int) or source_count < 1:
            raise ValueError("source feature count must be positive")
        if not isinstance(audited_count, int) or not 0 <= audited_count <= source_count:
            raise ValueError("source audited feature count is invalid")
        source_total += source_count
        audited_source_total += audited_count
    if counts.get("source_feature_record_count") != source_total:
        raise ValueError("source feature record count is inconsistent")
    if audited_source_total != record_total:
        raise ValueError("source audited feature count is inconsistent")


def validate_smoothing_counterfactual_coverage_sources(
    report: dict[str, Any],
) -> None:
    """Rebuild the audit from bound files and compare every derived field."""

    validate_smoothing_counterfactual_coverage(report)
    registry = report["registry"]
    sources = report["sources"]
    registry_path = Path(registry["path"])
    if not registry_path.is_file() or _sha256(registry_path) != registry["sha256"]:
        raise ValueError("bound registry source is missing or hash-mismatched")
    feature_paths: list[Path] = []
    for source in sources:
        path = Path(source["path"])
        if not path.is_file() or _sha256(path) != source["sha256"]:
            raise ValueError("bound feature source is missing or hash-mismatched")
        feature_paths.append(path)
    replay = build_smoothing_counterfactual_coverage(
        registry_path=registry_path,
        feature_paths=feature_paths,
    )
    for key in (
        "schema_version",
        "report_version",
        "status",
        "registry",
        "sources",
        "counts",
        "feature_metrics",
        "semantics",
    ):
        if replay[key] != report.get(key):
            raise ValueError(f"source replay mismatch: {key}")


__all__ = [
    "COUNTERFACTUAL_METHOD",
    "REPORT_VERSION",
    "SCHEMA_VERSION",
    "build_smoothing_counterfactual_coverage",
    "validate_smoothing_counterfactual_coverage",
    "validate_smoothing_counterfactual_coverage_sources",
]
