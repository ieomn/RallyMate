from __future__ import annotations

from pathlib import Path

from rallymate_vision.pose.base import PoseBackend
from rallymate_vision.pose.rtmpose_backend import RtmposePoseBackend
from rallymate_vision.pose.yolo_backend import YoloPoseBackend


def build_pose_backend(
    backend: str,
    *,
    model_path: Path,
    device: str,
    input_size: int,
    confidence: float,
    runtime: str = "pytorch",
    profile: str = "realtime",
    config_path: Path | None = None,
    native_keypoint_format: str | None = None,
) -> PoseBackend:
    name = backend.strip().lower()
    if name == "yolo":
        if config_path is not None:
            raise ValueError("YOLO Pose does not use pose_config")
        return YoloPoseBackend(
            model_path,
            device=device,
            input_size=input_size,
            confidence=confidence,
            runtime=runtime,
            profile=profile,
        )
    if name == "rtmpose":
        if config_path is None:
            raise ValueError("RTMPose requires pose_config")
        return RtmposePoseBackend(
            model_path,
            config_path,
            device=device,
            runtime=runtime,
            profile=profile,
            native_keypoint_format=native_keypoint_format or "halpe26",
        )
    raise ValueError(f"unsupported pose backend: {backend!r}")
