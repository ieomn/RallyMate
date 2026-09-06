from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from rallymate_vision.pose import PoseBackend, PoseEstimator, build_pose_backend
from rallymate_vision.pose.metadata import (
    COCO_POSE_KEYPOINTS,
    COCO_TO_RALLYMATE_JOINT,
)
from rallymate_vision.utils import (
    box_intersection_over_min_area,
    box_iou,
    clamp,
    normalize_box,
    safe_float,
)


DOWNSTREAM_SYSTEMS = {
    "player": ["J", "S01", "S07"],
    "ball": ["BALL", "S02"],
    "racket": ["RK", "S03"],
}


class Yolo26Perception:
    """Ultralytics adapter isolated from the rest of the pipeline."""

    TARGET_ALIASES = {
        "person": "player",
        "sports ball": "ball",
        "tennis racket": "racket",
        "player": "player",
        "ball": "ball",
        "racket": "racket",
    }

    def __init__(
        self,
        detect_model: Path,
        pose_model: Path,
        device: str,
        detect_imgsz: int,
        pose_imgsz: int,
        detect_confidence: float,
        pose_confidence: float,
        pose_backend_name: str = "yolo",
        pose_runtime: str = "pytorch",
        pose_profile: str = "realtime",
        pose_config: Path | None = None,
        pose_native_keypoint_format: str | None = None,
        pose_backend_instance: PoseBackend | None = None,
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ultralytics is not installed; use the supplied yolo environment"
            ) from exc
        if not detect_model.exists():
            raise FileNotFoundError(f"detection model is missing: {detect_model}")
        self.detect_model_path = detect_model
        self.device = device
        self.detect_imgsz = detect_imgsz
        self.pose_imgsz = pose_imgsz
        self.detect_confidence = detect_confidence
        self.pose_confidence = pose_confidence
        self.detect_model = YOLO(str(detect_model))
        self.pose_backend = pose_backend_instance or build_pose_backend(
            pose_backend_name,
            model_path=pose_model,
            device=device,
            input_size=pose_imgsz,
            confidence=pose_confidence,
            runtime=pose_runtime,
            profile=pose_profile,
            config_path=pose_config,
            native_keypoint_format=pose_native_keypoint_format,
        )
        self.pose_estimator = PoseEstimator(self.pose_backend)
        pose_metadata = self.pose_backend.metadata()
        self.pose_model_path = Path(pose_metadata.model_path)
        # Temporary compatibility attribute for tooling that inspected the
        # combined facade before the backend split.
        self.pose_model = getattr(self.pose_backend, "model", None)
        self.target_class_ids = [
            class_id
            for class_id, name in self.detect_model.names.items()
            if name in self.TARGET_ALIASES
        ]

    def detect(self, frame: np.ndarray) -> list[dict]:
        height, width = frame.shape[:2]
        result = self.detect_model.predict(
            frame,
            imgsz=self.detect_imgsz,
            conf=self.detect_confidence,
            classes=self.target_class_ids,
            device=self.device,
            verbose=False,
        )[0]
        detections: list[dict] = []
        if result.boxes is None:
            return detections
        boxes = result.boxes.xyxy.detach().cpu().tolist()
        classes = result.boxes.cls.detach().cpu().tolist()
        confidences = result.boxes.conf.detach().cpu().tolist()
        for box, class_id, confidence in zip(boxes, classes, confidences):
            raw_name = str(result.names[int(class_id)])
            alias = self.TARGET_ALIASES.get(raw_name)
            if alias is None:
                continue
            bbox = [
                safe_float(clamp(box[0], 0.0, float(width)), 3),
                safe_float(clamp(box[1], 0.0, float(height)), 3),
                safe_float(clamp(box[2], 0.0, float(width)), 3),
                safe_float(clamp(box[3], 0.0, float(height)), 3),
            ]
            center_x = (bbox[0] + bbox[2]) / 2.0
            center_y = (bbox[1] + bbox[3]) / 2.0
            detections.append(
                {
                    "class_name": alias,
                    "source_class_name": raw_name,
                    "class_id": int(class_id),
                    "confidence": safe_float(confidence, 5),
                    "bbox_px": bbox,
                    "bbox_normalized": [
                        safe_float(value) for value in normalize_box(bbox, width, height)
                    ],
                    "center_px": [
                        safe_float(center_x, 3),
                        safe_float(center_y, 3),
                    ],
                    "center_normalized": [
                        safe_float(center_x / width),
                        safe_float(center_y / height),
                    ],
                    "downstream_systems": DOWNSTREAM_SYSTEMS[alias],
                    "track_id": None,
                }
            )
        return self._deduplicate(detections)

    @staticmethod
    def _deduplicate(detections: list[dict]) -> list[dict]:
        """Suppress rare same-object duplicate boxes before ID assignment."""

        kept: list[dict] = []
        thresholds = {"player": 0.82, "racket": 0.85, "ball": 0.92}
        for detection in sorted(
            detections,
            key=lambda item: float(item["confidence"]),
            reverse=True,
        ):
            threshold = thresholds.get(detection["class_name"], 0.9)
            is_duplicate = False
            for existing in kept:
                if existing["class_name"] != detection["class_name"]:
                    continue
                iou = box_iou(
                    existing["bbox_px"],
                    detection["bbox_px"],
                )
                nested_overlap = box_intersection_over_min_area(
                    existing["bbox_px"],
                    detection["bbox_px"],
                )
                if iou >= threshold or (
                    detection["class_name"] == "player"
                    and nested_overlap >= 0.90
                ):
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(detection)
        return kept

    def estimate_poses(
        self,
        frame: np.ndarray,
        player_detections: Sequence[dict],
        max_players: int,
    ) -> list[dict]:
        return self.pose_estimator.estimate(
            frame,
            player_detections,
            max_players,
        )

    def pose_metadata(self) -> dict:
        return self.pose_backend.metadata().to_dict()
