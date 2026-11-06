"""Conservative image-space checks for SAM estimates, never a confidence score.

Coordinates are kept verbatim. Rejected estimates remain auditable native rows,
but must not become scored poses or rendered surfaces. Checks use torso/leg
geometry, not fast-moving racket hands; clipping at the source-image edge is
allowed. Temporal state is local to a video, camera epoch and selection epoch.
"""
from __future__ import annotations

from collections import OrderedDict
import math

import numpy as np


VERSION = "sam3d-pose-quality-v1"
CORE = ("nose", "left_shoulder", "right_shoulder", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle")


class Sam3DPoseQualityGate:
    def __init__(self):
        self._previous = OrderedDict()

    def evaluate(self, record: dict) -> dict:
        reasons, metrics = [], {}
        receipt = {"version": VERSION, "accepted": False, "reasons": reasons,
                   "metrics": metrics, "semantics": "geometry_sanity_not_accuracy_or_confidence"}
        try:
            box = np.asarray(record["bbox_px"], dtype=float)
            size = np.asarray(record["image_size"], dtype=float)
            points = np.asarray([record["keypoints_2d"][name] for name in CORE], dtype=float)
            if (box.shape != (4,) or size.shape != (2,) or points.shape != (len(CORE), 2)
                    or not all(np.isfinite(a).all() for a in (box, size, points))
                    or np.any(size <= 0) or np.any(box[2:] <= box[:2])):
                raise ValueError("invalid geometry")
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.append("invalid_pose_quality_input")
            return receipt

        span = box[2:] - box[:2]
        # SAM estimates full bodies even when feet/arms are outside the image.
        # Compare observable positions with the detector, preserving raw points.
        visible = points.copy()
        edge_margin = np.maximum(2., size * .005)
        for axis in (0, 1):
            if box[axis] <= edge_margin[axis]:
                visible[:, axis] = np.maximum(visible[:, axis], 0.)
            if box[axis + 2] >= size[axis] - edge_margin[axis]:
                visible[:, axis] = np.minimum(visible[:, axis], size[axis])
        padding = np.maximum(span * [.35, .20], 6.)
        inside = np.all((visible >= box[:2] - padding) & (visible <= box[2:] + padding), axis=1)
        fraction = float(inside.mean())
        metrics["core_inside_expanded_bbox_fraction"] = round(fraction, 6)
        if fraction < .5:
            reasons.append("pose_projection_outside_person_bbox")

        shoulders = points[1:3].mean(axis=0)
        hips = points[3:5].mean(axis=0)
        ankles = points[7:9].mean(axis=0)
        head_foot = float((points[0, 1] - ankles[1]) / span[1])
        torso_inversion = float((shoulders[1] - hips[1]) / span[1])
        metrics["head_below_ankles_over_bbox_height"] = round(head_foot, 6)
        # Both torso and head/feet must be inverted. A deep lunge or horizontal
        # torso alone is not rejected. This policy is for upright tennis video.
        if head_foot > .20 and torso_inversion > .08:
            reasons.append("inverted_body_projection")

        normalized = (points - box[:2]) / span
        key = tuple(record.get(name) for name in (
            "video_sha256", "person_track_id", "selection_epoch", "camera_reference_epoch"))
        timestamp = record.get("timestamp_ms")
        previous = self._previous.get(key)
        if previous and isinstance(timestamp, (int, float)) and math.isfinite(timestamp):
            gap = timestamp - previous["timestamp"]
            if 0 < gap <= 150:
                # Detector-relative torso/leg displacement tolerates running
                # and camera translation; only a gross whole-body jump is vetoed.
                jump = float(np.median(np.linalg.norm(normalized - previous["points"], axis=1)))
                metrics["median_bbox_relative_jump"] = round(jump, 6)
                if jump > .85:
                    reasons.append("pose_temporal_projection_jump")

        receipt["accepted"] = not reasons
        if not reasons and isinstance(timestamp, (int, float)) and math.isfinite(timestamp):
            self._previous[key] = {"timestamp": timestamp, "points": normalized}
            self._previous.move_to_end(key)
            while len(self._previous) > 128:
                self._previous.popitem(last=False)
        return receipt
