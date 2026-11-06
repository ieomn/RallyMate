from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
import time

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
        if pose_backend_name == "sam3d" and pose_backend_instance is None:
            from rallymate_vision.sam3d_adapter import Sam3DPoseAdapter
            pose_backend_instance = Sam3DPoseAdapter(
                pose_model, device=device, runtime=pose_runtime,
                profile=pose_profile, config_path=pose_config,
            )
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
        self.pose_estimator = (self.pose_backend if pose_backend_name == "sam3d"
                               else PoseEstimator(self.pose_backend))
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
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: Sequence[np.ndarray]) -> list[list[dict]]:
        """Batch stateless detection, returning exactly one ordered slot per frame.

        Tracking, camera state and subject selection must still consume these
        slots chronologically. Grouping identical source shapes preserves YOLO's
        rectangular padding policy; mixing aspect ratios in one model call can
        otherwise change the effective input geometry. The caller owns the
        bounded video queue and must not accumulate a whole decoded video.
        """
        if isinstance(frames, np.ndarray):
            raise ValueError("detect_batch expects a sequence of source frames")
        if len(frames) > 16:
            raise ValueError("detection batch must contain at most 16 frames")
        frames = list(frames)
        for frame in frames:
            if (not isinstance(frame, np.ndarray) or frame.ndim != 3
                    or frame.shape[2] != 3 or frame.dtype != np.uint8
                    or min(frame.shape[:2]) < 1):
                raise ValueError("detection requires non-empty uint8 BGR source frames")
        started = time.perf_counter()
        self.last_detection_timing = {}
        self.last_detection_timings = [{} for _ in frames]
        outputs: list[list[dict]] = [[] for _ in frames]
        shape_groups: dict[tuple, list[int]] = {}
        for index, frame in enumerate(frames):
            shape_groups.setdefault(frame.shape, []).append(index)
        for indexes in shape_groups.values():
            results = self._predict_detection_group(
                frames, indexes, imgsz=self.detect_imgsz, confidence=self.detect_confidence,
                classes=self.target_class_ids, component="base",
            )
            for index, result in zip(indexes, results):
                height, width = frames[index].shape[:2]
                decode_started = time.perf_counter()
                outputs[index] = self._decode_detections(result, width, height)
                self.last_detection_timings[index]["base_output_transfer_decode_ms"] = (
                    time.perf_counter() - decode_started) * 1000
        # Refinement uses the same source pixels, never reconstructed detail.
        # Apply the per-frame policy first, then batch only compatible requests.
        ball_classes = [key for key, name in self.detect_model.names.items()
                        if self.TARGET_ALIASES.get(name) == "ball"]
        refinement_groups: dict[tuple, list[int]] = {}
        if ball_classes:
            for index, (frame, detections) in enumerate(zip(frames, outputs)):
                height, width = frame.shape[:2]
                size, confidence = self._ball_refinement_policy(width, height, detections)
                if size:
                    refinement_groups.setdefault((frame.shape, size, confidence), []).append(index)
        for (_, size, confidence), indexes in refinement_groups.items():
            results = self._predict_detection_group(
                frames, indexes, imgsz=size, confidence=confidence,
                classes=ball_classes, component="refinement",
            )
            for index, refined in zip(indexes, results):
                height, width = frames[index].shape[:2]
                decode_started = time.perf_counter()
                extras = [item for item in self._decode_detections(refined, width, height)
                          if item["class_name"] == "ball"
                          and item["confidence"] >= confidence
                          and self._small_ball_refinement(item, width, height)]
                outputs[index] = self._merge_ball_scales(outputs[index], extras)
                self.last_detection_timings[index]["refinement_output_transfer_decode_ms"] = (
                    time.perf_counter() - decode_started) * 1000
        outputs = [self._deduplicate(detections) for detections in outputs]
        total_ms = (time.perf_counter() - started) * 1000
        # Shared call wall time is apportioned, not per-frame latency. Preserve
        # actual batch wall time separately so profiling never sums it N times.
        for timing in self.last_detection_timings:
            timing["total_wall_ms"] = total_ms / len(frames)
        self.last_detection_timing = self.last_detection_timings[-1] if frames else {}
        self.last_detection_batch_timing = {
            "frames": len(frames), "base_calls": len(shape_groups),
            "refinement_calls": len(refinement_groups), "total_wall_ms": total_ms,
            "per_frame_wall_semantics": "shared_batch_wall_apportioned_not_latency",
        }
        return outputs

    def _predict_detection_group(self, frames, indexes, *, imgsz, confidence, classes, component):
        started = time.perf_counter()
        source = frames[indexes[0]] if len(indexes) == 1 else [frames[index] for index in indexes]
        results = list(self.detect_model.predict(
            source, imgsz=imgsz, conf=confidence, classes=classes,
            device=self.device, verbose=False,
        ))
        elapsed_ms = (time.perf_counter() - started) * 1000
        if len(results) != len(indexes):
            raise ValueError("detector must return exactly one result per source frame")
        for index, result in zip(indexes, results):
            timing = self.last_detection_timings[index]
            timing[f"{component}_predict_wall_ms"] = elapsed_ms / len(indexes)
            # Ultralytics publishes its own per-image stage measurements.
            for key, value in (getattr(result, "speed", None) or {}).items():
                if isinstance(value, (int, float)):
                    timing[f"{component}_{key}_ms"] = value
        return results

    def _ball_refinement_policy(self, width: int, height: int, detections: list[dict]) -> tuple[int | None, float]:
        """Choose one bounded extra scale without assuming the active ball ID."""
        configured = self.ball_refinement_imgsz
        minimum = max(self.detect_confidence, 0.25)
        if not configured or configured <= self.detect_imgsz:
            return None, minimum
        longest, shortest = max(width, height), min(width, height)
        if longest >= 1600:
            # Preserve the existing HD policy and its original confidence gate.
            return configured, minimum
        minimum = max(self.detect_confidence, 0.35)
        if longest < 1280 or shortest < 720:
            return None, minimum
        if any(item["class_name"] == "ball" and item["confidence"] >= 0.5 for item in detections):
            return None, minimum
        # A multiple of 32 no larger than the original long edge avoids asking
        # the detector to invent resolution in this additional 720p branch.
        native_size = min(configured, longest // 32 * 32)
        return (native_size if native_size > self.detect_imgsz else None), minimum

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
        return {"policy_version": "adaptive-ball-multiscale-v1.1.0",
                "base_imgsz": self.detect_imgsz,
                "ball_refinement_imgsz": self.ball_refinement_imgsz,
                "minimum_source_long_edge": 1280,
                "hd_unconditional_minimum_long_edge": 1600,
                "refinement_min_confidence": max(self.detect_confidence, 0.25),
                "native_refinement": {"minimum_short_edge": 720, "maximum_long_edge_exclusive": 1600,
                                      "minimum_confidence": max(self.detect_confidence, 0.35),
                                      "skip_if_base_ball_confidence_at_least": 0.5,
                                      "resize_cap": "source_long_edge_floor_multiple_32"},
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
        *,
        source_binding: dict | None = None,
    ) -> list[dict]:
        self.last_pose_association_diagnostics = None
        if self.pose_backend.metadata().backend == "sam3d":
            poses = self.pose_estimator.estimate(
                frame, player_detections, max_players, source_binding=source_binding,
            )
        else:
            poses = self.pose_estimator.estimate(
                frame,
                player_detections,
                max_players,
            )
        self.last_pose_association_diagnostics = deepcopy(
            getattr(self.pose_estimator, "last_association_diagnostics", None)
        )
        return poses

    def estimate_pose_batch(self, requests: list[dict]) -> list[dict]:
        if self.pose_backend.metadata().backend != "sam3d":
            raise ValueError("multi-frame pose requests require the SAM backend")
        self.last_pose_association_diagnostics = None
        return self.pose_estimator.estimate_batch(requests)

    def pose_metadata(self) -> dict:
        if callable(getattr(self.pose_backend, "details", None)):
            return self.pose_backend.details()
        return self.pose_backend.metadata().to_dict()

    def pose_frame_records(self) -> list[dict]:
        return getattr(self.pose_backend, "last_records", [])

    def close(self) -> None:
        close = getattr(self.pose_backend, "close", None)
        if callable(close):
            close()
