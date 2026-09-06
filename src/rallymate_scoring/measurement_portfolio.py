from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from rallymate_scoring.feasibility import measurement_feature_names


PORTFOLIO_VERSION = "indicator-measurement-portfolio-v1.0.0"
SELECTION_POLICY_VERSION = "observation-quality-only-v1.0.0"
REQUIRED_SOURCE_KEYS = frozenset(
    {"feasibility_registry", "events", "indicator_features", "scores"}
)


class MeasurementPortfolioError(ValueError):
    pass


def _feature_confidence_summary(record: dict[str, Any]) -> tuple[float, float]:
    values = [float(item["confidence"]) for item in record.get("features", [])]
    if not values:
        return 0.0, 0.0
    return min(values), sum(values) / len(values)


def _measurement_rank(
    record: dict[str, Any], event: dict[str, Any]
) -> tuple[float, float, float, int, int, str]:
    minimum, mean = _feature_confidence_summary(record)
    gate_rank = {"pass": 2, "advisory": 1, "hard_fail": 0}.get(
        str(record.get("quality_gate", {}).get("status")), -1
    )
    return (
        -minimum,
        -mean,
        -float(event.get("confidence", 0.0)),
        -gate_rank,
        int(event["start_ms"]),
        str(record["event_id"]),
    )


def _diagnostic_rank(
    record: dict[str, Any], event: dict[str, Any]
) -> tuple[int, float, float, int, str]:
    features = record.get("features", [])
    valid_count = sum(item.get("valid") is True for item in features)
    _, mean = _feature_confidence_summary(record)
    return (
        -valid_count,
        -mean,
        -float(event.get("confidence", 0.0)),
        int(event["start_ms"]),
        str(record["event_id"]),
    )


def _event_reference(
    record: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    return {
        "event_id": str(record["event_id"]),
        "event_code": str(record["event_code"]),
        "start_ms": int(event["start_ms"]),
        "end_ms": int(event["end_ms"]),
        "person_track_id": record["person_track_id"],
        "event_confidence": float(event.get("confidence", 0.0)),
    }


def _feature_vector(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "feature_name": str(item["feature_name"]),
            "feature_version": str(item["feature_version"]),
            "value": item.get("value"),
            "unit": str(item["unit"]),
            "confidence": float(item["confidence"]),
            "valid": item.get("valid") is True,
            "reason": str(item["reason"]),
            "source_frames": [int(frame) for frame in item.get("source_frames", [])],
        }
        for item in record.get("features", [])
    ]


def build_indicator_measurement_portfolio(
    *,
    registry: dict[str, Any],
    events: list[dict[str, Any]],
    indicator_records: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    video_id: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    indicators = registry.get("indicators", [])
    registry_by_id = {str(item["indicator_id"]): item for item in indicators}
    if not registry_by_id or len(registry_by_id) != len(indicators):
        raise MeasurementPortfolioError("registry indicator set is empty or duplicated")
    event_by_id = {str(item["event_id"]): item for item in events}
    if len(event_by_id) != len(events):
        raise MeasurementPortfolioError("event IDs are duplicated")
    score_by_key = {
        (str(item["event_id"]), str(item["indicator_id"])): item for item in scores
    }
    if len(score_by_key) != len(scores) or len(scores) != len(indicator_records):
        raise MeasurementPortfolioError("indicator and score membership differs")

    records_by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_versions: dict[str, Any] | None = None
    video_sha256: str | None = None
    for record in indicator_records:
        indicator_id = str(record.get("indicator_id"))
        event_id = str(record.get("event_id"))
        if indicator_id not in registry_by_id or event_id not in event_by_id:
            raise MeasurementPortfolioError("indicator record is outside registry or events")
        if record.get("video_id") != video_id or event_by_id[event_id].get("video_id") != video_id:
            raise MeasurementPortfolioError("video identity differs across portfolio inputs")
        expected_features = measurement_feature_names(registry_by_id[indicator_id])
        actual_features = [str(item.get("feature_name")) for item in record.get("features", [])]
        if actual_features != expected_features:
            raise MeasurementPortfolioError(
                f"required feature order differs for {event_id}/{indicator_id}"
            )
        score = score_by_key.get((event_id, indicator_id))
        if score is None or score.get("status") != record.get("scoring_status"):
            raise MeasurementPortfolioError("score status differs from indicator record")
        if score.get("grade") is not None or score.get("threshold_version") is not None:
            raise MeasurementPortfolioError(
                "F2 measurement portfolio cannot ingest a graded score"
            )
        current_versions = score.get("model_versions")
        if model_versions is None:
            model_versions = current_versions
        elif current_versions != model_versions:
            raise MeasurementPortfolioError("model versions differ across score records")
        evidence = score.get("evidence", [])
        current_video_hashes = {
            str(item.get("video_sha256")) for item in evidence if item.get("video_sha256")
        }
        if len(current_video_hashes) > 1:
            raise MeasurementPortfolioError("score evidence contains multiple video hashes")
        if current_video_hashes:
            current_video_sha256 = next(iter(current_video_hashes))
            if video_sha256 is None:
                video_sha256 = current_video_sha256
            elif current_video_sha256 != video_sha256:
                raise MeasurementPortfolioError("video hash differs across score records")
        records_by_indicator[indicator_id].append(record)

    items: list[dict[str, Any]] = []
    measured_indicator_count = 0
    measured_candidate_count = unavailable_candidate_count = 0
    for indicator_id in sorted(registry_by_id):
        registry_item = registry_by_id[indicator_id]
        records = records_by_indicator[indicator_id]
        measured = [item for item in records if item.get("feature_status") == "measured"]
        unavailable = [
            item for item in records if item.get("feature_status") == "unavailable"
        ]
        if len(measured) + len(unavailable) != len(records):
            raise MeasurementPortfolioError("unexpected feature_status")
        measured_candidate_count += len(measured)
        unavailable_candidate_count += len(unavailable)
        representative = None
        diagnostic = None
        if measured:
            selected = min(
                measured,
                key=lambda item: _measurement_rank(
                    item, event_by_id[str(item["event_id"])]
                ),
            )
            event = event_by_id[str(selected["event_id"])]
            minimum_confidence, mean_confidence = _feature_confidence_summary(selected)
            representative = {
                **_event_reference(selected, event),
                "selection_quality": {
                    "minimum_required_feature_confidence": minimum_confidence,
                    "mean_required_feature_confidence": mean_confidence,
                    "event_confidence": float(event.get("confidence", 0.0)),
                    "quality_gate_status": str(
                        selected.get("quality_gate", {}).get("status")
                    ),
                },
                "feature_vector": _feature_vector(selected),
                "scoring_status": str(selected["scoring_status"]),
                "grade": None,
                "threshold_version": None,
            }
            measured_indicator_count += 1
        elif unavailable:
            selected = min(
                unavailable,
                key=lambda item: _diagnostic_rank(
                    item, event_by_id[str(item["event_id"])]
                ),
            )
            event = event_by_id[str(selected["event_id"])]
            diagnostic = {
                **_event_reference(selected, event),
                "feature_vector": _feature_vector(selected),
                "reason_codes": [str(value) for value in selected.get("reason_codes", [])],
                "hard_fail_flags": [
                    str(value)
                    for value in selected.get("quality_gate", {}).get(
                        "hard_fail_flags", []
                    )
                ],
            }
        items.append(
            {
                "indicator_id": indicator_id,
                "feasibility_level": str(registry_item["feasibility_level"]),
                "required_events": list(registry_item["required_events"]),
                "required_features": measurement_feature_names(registry_item),
                "required_scoring_features": list(registry_item["required_features"]),
                "measurement_status": "measured" if representative else "unavailable",
                "candidate_event_count": len(records),
                "measured_candidate_count": len(measured),
                "unavailable_candidate_count": len(unavailable),
                "all_candidate_event_ids": sorted(str(item["event_id"]) for item in records),
                "measured_candidate_event_ids": sorted(
                    str(item["event_id"]) for item in measured
                ),
                "representative_measurement": representative,
                "best_unavailable_diagnostic": diagnostic,
                "formal_score_status": "not_authorized_F2_measurement_only",
            }
        )

    report = {
        "schema_version": "1.0.0",
        "portfolio_version": PORTFOLIO_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "all_registry_indicators_measured"
            if measured_indicator_count == len(items)
            else "partial_registry_indicator_measurement"
        ),
        "video": {"video_id": video_id, "video_sha256": video_sha256},
        "sources": sources,
        "registry": {
            "registry_version": str(registry.get("registry_version")),
            "indicator_ids": sorted(registry_by_id),
        },
        "model_versions": model_versions,
        "summary": {
            "registry_indicator_count": len(items),
            "measured_indicator_count": measured_indicator_count,
            "unavailable_indicator_count": len(items) - measured_indicator_count,
            "candidate_event_count": len(events),
            "indicator_event_instance_count": len(indicator_records),
            "measured_indicator_event_instance_count": measured_candidate_count,
            "unavailable_indicator_event_instance_count": unavailable_candidate_count,
            "formal_grade_count": 0,
            "threshold_version_count": 0,
        },
        "selection_policy": {
            "purpose": "choose one representative F2 measurement by observation quality only",
            "ranking": [
                "minimum_required_feature_confidence_desc",
                "mean_required_feature_confidence_desc",
                "event_confidence_desc",
                "quality_gate_pass_then_advisory",
                "event_start_ms_asc",
                "event_id_asc",
            ],
            "feature_values_used_for_selection": False,
            "athletic_performance_used_for_selection": False,
            "cross_model_cherry_picking_used": False,
        },
        "indicators": items,
        "safety": {
            "candidate_events_are_ground_truth": False,
            "accuracy_claim": False,
            "formal_scoring_ready": False,
            "grade_generated": False,
            "threshold_generated": False,
            "quality_gate_modified": False,
            "missing_value_zero_filled": False,
            "maturity_promoted": False,
        },
    }
    validate_indicator_measurement_portfolio(report)
    return report


def validate_indicator_measurement_portfolio(report: dict[str, Any]) -> None:
    if (
        report.get("schema_version") != "1.0.0"
        or report.get("portfolio_version") != PORTFOLIO_VERSION
        or report.get("selection_policy_version") != SELECTION_POLICY_VERSION
    ):
        raise MeasurementPortfolioError("unsupported measurement portfolio")
    sources = report.get("sources", {})
    if not isinstance(sources, dict) or not REQUIRED_SOURCE_KEYS.issubset(sources):
        raise MeasurementPortfolioError("measurement portfolio source lineage is incomplete")
    for source in sources.values():
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not source.get("path")
            or not isinstance(source.get("sha256"), str)
            or len(source["sha256"]) != 64
            or any(char not in "0123456789ABCDEF" for char in source["sha256"])
        ):
            raise MeasurementPortfolioError("measurement portfolio source lineage is malformed")
    rows = report.get("indicators", [])
    registry = report.get("registry", {})
    video = report.get("video", {})
    if (
        not isinstance(registry, dict)
        or not isinstance(registry.get("registry_version"), str)
        or not registry.get("registry_version")
        or not isinstance(video, dict)
        or not isinstance(video.get("video_id"), str)
        or not video.get("video_id")
    ):
        raise MeasurementPortfolioError("measurement portfolio scope is malformed")
    registry_ids = sorted(str(value) for value in registry.get("indicator_ids", []))
    row_ids = sorted(str(item.get("indicator_id")) for item in rows)
    if not registry_ids or registry_ids != row_ids or len(row_ids) != len(set(row_ids)):
        raise MeasurementPortfolioError("measurement portfolio indicator membership differs")
    measured = 0
    candidate_total = measured_candidates = unavailable_candidates = 0
    for item in rows:
        candidate_count = int(item.get("candidate_event_count", -1))
        measured_count = int(item.get("measured_candidate_count", -1))
        unavailable_count = int(item.get("unavailable_candidate_count", -1))
        if (
            min(candidate_count, measured_count, unavailable_count) < 0
            or measured_count + unavailable_count != candidate_count
            or candidate_count != len(item.get("all_candidate_event_ids", []))
            or measured_count != len(item.get("measured_candidate_event_ids", []))
            or not set(item.get("measured_candidate_event_ids", [])).issubset(
                set(item.get("all_candidate_event_ids", []))
            )
        ):
            raise MeasurementPortfolioError("measurement portfolio candidate accounting differs")
        representative = item.get("representative_measurement")
        expected_status = "measured" if measured_count else "unavailable"
        if item.get("measurement_status") != expected_status:
            raise MeasurementPortfolioError("measurement portfolio status differs")
        if measured_count:
            measured += 1
            if not isinstance(representative, dict) or item.get("best_unavailable_diagnostic") is not None:
                raise MeasurementPortfolioError("measured indicator representative differs")
            if representative.get("event_id") not in item.get(
                "measured_candidate_event_ids", []
            ):
                raise MeasurementPortfolioError("representative event membership differs")
            feature_vector = representative.get("feature_vector", [])
            if [entry.get("feature_name") for entry in feature_vector] != item.get("required_features"):
                raise MeasurementPortfolioError("representative feature contract differs")
            if any(entry.get("valid") is not True or entry.get("value") is None for entry in feature_vector):
                raise MeasurementPortfolioError("representative contains unavailable feature")
            if any(not entry.get("source_frames") for entry in feature_vector):
                raise MeasurementPortfolioError("representative feature lacks source frames")
            if representative.get("grade") is not None or representative.get("threshold_version") is not None:
                raise MeasurementPortfolioError("measurement portfolio contains a grade or threshold")
        else:
            if representative is not None:
                raise MeasurementPortfolioError("unavailable indicator has representative measurement")
            diagnostic = item.get("best_unavailable_diagnostic")
            if candidate_count and not isinstance(diagnostic, dict):
                raise MeasurementPortfolioError("unavailable candidates lack diagnostic evidence")
            if not candidate_count and diagnostic is not None:
                raise MeasurementPortfolioError("missing-event indicator has a diagnostic event")
            if isinstance(diagnostic, dict):
                if diagnostic.get("event_id") not in item.get("all_candidate_event_ids", []):
                    raise MeasurementPortfolioError("diagnostic event membership differs")
                if [
                    entry.get("feature_name")
                    for entry in diagnostic.get("feature_vector", [])
                ] != item.get("required_features"):
                    raise MeasurementPortfolioError("diagnostic feature contract differs")
        candidate_total += candidate_count
        measured_candidates += measured_count
        unavailable_candidates += unavailable_count
    summary = report.get("summary", {})
    if (
        int(summary.get("registry_indicator_count", -1)) != len(rows)
        or int(summary.get("measured_indicator_count", -1)) != measured
        or int(summary.get("unavailable_indicator_count", -1)) != len(rows) - measured
        or int(summary.get("indicator_event_instance_count", -1)) != candidate_total
        or int(summary.get("measured_indicator_event_instance_count", -1)) != measured_candidates
        or int(summary.get("unavailable_indicator_event_instance_count", -1)) != unavailable_candidates
        or int(summary.get("formal_grade_count", -1)) != 0
        or int(summary.get("threshold_version_count", -1)) != 0
    ):
        raise MeasurementPortfolioError("measurement portfolio summary differs")
    expected_report_status = (
        "all_registry_indicators_measured"
        if measured == len(rows)
        else "partial_registry_indicator_measurement"
    )
    if report.get("status") != expected_report_status:
        raise MeasurementPortfolioError("measurement portfolio report status differs")
    policy = report.get("selection_policy", {})
    for field in (
        "feature_values_used_for_selection",
        "athletic_performance_used_for_selection",
        "cross_model_cherry_picking_used",
    ):
        if policy.get(field) is not False:
            raise MeasurementPortfolioError(f"unsafe representative selection: {field}")
    safety = report.get("safety", {})
    for field in (
        "candidate_events_are_ground_truth",
        "accuracy_claim",
        "formal_scoring_ready",
        "grade_generated",
        "threshold_generated",
        "quality_gate_modified",
        "missing_value_zero_filled",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise MeasurementPortfolioError(f"unsafe measurement portfolio claim: {field}")
