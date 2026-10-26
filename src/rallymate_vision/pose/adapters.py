from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from rallymate_vision.pose.base import PoseBackend
from rallymate_vision.pose.association import (
    POLICY_VERSION, audit_pose_person_associations, person_box_ownership, valid_box,
)
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
        self.last_association_diagnostics: dict = {}

    def estimate(
        self,
        frame: np.ndarray,
        player_detections: Sequence[dict],
        max_players: int,
        *,
        timestamp_ms: int | None = None,
    ) -> list[dict]:
        height, width = frame.shape[:2]
        self.last_association_diagnostics = {"policy_version": POLICY_VERSION, "rois": [], "duplicate_audit": None}
        if isinstance(max_players, bool) or not isinstance(max_players, int) or max_players < 0:
            raise ValueError("max_players must be a non-negative integer")
        ids = []
        for player in player_detections:
            track = player.get("track_id")
            if isinstance(track, bool) or not isinstance(track, int) or track < 0 or not valid_box(player.get("bbox_px"), (width, height)):
                raise ValueError("player target must have a valid track id and original-frame box")
            ids.append(track)
        if len(ids) != len(set(ids)):
            raise ValueError("player target track ids must be unique within a frame")
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
                self.last_association_diagnostics["rois"].append({"person_track_id": player["track_id"], "target_box_px": list(player["bbox_px"]), "roi_bbox_px": crop_box, "reason_code": "pose_roi_too_small"})
                continue
            crops.append(frame[y1:y2, x1:x2])
            crop_boxes.append(crop_box)
            accepted_players.append(player)
        if not crops:
            return []

        timestamps = [timestamp_ms] * len(crops) if timestamp_ms is not None else None
        targeted = getattr(self.backend, "infer_targeted", None)
        if callable(targeted):
            targets = [[player["bbox_px"][0] - crop[0], player["bbox_px"][1] - crop[1], player["bbox_px"][2] - crop[0], player["bbox_px"][3] - crop[1]] for player, crop in zip(accepted_players, crop_boxes)]
            outputs = targeted(crops, targets, timestamps_ms=timestamps)
        else:
            outputs = self.backend.infer(crops, timestamps_ms=timestamps)
        if len(outputs) != len(crops):
            raise ValueError("PoseBackend must return exactly one output slot per ROI")
        metadata = self.backend.metadata()
        schema = keypoint_schema(metadata.native_keypoint_format)
        definitions = schema["keypoints"]
        poses: list[dict] = []
        backend_diagnostics = getattr(self.backend, "last_association_diagnostics", []) if callable(targeted) else []
        for index, (player, crop_box, output) in enumerate(zip(accepted_players, crop_boxes, outputs)):
            diagnostic = {"person_track_id": player["track_id"], "target_box_px": list(player["bbox_px"]), "roi_bbox_px": crop_box, "reason_code": "pose_backend_no_output" if output is None else "pose_legacy_single_roi_output"}
            if isinstance(backend_diagnostics, list) and index < len(backend_diagnostics):
                diagnostic["backend"] = backend_diagnostics[index]
                diagnostic["reason_code"] = backend_diagnostics[index].get("reason_code", diagnostic["reason_code"])
            self.last_association_diagnostics["rois"].append(diagnostic)
            if output is None:
                continue
            if callable(targeted) and output.person_bbox_xyxy is None:
                diagnostic["reason_code"] = "pose_targeted_candidate_box_missing"
                continue
            association = None
            if output.person_bbox_xyxy is not None:
                local_box = output.person_bbox_xyxy
                if not valid_box(local_box, (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])):
                    diagnostic["reason_code"] = "pose_candidate_box_invalid"
                    continue
                global_box = [local_box[0] + crop_box[0], local_box[1] + crop_box[1], local_box[2] + crop_box[0], local_box[3] + crop_box[1]]
                ownership = person_box_ownership(global_box, player_detections, frame_size=(width, height))
                diagnostic["candidate_bbox_px"] = global_box
                diagnostic["person_box_ownership"] = ownership
                if ownership["preferred_person_track_id"] != player["track_id"]:
                    diagnostic["reason_code"] = "pose_candidate_wrong_or_ambiguous_person"
                    continue
                association = {"policy_version": POLICY_VERSION, "target_box_px": list(player["bbox_px"]), "candidate_bbox_px": global_box, "person_track_id": player["track_id"], "person_box_ownership": ownership, "roi_selection": output.person_association}
                diagnostic["reason_code"] = "pose_target_verified"
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
            pose = {
                    "person_track_id": player["track_id"],
                    "confidence": safe_float(clamp(output.pose_score, 0.0, 1.0), 5),
                    "roi_bbox_px": crop_box,
                    "coordinate_space": "original_frame",
                    "keypoint_format": metadata.native_keypoint_format,
                    "keypoints": keypoints,
                }
            if association is not None:
                pose["person_association"] = association
            poses.append(pose)
        audit = audit_pose_person_associations(poses, player_detections, frame_size=(width, height))
        self.last_association_diagnostics["duplicate_audit"] = audit
        rejected = set(audit["rejected_person_track_ids"])
        for diagnostic in self.last_association_diagnostics["rois"]:
            if diagnostic["person_track_id"] in rejected:
                diagnostic["reason_code"] = "pose_duplicate_body_different_tracks"
        poses = [pose for pose in poses if pose["person_track_id"] not in rejected]
        return poses
