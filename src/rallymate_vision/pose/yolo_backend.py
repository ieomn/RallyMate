from __future__ import annotations

import time
from pathlib import Path
from typing import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseBackendOutput, PoseModelMetadata
from rallymate_vision.pose.metadata import keypoint_schema, sha256_file


class YoloPoseBackend:
    """Top-down Ultralytics pose backend over already-cropped player ROIs."""

    def __init__(
        self,
        model_path: Path,
        *,
        device: str,
        input_size: int = 640,
        confidence: float = 0.25,
        runtime: str = "pytorch",
        profile: str = "realtime",
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.input_size = int(input_size)
        self.confidence = float(confidence)
        self.runtime = runtime
        self.profile = profile
        self._model_sha256 = sha256_file(self.model_path) if self.model_path.exists() else ""
        self.model = None
        self._load_seconds = 0.0
        self.load()

    def load(self) -> None:
        if self.model is not None:
            return
        if self.runtime != "pytorch":
            raise ValueError("YOLO Pose currently supports runtime=pytorch only")
        if not self.model_path.exists():
            raise FileNotFoundError(f"pose model is missing: {self.model_path}")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed; use the supplied yolo environment"
            ) from exc
        started = time.perf_counter()
        self.model = YOLO(str(self.model_path))
        self._load_seconds = time.perf_counter() - started

    def infer(
        self,
        rois: Sequence[np.ndarray],
        *,
        timestamps_ms: Sequence[int] | None = None,
    ) -> list[PoseBackendOutput | None]:
        del timestamps_ms
        if not rois:
            return []
        assert self.model is not None
        results = self.model.predict(
            list(rois),
            imgsz=self.input_size,
            conf=self.confidence,
            device=self.device,
            verbose=False,
        )
        outputs: list[PoseBackendOutput | None] = []
        for result in results:
            if (
                result.boxes is None
                or len(result.boxes) == 0
                or result.keypoints is None
                or result.keypoints.xy is None
            ):
                outputs.append(None)
                continue
            best_index = int(result.boxes.conf.argmax().item())
            xy = result.keypoints.xy[best_index].detach().cpu().numpy()
            if result.keypoints.conf is not None:
                scores = result.keypoints.conf[best_index].detach().cpu().numpy()
            else:
                scores = np.ones((xy.shape[0],), dtype=np.float32)
            outputs.append(
                PoseBackendOutput(
                    keypoints_xy=xy,
                    keypoint_scores=scores,
                    pose_score=float(result.boxes.conf[best_index].item()),
                )
            )
        outputs.extend([None] * (len(rois) - len(outputs)))
        return outputs[: len(rois)]

    def metadata(self) -> PoseModelMetadata:
        schema = keypoint_schema("coco17")
        return PoseModelMetadata(
            backend="yolo",
            runtime=self.runtime,
            profile=self.profile,
            model_name=self.model_path.stem,
            model_path=str(self.model_path),
            model_sha256=self._model_sha256,
            input_size=(self.input_size, self.input_size),
            native_keypoint_format="coco17",
            native_keypoint_count=int(schema["count"]),
            keypoint_schema_version="1.0.0",
            load_seconds=round(self._load_seconds, 6),
            load_semantics="ultralytics_model_construction_weights_may_materialize_lazily",
            pose_score_semantics="roi_person_box_confidence",
        )
