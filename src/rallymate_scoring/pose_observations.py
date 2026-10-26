"""Explicit, source-bound SAM image geometry; never confidence-based scoring.

The 3D coordinates and inferred visibility are not accepted here. Only exact
named body landmarks with original in-frame image coordinates can enter the
existing 2D measurement path. The original detector confidence path is separate.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

MODEL_ESTIMATE = "monocular_model_estimate"
SOURCE_FIELDS = ("video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                 "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch")


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def sam_source_bound_points(pose: Mapping, frame: Mapping):
    """Return raw 2D points and binding, or None for an unverified contract."""
    if (pose.get("source") != "sam3d" or pose.get("keypoint_format") != "mhr70"
            or pose.get("observation_semantics") != MODEL_ESTIMATE
            or pose.get("confidence_semantics") != "not_provided_by_sam3d"
            or "confidence" not in pose or pose["confidence"] is not None
            or pose.get("status") != "ok" or pose.get("coordinate_space") != "original_frame"):
        return None
    binding = pose.get("source_binding")
    if not isinstance(binding, Mapping) or any(key not in binding for key in SOURCE_FIELDS):
        return None
    sha = binding["video_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
        return None
    if any(not _integer(binding[key]) for key in ("frame_index", "processed_index", "person_track_id", "selection_epoch", "camera_reference_epoch")):
        return None
    if (not _integer(binding["pts"], minimum=0) or not _integer(binding["time_base_num"], minimum=1)
            or not _integer(binding["time_base_den"], minimum=1) or not _number(binding["timestamp_ms"])
            or binding["timestamp_ms"] < 0
            or abs(binding["timestamp_ms"] - binding["pts"] * binding["time_base_num"] / binding["time_base_den"] * 1000) > .51
            or any(binding[key] != frame.get(field) for key, field in (("frame_index", "index"), ("processed_index", "processed_index")))
            or binding["timestamp_ms"] != frame.get("source_timestamp_ms", frame.get("timestamp_ms"))
            or not _number(frame.get("timestamp_ms")) or frame["timestamp_ms"] < 0
            or binding["person_track_id"] != pose.get("person_track_id")
            or frame.get("timestamp_source") != "decoder_pts"):
        return None
    camera = frame.get("camera_motion")
    if isinstance(camera, Mapping) and camera.get("reference_epoch") != binding["camera_reference_epoch"]:
        return None
    width, height = frame.get("width"), frame.get("height")
    if not _integer(width, 1) or not _integer(height, 1):
        return None
    points = pose.get("keypoints")
    if not isinstance(points, list) or len(points) != 70:
        return None
    # Share only the pinned anatomical mapping, without importing a model or
    # the frame validator. Foot tips/auxiliary mesh joints remain unmapped.
    from rallymate_vision.pose.metadata import keypoint_schema
    definitions = keypoint_schema("mhr70")["keypoints"]
    raw = {}
    for point, definition in zip(points, definitions):
        if (not isinstance(point, Mapping)
                or any(point.get(key) != definition[key] for key in ("index", "name", "downstream_joint_id"))):
            return None
        if definition["downstream_joint_id"] is None:
            continue
        if (point.get("source") != "sam3d" or point.get("observation_semantics") != "model_estimate"
                or point.get("confidence_semantics") != "not_provided_by_sam3d"
                or any(key not in point or point[key] is not None for key in ("confidence", "observed", "visibility"))):
            return None
        x, y = point.get("raw_x_px"), point.get("raw_y_px")
        if point.get("in_frame") is not True or not _number(x) or not _number(y) or not 0 <= x < width or not 0 <= y < height:
            continue
        raw[definition["name"]] = (float(x), float(y))
    return raw, dict(binding)


def sample_source_reference(sample):
    """Extra reference keys only for explicitly bound model estimates."""
    if getattr(sample, "observation_semantics", None) != MODEL_ESTIMATE:
        return {}
    binding = getattr(sample, "source_binding", {})
    return {"pose_source": getattr(sample, "pose_source", None), "observation_semantics": MODEL_ESTIMATE,
            "video_sha256": binding.get("video_sha256"), "source_selection_epoch": binding.get("selection_epoch")}


def measurement_provenance(samples):
    estimates = [sample for sample in samples if getattr(sample, "observation_semantics", None) == MODEL_ESTIMATE]
    if not estimates:
        return {}
    return {"pose_source": "sam3d", "observation_semantics": MODEL_ESTIMATE,
            "assessment_status": "measurement_only", "scoring_allowed": False,
            "confidence_available": False, "visibility_verified": False,
            "model_estimate_sample_count": len(estimates),
            "reason_zh": "基于单目模型估计的二维关节几何；模型未提供关键点置信度或可见性，保留测量但不用于技术计分。"}


def source_time_is_continuous(before, after):
    if getattr(before, "observation_semantics", None) != MODEL_ESTIMATE:
        return True
    a, b = getattr(before, "source_binding", {}), getattr(after, "source_binding", {})
    if not _number(a.get("timestamp_ms")) or not _number(b.get("timestamp_ms")):
        return False
    # Relative player time and absolute decoder time must advance together.
    # One millisecond accommodates independent rounding of two endpoints.
    return (b["pts"] * b["time_base_num"] * a["time_base_den"] > a["pts"] * a["time_base_num"] * b["time_base_den"]
            and abs((b["timestamp_ms"] - a["timestamp_ms"]) - (after.time - before.time)) <= 1.01)
