"""Bounded hip path geometry in a stable image reference; never an FS05 event.

Pass an exact external window from ``pose_sequence_from_records`` and its
primary timeline. No window discovery, interpolation, smoothing or scoring is
performed. Bindings validate the supplied provenance, not anatomical accuracy
or independent truth of a caller's original-sampling declaration.
"""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Any, Sequence

import numpy as np

from .coordinates import COORDINATE_CONTRACT_VERSION, point_series, validated_camera_transform
from .schemas import MODEL_MEASUREMENT_PROVENANCE, PoseSequence, validated_source_time_binding


VERSION = "bounded-hip-path-ratio-v1.0.0"
SAMPLING_PROTOCOL = "original_source_frames"
HIPS = ("left_hip", "right_hip")
# Existing observation cutoff, not a Word validity or technical-grade threshold.
CONFIDENCE_MIN = .35


def _exact_ms(binding: dict) -> Fraction:
    return Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"])


def _rational(value: Fraction) -> dict:
    return {"numerator": value.numerator, "denominator": value.denominator}


def compute_hip_path_ratio(
    sequence: PoseSequence, *, primary_timeline: Sequence[dict[str, Any]],
    start_source_frame: int, end_source_frame: int, sampling_protocol: str,
) -> dict[str, Any]:
    """Measure only the supplied complete, consecutive original-frame window.

    Coordinates are already camera-compensated by the shared loader. Only its
    isotropic long-edge normalization is undone, once. Missing or conflicting
    evidence rejects the entire requested window rather than joining pieces.
    Zero net displacement preserves L and D but leaves the ratio unavailable.
    The shared record loader must establish original SAM ledger, anatomical
    mapping and in-frame evidence; PoseSequence cannot reauthenticate these.
    """
    result: dict[str, Any] = {
        "version": VERSION, "formula_version": VERSION,
        "status": "unavailable", "reason": None,
        "assessment_status": "measurement_only", "scoring_allowed": False,
        "formal_event_verified": False, "score": None, "grade": None,
        "scope": "explicit_external_observation_window_not_FS05",
        "coordinate_system": "stable_camera_reference_image_pixels",
        "source_refs": ["CARD-FS:word/document.xml:P0827"],
        "formula": "c_i=(left_hip_i+right_hip_i)/2; L=sum(norm(c_i-c_(i-1))); D=norm(c_last-c_first); ratio=L/D",
        "hip_path_length_px": None, "hip_net_displacement_px": None,
        "hip_path_over_net_ratio": None, "source_frames": [], "source_bindings": [],
        "requested_window": {"start_source_frame": start_source_frame, "end_source_frame": end_source_frame},
        "sampling_protocol": sampling_protocol,
        "limitations": [
            "Discrete image-plane path only; model jitter and sampling rate affect length.",
            "No step count, body-height normalization, target direction, FS05 boundary or grade is inferred.",
            "The source 70% ankle validity and complete A-E predicates are not established by hip geometry.",
            "The ratio becomes sensitive to endpoint uncertainty as net displacement approaches zero; no physical small-motion threshold or cap is applied.",
        ],
    }

    def reject(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if (type(start_source_frame) is not int or type(end_source_frame) is not int
            or start_source_frame < 0 or end_source_frame <= start_source_frame):
        return reject("invalid_external_window")
    if sampling_protocol != SAMPLING_PROTOCOL:
        return reject("original_source_sampling_not_declared")
    timestamps = np.asarray(sequence.timestamp_ms)
    if timestamps.ndim != 1 or timestamps.dtype.kind not in "fiu" or not np.isfinite(timestamps).all():
        return reject("invalid_video_time_array")
    count = len(timestamps)
    frames = np.asarray(sequence.source_frames)
    if (frames.dtype.kind not in "iu" or frames.shape != (count,)
            or count != end_source_frame - start_source_frame + 1
            or not np.array_equal(frames, np.arange(start_source_frame, end_source_frame + 1))):
        return reject("window_not_complete_consecutive_source_frames")
    result["source_frames"] = [int(frame) for frame in frames]
    bindings = [validated_source_time_binding(sequence.source_time_bindings.get(int(frame))) for frame in frames]
    if any(binding is None for binding in bindings):
        return reject("source_time_binding_unavailable")
    result["source_bindings"] = bindings
    times = [_exact_ms(binding) for binding in bindings]
    result["source_timestamps_ms_exact"] = [_rational(time) for time in times]
    origin_lower = origin_upper = None
    for i, binding in enumerate(bindings):
        if (binding["frame_index"] != int(frames[i])
                or binding["video_timestamp_ms"] != sequence.timestamp_ms[i]
                or binding["primary_player_id"] != sequence.primary_player_id):
            return reject("source_time_binding_mismatch")
        offset = times[i] - binding["video_timestamp_ms"]
        lower, upper = offset - Fraction(51, 100), offset + Fraction(51, 100)
        origin_lower = lower if origin_lower is None else max(lower, origin_lower)
        origin_upper = upper if origin_upper is None else min(upper, origin_upper)
        if origin_lower > origin_upper:
            return reject("source_video_clock_origin_changed")
        if i:
            previous = bindings[i - 1]
            for keys, reason in (
                (("video_sha256",), "video_identity_changed"),
                (("primary_player_id", "person_track_id", "selection_epoch"), "subject_identity_changed"),
                (("camera_reference_epoch",), "camera_reference_changed"),
                (("time_base_num", "time_base_den"), "source_timebase_changed"),
            ):
                if any(previous[key] != binding[key] for key in keys):
                    return reject(reason)
            if binding["processed_index"] != previous["processed_index"] + 1:
                return reject("nonadjacent_processed_indexes")
            if times[i] <= times[i - 1]:
                return reject("source_pts_not_increasing")
            if binding["video_timestamp_ms"] <= previous["video_timestamp_ms"]:
                return reject("video_time_not_increasing")

    # Require independent selection records, not merely self-consistent poses.
    if not isinstance(primary_timeline, (list, tuple)) or len(primary_timeline) != count:
        return reject("primary_timeline_window_mismatch")
    field_map = {"source_frame_index": "frame_index", "processed_index": "processed_index",
                 "timestamp_ms": "video_timestamp_ms", "primary_player_id": "primary_player_id",
                 "source_track_id": "person_track_id", "selection_epoch": "selection_epoch",
                 "camera_reference_epoch": "camera_reference_epoch"}
    for row, binding in zip(primary_timeline, bindings):
        if not isinstance(row, dict) or row.get("selection_status") != "selected" or row.get("identity_ambiguous") is not False:
            return reject("primary_identity_unverified")
        if any(type(row.get(key)) is not int or row[key] != binding[target] for key, target in field_map.items()):
            return reject("primary_timeline_binding_mismatch")
        for key in ("pts", "time_base_num", "time_base_den", "source_timestamp_ms", "video_sha256"):
            if key in row:
                value = row[key].lower() if key == "video_sha256" and isinstance(row[key], str) else row[key]
                if isinstance(value, bool) or value != binding[key]:
                    return reject("primary_timeline_clock_mismatch")

    metadata = sequence.coordinate_metadata
    if (not isinstance(metadata, dict) or metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
            or metadata.get("coordinate_system") != "camera_reference_xy_divided_by_source_long_edge"
            or metadata.get("axis_units") != "same_for_x_and_y"):
        return reject("coordinate_contract_unverified")
    if any(metadata.get(key) not in (None, False) for key in ("resampled", "interpolated", "smoothed")):
        return reject("modified_sampling_or_geometry")
    if (sequence.camera_motion_status is None or sequence.camera_reference_epochs is None
            or sequence.frame_transforms_to_reference is None
            or len(sequence.camera_motion_status) != count or len(sequence.camera_reference_epochs) != count
            or np.asarray(sequence.frame_transforms_to_reference).shape != (count, 2, 3)):
        return reject("camera_reference_unverified")
    compensation = metadata.get("camera_compensation")
    applied = compensation.get("applied_source_frames", []) if isinstance(compensation, dict) else []
    if not isinstance(applied, list) or any(type(frame) is not int for frame in applied):
        return reject("camera_application_evidence_invalid")
    for i, binding in enumerate(bindings):
        if (sequence.camera_motion_status[i] not in {"reference", "stationary", "compensated"}
                or sequence.camera_reference_epochs[i] != binding["camera_reference_epoch"]
                or applied.count(binding["frame_index"]) != 1
                or validated_camera_transform({"compensation_valid": True,
                    "matrix_to_reference": sequence.frame_transforms_to_reference[i]}) is None):
            return reject("camera_reference_unverified")
    if sequence.frame_dimensions_px is None:
        return reject("frame_dimensions_unavailable")
    dimensions = np.asarray(sequence.frame_dimensions_px)
    if (dimensions.shape != (count, 2) or dimensions.dtype.kind not in "fiu" or not np.isfinite(dimensions).all()
            or np.any(dimensions <= 0) or np.any(dimensions != np.floor(dimensions))
            or not np.all(dimensions == dimensions[0])):
        return reject("frame_dimensions_invalid_or_changed")

    is_model = sequence.model_estimate_bindings is not None
    if is_model:
        result["measurement_provenance"] = dict(MODEL_MEASUREMENT_PROVENANCE)
        if len(sequence.model_estimate_bindings) != count:
            return reject("model_source_binding_unavailable")
        for model, binding in zip(sequence.model_estimate_bindings, bindings):
            if not isinstance(model, dict):
                return reject("model_source_binding_unavailable")
            for key in ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                        "person_track_id", "selection_epoch", "camera_reference_epoch"):
                if type(model.get(key)) is not int or model[key] != binding[key]:
                    return reject("model_source_binding_mismatch")
            if (not isinstance(model.get("video_sha256"), str)
                    or model["video_sha256"].lower() != binding["video_sha256"]
                    or type(model.get("timestamp_ms")) not in (int, float)
                    or model["timestamp_ms"] != binding["source_timestamp_ms"]):
                return reject("model_source_binding_mismatch")
    else:
        result["measurement_provenance"] = {"observation_semantics": "confidence_qualified_image_points",
            "confidence_min": CONFIDENCE_MIN, "scoring_allowed": False}

    points = []
    for name in HIPS:
        if name not in sequence.keypoints_xy or name not in sequence.confidence:
            return reject("hip_geometry_unavailable")
        confidence = np.asarray(sequence.confidence[name])
        xy_input = np.asarray(sequence.keypoints_xy[name])
        if (confidence.shape != (count,) or xy_input.shape != (count, 2)
                or xy_input.dtype.kind not in "fiu"):
            return reject("hip_geometry_shape_invalid")
        if is_model:
            mask = np.asarray(sequence.model_estimate_masks.get(name, []))
            if mask.shape != (count,) or mask.dtype.kind != "b" or not mask.all():
                return reject("hip_model_geometry_unavailable")
            if confidence.dtype.kind not in "fiu" or not np.isnan(confidence).all():
                return reject("model_confidence_must_remain_unknown")
        elif confidence.dtype.kind not in "fiu" or not np.isfinite(confidence).all() or np.any(confidence > 1):
            return reject("hip_confidence_invalid")
        xy, mask = point_series(sequence, name, confidence_min=CONFIDENCE_MIN)
        if not mask.all():
            return reject("hip_geometry_unavailable")
        # The shared loader already applied the affine map. Do not reapply it.
        with np.errstate(over="ignore", invalid="ignore"):
            reference = xy * float(max(dimensions[0]))
        if not np.isfinite(reference).all():
            return reject("nonfinite_reference_geometry")
        # Sequence arrays are shallow-mutable. Recheck original image bounds
        # without forbidding legitimate reference points outside the image.
        for i, point in enumerate(reference):
            matrix = np.asarray(sequence.frame_transforms_to_reference[i], dtype=float)
            with np.errstate(over="ignore", invalid="ignore"):
                original = np.linalg.solve(matrix[:, :2], point - matrix[:, 2])
            # Round-trip arithmetic tolerance only; no physical motion cutoff.
            tolerance = 8 * np.finfo(float).eps * max(1., float(max(dimensions[i])),
                float(np.max(np.abs(point))), float(np.max(np.abs(matrix[:, 2]))))
            if (not np.isfinite(original).all() or np.any(original < -tolerance)
                    or np.any(original >= dimensions[i] + tolerance)):
                return reject("hip_outside_original_frame")
        points.append(reference)
    centers = points[0] / 2 + points[1] / 2
    if not np.isfinite(centers).all():
        return reject("nonfinite_reference_geometry")
    try:
        length = math.fsum(math.dist(a, b) for a, b in zip(centers, centers[1:]))
        net = math.dist(centers[0], centers[-1])
    except OverflowError:
        return reject("nonfinite_path_geometry")
    if not math.isfinite(length) or not math.isfinite(net):
        return reject("nonfinite_path_geometry")
    result.update(hip_path_length_px=length, hip_net_displacement_px=net,
                  hip_centers_reference_px=centers.tolist(), sample_count=count,
                  source_duration_ms_exact=_rational(times[-1] - times[0]),
                  source_reference={key: bindings[0][key] for key in (
                      "video_sha256", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch")},
                  sensitivity={"denominator_px": net, "threshold_applied": False,
                      "warning": "As net displacement approaches zero, endpoint uncertainty can dominate this ratio."})
    if net == 0:
        return reject("zero_net_displacement")
    ratio = length / net
    if not math.isfinite(ratio):
        return reject("nonfinite_ratio")
    result.update(status="measured", reason="bounded_image_plane_geometry_only", hip_path_over_net_ratio=ratio)
    return result
