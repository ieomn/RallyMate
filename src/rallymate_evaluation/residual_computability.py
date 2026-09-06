from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from rallymate_features.event_features import FEATURE_DEFINITIONS


AUDIT_VERSION = "residual-indicator-computability-v1.0.0"
GEOMETRY_TOLERANCE_PX = 1.0


class ResidualComputabilityError(ValueError):
    pass


def _frame_source_index(record: dict[str, Any]) -> int:
    frame = record["frame"]
    return int(frame.get("source_frame_index", frame.get("index")))


def _selected_detection(
    record: dict[str, Any], source_track_id: Any
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in record.get("detections", [])
            if item.get("class_name") == "player"
            and item.get("track_id") == source_track_id
        ),
        None,
    )


def _bbox_is_clipped(
    bbox: list[float], width: int, height: int, *, tolerance_px: float
) -> bool:
    x1, y1, x2, y2 = (float(value) for value in bbox)
    return (
        x1 <= tolerance_px
        or y1 <= tolerance_px
        or x2 >= width - tolerance_px
        or y2 >= height - tolerance_px
    )


def _classification(
    *,
    event: dict[str, Any],
    first_timestamp_ms: int,
    source_track_ids: list[Any],
    clipped_bbox_frame_count: int,
) -> tuple[str, str, str]:
    if int(event["start_ms"]) <= first_timestamp_ms:
        return (
            "video_start_boundary_censored",
            "source video has no pre-event frames, so an event-local change/duration cannot be fully observed",
            "a source recording with pre-event lead-in and accepted manual event boundary",
        )
    if len(source_track_ids) > 1:
        return (
            "primary_source_track_transition",
            "the candidate interval crosses more than one source Track identity",
            "accepted identity continuity truth or an accepted manual event interval that does not cross the Track transition",
        )
    if clipped_bbox_frame_count > 0:
        return (
            "primary_bbox_clipped_at_image_boundary",
            "the selected player's detection reaches the image boundary, so missing body joints are outside or truncated by the source view",
            "an in-frame camera view and accepted manual event/keypoint truth; enlarging the existing image cannot recover off-frame joints",
        )
    return (
        "pose_observation_insufficient_without_boundary_or_track_failure",
        "required joint observations remain below their existing feature quality gate",
        "independently adjudicated keypoints and a preregistered alternative Pose/profile evaluation",
    )


def build_residual_computability_audit(
    *,
    comparison: dict[str, Any],
    registry: dict[str, Any],
    frames: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    model_key: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if comparison.get("comparison_semantics", {}).get("accuracy_claim") is not False:
        raise ResidualComputabilityError("fixed-boundary comparison is not a safe input")
    if model_key not in comparison.get("models", {}):
        raise ResidualComputabilityError("requested model is absent from comparison")
    indicators = registry.get("indicators", [])
    indicator_by_id = {str(item["indicator_id"]): item for item in indicators}
    if len(indicator_by_id) != len(indicators) or not indicator_by_id:
        raise ResidualComputabilityError("registry indicator set is empty or duplicated")
    comparison_ids = set(comparison.get("registry_source", {}).get("indicator_ids", []))
    if comparison_ids != set(indicator_by_id):
        raise ResidualComputabilityError("comparison and registry indicator sets differ")
    pair_by_key = {
        (str(item["event_id"]), str(item["feature_name"])): item
        for item in comparison.get("feature_pairs", [])
    }
    if len(pair_by_key) != len(comparison.get("feature_pairs", [])):
        raise ResidualComputabilityError("feature comparison contains duplicate keys")
    timeline_by_processed = {int(item["processed_index"]): item for item in timeline}
    if len(timeline_by_processed) != len(timeline):
        raise ResidualComputabilityError("primary timeline contains duplicate processed indices")
    if not frames:
        raise ResidualComputabilityError("frames are required")
    first_timestamp_ms = min(int(item["frame"]["timestamp_ms"]) for item in frames)
    indicator_rows: dict[str, dict[str, Any]] = {}
    residual_by_event: dict[str, dict[str, Any]] = {}
    total_instances = measured_instances = unavailable_instances = 0

    for indicator_id, registry_item in indicator_by_id.items():
        required_names = [str(value) for value in registry_item["required_features"]]
        definitions = []
        for name in required_names:
            definition = FEATURE_DEFINITIONS.get(name)
            if not isinstance(definition, dict):
                raise ResidualComputabilityError(f"missing feature definition: {name}")
            if not definition.get("unit") or not definition.get("version"):
                raise ResidualComputabilityError(f"incomplete feature definition: {name}")
            definitions.append(
                {
                    "feature_name": name,
                    "unit": str(definition["unit"]),
                    "feature_version": str(definition["version"]),
                }
            )
        indicator_rows[indicator_id] = {
            "indicator_id": indicator_id,
            "feasibility_level": str(registry_item["feasibility_level"]),
            "required_events": list(registry_item["required_events"]),
            "required_features": definitions,
            "instance_count": 0,
            "measured_instance_count": 0,
            "unavailable_instance_count": 0,
            "all_measured_instances_contract_complete": True,
            "measured_event_ids": [],
            "evidence_example": None,
        }

    for wrapper in comparison.get("events", []):
        event = wrapper["boundary"]
        event_id = str(event["event_id"])
        for indicator in wrapper.get("indicators", []):
            indicator_id = str(indicator["indicator_id"])
            if indicator_id not in indicator_rows:
                raise ResidualComputabilityError(f"comparison contains unknown indicator {indicator_id}")
            required_names = [
                item["feature_name"]
                for item in indicator_rows[indicator_id]["required_features"]
            ]
            if list(indicator.get("required_features", [])) != required_names:
                raise ResidualComputabilityError(
                    f"required feature order differs for {event_id}/{indicator_id}"
                )
            model = indicator.get("models", {}).get(model_key, {})
            status = str(model.get("measurement_status", ""))
            if status not in {"measured", "unavailable"}:
                raise ResidualComputabilityError("unexpected indicator measurement status")
            total_instances += 1
            row = indicator_rows[indicator_id]
            row["instance_count"] += 1
            evidence = []
            invalid_features = []
            for name in required_names:
                pair = pair_by_key.get((event_id, name))
                if pair is None:
                    raise ResidualComputabilityError(f"missing feature pair {event_id}/{name}")
                feature = pair.get("models", {}).get(model_key, {})
                definition = FEATURE_DEFINITIONS[name]
                if str(feature.get("unit")) != str(definition["unit"]):
                    raise ResidualComputabilityError(f"feature unit drift {event_id}/{name}")
                if str(feature.get("feature_version")) != str(definition["version"]):
                    raise ResidualComputabilityError(f"feature version drift {event_id}/{name}")
                item = {
                    "feature_name": name,
                    "value": feature.get("value"),
                    "unit": feature.get("unit"),
                    "feature_version": feature.get("feature_version"),
                    "confidence": feature.get("confidence"),
                    "valid": feature.get("valid") is True,
                    "reason": feature.get("reason"),
                    "source_frames": list(feature.get("source_frames", [])),
                }
                evidence.append(item)
                if not item["valid"] or item["value"] is None or not item["source_frames"]:
                    invalid_features.append(item)
            contract_complete = not invalid_features
            if status == "measured" and not contract_complete:
                raise ResidualComputabilityError(
                    f"measured instance lacks valid evidence {event_id}/{indicator_id}"
                )
            if status == "unavailable" and contract_complete:
                raise ResidualComputabilityError(
                    f"unavailable instance has no feature blocker {event_id}/{indicator_id}"
                )
            if status == "measured":
                measured_instances += 1
                row["measured_instance_count"] += 1
                row["measured_event_ids"].append(event_id)
                if row["evidence_example"] is None:
                    row["evidence_example"] = {
                        "event_id": event_id,
                        "event_code": event["event_code"],
                        "start_ms": int(event["start_ms"]),
                        "end_ms": int(event["end_ms"]),
                        "person_track_id": event["person_track_id"],
                        "features": evidence,
                    }
            else:
                unavailable_instances += 1
                row["unavailable_instance_count"] += 1
                residual = residual_by_event.setdefault(
                    event_id,
                    {
                        "event": event,
                        "affected_indicator_ids": [],
                        "invalid_features": {},
                    },
                )
                residual["affected_indicator_ids"].append(indicator_id)
                for item in invalid_features:
                    residual["invalid_features"].setdefault(
                        item["feature_name"], item
                    )

    residual_events = []
    reason_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    for event_id, residual in residual_by_event.items():
        event = residual["event"]
        event_frames = [
            frame
            for frame in frames
            if int(event["start_ms"])
            <= int(frame["frame"]["timestamp_ms"])
            <= int(event["end_ms"])
        ]
        source_track_ids = []
        missing_timeline_track_frames = 0
        clipped_bbox_frames = []
        bbox_long_sides = []
        for frame in event_frames:
            processed_index = int(frame["frame"]["processed_index"])
            timeline_row = timeline_by_processed.get(processed_index)
            if timeline_row is None:
                raise ResidualComputabilityError(
                    f"timeline misses event frame {processed_index}"
                )
            source_track_id = timeline_row.get("source_track_id")
            if source_track_id is None:
                missing_timeline_track_frames += 1
                continue
            if source_track_id not in source_track_ids:
                source_track_ids.append(source_track_id)
            detection = _selected_detection(frame, source_track_id)
            if detection is None:
                continue
            bbox = [float(value) for value in detection["bbox_px"]]
            bbox_long_sides.append(max(bbox[2] - bbox[0], bbox[3] - bbox[1]))
            if _bbox_is_clipped(
                bbox,
                int(frame["frame"]["width"]),
                int(frame["frame"]["height"]),
                tolerance_px=GEOMETRY_TOLERANCE_PX,
            ):
                clipped_bbox_frames.append(_frame_source_index(frame))
        classification, explanation, required_next_evidence = _classification(
            event=event,
            first_timestamp_ms=first_timestamp_ms,
            source_track_ids=source_track_ids,
            clipped_bbox_frame_count=len(clipped_bbox_frames),
        )
        classification_counts[classification] += len(residual["affected_indicator_ids"])
        for item in residual["invalid_features"].values():
            reason_counts[str(item["reason"])] += 1
        residual_events.append(
            {
                "event_id": event_id,
                "event_code": event["event_code"],
                "person_track_id": event["person_track_id"],
                "start_ms": int(event["start_ms"]),
                "end_ms": int(event["end_ms"]),
                "affected_indicator_ids": sorted(residual["affected_indicator_ids"]),
                "affected_indicator_instance_count": len(
                    residual["affected_indicator_ids"]
                ),
                "invalid_features": sorted(
                    residual["invalid_features"].values(),
                    key=lambda item: item["feature_name"],
                ),
                "observation_diagnostics": {
                    "event_frame_count": len(event_frames),
                    "source_track_ids": source_track_ids,
                    "source_track_transition_count": max(0, len(source_track_ids) - 1),
                    "missing_timeline_track_frame_count": missing_timeline_track_frames,
                    "bbox_observed_frame_count": len(bbox_long_sides),
                    "bbox_clipped_frame_count": len(clipped_bbox_frames),
                    "bbox_clipped_source_frames": clipped_bbox_frames,
                    "bbox_long_side_median_px": (
                        round(float(statistics.median(bbox_long_sides)), 6)
                        if bbox_long_sides
                        else None
                    ),
                    "geometry_tolerance_px": GEOMETRY_TOLERANCE_PX,
                    "geometry_tolerance_is_not_scoring_threshold": True,
                },
                "classification": classification,
                "explanation": explanation,
                "required_next_evidence": required_next_evidence,
                "recoverable_by_zero_fill": False,
                "recoverable_by_lowering_existing_feature_quality_gate": False,
                "current_decision": "keep_unavailable",
            }
        )
    per_indicator = [indicator_rows[key] for key in sorted(indicator_rows)]
    indicators_without_measured = [
        row["indicator_id"] for row in per_indicator if row["measured_instance_count"] <= 0
    ]
    report = {
        "schema_version": "1.0.0",
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "all_registry_indicators_have_real_measured_instances_residuals_fail_closed"
            if not indicators_without_measured
            else "indicator_computability_incomplete"
        ),
        "sources": sources,
        "scope": {
            "model_key": model_key,
            "registry_version": registry.get("registry_version"),
            "indicator_ids": sorted(indicator_by_id),
            "fixed_candidate_event_count": len(comparison.get("events", [])),
            "event_boundaries_are_ground_truth": False,
        },
        "counts": {
            "registry_indicator_count": len(indicator_by_id),
            "indicator_with_measured_instance_count": len(indicator_by_id)
            - len(indicators_without_measured),
            "indicator_without_measured_instance_count": len(indicators_without_measured),
            "indicator_event_instance_count": total_instances,
            "measured_indicator_event_instance_count": measured_instances,
            "unavailable_indicator_event_instance_count": unavailable_instances,
            "residual_event_count": len(residual_events),
        },
        "classification_counts_by_indicator_instance": dict(
            sorted(classification_counts.items())
        ),
        "invalid_feature_reason_counts_by_unique_event_feature": dict(
            sorted(reason_counts.items())
        ),
        "per_indicator": per_indicator,
        "residual_events": sorted(
            residual_events, key=lambda item: (item["start_ms"], item["event_id"])
        ),
        "indicators_without_measured_instances": indicators_without_measured,
        "interpretation": {
            "measured_means": "all registry-required features for at least one real candidate event have non-null values, declared units/versions, confidence, and source frames",
            "measured_does_not_mean": "event accuracy, feature truth accuracy, coach grade, or production scoring readiness",
            "unavailable_means": "the current source evidence is insufficient and the system intentionally does not fabricate a value",
        },
        "safety": {
            "accuracy_claim": False,
            "event_ground_truth_provided": False,
            "keypoint_ground_truth_provided": False,
            "feature_quality_gate_modified": False,
            "zero_fill_used": False,
            "cross_model_cherry_picking_used": False,
            "production_route_changed": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    validate_residual_computability_audit(report)
    return report


def validate_residual_computability_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get(
        "audit_version"
    ) != AUDIT_VERSION:
        raise ResidualComputabilityError("unsupported residual computability audit")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "event_ground_truth_provided",
        "keypoint_ground_truth_provided",
        "feature_quality_gate_modified",
        "zero_fill_used",
        "cross_model_cherry_picking_used",
        "production_route_changed",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ResidualComputabilityError(f"unsafe audit claim: {field}")
    counts = report.get("counts", {})
    total = int(counts.get("indicator_event_instance_count", -1))
    measured = int(counts.get("measured_indicator_event_instance_count", -1))
    unavailable = int(counts.get("unavailable_indicator_event_instance_count", -1))
    if total <= 0 or measured + unavailable != total:
        raise ResidualComputabilityError("indicator instance accounting is inconsistent")
    indicators = report.get("per_indicator", [])
    if len(indicators) != int(counts.get("registry_indicator_count", -1)):
        raise ResidualComputabilityError("indicator accounting is inconsistent")
    with_measured = sum(int(item.get("measured_instance_count", 0)) > 0 for item in indicators)
    if with_measured != int(counts.get("indicator_with_measured_instance_count", -1)):
        raise ResidualComputabilityError("measured indicator count is inconsistent")
    if report.get("status") == "all_registry_indicators_have_real_measured_instances_residuals_fail_closed":
        if with_measured != len(indicators) or report.get(
            "indicators_without_measured_instances"
        ):
            raise ResidualComputabilityError("complete status overstates indicator coverage")
    if sum(
        int(item.get("affected_indicator_instance_count", 0))
        for item in report.get("residual_events", [])
    ) != unavailable:
        raise ResidualComputabilityError("residual event accounting is inconsistent")
    for item in report.get("residual_events", []):
        if item.get("current_decision") != "keep_unavailable":
            raise ResidualComputabilityError("residual audit silently recovers an unsafe value")
        if item.get("recoverable_by_zero_fill") is not False or item.get(
            "recoverable_by_lowering_existing_feature_quality_gate"
        ) is not False:
            raise ResidualComputabilityError("residual audit weakens a quality invariant")
