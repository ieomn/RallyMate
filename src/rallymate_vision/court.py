from __future__ import annotations

import math

import cv2
import numpy as np

from rallymate_vision.utils import safe_float


class CourtDetector:
    """Detects visible court-line geometry or applies a manual polygon.

    The automatic branch is intentionally conservative. Hough lines are useful
    as a visible-region hint, but a fence, net, or a close player's silhouette
    must not be promoted to a calibrated tennis court.
    """

    def __init__(
        self,
        mode: str = "auto",
        manual_polygon_normalized: list[list[float]] | None = None,
        manual_polygon_role: str = "visible_region",
    ) -> None:
        self.mode = mode
        self.manual_polygon_normalized = manual_polygon_normalized
        self.manual_polygon_role = manual_polygon_role

    def detect(
        self,
        frame: np.ndarray,
        player_boxes: list[list[float]] | None = None,
    ) -> dict:
        height, width = frame.shape[:2]
        if self.mode == "disabled":
            return {
                "status": "disabled",
                "method": "disabled",
                "downstream_system": "S05",
                "confidence": 0.0,
                "region_usable": False,
                "calibration_usable": False,
                "polygon_px": [],
                "polygon_normalized": [],
                "line_segments_px": [],
            }
        if self.manual_polygon_normalized is not None:
            polygon_px = [
                [int(point[0] * width), int(point[1] * height)]
                for point in self.manual_polygon_normalized
            ]
            is_calibrated = (
                self.manual_polygon_role
                == "court_outer_doubles_corners"
            )
            homography = None
            if is_calibrated:
                source_points = np.asarray(polygon_px, dtype=np.float32)
                normalized_court = np.asarray(
                    [[0, 0], [1, 0], [1, 1], [0, 1]],
                    dtype=np.float32,
                )
                matrix = cv2.getPerspectiveTransform(
                    source_points,
                    normalized_court,
                )
                homography = [
                    [safe_float(value, 10) for value in row]
                    for row in matrix.tolist()
                ]
            return {
                "status": "calibrated" if is_calibrated else "detected",
                "method": (
                    "manual_court_outer_doubles_corners"
                    if is_calibrated
                    else "manual_visible_region"
                ),
                "downstream_system": "S05",
                "confidence": 1.0,
                "region_usable": True,
                "calibration_usable": is_calibrated,
                "polygon_px": polygon_px,
                "polygon_normalized": self.manual_polygon_normalized,
                "manual_polygon_role": self.manual_polygon_role,
                "homography_image_to_court_normalized": homography,
                "court_dimensions_m": (
                    {"length": 23.77, "doubles_width": 10.97}
                    if is_calibrated
                    else None
                ),
                "line_segments_px": [],
            }
        return self._detect_lines(frame, player_boxes or [])

    def _detect_lines(
        self,
        frame: np.ndarray,
        player_boxes: list[list[float]],
    ) -> dict:
        height, width = frame.shape[:2]
        surface_mask, surface_diagnostics = self._court_surface_mask(frame)
        if surface_mask is None:
            result = self._empty("absent")
            result["diagnostics"]["surface"] = surface_diagnostics
            return result
        surface_margin = cv2.dilate(
            surface_mask,
            np.ones((15, 15), dtype=np.uint8),
            iterations=1,
        )
        gray_original = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
            gray_original
        )
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 70, 180)
        min_length = max(40, int(min(width, height) * 0.10))
        raw_lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=max(40, int(min(width, height) * 0.04)),
            minLineLength=min_length,
            maxLineGap=max(15, int(min(width, height) * 0.025)),
        )
        if raw_lines is None:
            return self._empty("absent")

        candidates: list[tuple[float, list[int], float, float, float]] = []
        perpendicular_offset = max(4, int(min(width, height) * 0.006))
        for raw in raw_lines[:, 0, :]:
            x1, y1, x2, y2 = [int(value) for value in raw]
            length = math.hypot(x2 - x1, y2 - y1)
            if length < min_length:
                continue
            midpoint_y = (y1 + y2) / 2.0
            if midpoint_y < height * 0.20:
                continue

            support_samples = np.linspace(0.0, 1.0, 64)
            support_x = np.clip(
                np.rint(
                    x1 + (x2 - x1) * support_samples
                ).astype(int),
                0,
                width - 1,
            )
            support_y = np.clip(
                np.rint(
                    y1 + (y2 - y1) * support_samples
                ).astype(int),
                0,
                height - 1,
            )
            supported = surface_margin[support_y, support_x] > 0
            surface_support_fraction = float(np.mean(supported))
            if surface_support_fraction < 0.55:
                continue
            supported_indices = np.flatnonzero(supported)
            first_t = float(support_samples[supported_indices[0]])
            last_t = float(support_samples[supported_indices[-1]])
            clipped_x1 = int(round(x1 + (x2 - x1) * first_t))
            clipped_y1 = int(round(y1 + (y2 - y1) * first_t))
            clipped_x2 = int(round(x1 + (x2 - x1) * last_t))
            clipped_y2 = int(round(y1 + (y2 - y1) * last_t))
            clipped_length = math.hypot(
                clipped_x2 - clipped_x1,
                clipped_y2 - clipped_y1,
            )
            if clipped_length < min_length:
                continue
            x1, y1, x2, y2 = (
                clipped_x1,
                clipped_y1,
                clipped_x2,
                clipped_y2,
            )
            length = clipped_length

            samples = np.linspace(0.08, 0.92, 24)
            line_x = x1 + (x2 - x1) * samples
            line_y = y1 + (y2 - y1) * samples
            normal_x = -(y2 - y1) / max(length, 1.0)
            normal_y = (x2 - x1) / max(length, 1.0)

            center_values: list[np.ndarray] = []
            for offset in (-2, 0, 2):
                xs = np.clip(
                    np.rint(line_x + normal_x * offset).astype(int),
                    0,
                    width - 1,
                )
                ys = np.clip(
                    np.rint(line_y + normal_y * offset).astype(int),
                    0,
                    height - 1,
                )
                center_values.append(gray_original[ys, xs])
            center_band = np.max(np.stack(center_values), axis=0)

            side_values: list[np.ndarray] = []
            for direction in (-1, 1):
                xs = np.clip(
                    np.rint(
                        line_x
                        + normal_x * perpendicular_offset * direction
                    ).astype(int),
                    0,
                    width - 1,
                )
                ys = np.clip(
                    np.rint(
                        line_y
                        + normal_y * perpendicular_offset * direction
                    ).astype(int),
                    0,
                    height - 1,
                )
                side_values.append(gray_original[ys, xs])
            side_band = np.mean(np.stack(side_values), axis=0)

            line_brightness = float(np.mean(center_band))
            local_contrast = float(np.mean(center_band - side_band))
            if line_brightness < 70.0 or local_contrast < 2.5:
                continue

            angle = (
                math.degrees(math.atan2(y2 - y1, x2 - x1)) + 180.0
            ) % 180.0
            folded_angle = min(angle, 180.0 - angle)
            score = (
                length
                * (0.45 + line_brightness / 340.0)
                * (0.8 + min(max(local_contrast, 0.0), 40.0) / 40.0)
            )
            candidates.append(
                (
                    score,
                    [x1, y1, x2, y2],
                    folded_angle,
                    local_contrast,
                    midpoint_y / height,
                )
            )

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        selected = candidates[:36]
        segments = [segment for _, segment, _, _, _ in selected]
        if len(segments) < 2:
            return self._empty("absent")

        points = np.array(
            [[x1, y1] for x1, y1, _, _ in segments]
            + [[x2, y2] for _, _, x2, y2 in segments],
            dtype=np.int32,
        )
        hull = cv2.convexHull(points)
        perimeter = cv2.arcLength(hull, True)
        polygon = cv2.approxPolyDP(hull, 0.035 * perimeter, True).reshape(-1, 2)
        if len(polygon) < 4 or len(polygon) > 8:
            rectangle = cv2.boxPoints(cv2.minAreaRect(points.astype(np.float32)))
            polygon = rectangle.astype(np.int32)
        polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
        polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
        polygon_area = abs(float(cv2.contourArea(polygon)))
        area_ratio = polygon_area / max(float(width * height), 1.0)
        polygon_center_y = float(np.mean(polygon[:, 1])) / height
        polygon_bottom_y = float(np.max(polygon[:, 1])) / height

        angles = sorted(angle for _, _, angle, _, _ in selected)
        angle_family_count = 0
        last_family_angle: float | None = None
        for angle in angles:
            if last_family_angle is None or angle - last_family_angle >= 10.0:
                angle_family_count += 1
                last_family_angle = angle
        mean_contrast = float(
            np.mean([contrast for _, _, _, contrast, _ in selected])
        )
        mean_line_y = float(
            np.mean([line_y for _, _, _, _, line_y in selected])
        )
        frame_area = max(float(width * height), 1.0)
        max_player_area_ratio = max(
            (
                max(0.0, box[2] - box[0])
                * max(0.0, box[3] - box[1])
                / frame_area
                for box in player_boxes
            ),
            default=0.0,
        )

        line_factor = min(1.0, len(segments) / 12.0)
        area_factor = min(1.0, area_ratio / 0.18)
        contrast_factor = min(1.0, max(0.0, mean_contrast) / 18.0)
        structure_factor = min(1.0, angle_family_count / 2.0)
        location_factor = min(
            1.0,
            max(0.0, polygon_center_y - 0.25) / 0.25,
        )
        occlusion_factor = min(
            1.0,
            max(0.0, 0.24 - max_player_area_ratio) / 0.12,
        )
        confidence = (
            0.22 * line_factor
            + 0.18 * area_factor
            + 0.20 * contrast_factor
            + 0.16 * structure_factor
            + 0.12 * location_factor
            + 0.12 * occlusion_factor
        )
        validation = {
            "enough_lines": len(segments) >= 5,
            "plausible_area": 0.03 <= area_ratio <= 0.85,
            "multiple_orientations": angle_family_count >= 2,
            "bright_line_contrast": mean_contrast >= 5.0,
            "lower_frame_extent": (
                polygon_center_y >= 0.38 and polygon_bottom_y >= 0.55
            ),
            "not_closeup_occluded": max_player_area_ratio < 0.14,
        }
        validated = all(validation.values())
        if not validated:
            confidence = min(confidence, 0.49)
        status = (
            "detected"
            if validated and confidence >= 0.58
            else "uncertain"
        )

        polygon_px = polygon.astype(int).tolist()
        polygon_normalized = [
            [safe_float(x / width), safe_float(y / height)] for x, y in polygon_px
        ]
        return {
            "status": status,
            "method": "hough_line_geometry_hint",
            "downstream_system": "S05",
            "confidence": safe_float(confidence, 4),
            "region_usable": status == "detected",
            "calibration_usable": False,
            "polygon_px": polygon_px,
            "polygon_normalized": polygon_normalized,
            "line_segments_px": segments,
            "diagnostics": {
                "line_count": len(segments),
                "polygon_area_ratio": safe_float(area_ratio, 4),
                "polygon_center_y": safe_float(polygon_center_y, 4),
                "polygon_bottom_y": safe_float(polygon_bottom_y, 4),
                "angle_family_count": angle_family_count,
                "mean_local_contrast": safe_float(mean_contrast, 3),
                "mean_line_y": safe_float(mean_line_y, 4),
                "max_player_area_ratio": safe_float(
                    max_player_area_ratio, 4
                ),
                "surface": surface_diagnostics,
                "validation": validation,
            },
        }

    @staticmethod
    def _court_surface_mask(
        frame: np.ndarray,
    ) -> tuple[np.ndarray | None, dict]:
        """Find the dominant lower-frame surface connected to the foreground.

        Court paint can be green, blue, red, or clay. Using a Lab-color
        reference sampled from the lower central image avoids hard-coding a
        particular surface color while rejecting sky, banners, and fencing.
        """

        height, width = frame.shape[:2]
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
        x1, x2 = int(width * 0.12), int(width * 0.88)
        y1, y2 = int(height * 0.72), int(height * 0.94)
        sample = lab[y1:y2, x1:x2].reshape(-1, 3)
        if sample.size == 0:
            return None, {"status": "no_reference_sample"}
        reference = np.median(sample, axis=0)
        delta = lab - reference
        distance = np.sqrt(
            (delta[:, :, 0] * 0.55) ** 2
            + delta[:, :, 1] ** 2
            + delta[:, :, 2] ** 2
        )
        raw_mask = (distance <= 31.0).astype(np.uint8)
        open_size = max(3, int(min(width, height) * 0.006))
        if open_size % 2 == 0:
            open_size += 1
        close_size = max(7, int(min(width, height) * 0.018))
        if close_size % 2 == 0:
            close_size += 1
        raw_mask = cv2.morphologyEx(
            raw_mask,
            cv2.MORPH_OPEN,
            np.ones((open_size, open_size), dtype=np.uint8),
        )
        raw_mask = cv2.morphologyEx(
            raw_mask,
            cv2.MORPH_CLOSE,
            np.ones((close_size, close_size), dtype=np.uint8),
        )

        label_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            raw_mask,
            connectivity=8,
        )
        best_label: int | None = None
        best_score = 0.0
        frame_area = float(width * height)
        foreground_band = labels[int(height * 0.78) :, :]
        for label in range(1, label_count):
            area = float(stats[label, cv2.CC_STAT_AREA])
            area_ratio = area / max(frame_area, 1.0)
            if area_ratio < 0.05:
                continue
            foreground_fraction = float(
                np.mean(foreground_band == label)
            )
            centroid_y = float(centroids[label][1]) / height
            if foreground_fraction < 0.04 or centroid_y < 0.48:
                continue
            score = area_ratio * (1.0 + foreground_fraction)
            if score > best_score:
                best_label = label
                best_score = score

        diagnostics = {
            "status": "detected" if best_label is not None else "absent",
            "reference_lab": [
                safe_float(value, 2) for value in reference.tolist()
            ],
            "candidate_component_count": max(0, label_count - 1),
        }
        if best_label is None:
            diagnostics["mask_area_ratio"] = 0.0
            return None, diagnostics

        mask = (labels == best_label).astype(np.uint8) * 255
        diagnostics["mask_area_ratio"] = safe_float(
            np.count_nonzero(mask) / max(frame_area, 1.0),
            4,
        )
        diagnostics["centroid_normalized"] = [
            safe_float(centroids[best_label][0] / width, 4),
            safe_float(centroids[best_label][1] / height, 4),
        ]
        return mask, diagnostics

    @staticmethod
    def _empty(status: str) -> dict:
        return {
            "status": status,
            "method": "hough_line_geometry_hint",
            "downstream_system": "S05",
            "confidence": 0.0,
            "region_usable": False,
            "calibration_usable": False,
            "polygon_px": [],
            "polygon_normalized": [],
            "line_segments_px": [],
            "diagnostics": {
                "line_count": 0,
                "polygon_area_ratio": 0.0,
                "validation": {},
            },
        }
