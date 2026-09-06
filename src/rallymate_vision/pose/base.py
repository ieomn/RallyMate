from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, Sequence, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class PoseModelMetadata:
    backend: str
    runtime: str
    profile: str
    model_name: str
    model_path: str
    model_sha256: str
    input_size: tuple[int, int]
    native_keypoint_format: str
    native_keypoint_count: int
    keypoint_schema_version: str
    load_seconds: float
    load_semantics: str
    config_path: str | None = None
    config_sha256: str | None = None
    pose_score_semantics: str = "backend_defined"

    def to_dict(self) -> dict:
        value = asdict(self)
        value["input_size"] = list(self.input_size)
        return value


@dataclass(frozen=True)
class PoseBackendOutput:
    """One top-down pose in ROI-local pixel coordinates."""

    keypoints_xy: np.ndarray
    keypoint_scores: np.ndarray
    pose_score: float

    def __post_init__(self) -> None:
        xy = np.asarray(self.keypoints_xy)
        scores = np.asarray(self.keypoint_scores)
        if xy.ndim != 2 or xy.shape[1] != 2:
            raise ValueError("keypoints_xy must have shape [K, 2]")
        if scores.ndim != 1 or scores.shape[0] != xy.shape[0]:
            raise ValueError("keypoint_scores must have shape [K]")


@runtime_checkable
class PoseBackend(Protocol):
    def load(self) -> None: ...

    def infer(
        self,
        rois: Sequence[np.ndarray],
        *,
        timestamps_ms: Sequence[int] | None = None,
    ) -> list[PoseBackendOutput | None]: ...

    def metadata(self) -> PoseModelMetadata: ...
