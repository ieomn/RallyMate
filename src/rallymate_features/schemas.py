from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PoseSequence:
    timestamp_ms: np.ndarray
    source_frames: np.ndarray
    keypoints_xy: dict[str, np.ndarray]
    confidence: dict[str, np.ndarray]
    primary_player_id: int = 1

    def __post_init__(self) -> None:
        timestamps = np.asarray(self.timestamp_ms)
        frames = np.asarray(self.source_frames)
        if timestamps.ndim != 1 or frames.shape != timestamps.shape:
            raise ValueError("timestamp_ms and source_frames must be same-length 1D arrays")
        if timestamps.size and np.any(np.diff(timestamps) <= 0):
            raise ValueError("timestamp_ms must be strictly increasing")
        for name, xy in self.keypoints_xy.items():
            if np.asarray(xy).shape != (timestamps.size, 2):
                raise ValueError(f"{name} coordinates must have shape [T, 2]")
            if name not in self.confidence or np.asarray(self.confidence[name]).shape != timestamps.shape:
                raise ValueError(f"{name} confidence must have shape [T]")


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
    confidence: float
    valid: bool
    reason: str
    source_frames: list[int]
    raw_value: Any
    smoothed_value: Any
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 6)
        return payload
