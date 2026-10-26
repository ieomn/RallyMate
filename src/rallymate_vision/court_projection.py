"""Display-only ground-plane projection, bound to each observed video frame.

A flying ball is not on the court plane. These positions must never be used as
landing, contact, ball speed, or scoring evidence. No calibration is carried
forward through camera motion or missing frames.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from rallymate_features.coordinates import validated_camera_transform


class FixedCameraReferenceGuard:
    """Follow every frame, including frames without a court or ball observation."""

    def __init__(self):
        self.reference_epoch = None
        self.frame_size = None
        self.invalid_reason = None

    def observe(self, camera, width, height):
        if self.invalid_reason:
            return False
        camera = camera if isinstance(camera, Mapping) else {}
        epoch = camera.get("reference_epoch")
        transform = validated_camera_transform(camera)
        reason = None
        if any(_finite(v) is None or v <= 0 for v in (width, height)):
            reason = "manual_calibration_resolution_unavailable"
        elif (camera.get("status") not in {"reference", "stationary"}
              or transform is None or not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0):
            reason = "manual_calibration_camera_motion_or_unverified"
        elif self.frame_size is not None and self.frame_size != (width, height):
            reason = "manual_calibration_resolution_changed"
        elif self.reference_epoch is not None and self.reference_epoch != epoch:
            reason = "manual_calibration_camera_reference_changed"
        elif self.reference_epoch is None and camera.get("status") != "reference":
            reason = "manual_calibration_first_frame_reference_missing"
        else:
            tolerance = max(1.0, math.hypot(width, height) * .003)
            for x, y in ((0, 0), (width, 0), (width, height), (0, height)):
                dx = transform[0, 0] * x + transform[0, 1] * y + transform[0, 2] - x
                dy = transform[1, 0] * x + transform[1, 1] * y + transform[1, 2] - y
                if math.hypot(dx, dy) > tolerance:
                    reason = "manual_calibration_accumulated_camera_drift"
                    break
        if reason:
            self.invalid_reason = reason
            return False
        self.frame_size, self.reference_epoch = (width, height), epoch
        return True

    def calibration(self, court, width, height):
        if isinstance(court, Mapping) and str(court.get("method", "")).startswith("manual"):
            if self.invalid_reason or self.reference_epoch is None:
                return None
            declared_epoch = court.get("camera_reference_epoch")
            if declared_epoch is not None and (isinstance(declared_epoch, bool)
                                               or not isinstance(declared_epoch, int)
                                               or declared_epoch != self.reference_epoch):
                return None
        return validated_calibration(court, width, height)


def _finite(value):
    try:
        return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _transform(matrix, x, y):
    denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(denominator) < 1e-10:
        return None
    result = tuple((row[0] * x + row[1] * y + row[2]) / denominator for row in matrix[:2])
    return result if all(math.isfinite(value) for value in result) else None


def validated_calibration(court, width, height):
    """Require usable calibration and verify its four pixel/court correspondences."""
    if not isinstance(court, Mapping) or court.get("status") != "calibrated" or court.get("calibration_usable") is not True:
        return None
    if any(_finite(value) is None or value <= 0 for value in (width, height)):
        return None
    matrix, corners = court.get("homography_image_to_court_normalized"), court.get("polygon_px")
    if not isinstance(matrix, list) or len(matrix) != 3 or any(not isinstance(row, list) or len(row) != 3 for row in matrix):
        return None
    if any(_finite(v) is None for row in matrix for v in row):
        return None
    if not isinstance(corners, list) or len(corners) != 4 or any(not isinstance(p, list) or len(p) != 2 or any(_finite(v) is None for v in p) for p in corners):
        return None
    if any(not 0 <= x <= width or not 0 <= y <= height for x, y in corners):
        return None
    # Consistent signed turns reject self-intersecting/collinear quadrilaterals.
    crosses = []
    for i in range(4):
        a, b, c = (corners[(i + j) % 4] for j in range(3))
        crosses.append((b[0]-a[0]) * (c[1]-b[1]) - (b[1]-a[1]) * (c[0]-b[0]))
    if not (all(v > 1 for v in crosses) or all(v < -1 for v in crosses)):
        return None
    for corner, expected in zip(corners, ((0, 0), (1, 0), (1, 1), (0, 1))):
        point = _transform(matrix, *corner)
        if point is None or math.hypot(point[0]-expected[0], point[1]-expected[1]) > .002:
            return None
    return matrix, width, height


def build_court_projection(reconstruction, calibrations):
    """Project bounded reconstruction observations using same-frame calibration."""
    points = []
    segment_number = 0
    calibrated_count = 0
    for segment in reconstruction.get("segments", []):
        previous = None
        for point in segment.get("points", []):
            key = (point.get("frame_index"), point.get("processed_index"), point.get("timestamp_ms"))
            calibration = calibrations.get(key) if point.get("source") == "observed" else None
            x, y = _finite(point.get("x")), _finite(point.get("y"))
            if calibration is None or x is None or y is None:
                previous = None
                continue
            matrix, width, height = calibration
            mapped = _transform(matrix, x * width, y * height)
            # Keep ordinary out-of-court points, never clamp them onto a line.
            # Points close to the projective horizon have no useful display.
            if mapped is None or any(abs(value) > 4 for value in mapped):
                previous = None
                continue
            timestamp = point["timestamp_ms"]
            calibrated_count += 1
            if previous is None or not 0 < timestamp - previous <= 250:
                segment_number += 1
            points.append({"x": round(mapped[0], 6), "y": round(mapped[1], 6),
                           "timestamp_ms": timestamp, "source": "observed",
                           "track_id": point.get("track_id"), "segment_id": f"court-{segment_number}"})
            previous = timestamp
    return {
        "version": "court-projection-v1.0.0",
        "status": "calibrated" if points else "unavailable",
        "coordinate_space": "court_normalized",
        "semantics": "ground_plane_projection_not_ball_landing",
        "reason_zh": ("球路按球位于场地平面的假设投影，仅供回看；空中球的显示位置不是真实落点，不参与评分。"
                      if points else "尚无同时具备球观测和有效场地标定的帧；固定机位可在球场设置中标记四个外角。"),
        "calibrated_observation_count": calibrated_count,
        "points": points,
    }
