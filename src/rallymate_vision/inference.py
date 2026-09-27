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
        ball_refinement_imgsz: int | None = 1536,
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
        self.ball_refinement_imgsz = ball_refinement_imgsz
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
        detections = self._decode_detections(result, width, height)
        # HD tennis balls can shrink to only a few pixels at the base input
        # size. Refine this class alone; player/racket boxes and low-resolution
        # inputs retain their existing inference path.
        refinement_size = self.ball_refinement_imgsz
        ball_classes = [key for key, name in self.detect_model.names.items()
                        if self.TARGET_ALIASES.get(name) == "ball"]
        if (refinement_size and max(width, height) >= 1600
                and self.detect_imgsz < refinement_size and ball_classes):
            refined = self.detect_model.predict(
                frame, imgsz=refinement_size, conf=max(self.detect_confidence, 0.25),
                classes=ball_classes, device=self.device, verbose=False,
            )[0]
            extras = [item for item in self._decode_detections(refined, width, height)
                      if item["class_name"] == "ball"
                      and self._small_ball_refinement(item, width, height)]
            detections = self._merge_ball_scales(detections, extras)
        return self._deduplicate(detections)

    @classmethod
    def _decode_detections(cls, result, width: int, height: int) -> list[dict]:
        detections: list[dict] = []
        if result.boxes is None:
            return detections
        boxes = result.boxes.xyxy.detach().cpu().tolist()
        classes = result.boxes.cls.detach().cpu().tolist()
        confidences = result.boxes.conf.detach().cpu().tolist()
        for box, class_id, confidence in zip(boxes, classes, confidences):
            raw_name = str(result.names[int(class_id)])
            alias = cls.TARGET_ALIASES.get(raw_name)
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
        return detections

    @staticmethod
    def _small_ball_refinement(item: dict, width: int, height: int) -> bool:
        # This extra scale recovers tiny balls only. Large circular signage is
        # a common HD false positive; larger genuine balls keep the base path.
        x1, y1, x2, y2 = item["bbox_px"]
        short_side, long_side = sorted((x2 - x1, y2 - y1))
        return 0 < short_side <= min(width, height) * 0.03 and long_side <= short_side * 3.5

    @staticmethod
    def _merge_ball_scales(base: list[dict], refined: list[dict]) -> list[dict]:
        """Fuse overlapping observations of one ball without merging neighbours."""
        kept = [item for item in base if item["class_name"] != "ball"]
        balls = [item for item in base if item["class_name"] == "ball"]
        unmatched = set(range(len(balls)))
        extras: list[dict] = []
        for item in sorted(refined, key=lambda item: item["confidence"], reverse=True):
            box = item["bbox_px"]
            area = (box[2] - box[0]) * (box[3] - box[1])
            matches = []
            for index in unmatched:
                old = balls[index]["bbox_px"]
                old_area = (old[2] - old[0]) * (old[3] - old[1])
                if min(area, old_area) <= 0 or max(area, old_area) > min(area, old_area) * 4:
                    continue
                overlap = box_iou(box, old)
                if overlap >= 0.3 or box_intersection_over_min_area(box, old) >= 0.65:
                    matches.append((overlap, index))
            if matches:
                _, index = max(matches)
                unmatched.remove(index)
                if item["confidence"] > balls[index]["confidence"]:
                    balls[index] = item
            else:
                extras.append(item)
        balls.extend(extras)
        return kept + balls

    def detection_metadata(self) -> dict:
        return {"policy_version": "hd-ball-multiscale-v1.0.0",
                "base_imgsz": self.detect_imgsz,
                "ball_refinement_imgsz": self.ball_refinement_imgsz,
                "minimum_source_long_edge": 1600,
                "refinement_min_confidence": max(self.detect_confidence, 0.25),
                "refinement_max_short_side_fraction": 0.03,
                "semantics": "additional_model_observations_not_interpolated_points"}

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
