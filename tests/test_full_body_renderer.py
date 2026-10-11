from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rallymate_vision.full_body_renderer import FULL_BODY_STYLE, LEGACY_STYLE
from rallymate_vision.motion_replay import (
    MotionReplayError, build_motion_replay, render_legacy_motion_frame, render_motion_frame,
)
from tests.test_motion_replay import write_source


class FullBodyRendererTests(unittest.TestCase):
    def test_full_surface_retains_raw_evidence_and_fixed_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, _, _ = write_source(Path(temporary))
            full = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            legacy = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000,
                                         appearance_style="legacy_capsule")
            self.assertEqual(full["render"]["style"], FULL_BODY_STYLE)
            self.assertFalse(full["render"]["is_3d_reconstruction"])
            self.assertIn("not_recovered_body_shape", full["render"]["surface_semantics"])
            self.assertEqual(full["observations"], legacy["observations"])
            self.assertEqual(full["display_observations"], legacy["display_observations"])
            self.assertEqual(full["frame_map"], legacy["frame_map"])
            self.assertEqual(full["source"], legacy["source"])
            before = copy.deepcopy(full)
            image = render_motion_frame(full["display_frames"][0], full["render"])
            self.assertEqual(full, before)
            foreground = np.min(image, axis=2) > 100
            # Full-body padding encloses the skin, palms and feet without
            # altering the source coordinates or a per-frame camera crop.
            self.assertFalse(foreground[:10].any())
            self.assertFalse(foreground[-10:].any())
            self.assertFalse(foreground[:, :10].any())
            self.assertFalse(foreground[:, -10:].any())
            count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground.astype(np.uint8), 8)
            self.assertGreater(count, 1)
            self.assertGreater(stats[1:, cv2.CC_STAT_AREA].max() / foreground.sum(), .999)

    def test_side_view_torso_has_thickness_and_more_skin_than_capsules(self):
        render = {"width": 512, "height": 512, "body_scale_source_px": 110,
                  "transform": {"scale": 1}, "style": FULL_BODY_STYLE}
        coordinates = {"head": (243, 55), "neck": (251, 102),
                       "left_shoulder": (255, 120), "right_shoulder": (257, 120),
                       "left_elbow": (228, 165), "right_elbow": (240, 167),
                       "left_wrist": (178, 170), "right_wrist": (191, 182),
                       "left_hip": (276, 230), "right_hip": (278, 230),
                       "left_knee": (268, 318), "right_knee": (280, 318),
                       "left_ankle": (284, 405), "right_ankle": (298, 405),
                       "left_heel": (289, 411), "right_heel": (304, 410),
                       "left_big_toe": (259, 415), "right_big_toe": (279, 415)}
        frame = {"keypoints": [{"name": name, "x_px": x, "y_px": y} for name, (x, y) in coordinates.items()]}
        full = render_motion_frame(frame, render)
        legacy = render_legacy_motion_frame(frame, render)
        full_mask, legacy_mask = np.min(full, axis=2) > 100, np.min(legacy, axis=2) > 100
        self.assertGreater(full_mask.sum(), legacy_mask.sum() * 1.25)
        # The two shoulders are only 2 pixels apart: the body must nevertheless
        # retain rib-cage volume, rather than a collapsed torso quadrilateral.
        self.assertGreater(full_mask[192, 230:300].sum(), 30)
        self.assertGreater(int(full.max()), 230)

    def test_legacy_recorded_style_reproduces_original_renderer(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, _, _ = write_source(Path(temporary))
            trajectory = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000,
                                              appearance_style="legacy_capsule")
            self.assertEqual(trajectory["render"]["style"], LEGACY_STYLE)
            for frame in trajectory["display_frames"][::20]:
                np.testing.assert_array_equal(render_motion_frame(frame, trajectory["render"]),
                                              render_legacy_motion_frame(frame, trajectory["render"]))
            with self.assertRaisesRegex(MotionReplayError, "invalid_appearance_style"):
                build_motion_replay(frames, appearance_style="unknown")

    def test_no_landmarks_does_not_invent_a_body(self):
        render = {"width": 512, "height": 512, "body_scale_source_px": 110,
                  "transform": {"scale": 1}, "style": FULL_BODY_STYLE}
        image = render_motion_frame({"keypoints": []}, render)
        self.assertFalse((image > 100).any())

    def test_degenerate_head_uses_observed_face_without_changing_keypoints(self):
        render = {"width": 512, "height": 512, "body_scale_source_px": 100,
                  "transform": {"scale": 1}, "style": FULL_BODY_STYLE}
        frame = {"keypoints": [{"name": "head", "x_px": 256, "y_px": 200},
                               {"name": "neck", "x_px": 256, "y_px": 200},
                               {"name": "nose", "x_px": 256, "y_px": 166}]}
        before = copy.deepcopy(frame)
        degenerate = render_motion_frame(frame, render)
        without_head = {"keypoints": frame["keypoints"][1:]}
        fallback = render_motion_frame(without_head, render)
        np.testing.assert_array_equal(degenerate, fallback)
        self.assertGreater((np.min(degenerate, axis=2) > 100).sum(), 500)
        self.assertEqual(frame, before)
        no_face = {"keypoints": frame["keypoints"][:2]}
        self.assertFalse((render_motion_frame(no_face, render) > 100).any())

    def test_observed_heel_keeps_rear_foot_when_toes_are_missing(self):
        render = {"width": 512, "height": 512, "body_scale_source_px": 100,
                  "transform": {"scale": 1}, "style": FULL_BODY_STYLE}
        frame = {"keypoints": [{"name": "left_ankle", "x_px": 250, "y_px": 280},
                               {"name": "left_heel", "x_px": 270, "y_px": 285}]}
        before = copy.deepcopy(frame)
        image = render_motion_frame(frame, render)
        foreground = np.min(image, axis=2) > 100
        self.assertTrue(foreground[280, 250])
        self.assertTrue(foreground[285, 270])
        self.assertFalse(foreground[:, :240].any())
        self.assertFalse(foreground[:, 280:].any())
        self.assertEqual(frame, before)
        # An ankle alone supplies no observed foot direction.
        no_heel = {"keypoints": frame["keypoints"][:1]}
        self.assertFalse((render_motion_frame(no_heel, render) > 100).any())


if __name__ == "__main__":
    unittest.main()
