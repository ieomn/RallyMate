"""Explicit fixed-camera manual court calibration for display only."""
from __future__ import annotations

import cv2

from .court import CourtDetector
from .court_projection import FixedCameraReferenceGuard, validated_calibration


class FixedManualCourtObservation:
    """Invalidate a manual polygon permanently after its camera reference breaks.

    The polygon belongs to the first processed frame. Neither a later camera
    stop nor a new reference makes those original pixels valid again. Automatic
    localization stays disabled, and this output never authorizes scoring.
    """

    def __init__(self, config):
        self.enabled = (config.mode == "manual" and config.manual_polygon_role == "court_outer_doubles_corners"
                        and config.manual_polygon_normalized is not None)
        self.detector = CourtDetector(mode="manual" if self.enabled else "disabled",
                                      manual_polygon_normalized=config.manual_polygon_normalized,
                                      manual_polygon_role=config.manual_polygon_role)
        self.camera_guard = FixedCameraReferenceGuard()
        self.invalid_reason = None

    def _unavailable(self, reason):
        return {"status": "uncertain" if self.enabled else "disabled",
                "method": "manual_fixed_camera_display_only" if self.enabled else "disabled_by_configuration",
                "downstream_system": "S05", "confidence": 0.0, "region_usable": False,
                "calibration_usable": False, "polygon_px": [], "polygon_normalized": [], "line_segments_px": [],
                "scoring_usable": False, "purpose": "display_only", "reason": reason}

    def observe(self, frame, camera_motion):
        if not self.enabled:
            return self._unavailable("manual_outer_corners_not_requested")
        if self.invalid_reason:
            return self._unavailable(self.invalid_reason)
        height, width = frame.shape[:2]
        if not self.camera_guard.observe(camera_motion, width, height):
            self.invalid_reason = self.camera_guard.invalid_reason
            return self._unavailable(self.invalid_reason)
        try:
            result = self.detector.detect(frame)
            calibration = validated_calibration(result, width, height)
        except (cv2.error, ValueError, TypeError, IndexError, OverflowError):
            calibration = None
        if calibration is None:
            self.invalid_reason = "manual_calibration_invalid_outer_corners"
            return self._unavailable(self.invalid_reason)
        return {**result, "purpose": "display_only", "scoring_usable": False,
                "camera_reference_epoch": self.camera_guard.reference_epoch,
                "camera_assumption": "fixed_first_processed_frame_invalidated_on_motion_or_reference_break",
                "reason": "manual_outer_corners_with_verified_fixed_camera"}
