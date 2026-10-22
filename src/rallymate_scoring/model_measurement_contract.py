"""Validate transported model geometry evidence without authorizing a grade.

The producer must first validate against independent decoder/selection records.
This module checks that consumers preserve that proof and never reinterpret
unknown confidence or model geometry as observed anatomical visibility.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping

MODEL_MEASUREMENT_PROVENANCE = {
    "pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
    "assessment_status": "measurement_only", "scoring_allowed": False,
    "confidence_available": False, "visibility_verified": False,
}
SOURCE_BINDING_FIELDS = (
    "video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
    "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch",
)


def _finite(value):
    try:
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def model_measurement_provenance(value):
    return isinstance(value, Mapping) and all(value.get(key) == expected and
        (not isinstance(expected, bool) or value.get(key) is expected)
        for key, expected in MODEL_MEASUREMENT_PROVENANCE.items())


def has_model_measurement_context(*values):
    for value in values:
        if not isinstance(value, Mapping):
            continue
        provenance = value.get("measurement_provenance")
        if (isinstance(provenance, Mapping) and (provenance.get("pose_source") == "sam3d"
                or provenance.get("observation_semantics") in ("model_estimate", "monocular_model_estimate"))
                or "source_window" in value or "geometry_mask" in value):
            return True
    return False


def validated_model_window(payload, *, person_track_id=None, parent_window=None):
    """Return a copied source_window, or None for an incomplete/inconsistent proof.

    Parent windows must themselves have passed this function. A child window
    may cover a subset, but cannot substitute a different frame, clock or epoch.
    Neither this proof nor a complete geometry mask establishes visibility.
    """
    if not isinstance(payload, Mapping) or not model_measurement_provenance(payload.get("measurement_provenance")):
        return None
    window = payload.get("source_window")
    if not isinstance(window, Mapping):
        return None
    start, end = window.get("start_ms"), window.get("end_ms")
    frames, local, source, bindings = (window.get(key) for key in (
        "source_frames", "video_timestamps_ms", "source_timestamps_ms", "source_bindings"))
    sha = window.get("video_sha256")
    identity = ("person_track_id", "selection_epoch", "camera_reference_epoch")
    if (not _integer(start) or not _integer(end) or end <= start
            or not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha)
            or any(not _integer(window.get(key)) for key in identity)
            or person_track_id is not None and window["person_track_id"] != person_track_id
            or not all(isinstance(value, list) for value in (frames, local, source, bindings))
            or not 2 <= len(frames) <= 20000 or any(len(value) != len(frames) for value in (local, source, bindings))
            or not all(_integer(value) for value in frames + local)
            or not all(_finite(value) and value >= 0 for value in source)
            or local[0] != start or local[-1] != end
            or any(b != a+1 for a, b in zip(frames, frames[1:]))
            or any(not 0 < b-a <= 160 for a, b in zip(local, local[1:]))
            or any(not 0 < b-a <= 161.01 for a, b in zip(source, source[1:]))
            or any(abs((timestamp-source[0])-(playback-start)) > 1.01 for timestamp, playback in zip(source, local))):
        return None
    previous_processed = -1
    for index, binding in enumerate(bindings):
        if (not isinstance(binding, Mapping) or any(key not in binding for key in SOURCE_BINDING_FIELDS)
                or binding["video_sha256"] != sha or any(not _integer(binding[key]) or binding[key] != window[key] for key in identity)
                or any(not _integer(binding[key]) for key in ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den"))
                or binding["frame_index"] != frames[index] or index > 0 and binding["processed_index"] != previous_processed+1
                or index > 0 and any(binding[key] != bindings[0][key] for key in ("time_base_num", "time_base_den"))
                or binding["time_base_num"] == 0 or binding["time_base_den"] == 0
                or not _finite(binding["timestamp_ms"]) or abs(binding["timestamp_ms"]-source[index]) > .51):
            return None
        try:
            pts_ms = binding["pts"]*binding["time_base_num"]/binding["time_base_den"]*1000
        except (OverflowError, ZeroDivisionError):
            return None
        if not _finite(pts_ms) or abs(pts_ms-source[index]) > .000001:
            return None
        if "video_timestamp_ms" in binding and binding["video_timestamp_ms"] != local[index]:
            return None
        previous_processed = binding["processed_index"]
    if parent_window is not None:
        if (not isinstance(parent_window, Mapping)
                or any(window[key] != parent_window.get(key) for key in (*identity, "video_sha256"))
                or start < parent_window.get("start_ms", end) or end > parent_window.get("end_ms", start)):
            return None
        parent_frames = parent_window.get("source_frames", [])
        positions = {frame: i for i, frame in enumerate(parent_frames)}
        for i, frame in enumerate(frames):
            j = positions.get(frame)
            if j is None or local[i] != parent_window["video_timestamps_ms"][j] or source[i] != parent_window["source_timestamps_ms"][j]:
                return None
            if any(bindings[i][key] != parent_window["source_bindings"][j].get(key) for key in SOURCE_BINDING_FIELDS):
                return None
    result = {key: copy.deepcopy(window[key]) for key in ("start_ms", "end_ms", "source_frames", "video_timestamps_ms",
              "source_timestamps_ms", "person_track_id", "selection_epoch", "camera_reference_epoch", "video_sha256")}
    result["source_bindings"] = [{key: binding[key] for key in SOURCE_BINDING_FIELDS +
                                (("video_timestamp_ms",) if "video_timestamp_ms" in binding else ())} for binding in bindings]
    return result


def validated_geometry_mask(value, window, *, required_joints=()):
    """Validate boolean geometry availability, with no invented confidence cut."""
    if not isinstance(value, Mapping) or not value or len(value) > 200:
        return None
    count = len(window["source_frames"])
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
           or not isinstance(mask, list) or len(mask) != count or any(not isinstance(item, bool) for item in mask)
           for name, mask in value.items()):
        return None
    if any(name not in value for name in required_joints):
        return None
    return {name: list(mask) for name, mask in value.items()}
