"""Background-supported image-plane stabilization, never 3D reconstruction.

Only distributed, bidirectionally tracked RANSAC inliers support a similarity
transform. Unknown intervals reset the reference and break motion analysis.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


class CameraMotionGuard:
    def __init__(self) -> None:
        self.previous: np.ndarray | None = None
        self.previous_mask: np.ndarray | None = None
        self.previous_ms: int | None = None
        self.to_reference = np.eye(3, dtype=np.float64)
        self.reference_epoch = 0

    def reset_reference(self) -> None:
        """Discard an unverifiable reference without reusing its coordinate epoch."""
        self.previous = None
        self.previous_mask = None
        self.previous_ms = None
        self.to_reference = np.eye(3, dtype=np.float64)
        self.reference_epoch += 1

    def update(self, frame: np.ndarray, player_boxes: list, timestamp_ms: int) -> dict:
        height, width = frame.shape[:2]
        factor = min(1.0, 640 / max(height, width))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (round(width * factor), round(height * factor)))
        mask = np.full(gray.shape, 255, dtype=np.uint8)
        for box in player_boxes:
            x1, y1, x2, y2 = [float(value) * factor for value in box]
            pad = max(4, (x2 - x1) * 0.2)
            cv2.rectangle(mask, (int(max(0, x1 - pad)), int(max(0, y1 - pad))),
                          (int(min(gray.shape[1] - 1, x2 + pad)), int(min(gray.shape[0] - 1, y2 + pad))), 0, -1)
        previous, previous_mask, previous_ms = self.previous, self.previous_mask, self.previous_ms
        self.previous, self.previous_mask, self.previous_ms = gray, mask, timestamp_ms
        result = {"method": "distributed_background_affine_guard_v1", "status": "unavailable",
                  "fixed_camera_supported": False, "correspondences": 0, "inlier_fraction": None,
                  "translation_fraction_per_s": None, "rotation_deg_per_s": None,
                  "scale_change_per_s": None, "reason": "background_evidence_insufficient",
                  "compensation_valid": False, "matrix_to_reference": None,
                  "reference_epoch": self.reference_epoch,
                  "compensation_semantics": "background_image_plane_similarity_not_metric_or_3d"}
        def unavailable(reason: str = "background_evidence_insufficient") -> dict:
            self.to_reference = np.eye(3, dtype=np.float64)
            self.reference_epoch += 1
            return {**result, "reason": reason, "reference_epoch": self.reference_epoch}
        if previous is None:
            return {**result, "status": "reference", "reason": "first_frame_reference",
                    "compensation_valid": True, "matrix_to_reference": self.to_reference[:2].tolist()}
        dt = (timestamp_ms - previous_ms) / 1000
        if previous.shape != gray.shape or not 0 < dt <= 0.5:
            return unavailable("frame_geometry_or_time_discontinuity")
        corners = cv2.goodFeaturesToTrack(previous, maxCorners=160, qualityLevel=0.02,
                                         minDistance=8, mask=previous_mask)
        if corners is None or len(corners) < 16:
            return unavailable()
        points, status, _ = cv2.calcOpticalFlowPyrLK(previous, gray, corners, None)
        if points is None or status is None:
            return unavailable()
        back, back_status, _ = cv2.calcOpticalFlowPyrLK(gray, previous, points, None)
        if back is None or back_status is None:
            return unavailable()
        a, b = corners.reshape(-1, 2), points.reshape(-1, 2)
        valid = status.ravel().astype(bool) & back_status.ravel().astype(bool)
        valid &= np.isfinite(b).all(axis=1) & (np.linalg.norm(back.reshape(-1, 2) - a, axis=1) < 1.5)
        valid &= (b[:, 0] >= 0) & (b[:, 0] < gray.shape[1]) & (b[:, 1] >= 0) & (b[:, 1] < gray.shape[0])
        indexes = np.flatnonzero(valid)
        valid[indexes] &= mask[b[indexes, 1].astype(int), b[indexes, 0].astype(int)] > 0
        a, b = a[valid], b[valid]
        result["correspondences"] = len(a)
        if len(a) < 16:
            return unavailable()
        transform, inliers = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC,
                                                        ransacReprojThreshold=1.5)
        if transform is None or inliers is None or not np.isfinite(transform).all():
            return unavailable()
        keep = inliers.ravel().astype(bool)
        fraction = float(keep.mean())
        result["inlier_fraction"] = round(fraction, 3)
        # A moving foreground patch must not masquerade as the background.
        supported = a[keep]
        cells = {(min(2, int(x / gray.shape[1] * 3)), min(2, int(y / gray.shape[0] * 3))) for x, y in supported}
        if len(supported) < 16 or fraction < 0.7 or len(cells) < 4:
            return unavailable()
        translation = math.hypot(transform[0, 2], transform[1, 2]) / math.hypot(*gray.shape) / dt
        rotation = abs(math.degrees(math.atan2(transform[1, 0], transform[0, 0]))) / dt
        scale_change = abs(math.hypot(transform[0, 0], transform[1, 0]) - 1) / dt
        # Noise tolerance, not a calibrated biomechanics/quality threshold.
        stationary = translation <= 0.012 and rotation <= 1.5 and scale_change <= 0.015
        frame_scale = math.hypot(transform[0, 0], transform[1, 0])
        if not 0.8 <= frame_scale <= 1.25:
            return unavailable("background_scale_discontinuity")
        # Optical flow is measured on a smaller image. Convert translation
        # back to original pixels before composing the inverse transform.
        pixel_transform = np.eye(3, dtype=np.float64)
        pixel_transform[:2] = transform
        pixel_transform[:2, 2] /= factor
        to_reference = self.to_reference @ np.linalg.inv(pixel_transform)
        if not np.isfinite(to_reference).all() or not 0.25 <= np.linalg.det(to_reference[:2, :2]) <= 4:
            return unavailable("accumulated_scale_outside_supported_range")
        self.to_reference = to_reference
        return {**result, "status": "stationary" if stationary else "moving",
                "fixed_camera_supported": stationary,
                "compensation_valid": True, "matrix_to_reference": to_reference[:2].round(10).tolist(),
                "translation_fraction_per_s": round(translation, 5),
                "rotation_deg_per_s": round(rotation, 3), "scale_change_per_s": round(scale_change, 5),
                "reason": "background_stable" if stationary else "camera_or_background_motion_detected"}
