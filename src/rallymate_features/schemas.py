from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from fractions import Fraction
import math
import re
from typing import Any

import numpy as np


SOURCE_TIME_BINDING_FIELDS = frozenset({
    "frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den",
    "source_timestamp_ms", "video_timestamp_ms", "timestamp_source", "primary_player_id",
    "person_track_id", "selection_epoch", "camera_reference_epoch",
})


def validated_source_time_binding(value: Any) -> dict[str, Any] | None:
    """Validate decoder time/identity evidence, independently of pose validity.

    The absolute export timestamp may be rounded to milliseconds. The exact
    source time is always Fraction(pts * time_base_num * 1000, time_base_den);
    the .51 ms tolerance below must never become a duration/window tolerance.
    """
    if not isinstance(value, dict) or set(value) != SOURCE_TIME_BINDING_FIELDS:
        return None
    if value.get("timestamp_source") != "decoder_pts":
        return None
    digest = value.get("video_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        return None
    for key in ("frame_index", "processed_index", "pts", "video_timestamp_ms", "primary_player_id",
                "person_track_id", "selection_epoch", "camera_reference_epoch"):
        if type(value.get(key)) is not int or value[key] < 0:
            return None
    if any(type(value.get(key)) is not int or value[key] <= 0 for key in ("time_base_num", "time_base_den")):
        return None
    source_ms = value.get("source_timestamp_ms")
    try:
        if isinstance(source_ms, bool) or not isinstance(source_ms, (int, float)) or not math.isfinite(source_ms) or source_ms < 0:
            return None
        exact_ms = Fraction(value["pts"] * value["time_base_num"] * 1000, value["time_base_den"])
        if abs(Fraction(str(source_ms)) - exact_ms) > Fraction(51, 100):
            return None
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    return {**value, "video_sha256": digest.lower()}


@dataclass(frozen=True)
class PoseSequence:
    timestamp_ms: np.ndarray
    source_frames: np.ndarray
    keypoints_xy: dict[str, np.ndarray]
    confidence: dict[str, np.ndarray]
    primary_player_id: int = 1
    # Record-derived sequences use equal x/y units, never x/width,y/height.
    # The per-frame dimensions also convert normalized manual annotations
    # into exactly the same coordinate system as model observations.
    frame_dimensions_px: np.ndarray | None = None
    coordinate_metadata: dict[str, Any] = field(default_factory=dict)
    camera_motion_status: tuple[str, ...] | None = None
    frame_transforms_to_reference: np.ndarray | None = None
    camera_reference_epochs: tuple[int | None, ...] | None = None
    # Explicitly source-validated model geometry is independent of confidence.
    # NaN confidence remains unknown; these masks never claim visibility.
    model_estimate_masks: dict[str, np.ndarray] = field(default_factory=dict)
    model_estimate_bindings: tuple[dict[str, Any] | None, ...] | None = None
    # Decoder source time for any pose backend. Keyed by original source frame,
    # with exact integer PTS/timebase retained; never a model visibility mask.
    source_time_bindings: dict[int, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        timestamps = np.asarray(self.timestamp_ms)
        frames = np.asarray(self.source_frames)
        if timestamps.ndim != 1 or frames.shape != timestamps.shape:
            raise ValueError("timestamp_ms and source_frames must be same-length 1D arrays")
        if timestamps.size and np.any(np.diff(timestamps) <= 0):
            raise ValueError("timestamp_ms must be strictly increasing")
        if self.frame_dimensions_px is not None and np.asarray(self.frame_dimensions_px).shape != (timestamps.size, 2):
            raise ValueError("frame_dimensions_px must have shape [T, 2]")
        if self.camera_motion_status is not None and (
                len(self.camera_motion_status) != timestamps.size
                or any(status not in {"reference", "stationary", "compensated", "moving", "unavailable"} for status in self.camera_motion_status)):
            raise ValueError("camera_motion_status must contain one supported status per frame")
        if self.frame_transforms_to_reference is not None and np.asarray(self.frame_transforms_to_reference).shape != (timestamps.size, 2, 3):
            raise ValueError("frame_transforms_to_reference must have shape [T, 2, 3]")
        if self.camera_reference_epochs is not None and (
                len(self.camera_reference_epochs) != timestamps.size
                or any(epoch is not None and (not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0)
                       for epoch in self.camera_reference_epochs)):
            raise ValueError("camera_reference_epochs must contain one nonnegative epoch or None per frame")
        for name, xy in self.keypoints_xy.items():
            if np.asarray(xy).shape != (timestamps.size, 2):
                raise ValueError(f"{name} coordinates must have shape [T, 2]")
            if name not in self.confidence or np.asarray(self.confidence[name]).shape != timestamps.shape:
                raise ValueError(f"{name} confidence must have shape [T]")
        if self.model_estimate_bindings is not None and len(self.model_estimate_bindings) != timestamps.size:
            raise ValueError("model_estimate_bindings must contain one binding or None per frame")
        if not isinstance(self.source_time_bindings, dict):
            raise ValueError("source_time_bindings must be indexed by source frame")
        frame_indexes = {int(frame): index for index, frame in enumerate(frames)}
        time_origin_ranges = {}
        for frame, binding in self.source_time_bindings.items():
            validated = validated_source_time_binding(binding)
            if (type(frame) is not int or frame not in frame_indexes or validated is None
                    or binding["frame_index"] != frame
                    or np.count_nonzero(frames == frame) != 1
                    or binding["video_timestamp_ms"] != timestamps[frame_indexes[frame]]
                    or binding["primary_player_id"] != self.primary_player_id
                    or self.camera_reference_epochs is not None and
                       binding["camera_reference_epoch"] != self.camera_reference_epochs[frame_indexes[frame]]):
                raise ValueError("source_time_bindings must match unique source frames, video times and identity")
            identity = tuple(validated[key] for key in ("video_sha256", "time_base_num", "time_base_den"))
            offset = Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"]) - binding["video_timestamp_ms"]
            lower, upper = offset - Fraction(51, 100), offset + Fraction(51, 100)
            if identity in time_origin_ranges:
                previous_lower, previous_upper = time_origin_ranges[identity]
                lower, upper = max(lower, previous_lower), min(upper, previous_upper)
            if lower > upper:
                raise ValueError("source_time_bindings must share a constant source/video time origin")
            time_origin_ranges[identity] = lower, upper
        for name, mask in self.model_estimate_masks.items():
            values = np.asarray(mask)
            if values.shape != timestamps.shape or values.dtype.kind != "b" or name not in self.keypoints_xy:
                raise ValueError("model_estimate_masks must contain named boolean [T] geometry masks")
            if self.model_estimate_bindings is None:
                raise ValueError("model geometry masks require source bindings")
            if np.any(values & np.asarray([b is None for b in self.model_estimate_bindings])):
                raise ValueError("unbound frames cannot contain accepted model geometry")

    def slice(self, window: slice, **changes) -> "PoseSequence":
        from dataclasses import replace
        return replace(self, timestamp_ms=self.timestamp_ms[window], source_frames=self.source_frames[window],
            keypoints_xy={name: values[window] for name, values in self.keypoints_xy.items()},
            confidence={name: values[window] for name, values in self.confidence.items()},
            frame_dimensions_px=self.frame_dimensions_px[window] if self.frame_dimensions_px is not None else None,
            frame_transforms_to_reference=self.frame_transforms_to_reference[window] if self.frame_transforms_to_reference is not None else None,
            camera_reference_epochs=self.camera_reference_epochs[window] if self.camera_reference_epochs is not None else None,
            camera_motion_status=self.camera_motion_status[window] if self.camera_motion_status is not None else None,
            model_estimate_masks={name: values[window] for name, values in self.model_estimate_masks.items()},
            model_estimate_bindings=self.model_estimate_bindings[window] if self.model_estimate_bindings is not None else None,
            source_time_bindings={int(frame): dict(self.source_time_bindings[int(frame)])
                                  for frame in self.source_frames[window] if int(frame) in self.source_time_bindings},
            **changes)


MODEL_MEASUREMENT_PROVENANCE = {
    "pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
    "assessment_status": "measurement_only", "scoring_allowed": False,
    "confidence_available": False, "visibility_verified": False,
}


def model_binding_key(binding):
    if binding is None:
        return None
    return tuple(binding[key] for key in ("video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den"))


def model_bindings_continuous(sequence: PoseSequence, before: int, after: int) -> bool:
    bindings = sequence.model_estimate_bindings
    if bindings is None:
        return True
    a, b = bindings[before], bindings[after]
    if a is None or b is None or model_binding_key(a) != model_binding_key(b):
        return False
    source_dt = (b["pts"] - a["pts"]) * a["time_base_num"] / a["time_base_den"] * 1000
    video_dt = float(sequence.timestamp_ms[after] - sequence.timestamp_ms[before])
    return (b["frame_index"] == a["frame_index"] + 1 and b["processed_index"] == a["processed_index"] + 1
            and 0 < source_dt <= 160 and 0 < video_dt <= 160 and abs(source_dt - video_dt) <= 1.01)


def model_geometry_evidence(sequence: PoseSequence, indexes, joint_names=None) -> dict[str, Any]:
    """Serialize proof of source-bound geometry, never observed visibility."""
    if sequence.model_estimate_bindings is None:
        return {}
    indexes = [int(i) for i in indexes]
    result = {"measurement_provenance": dict(MODEL_MEASUREMENT_PROVENANCE)}
    if not indexes or any(sequence.model_estimate_bindings[i] is None for i in indexes):
        return result
    if any(not model_bindings_continuous(sequence, a, b) for a, b in zip(indexes, indexes[1:])):
        return result
    bindings = [dict(sequence.model_estimate_bindings[i]) for i in indexes]
    first = bindings[0]
    video_times = [int(sequence.timestamp_ms[i]) for i in indexes]
    names = list(joint_names if joint_names is not None else sequence.model_estimate_masks)
    result.update(source_window={
        "start_ms": video_times[0], "end_ms": video_times[-1],
        "source_frames": [int(sequence.source_frames[i]) for i in indexes],
        "video_timestamps_ms": video_times,
        "source_timestamps_ms": [b["pts"] * b["time_base_num"] / b["time_base_den"] * 1000 for b in bindings],
        "source_bindings": bindings,
        **{key: first[key] for key in ("video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch")},
    }, geometry_mask={name: [bool(sequence.model_estimate_masks.get(name, np.zeros(len(sequence.timestamp_ms), dtype=bool))[i]) for i in indexes]
                     for name in names})
    return result


@dataclass(frozen=True)
class EventInterval:
    event_id: str
    event_code: str
    start_ms: int
    end_ms: int
    person_track_id: int = 1
    key_phases: dict[str, int | None] | None = None

    def __post_init__(self) -> None:
        if self.end_ms <= self.start_ms:
            raise ValueError("event end_ms must be greater than start_ms")


@dataclass(frozen=True)
class FeatureResult:
    feature_name: str
    feature_version: str
    value: float | int | list[float] | dict[str, Any] | None
    unit: str
    confidence: float | None
    valid: bool
    reason: str
    source_frames: list[int]
    raw_value: Any
    smoothed_value: Any
    provenance: dict[str, Any]

    def to_dict(self, *, copy_evidence: bool = True) -> dict[str, Any]:
        """Export a mutable record, optionally sharing read-only evidence.

        The scoring writer only annotates the top level and provenance. Its
        immutable series/coordinate evidence can be shared until serialization
        rather than duplicating the full-video camera manifest per feature.
        General callers retain the original independent deep-copy contract.
        """
        payload = asdict(self) if copy_evidence else {
            item.name: getattr(self, item.name) for item in fields(self)
        }
        if not copy_evidence:
            payload["provenance"] = dict(self.provenance)
        payload["confidence"] = None if self.confidence is None else round(float(self.confidence), 6)
        for key in ("measurement_provenance", "source_window", "geometry_mask"):
            if key in self.provenance:
                payload[key] = payload["provenance"][key]
        if "measurement_provenance" in self.provenance and "selected_side" in self.provenance:
            payload["selected_side"] = payload["provenance"]["selected_side"]
            payload["required_joints_used"] = payload["provenance"].get("required_joints_used", [])
        return payload
