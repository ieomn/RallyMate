from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_evaluation.event_bounded_pose_gaps import (
    interpolate_event_bounded_pose_gaps,
    validate_event_bounded_pose_gap_report_sources,
)
from rallymate_evaluation.event_gap_keypoint_truth import (
    EventGapTruthError,
    evaluate_event_gap_keypoints,
)
from rallymate_evaluation.ground_truth import validate_keypoint_annotation
from rallymate_evaluation.small_roi_keypoint_truth import _read_jsonl, _source, sha256_file
from rallymate_features import (
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)
from rallymate_features.schemas import PoseSequence


REPORT_VERSION = "event-gap-truth-conditioned-feature-error-v1.0.0"


class EventGapFeatureTruthError(ValueError):
    pass


def _event(record: dict[str, Any]) -> EventInterval:
    return EventInterval(
        event_id=str(record["event_id"]),
        event_code=str(record["event_code"]),
        start_ms=int(record["start_ms"]),
        end_ms=int(record["end_ms"]),
        person_track_id=int(record["person_track_id"]),
        key_phases=record.get("key_phases"),
    )


def _snapshot(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_name": result["feature_name"],
        "feature_version": result["feature_version"],
        "value": result["value"],
        "unit": result["unit"],
        "confidence": result["confidence"],
        "valid": bool(result["valid"]),
        "reason": result["reason"],
        "source_frames": result["source_frames"],
    }


def _same_interpolated_points(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    fields = (
        "joint_name",
        "timestamp_ms",
        "source_frame",
        "x_normalized",
        "y_normalized",
        "effective_confidence",
        "left_boundary_timestamp_ms",
        "left_boundary_source_frame",
        "left_boundary_confidence",
        "right_boundary_timestamp_ms",
        "right_boundary_source_frame",
        "right_boundary_confidence",
        "bounding_observation_span_ms",
    )
    normalize = lambda rows: sorted(
        [{name: row[name] for name in fields} for row in rows],
        key=lambda row: (row["joint_name"], row["source_frame"]),
    )
    return normalize(left) == normalize(right)


def apply_adjudicated_gap_truth(
    interpolated_sequence: PoseSequence,
    *,
    video_id: str,
    interpolated_points: list[dict[str, Any]],
    truth_by_point: dict[tuple[str, int, str], dict[str, Any]],
) -> tuple[PoseSequence, dict[str, Any]]:
    """Replace only M74 gap candidates with adjudicated coordinates.

    Visible truth keeps the candidate's bounded-endpoint confidence so that the
    feature comparison isolates coordinate/visibility error instead of changing
    smoothing weights.  Invisible truth restores NaN coordinates/confidence.
    The returned sequence is evaluation-only and is never a model Pose artifact.
    """

    frame_to_index = {
        int(frame): index for index, frame in enumerate(interpolated_sequence.source_frames)
    }
    xy = {
        name: np.asarray(values, dtype=np.float64).copy()
        for name, values in interpolated_sequence.keypoints_xy.items()
    }
    confidence = {
        name: np.asarray(values, dtype=np.float64).copy()
        for name, values in interpolated_sequence.confidence.items()
    }
    visible_count = invisible_count = 0
    source_frames: set[int] = set()
    for point in interpolated_points:
        frame = int(point["source_frame"])
        joint = str(point["joint_name"])
        key = (str(video_id), frame, joint)
        if key not in truth_by_point:
            raise EventGapFeatureTruthError(f"missing adjudicated gap truth: {key}")
        if frame not in frame_to_index or joint not in xy or joint not in confidence:
            raise EventGapFeatureTruthError(f"gap truth is outside the Pose sequence: {key}")
        index = frame_to_index[frame]
        value = truth_by_point[key]
        if value["visible"] is True:
            x = value.get("x_normalized")
            y = value.get("y_normalized")
            if not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in (x, y)):
                raise EventGapFeatureTruthError(f"visible gap truth lacks coordinates: {key}")
            if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
                raise EventGapFeatureTruthError(f"visible gap truth is outside normalized bounds: {key}")
            candidate_confidence = float(confidence[joint][index])
            if not math.isfinite(candidate_confidence):
                raise EventGapFeatureTruthError(f"candidate confidence is absent at gap point: {key}")
            xy[joint][index] = [float(x), float(y)]
            confidence[joint][index] = candidate_confidence
            visible_count += 1
        else:
            xy[joint][index] = [math.nan, math.nan]
            confidence[joint][index] = math.nan
            invisible_count += 1
        source_frames.add(frame)
    return replace(interpolated_sequence, keypoints_xy=xy, confidence=confidence), {
        "applied_joint_observation_count": visible_count + invisible_count,
        "visible_truth_count": visible_count,
        "invisible_truth_count": invisible_count,
        "source_frames": sorted(source_frames),
        "confidence_policy": (
            "freeze_candidate_bounded_endpoint_confidence_for_coordinate_error_isolation;"
            "not_manual_truth_confidence_or_model_accuracy"
        ),
        "evaluation_only": True,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    truth_valid = [row for row in rows if row["truth_conditioned"]["valid"]]
    comparable = [
        row
        for row in truth_valid
        if row["candidate"]["valid"]
        and isinstance(row["candidate"]["value"], (int, float))
        and isinstance(row["truth_conditioned"]["value"], (int, float))
        and math.isfinite(float(row["candidate"]["value"]))
        and math.isfinite(float(row["truth_conditioned"]["value"]))
    ]
    numeric = [row for row in comparable if row["candidate"]["unit"] != "code"]
    codes = [row for row in comparable if row["candidate"]["unit"] == "code"]
    errors = np.asarray([row["signed_error"] for row in numeric], dtype=np.float64)
    agreements = [bool(row["code_agreement"]) for row in codes]
    return {
        "feature_record_count": len(rows),
        "truth_valid_count": len(truth_valid),
        "candidate_valid_count": sum(row["candidate"]["valid"] for row in rows),
        "comparable_count": len(comparable),
        "comparison_valid_rate": round(len(comparable) / len(truth_valid), 8) if truth_valid else None,
        "numeric": {
            "count": len(numeric),
            "mae": round(float(np.mean(np.abs(errors))), 8) if errors.size else None,
            "p95_absolute_error": round(float(np.quantile(np.abs(errors), 0.95)), 8) if errors.size else None,
            "bias_candidate_minus_truth": round(float(np.mean(errors)), 8) if errors.size else None,
        },
        "categorical_code": {
            "count": len(codes),
            "agreement_count": sum(agreements),
            "agreement_rate": round(sum(agreements) / len(agreements), 8) if agreements else None,
        },
    }


def _numeric_signed_error(
    feature_name: str, candidate_value: float, truth_value: float
) -> tuple[float, str]:
    if feature_name == "launch_direction_deg":
        return ((candidate_value - truth_value + 180.0) % 360.0) - 180.0, (
            "wrapped_candidate_minus_truth_deg"
        )
    return candidate_value - truth_value, "candidate_minus_truth"


def _load_truth(pack_dir: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    truth: dict[tuple[str, int, str], dict[str, Any]] = {}
    for record in _read_jsonl(pack_dir / "compiled" / "manual-keypoints.jsonl"):
        validate_keypoint_annotation(record)
        for joint, value in record["joints"].items():
            key = (str(record["video_id"]), int(record["source_frame_index"]), str(joint))
            if key in truth:
                raise EventGapFeatureTruthError(f"duplicate M76 truth point: {key}")
            truth[key] = value
    return truth


def evaluate_event_gap_feature_truth(pack_dir: str | Path) -> dict[str, Any]:
    pack_dir = Path(pack_dir).resolve()
    keypoint_report = evaluate_event_gap_keypoints(pack_dir)
    manifest_path = pack_dir / "manifest.json"
    validation_path = pack_dir / "compiled" / "validation-report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    m74_path = Path(manifest["sources"]["m74_report"]["path"]).resolve()
    m74 = json.loads(m74_path.read_text(encoding="utf-8"))
    validate_event_bounded_pose_gap_report_sources(m74)
    interpolation_items = [
        item for item in m74["items"] if item["gap_audit"]["interpolated_points"]
    ]
    feature_record_count = sum(len(item["required_features"]) for item in interpolation_items)
    unique_features = sorted(
        {feature for item in interpolation_items for feature in item["required_features"]}
    )
    if len(interpolation_items) != 11 or feature_record_count != 47 or len(unique_features) != 25:
        raise EventGapFeatureTruthError("M76 feature replay scope drifted")
    registry_path = Path(m74["sources"]["registry"]["path"]).resolve()
    base = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "annotation_required",
        "scope": {
            "interpolation_candidate_joint_tasks": 142,
            "indicator_instances_with_interpolation_points": len(interpolation_items),
            "required_feature_records": feature_record_count,
            "unique_required_features": len(unique_features),
            "candidate_complete_indicator_instances": sum(
                bool(item["counterfactual_feature_vector_complete"])
                for item in interpolation_items
            ),
            "phase_only_residual_indicator_instances_excluded": sum(
                not item["gap_audit"]["interpolated_points"] for item in m74["items"]
            ),
            "feature_names": unique_features,
            "comparison_semantics": (
                "M74 interpolated feature versus adjudicated-gap-coordinate-conditioned feature;"
                "non-gap Pose and event boundaries remain model candidates"
            ),
        },
        "sources": {
            "m75_manifest": _source(manifest_path),
            "m75_validation": _source(validation_path),
            "m74_report": _source(m74_path),
            "registry": _source(registry_path),
            "per_video": m74["sources"]["per_video"],
        },
        "m75_keypoint_evaluation": {
            "status": keypoint_report["status"],
            "pack": keypoint_report["pack"],
            "counts": keypoint_report["counts"],
        },
        "counts": {
            "feature_records": 0,
            "candidate_complete_indicator_instances": 0,
            "truth_conditioned_complete_indicator_instances": 0,
            "visible_gap_truth_references": 0,
            "invisible_gap_truth_references": 0,
        },
        "metrics": None,
        "per_feature": None,
        "per_indicator": None,
        "details": [],
        "acceptance": {
            "status": "external_preregistered_protocol_required",
            "thresholds": None,
            "production_interpolation_allowed": False,
            "f2_to_f3_allowed": False,
        },
        "safety": {
            "full_adjudicated_gap_truth_provided": False,
            "metrics_are_total_pose_feature_error": False,
            "metrics_are_event_detector_accuracy": False,
            "non_gap_model_pose_replaced_by_truth": False,
            "event_boundaries_replaced_by_truth": False,
            "candidate_confidence_relabelled_as_truth_confidence": False,
            "production_pose_artifact_generated": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    if keypoint_report["status"] == "annotation_required":
        validate_event_gap_feature_truth_report(base)
        return base
    if keypoint_report["status"] != "evaluated_external_acceptance_protocol_required":
        raise EventGapFeatureTruthError("M75 keypoint evaluation is not eligible for M76")

    truth = _load_truth(pack_dir)
    sequences: dict[str, PoseSequence] = {}
    events: dict[str, dict[str, dict[str, Any]]] = {}
    for source in m74["sources"]["per_video"]:
        video_id = str(source["video_id"])
        sequences[video_id] = pose_sequence_from_records(
            _read_jsonl(source["frames"]["path"]),
            _read_jsonl(source["primary_timeline"]["path"]),
        )
        events[video_id] = {
            str(row["event_id"]): row for row in _read_jsonl(source["events"]["path"])
        }

    details: list[dict[str, Any]] = []
    indicator_summaries: list[dict[str, Any]] = []
    visible_references = invisible_references = 0
    for item in interpolation_items:
        video_id = str(item["video_id"])
        event = _event(events[video_id][str(item["event_id"])])
        sequence = sequences[video_id]
        candidate_sequence, replay = interpolate_event_bounded_pose_gaps(
            sequence, event, item["joint_scope"], max_gap_ms=160
        )
        if not _same_interpolated_points(
            replay["interpolated_points"], item["gap_audit"]["interpolated_points"]
        ):
            raise EventGapFeatureTruthError("current interpolation no longer replays M74")
        truth_sequence, application = apply_adjudicated_gap_truth(
            candidate_sequence,
            video_id=video_id,
            interpolated_points=replay["interpolated_points"],
            truth_by_point=truth,
        )
        visible_references += int(application["visible_truth_count"])
        invisible_references += int(application["invisible_truth_count"])
        required = list(item["required_features"])
        candidate_results = {
            result.feature_name: result.to_dict()
            for result in compute_event_features(candidate_sequence, event, required)
        }
        truth_results = {
            result.feature_name: result.to_dict()
            for result in compute_event_features(truth_sequence, event, required)
        }
        candidate_complete = all(candidate_results[name]["valid"] for name in required)
        if candidate_complete != bool(item["counterfactual_feature_vector_complete"]):
            raise EventGapFeatureTruthError("current feature code no longer replays M74")
        truth_complete = all(truth_results[name]["valid"] for name in required)
        indicator_summaries.append(
            {
                "video_id": video_id,
                "event_id": event.event_id,
                "indicator_id": item["indicator_id"],
                "candidate_feature_vector_complete": candidate_complete,
                "truth_conditioned_feature_vector_complete": truth_complete,
                "truth_application": application,
            }
        )
        for name in required:
            candidate = _snapshot(candidate_results[name])
            conditioned = _snapshot(truth_results[name])
            if (
                candidate["feature_version"] != conditioned["feature_version"]
                or candidate["unit"] != conditioned["unit"]
            ):
                raise EventGapFeatureTruthError("candidate/truth feature contract differs")
            comparable = (
                candidate["valid"]
                and conditioned["valid"]
                and isinstance(candidate["value"], (int, float))
                and isinstance(conditioned["value"], (int, float))
                and math.isfinite(float(candidate["value"]))
                and math.isfinite(float(conditioned["value"]))
            )
            is_code = candidate["unit"] == "code"
            signed_error_raw, error_semantics = (
                _numeric_signed_error(
                    name, float(candidate["value"]), float(conditioned["value"])
                )
                if comparable and not is_code
                else (None, "categorical_exact_agreement" if is_code else None)
            )
            signed_error = (
                round(float(signed_error_raw), 8)
                if signed_error_raw is not None
                else None
            )
            details.append(
                {
                    "video_id": video_id,
                    "event_id": event.event_id,
                    "event_code": event.event_code,
                    "indicator_id": item["indicator_id"],
                    "feature_name": name,
                    "candidate": candidate,
                    "truth_conditioned": conditioned,
                    "comparison_status": (
                        "comparable_categorical_code"
                        if comparable and is_code
                        else "comparable_numeric"
                        if comparable
                        else "truth_conditioned_invalid"
                        if not conditioned["valid"]
                        else "candidate_invalid"
                    ),
                    "signed_error": signed_error,
                    "absolute_error": abs(signed_error) if signed_error is not None else None,
                    "error_semantics": error_semantics,
                    "code_agreement": (
                        bool(candidate["value"] == conditioned["value"])
                        if comparable and is_code
                        else None
                    ),
                    "truth_task_ids": [
                        f"m75:{video_id}:{event.event_id}:{point['source_frame']}:{point['joint_name']}"
                        for point in replay["interpolated_points"]
                    ],
                }
            )
        clear_feature_cache(candidate_sequence)
        clear_feature_cache(truth_sequence)

    by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_feature[row["feature_name"]].append(row)
        by_indicator[row["indicator_id"]].append(row)
    base.update(
        {
            "status": "evaluated_external_acceptance_protocol_required",
            "counts": {
                "feature_records": len(details),
                "candidate_complete_indicator_instances": sum(
                    row["candidate_feature_vector_complete"] for row in indicator_summaries
                ),
                "truth_conditioned_complete_indicator_instances": sum(
                    row["truth_conditioned_feature_vector_complete"]
                    for row in indicator_summaries
                ),
                "visible_gap_truth_references": visible_references,
                "invisible_gap_truth_references": invisible_references,
            },
            "metrics": _aggregate(details),
            "per_feature": {
                name: _aggregate(rows) for name, rows in sorted(by_feature.items())
            },
            "per_indicator": {
                name: _aggregate(rows) for name, rows in sorted(by_indicator.items())
            },
            "details": details,
            "indicator_instances": indicator_summaries,
            "safety": {
                **base["safety"],
                "full_adjudicated_gap_truth_provided": True,
            },
        }
    )
    validate_event_gap_feature_truth_report(base)
    return base


def validate_event_gap_feature_truth_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise EventGapFeatureTruthError("unsupported M76 report contract")
    if report.get("status") not in {
        "annotation_required",
        "evaluated_external_acceptance_protocol_required",
    }:
        raise EventGapFeatureTruthError("unsupported M76 report status")
    scope = report.get("scope", {})
    if (
        scope.get("interpolation_candidate_joint_tasks") != 142
        or scope.get("indicator_instances_with_interpolation_points") != 11
        or scope.get("required_feature_records") != 47
        or scope.get("unique_required_features") != 25
        or scope.get("phase_only_residual_indicator_instances_excluded") != 2
    ):
        raise EventGapFeatureTruthError("M76 report scope drifted")
    feature_names = scope.get("feature_names")
    if (
        not isinstance(feature_names, list)
        or len(feature_names) != 25
        or len(set(feature_names)) != 25
        or not all(isinstance(name, str) and name for name in feature_names)
    ):
        raise EventGapFeatureTruthError("M76 feature-name scope drifted")
    acceptance = report.get("acceptance", {})
    if (
        acceptance.get("thresholds") is not None
        or acceptance.get("production_interpolation_allowed") is not False
        or acceptance.get("f2_to_f3_allowed") is not False
    ):
        raise EventGapFeatureTruthError("M76 cannot approve interpolation or maturity")
    safety = report.get("safety", {})
    for field in (
        "metrics_are_total_pose_feature_error",
        "metrics_are_event_detector_accuracy",
        "non_gap_model_pose_replaced_by_truth",
        "event_boundaries_replaced_by_truth",
        "candidate_confidence_relabelled_as_truth_confidence",
        "production_pose_artifact_generated",
        "production_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise EventGapFeatureTruthError(f"unsafe M76 report field: {field}")
    counts = report.get("counts", {})
    if report["status"] == "annotation_required":
        if (
            report.get("metrics") is not None
            or report.get("per_feature") is not None
            or report.get("per_indicator") is not None
            or report.get("details") != []
            or "indicator_instances" in report
        ):
            raise EventGapFeatureTruthError("M76 cannot expose metrics without truth")
        if any(int(value) != 0 for value in counts.values()):
            raise EventGapFeatureTruthError("empty M76 report contains evaluated counts")
        if safety.get("full_adjudicated_gap_truth_provided") is not False:
            raise EventGapFeatureTruthError("empty M76 report claims truth")
    else:
        details = report.get("details", [])
        instances = report.get("indicator_instances", [])
        if int(counts.get("feature_records", -1)) != 47 or len(details) != 47:
            raise EventGapFeatureTruthError("evaluated M76 report must contain 47 feature rows")
        if report.get("metrics") is None or safety.get("full_adjudicated_gap_truth_provided") is not True:
            raise EventGapFeatureTruthError("evaluated M76 report lacks truth metrics")
        if len(instances) != 11:
            raise EventGapFeatureTruthError("evaluated M76 report must contain 11 indicator instances")
        if int(counts.get("candidate_complete_indicator_instances", -1)) != sum(
            bool(item.get("candidate_feature_vector_complete")) for item in instances
        ):
            raise EventGapFeatureTruthError("M76 candidate completeness count drifted")
        if int(counts.get("truth_conditioned_complete_indicator_instances", -1)) != sum(
            bool(item.get("truth_conditioned_feature_vector_complete")) for item in instances
        ):
            raise EventGapFeatureTruthError("M76 truth-conditioned completeness count drifted")
        for row in details:
            if set(row) != {
                "video_id", "event_id", "event_code", "indicator_id", "feature_name",
                "candidate", "truth_conditioned", "comparison_status", "signed_error",
                "absolute_error", "error_semantics", "code_agreement", "truth_task_ids",
            }:
                raise EventGapFeatureTruthError("M76 detail contract drifted")
            candidate = row["candidate"]
            conditioned = row["truth_conditioned"]
            if (
                row["feature_name"] not in feature_names
                or candidate.get("feature_name") != row["feature_name"]
                or conditioned.get("feature_name") != row["feature_name"]
                or candidate.get("feature_version") != conditioned.get("feature_version")
                or candidate.get("unit") != conditioned.get("unit")
            ):
                raise EventGapFeatureTruthError("M76 detail feature provenance drifted")
            if not isinstance(row.get("truth_task_ids"), list) or not row["truth_task_ids"]:
                raise EventGapFeatureTruthError("M76 detail lacks truth task lineage")
        expected_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
        expected_by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in details:
            expected_by_feature[row["feature_name"]].append(row)
            expected_by_indicator[row["indicator_id"]].append(row)
        expected_feature_metrics = {
            name: _aggregate(rows) for name, rows in sorted(expected_by_feature.items())
        }
        expected_indicator_metrics = {
            name: _aggregate(rows) for name, rows in sorted(expected_by_indicator.items())
        }
        if report.get("metrics") != _aggregate(details):
            raise EventGapFeatureTruthError("M76 aggregate metrics drifted")
        if report.get("per_feature") != expected_feature_metrics:
            raise EventGapFeatureTruthError("M76 per-feature metrics drifted")
        if report.get("per_indicator") != expected_indicator_metrics:
            raise EventGapFeatureTruthError("M76 per-indicator metrics drifted")


def validate_event_gap_feature_truth_report_sources(report: dict[str, Any]) -> None:
    validate_event_gap_feature_truth_report(report)
    records = [
        report.get("sources", {}).get(name)
        for name in ("m75_manifest", "m75_validation", "m74_report", "registry")
    ]
    records.extend(
        item.get(name)
        for item in report.get("sources", {}).get("per_video", [])
        for name in ("m73_report", "frames", "primary_timeline", "events")
    )
    if not records or any(not isinstance(item, dict) for item in records):
        raise EventGapFeatureTruthError("M76 source bindings are incomplete")
    for record in records:
        path = Path(str(record.get("path", ""))).resolve()
        if not path.is_file() or sha256_file(path) != str(record.get("sha256", "")).upper():
            raise EventGapFeatureTruthError(f"M76 source SHA mismatch: {path}")
    pack_dir = Path(report["sources"]["m75_manifest"]["path"]).resolve().parent
    replay = evaluate_event_gap_feature_truth(pack_dir)
    for name in (
        "status", "scope", "counts", "metrics", "per_feature", "per_indicator",
        "details", "indicator_instances", "acceptance", "safety",
    ):
        if replay.get(name) != report.get(name):
            raise EventGapFeatureTruthError(f"M76 source replay mismatch: {name}")


def report_sha256(report: dict[str, Any]) -> str:
    encoded = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()
