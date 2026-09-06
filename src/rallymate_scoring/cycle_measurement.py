from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from rallymate_scoring.feasibility import measurement_feature_names


ARTIFACT_VERSION = "scoring-cycle-measurement-v1.0.0"
LINK_POLICY_VERSION = "phase-linked-fs01-fs02-fs09-v1.0.0"
SELECTION_POLICY_VERSION = "cycle-observation-quality-only-v1.0.0"
REQUIRED_SOURCE_KEYS = frozenset(
    {"feasibility_registry", "events", "indicator_features", "scores"}
)
EVENT_SEQUENCE = ("FS01", "FS02", "FS09")


class CycleMeasurementError(ValueError):
    pass


def _source_id(event: dict[str, Any]) -> str:
    return str(event.get("provenance", {}).get("source_id", ""))


def _phase(event: dict[str, Any], name: str) -> int | None:
    value = event.get("key_phases_ms", {}).get(name)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _cycle_id(video_id: str, track_id: Any, event_ids: list[str]) -> str:
    payload = "|".join(
        [video_id, str(track_id), LINK_POLICY_VERSION, *event_ids]
    ).encode("utf-8")
    return "cycle-" + hashlib.sha256(payload).hexdigest()[:16]


def _feature_confidences(record: dict[str, Any]) -> list[float]:
    return [float(item["confidence"]) for item in record.get("features", [])]


def _compact_feature_vector(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "feature_name": str(item["feature_name"]),
            "feature_version": str(item["feature_version"]),
            "value": item.get("value"),
            "unit": str(item["unit"]),
            "confidence": float(item["confidence"]),
            "valid": item.get("valid") is True,
            "reason": str(item["reason"]),
            "source_frames": [int(value) for value in item.get("source_frames", [])],
        }
        for item in record.get("features", [])
    ]


def _event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(event["event_id"]),
        "event_code": str(event["event_code"]),
        "start_ms": int(event["start_ms"]),
        "end_ms": int(event["end_ms"]),
        "confidence": float(event.get("confidence", 0.0)),
        "boundary_uncertainty_ms": int(event.get("boundary_uncertainty_ms", 0)),
        "link_phases_ms": {
            "initiation_ms": _phase(event, "initiation_ms"),
            "peak_speed_ms": _phase(event, "peak_speed_ms"),
        },
    }


def _link_cycles(
    events: list[dict[str, Any]], video_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_ids: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id"))
        if not event_id or event_id in event_ids:
            raise CycleMeasurementError("event IDs are empty or duplicated")
        event_ids.add(event_id)
        if event.get("video_id") != video_id:
            raise CycleMeasurementError("event video identity differs")
        code = str(event.get("event_code"))
        if code in EVENT_SEQUENCE:
            by_code[code].append(event)

    used_fs01: set[str] = set()
    used_fs09: set[str] = set()
    cycles: list[dict[str, Any]] = []
    for fs02 in sorted(
        by_code["FS02"], key=lambda item: (int(item["start_ms"]), str(item["event_id"]))
    ):
        track_id = fs02.get("person_track_id")
        source_id = _source_id(fs02)
        peak_ms = _phase(fs02, "peak_speed_ms")
        fs01_candidates = [
            item
            for item in by_code["FS01"]
            if str(item["event_id"]) not in used_fs01
            and item.get("person_track_id") == track_id
            and _source_id(item) == source_id
            and int(item["end_ms"]) == int(fs02["start_ms"])
            and _phase(item, "initiation_ms") == int(fs02["start_ms"])
        ]
        fs09_candidates = [
            item
            for item in by_code["FS09"]
            if str(item["event_id"]) not in used_fs09
            and item.get("person_track_id") == track_id
            and _source_id(item) == source_id
            and peak_ms is not None
            and _phase(item, "peak_speed_ms") == peak_ms
            and int(fs02["start_ms"]) <= int(item["start_ms"]) <= int(fs02["end_ms"])
            and int(item["end_ms"]) >= int(fs02["end_ms"])
        ]
        if len(fs01_candidates) != 1 or len(fs09_candidates) != 1:
            continue
        fs01 = fs01_candidates[0]
        fs09 = fs09_candidates[0]
        used_fs01.add(str(fs01["event_id"]))
        used_fs09.add(str(fs09["event_id"]))
        linked_events = [fs01, fs02, fs09]
        linked_ids = [str(item["event_id"]) for item in linked_events]
        cycles.append(
            {
                "cycle_id": _cycle_id(video_id, track_id, linked_ids),
                "person_track_id": track_id,
                "start_ms": int(fs01["start_ms"]),
                "end_ms": int(fs09["end_ms"]),
                "events": [_event_ref(item) for item in linked_events],
                "link_evidence": {
                    "fs01_initiation_equals_fs02_start": True,
                    "fs02_peak_equals_fs09_peak": True,
                    "fs09_starts_inside_fs02": True,
                    "fs09_covers_fs02_end": True,
                    "shared_person_track_id": True,
                    "shared_detector_source_id": True,
                    "fs02_peak_speed_ms": peak_ms,
                },
            }
        )
    linked_ids = {
        event["event_id"] for cycle in cycles for event in cycle["events"]
    }
    unmatched = sorted(event_ids - linked_ids)
    return cycles, unmatched


def _cycle_rank(cycle: dict[str, Any]) -> tuple[Any, ...]:
    quality = cycle["selection_quality"]
    return (
        -int(cycle["measured_indicator_count"]),
        -int(cycle["scoring_gate_passed_indicator_count"]),
        -float(quality["minimum_required_feature_confidence"]),
        -float(quality["mean_required_feature_confidence"]),
        -float(quality["minimum_event_confidence"]),
        int(cycle["start_ms"]),
        str(cycle["cycle_id"]),
    )


def build_scoring_cycle_measurement(
    *,
    registry: dict[str, Any],
    events: list[dict[str, Any]],
    indicator_records: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    video_id: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    registry_items = registry.get("indicators", [])
    registry_by_id = {str(item["indicator_id"]): item for item in registry_items}
    if not registry_by_id or len(registry_by_id) != len(registry_items):
        raise CycleMeasurementError("registry indicator set is empty or duplicated")
    expected_by_code: dict[str, set[str]] = defaultdict(set)
    for indicator_id, item in registry_by_id.items():
        event_codes = {
            str(value).split(".", 1)[0] for value in item.get("required_events", [])
        }
        if len(event_codes) != 1 or next(iter(event_codes)) not in EVENT_SEQUENCE:
            raise CycleMeasurementError("indicator event contract cannot map to one cycle stage")
        expected_by_code[next(iter(event_codes))].add(indicator_id)

    event_by_id = {str(item["event_id"]): item for item in events}
    if len(event_by_id) != len(events):
        raise CycleMeasurementError("event IDs are duplicated")
    score_by_key = {
        (str(item["event_id"]), str(item["indicator_id"])): item for item in scores
    }
    if len(score_by_key) != len(scores) or len(scores) != len(indicator_records):
        raise CycleMeasurementError("indicator and score membership differs")
    record_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    model_versions: dict[str, Any] | None = None
    video_sha256: str | None = None
    for record in indicator_records:
        event_id = str(record.get("event_id"))
        indicator_id = str(record.get("indicator_id"))
        key = (event_id, indicator_id)
        event = event_by_id.get(event_id)
        item = registry_by_id.get(indicator_id)
        if event is None or item is None or key in record_by_key:
            raise CycleMeasurementError("indicator record membership is invalid")
        if record.get("video_id") != video_id or event.get("video_id") != video_id:
            raise CycleMeasurementError("video identity differs across cycle inputs")
        expected_features = measurement_feature_names(item)
        actual_features = [str(value.get("feature_name")) for value in record.get("features", [])]
        if expected_features != actual_features:
            raise CycleMeasurementError("indicator required feature order differs")
        score = score_by_key.get(key)
        if score is None or score.get("status") != record.get("scoring_status"):
            raise CycleMeasurementError("score status differs from indicator record")
        if score.get("grade") is not None or score.get("threshold_version") is not None:
            raise CycleMeasurementError("F2 cycle measurement cannot ingest grades")
        if model_versions is None:
            model_versions = score.get("model_versions")
        elif model_versions != score.get("model_versions"):
            raise CycleMeasurementError("model versions differ across cycle inputs")
        hashes = {
            str(value.get("video_sha256"))
            for value in score.get("evidence", [])
            if value.get("video_sha256")
        }
        if len(hashes) > 1:
            raise CycleMeasurementError("score evidence contains multiple video hashes")
        if hashes:
            current_hash = next(iter(hashes))
            if video_sha256 is None:
                video_sha256 = current_hash
            elif video_sha256 != current_hash:
                raise CycleMeasurementError("video hashes differ across cycle inputs")
        record_by_key[key] = record

    cycles, unmatched_event_ids = _link_cycles(events, video_id)
    cycle_rows: list[dict[str, Any]] = []
    for cycle in cycles:
        records: list[dict[str, Any]] = []
        missing_indicator_ids: list[str] = []
        event_ids_by_code = {
            str(item["event_code"]): str(item["event_id"]) for item in cycle["events"]
        }
        for event_code in EVENT_SEQUENCE:
            for indicator_id in sorted(expected_by_code[event_code]):
                record = record_by_key.get((event_ids_by_code[event_code], indicator_id))
                if record is None:
                    missing_indicator_ids.append(indicator_id)
                else:
                    records.append(record)
        if missing_indicator_ids:
            raise CycleMeasurementError("linked cycle is missing registry indicator records")
        measured = [item for item in records if item.get("feature_status") == "measured"]
        unavailable = [item for item in records if item.get("feature_status") == "unavailable"]
        if len(measured) + len(unavailable) != len(records):
            raise CycleMeasurementError("unexpected feature status in cycle")
        confidences = [value for item in measured for value in _feature_confidences(item)]
        event_confidences = [float(item["confidence"]) for item in cycle["events"]]
        gate_counts = Counter(
            str(item.get("quality_gate", {}).get("status")) for item in records
        )
        scoring_gate_passed = sum(
            item.get("scoring_status") in {"calibration_required", "scored"}
            for item in records
        )
        cycle_rows.append(
            {
                **cycle,
                "indicator_count": len(records),
                "measured_indicator_count": len(measured),
                "unavailable_indicator_count": len(unavailable),
                "scoring_gate_passed_indicator_count": int(scoring_gate_passed),
                "measured_indicator_ids": sorted(str(item["indicator_id"]) for item in measured),
                "unavailable_indicator_ids": sorted(
                    str(item["indicator_id"]) for item in unavailable
                ),
                "score_status_counts": dict(
                    sorted(Counter(str(item["scoring_status"]) for item in records).items())
                ),
                "quality_gate_status_counts": dict(sorted(gate_counts.items())),
                "selection_quality": {
                    "minimum_required_feature_confidence": min(confidences) if confidences else 0.0,
                    "mean_required_feature_confidence": (
                        sum(confidences) / len(confidences) if confidences else 0.0
                    ),
                    "minimum_event_confidence": min(event_confidences),
                    "mean_event_confidence": sum(event_confidences) / len(event_confidences),
                },
                "complete_registry_measurement": len(measured) == len(registry_by_id),
            }
        )
    representative_summary = min(cycle_rows, key=_cycle_rank) if cycle_rows else None
    representative = None
    if representative_summary is not None:
        event_ids_by_code = {
            str(item["event_code"]): str(item["event_id"])
            for item in representative_summary["events"]
        }
        measurements = []
        for indicator_id in sorted(registry_by_id):
            event_code = next(iter({
                str(value).split(".", 1)[0]
                for value in registry_by_id[indicator_id]["required_events"]
            }))
            record = record_by_key[(event_ids_by_code[event_code], indicator_id)]
            measurements.append(
                {
                    "indicator_id": indicator_id,
                    "event_id": str(record["event_id"]),
                    "feature_status": str(record["feature_status"]),
                    "scoring_status": str(record["scoring_status"]),
                    "feature_vector": _compact_feature_vector(record),
                    "reason_codes": [str(value) for value in record.get("reason_codes", [])],
                    "grade": None,
                    "threshold_version": None,
                }
            )
        representative = {**representative_summary, "indicator_measurements": measurements}

    complete_count = sum(item["complete_registry_measurement"] for item in cycle_rows)
    best_count = (
        int(representative_summary["measured_indicator_count"])
        if representative_summary is not None
        else 0
    )
    report = {
        "schema_version": "1.0.0",
        "artifact_version": ARTIFACT_VERSION,
        "link_policy_version": LINK_POLICY_VERSION,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "complete_cycle_available"
            if complete_count
            else "partial_cycle_only"
            if cycle_rows
            else "no_linked_cycle"
        ),
        "video": {"video_id": video_id, "video_sha256": video_sha256},
        "sources": sources,
        "registry": {
            "registry_version": str(registry.get("registry_version")),
            "indicator_ids": sorted(registry_by_id),
            "required_features_by_indicator": {
                indicator_id: measurement_feature_names(registry_by_id[indicator_id])
                for indicator_id in sorted(registry_by_id)
            },
            "required_scoring_features_by_indicator": {
                indicator_id: list(registry_by_id[indicator_id]["required_features"])
                for indicator_id in sorted(registry_by_id)
            },
            "indicator_ids_by_event_code": {
                code: sorted(expected_by_code[code]) for code in EVENT_SEQUENCE
            },
        },
        "model_versions": model_versions,
        "summary": {
            "registry_indicator_count": len(registry_by_id),
            "linked_cycle_count": len(cycle_rows),
            "complete_cycle_count": int(complete_count),
            "partial_cycle_count": len(cycle_rows) - int(complete_count),
            "best_cycle_measured_indicator_count": best_count,
            "best_cycle_scoring_gate_passed_indicator_count": (
                int(representative_summary["scoring_gate_passed_indicator_count"])
                if representative_summary is not None
                else 0
            ),
            "unmatched_event_count": len(unmatched_event_ids),
            "formal_grade_count": 0,
            "threshold_version_count": 0,
        },
        "link_policy": {
            "event_sequence": list(EVENT_SEQUENCE),
            "invariants": [
                "FS01.initiation_ms_equals_FS02.start_ms",
                "FS02.peak_speed_ms_equals_FS09.peak_speed_ms",
                "FS09.start_ms_inside_FS02_interval",
                "FS09.end_ms_covers_FS02.end_ms",
                "same_person_track_id",
                "same_detector_source_id",
            ],
            "ground_truth_claim": False,
            "semantics": "explainable motion-bout linkage, not an action-label accuracy claim",
        },
        "selection_policy": {
            "ranking": [
                "measured_indicator_count_desc",
                "scoring_gate_passed_indicator_count_desc",
                "minimum_required_feature_confidence_desc",
                "mean_required_feature_confidence_desc",
                "minimum_event_confidence_desc",
                "cycle_start_ms_asc",
                "cycle_id_asc",
            ],
            "feature_values_used_for_selection": False,
            "athletic_performance_used_for_selection": False,
            "cross_cycle_feature_mixing_used": False,
            "cross_model_cherry_picking_used": False,
        },
        "cycles": cycle_rows,
        "representative_cycle": representative,
        "unmatched_event_ids": unmatched_event_ids,
        "safety": {
            "candidate_events_are_ground_truth": False,
            "event_accuracy_claim": False,
            "formal_scoring_ready": False,
            "grade_generated": False,
            "threshold_generated": False,
            "quality_gate_modified": False,
            "missing_value_zero_filled": False,
            "maturity_promoted": False,
        },
    }
    validate_scoring_cycle_measurement(report)
    return report


def validate_scoring_cycle_measurement(report: dict[str, Any]) -> None:
    if (
        report.get("schema_version") != "1.0.0"
        or report.get("artifact_version") != ARTIFACT_VERSION
        or report.get("link_policy_version") != LINK_POLICY_VERSION
        or report.get("selection_policy_version") != SELECTION_POLICY_VERSION
    ):
        raise CycleMeasurementError("unsupported scoring cycle measurement")
    sources = report.get("sources", {})
    if not isinstance(sources, dict) or not REQUIRED_SOURCE_KEYS.issubset(sources):
        raise CycleMeasurementError("cycle source lineage is incomplete")
    for source in sources.values():
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not source.get("path")
            or not isinstance(source.get("sha256"), str)
            or len(source["sha256"]) != 64
            or any(value not in "0123456789ABCDEF" for value in source["sha256"])
        ):
            raise CycleMeasurementError("cycle source lineage is malformed")
    registry_ids = sorted(str(value) for value in report.get("registry", {}).get("indicator_ids", []))
    if not registry_ids or len(registry_ids) != len(set(registry_ids)):
        raise CycleMeasurementError("cycle registry membership is malformed")
    cycles = report.get("cycles", [])
    required_features_by_indicator = report.get("registry", {}).get(
        "required_features_by_indicator", {}
    )
    if (
        not isinstance(required_features_by_indicator, dict)
        or sorted(required_features_by_indicator) != registry_ids
        or any(
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            for values in required_features_by_indicator.values()
        )
    ):
        raise CycleMeasurementError("cycle required feature contracts are malformed")
    cycle_ids = [str(item.get("cycle_id")) for item in cycles]
    if len(cycle_ids) != len(set(cycle_ids)):
        raise CycleMeasurementError("cycle IDs are duplicated")
    complete_count = 0
    best_count = 0
    for cycle in cycles:
        event_codes = [item.get("event_code") for item in cycle.get("events", [])]
        link_evidence = cycle.get("link_evidence", {})
        required_links = (
            "fs01_initiation_equals_fs02_start",
            "fs02_peak_equals_fs09_peak",
            "fs09_starts_inside_fs02",
            "fs09_covers_fs02_end",
            "shared_person_track_id",
            "shared_detector_source_id",
        )
        if event_codes != list(EVENT_SEQUENCE) or any(
            link_evidence.get(name) is not True for name in required_links
        ):
            raise CycleMeasurementError("cycle event linkage differs")
        fs01, fs02, fs09 = cycle["events"]
        fs01_initiation = fs01.get("link_phases_ms", {}).get("initiation_ms")
        fs02_peak = fs02.get("link_phases_ms", {}).get("peak_speed_ms")
        fs09_peak = fs09.get("link_phases_ms", {}).get("peak_speed_ms")
        if (
            fs01_initiation != fs02.get("start_ms")
            or fs01.get("end_ms") != fs02.get("start_ms")
            or fs02_peak is None
            or fs02_peak != fs09_peak
            or fs02_peak != link_evidence.get("fs02_peak_speed_ms")
            or not (
                int(fs02["start_ms"])
                <= int(fs09["start_ms"])
                <= int(fs02["end_ms"])
                <= int(fs09["end_ms"])
            )
        ):
            raise CycleMeasurementError("cycle phase/time evidence differs")
        measured_ids = sorted(str(value) for value in cycle.get("measured_indicator_ids", []))
        unavailable_ids = sorted(str(value) for value in cycle.get("unavailable_indicator_ids", []))
        if (
            sorted(measured_ids + unavailable_ids) != registry_ids
            or len(set(measured_ids).intersection(unavailable_ids)) != 0
            or int(cycle.get("measured_indicator_count", -1)) != len(measured_ids)
            or int(cycle.get("unavailable_indicator_count", -1)) != len(unavailable_ids)
            or int(cycle.get("indicator_count", -1)) != len(registry_ids)
        ):
            raise CycleMeasurementError("cycle indicator accounting differs")
        scoring_gate_passed = sum(
            int(value)
            for status, value in cycle.get("score_status_counts", {}).items()
            if status in {"calibration_required", "scored"}
        )
        if int(cycle.get("scoring_gate_passed_indicator_count", -1)) != scoring_gate_passed:
            raise CycleMeasurementError("cycle scoring gate accounting differs")
        complete = len(measured_ids) == len(registry_ids)
        if cycle.get("complete_registry_measurement") is not complete:
            raise CycleMeasurementError("cycle completeness differs")
        complete_count += complete
        best_count = max(best_count, len(measured_ids))
    summary = report.get("summary", {})
    if (
        int(summary.get("registry_indicator_count", -1)) != len(registry_ids)
        or int(summary.get("linked_cycle_count", -1)) != len(cycles)
        or int(summary.get("complete_cycle_count", -1)) != complete_count
        or int(summary.get("partial_cycle_count", -1)) != len(cycles) - complete_count
        or int(summary.get("best_cycle_measured_indicator_count", -1)) != best_count
        or int(summary.get("best_cycle_scoring_gate_passed_indicator_count", -1))
        != (
            int(min(cycles, key=_cycle_rank)["scoring_gate_passed_indicator_count"])
            if cycles
            else 0
        )
        or int(summary.get("unmatched_event_count", -1)) != len(report.get("unmatched_event_ids", []))
        or int(summary.get("formal_grade_count", -1)) != 0
        or int(summary.get("threshold_version_count", -1)) != 0
    ):
        raise CycleMeasurementError("cycle summary differs")
    expected_status = (
        "complete_cycle_available"
        if complete_count
        else "partial_cycle_only"
        if cycles
        else "no_linked_cycle"
    )
    if report.get("status") != expected_status:
        raise CycleMeasurementError("cycle status differs")
    representative = report.get("representative_cycle")
    if bool(cycles) != isinstance(representative, dict):
        raise CycleMeasurementError("representative cycle presence differs")
    if isinstance(representative, dict):
        selected = min(cycles, key=_cycle_rank)
        if representative.get("cycle_id") != selected.get("cycle_id"):
            raise CycleMeasurementError("representative cycle selection differs")
        measurements = representative.get("indicator_measurements", [])
        if sorted(str(item.get("indicator_id")) for item in measurements) != registry_ids:
            raise CycleMeasurementError("representative cycle indicator membership differs")
        for item in measurements:
            if item.get("grade") is not None or item.get("threshold_version") is not None:
                raise CycleMeasurementError("cycle measurement contains grade or threshold")
            if item.get("feature_status") == "measured":
                if any(
                    feature.get("valid") is not True
                    or feature.get("value") is None
                    or not feature.get("source_frames")
                    for feature in item.get("feature_vector", [])
                ):
                    raise CycleMeasurementError("measured cycle feature is unavailable")
            expected_features = required_features_by_indicator[str(item["indicator_id"])]
            if [
                str(feature.get("feature_name"))
                for feature in item.get("feature_vector", [])
            ] != expected_features:
                raise CycleMeasurementError("cycle feature contract differs")
    for field in (
        "feature_values_used_for_selection",
        "athletic_performance_used_for_selection",
        "cross_cycle_feature_mixing_used",
        "cross_model_cherry_picking_used",
    ):
        if report.get("selection_policy", {}).get(field) is not False:
            raise CycleMeasurementError(f"unsafe cycle selection: {field}")
    for field in (
        "candidate_events_are_ground_truth",
        "event_accuracy_claim",
        "formal_scoring_ready",
        "grade_generated",
        "threshold_generated",
        "quality_gate_modified",
        "missing_value_zero_filled",
        "maturity_promoted",
    ):
        if report.get("safety", {}).get(field) is not False:
            raise CycleMeasurementError(f"unsafe cycle claim: {field}")
