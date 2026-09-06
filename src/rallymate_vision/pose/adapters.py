from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseBackend
from rallymate_vision.pose.metadata import keypoint_schema
from rallymate_vision.utils import clamp, expand_box, safe_float


class PoseEstimator:
    """Preserve the Detect + Player ROI contract across replaceable backends."""

    def __init__(
        self,
        backend: PoseBackend,
        *,
        roi_margin: float = 0.15,
        min_roi_size_px: int = 32,
    ) -> None:
        if not math.isfinite(float(roi_margin)) or float(roi_margin) < 0:
            raise ValueError("roi_margin must be a finite non-negative number")
        if isinstance(min_roi_size_px, bool) or int(min_roi_size_px) < 1:
            raise ValueError("min_roi_size_px must be a positive integer")
        self.backend = backend
        self.roi_margin = float(roi_margin)
        self.min_roi_size_px = int(min_roi_size_px)

    def estimate(
        self,
        frame: np.ndarray,
        player_detections: Sequence[dict],
        max_players: int,
        *,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        height, width = frame.shape[:2]
        players = sorted(
            player_detections,
            key=lambda detection: (
                (detection["bbox_px"][2] - detection["bbox_px"][0])
                * (detection["bbox_px"][3] - detection["bbox_px"][1])
            ),
            reverse=True,
        )[:max_players]
        crops: list[np.ndarray] = []
        crop_boxes: list[list[int]] = []
        accepted_players: list[dict] = []
        for player in players:
            crop_box = expand_box(
                player["bbox_px"], width, height, margin=self.roi_margin
            )
            x1, y1, x2, y2 = crop_box
            if (
                x2 - x1 < self.min_roi_size_px
                or y2 - y1 < self.min_roi_size_px
            ):
                continue
            crops.append(frame[y1:y2, x1:x2])
            crop_boxes.append(crop_box)
            accepted_players.append(player)
        if not crops:
            return []

        timestamps = [timestamp_ms] * len(crops) if timestamp_ms is not None else None
        outputs = self.backend.infer(crops, timestamps_ms=timestamps)
        if len(outputs) != len(crops):
            raise ValueError("PoseBackend must return exactly one output slot per ROI")
        metadata = self.backend.metadata()
        schema = keypoint_schema(metadata.native_keypoint_format)
        definitions = schema["keypoints"]
        poses: list[dict] = []
        for player, crop_box, output in zip(accepted_players, crop_boxes, outputs):
            if output is None:
                continue
            xy = np.asarray(output.keypoints_xy)
            scores = np.asarray(output.keypoint_scores)
            if xy.shape[0] != len(definitions):
                raise ValueError(
                    f"{metadata.native_keypoint_format} expects {len(definitions)} "
                    f"keypoints, backend returned {xy.shape[0]}"
                )
            x_offset, y_offset = crop_box[0], crop_box[1]
            keypoints: list[dict] = []
            for definition, point, point_confidence in zip(definitions, xy, scores):
                raw_x = float(point[0] + x_offset)
                raw_y = float(point[1] + y_offset)
                raw_confidence = float(point_confidence)
                if not math.isfinite(raw_x) or not math.isfinite(raw_y):
                    raise ValueError("PoseBackend returned a non-finite coordinate")
                if not math.isfinite(raw_confidence):
                    raise ValueError("PoseBackend returned a non-finite confidence")
                x_px = clamp(raw_x, 0.0, float(width))
                y_px = clamp(raw_y, 0.0, float(height))
                confidence = clamp(raw_confidence, 0.0, 1.0)
                serialized = {
                        "index": int(definition["index"]),
                        "name": definition["name"],
                        "downstream_joint_id": definition["downstream_joint_id"],
                        "x_px": safe_float(x_px, 3),
                        "y_px": safe_float(y_px, 3),
                        "x_normalized": safe_float(x_px / width),
                        "y_normalized": safe_float(y_px / height),
                        "confidence": safe_float(confidence, 5),
                    }
                if raw_x != x_px or raw_y != y_px:
                    serialized.update(
                        {
                            "raw_x_px": safe_float(raw_x, 3),
                            "raw_y_px": safe_float(raw_y, 3),
                            "in_frame": False,
                        }
                    )
                if raw_confidence != confidence:
                    serialized.update(
                        {
                            "raw_confidence": safe_float(raw_confidence, 6),
                            "confidence_in_range": False,
                        }
                    )
                keypoints.append(serialized)
            poses.append(
                {
                    "person_track_id": player["track_id"],
                    "confidence": safe_float(clamp(output.pose_score, 0.0, 1.0), 5),
                    "roi_bbox_px": crop_box,
                    "coordinate_space": "original_frame",
                    "keypoint_format": metadata.native_keypoint_format,
                    "keypoints": keypoints,
                }
            )
        return poses
