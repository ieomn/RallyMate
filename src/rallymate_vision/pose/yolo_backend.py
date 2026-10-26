from __future__ import annotations

import time
from pathlib import Path
from typing import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseBackendOutput, PoseModelMetadata
from rallymate_vision.pose.association import POLICY_VERSION, select_target_pose_candidate
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
        return self._infer(rois, None)

    def infer_targeted(
        self,
        rois: Sequence[np.ndarray],
        target_boxes_xyxy: Sequence[Sequence[float]],
        *,
        timestamps_ms: Sequence[int] | None = None,
    ) -> list[PoseBackendOutput | None]:
        del timestamps_ms
        if len(rois) != len(target_boxes_xyxy):
            raise ValueError("target boxes must have exactly one entry per ROI")
        return self._infer(rois, target_boxes_xyxy)

    def _infer(self, rois, target_boxes):
        self.last_association_diagnostics = []
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
        results = list(results)
        if len(results) != len(rois):
            raise ValueError("YOLO must return exactly one result per ROI")
        outputs: list[PoseBackendOutput | None] = []
        for index, (result, roi) in enumerate(zip(results, rois)):
            target = None if target_boxes is None else target_boxes[index]
            boxes = [] if result.boxes is None else result.boxes.xyxy.detach().cpu().numpy().tolist()
            diagnostic = select_target_pose_candidate(boxes, target, roi_size=(roi.shape[1], roi.shape[0]))
            diagnostic["roi_index"] = index
            diagnostic["candidate_boxes_xyxy"] = boxes
            self.last_association_diagnostics.append(diagnostic)
            if (
                diagnostic["selected_index"] is None
                or result.keypoints is None
                or result.keypoints.xy is None
            ):
                if diagnostic["selected_index"] is not None:
                    diagnostic["selected_index"] = None
                    diagnostic["reason_code"] = "pose_candidate_keypoints_missing"
                outputs.append(None)
                continue
            if len(result.keypoints.xy) != len(boxes) or len(result.boxes.conf) != len(boxes) or (result.keypoints.conf is not None and len(result.keypoints.conf) != len(boxes)):
                diagnostic["selected_index"] = None
                diagnostic["reason_code"] = "pose_candidate_arrays_misaligned"
                outputs.append(None)
                continue
            best_index = diagnostic["selected_index"]
            xy = result.keypoints.xy[best_index].detach().cpu().numpy()
            if result.keypoints.conf is not None:
                scores = result.keypoints.conf[best_index].detach().cpu().numpy()
            else:
                diagnostic["selected_index"] = None
                diagnostic["reason_code"] = "pose_candidate_keypoint_confidence_missing"
                outputs.append(None)
                continue
            pose_score = float(result.boxes.conf[best_index].item())
            if not np.isfinite(pose_score) or not 0 <= pose_score <= 1:
                diagnostic["selected_index"] = None
                diagnostic["reason_code"] = "pose_candidate_confidence_invalid"
                outputs.append(None)
                continue
            outputs.append(
                PoseBackendOutput(
                    keypoints_xy=xy,
                    keypoint_scores=scores,
                    pose_score=pose_score,
                    person_bbox_xyxy=tuple(boxes[best_index]),
                    person_association=dict(diagnostic),
                )
            )
        return outputs

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
            person_association_policy_version=POLICY_VERSION,
        )
