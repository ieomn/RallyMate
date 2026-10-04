from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from rallymate_scoring.stroke_candidates import _associate_rackets, _sample, detect_stroke_candidates
from rallymate_vision.camera_motion import CameraMotionGuard
from rallymate_events.rules import detect_pose_events
from test_events import _motion_sequence
from test_stroke_candidates import frame, serve, swing


def _similarity(angle=0.0, scale=1.0, tx=0.0, ty=0.0):
    c, s = math.cos(angle) * scale, math.sin(angle) * scale
    return np.array([[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]])


def _camera_frame(record, transform, epoch=0):
    result = deepcopy(record)
    result["frame"]["timestamp_source"] = "decoder_pts"
    for pose in result["poses"]:
        for point in pose["keypoints"]:
            xy = transform @ [point["x_px"], point["y_px"], 1]
            point["x_px"], point["y_px"] = float(xy[0]), float(xy[1])
    for detection in result["detections"]:
        x1, y1, x2, y2 = detection["bbox_px"]
        corners = np.array([[x1, y1, 1], [x1, y2, 1], [x2, y1, 1], [x2, y2, 1]]) @ transform.T
        detection["bbox_px"] = [float(corners[:, 0].min()), float(corners[:, 1].min()),
                                 float(corners[:, 0].max()), float(corners[:, 1].max())]
    result["camera_motion"] = {"status": "moving", "compensation_valid": True,
                                "reference_epoch": epoch, "matrix_to_reference": np.linalg.inv(transform)[:2].tolist()}
    return result


class CameraCompensatedStrokeTests(unittest.TestCase):
    def test_similarity_compensation_preserves_pose_and_raw_frame_racket_association(self):
        original = frame(0)
        reference = _sample(original["poses"][0], original["frame"])
        self.assertIsNotNone(reference)
        for transform in [_similarity(tx=150, ty=-60), _similarity(angle=.3),
                          _similarity(scale=1.2), _similarity(angle=-.2, scale=.8, tx=130, ty=40)]:
            with self.subTest(transform=transform.tolist()):
                record = _camera_frame(original, transform)
                sample = _sample(record["poses"][0], {**record["frame"], "camera_motion_status": "compensated", "camera_motion": record["camera_motion"]})
                self.assertIsNotNone(sample)
                np.testing.assert_allclose(sample.center, reference.center, atol=1e-9)
                self.assertAlmostEqual(sample.scale, reference.scale)
                for name in reference.points:
                    np.testing.assert_allclose(sample.points[name], reference.points[name], atol=1e-9)
                _associate_rackets([sample], record["detections"])
                self.assertEqual(sample.racket_sides, {"right"})

    def test_service_candidate_path_retains_swing_and_serve_under_pan_rotation_and_zoom(self):
        motions = [lambda i: _similarity(tx=i * 3, ty=i),
                   lambda i: _similarity(angle=i * .003),
                   lambda i: _similarity(scale=1 + i * .002),
                   lambda i: _similarity(angle=i * -.002, scale=1 + i * .001, tx=i * 2)]
        for factory in [swing, serve]:
            source = factory()
            timeline = [{"processed_index": i, "source_track_id": 1, "selection_status": "selected"} for i in range(len(source))]
            baseline = detect_stroke_candidates(source, primary_timeline=timeline)
            self.assertEqual(baseline["candidate_count"], 1)
            expected = baseline["candidates"][0]
            for motion in motions:
                with self.subTest(motion=factory.__name__, transform=motions.index(motion)):
                    transformed = [_camera_frame(record, motion(i)) for i, record in enumerate(source)]
                    result = detect_stroke_candidates(transformed, primary_timeline=timeline)
                    self.assertEqual(result["by_family"], baseline["by_family"])
                    actual = result["candidates"][0]
                    self.assertEqual(actual["racket_hand_candidate"], expected["racket_hand_candidate"])
                    self.assertEqual(actual["evidence"]["racket_associated_frames"], expected["evidence"]["racket_associated_frames"])
                    for key in ["start_ms", "peak_ms", "end_ms"]:
                        self.assertEqual(actual[key], expected[key])

    def test_invalid_declared_transform_breaks_the_selection_epoch(self):
        for invalid in ["shear", "missing_flag", "missing_epoch", "unknown_status"]:
            with self.subTest(invalid=invalid):
                records = [_camera_frame(record, np.eye(3)) for record in swing()]
                camera = records[14]["camera_motion"]
                camera["status"] = "stationary"
                if invalid == "shear":
                    camera["matrix_to_reference"] = [[1, .2, 0], [0, 1, 0]]
                elif invalid == "missing_flag":
                    camera.pop("compensation_valid")
                elif invalid == "missing_epoch":
                    camera.pop("reference_epoch")
                else:
                    camera["status"] = "unrecognized"
                with patch("rallymate_scoring.stroke_analysis.build_motion_analysis", return_value={}) as analysis:
                    detect_stroke_candidates(records)
                samples = analysis.call_args.args[1][1]
                before = next(sample for sample in samples if sample.frame_index == 13)
                after = next(sample for sample in samples if sample.frame_index == 15)
                self.assertNotEqual(before.selection_epoch, after.selection_epoch, "invalid compensated frame cannot be silently bridged")
                self.assertNotIn(14, [sample.frame_index for sample in samples])

    def test_reference_epoch_change_breaks_motion_even_without_missing_frames(self):
        records = [_camera_frame(record, np.eye(3), epoch=int(i >= 14)) for i, record in enumerate(swing())]
        with patch("rallymate_scoring.stroke_analysis.build_motion_analysis", return_value={}) as analysis:
            detect_stroke_candidates(records)
        samples = analysis.call_args.args[1][1]
        self.assertNotEqual(samples[13].selection_epoch, samples[14].selection_epoch)

    def test_ball_context_and_compensated_player_use_the_same_reference_coordinates(self):
        record = frame(0)
        record["detections"].append({"class_name": "ball", "track_id": 2, "confidence": .9, "bbox_px": [415, 215, 425, 225]})
        moved = _camera_frame(record, _similarity(angle=.1, scale=1.1, tx=150, ty=30))
        with patch("rallymate_scoring.stroke_analysis.build_motion_analysis", return_value={}) as analysis:
            detect_stroke_candidates([moved])
        sample = analysis.call_args.args[1][1][0]
        np.testing.assert_allclose(sample.center, [300, 250], atol=1e-9)
        balls = analysis.call_args.kwargs["ball_observations"]
        self.assertEqual(len(balls), 1)
        np.testing.assert_allclose(balls[0]["point"], [420, 220], atol=1e-9,
                                   err_msg="incoming-ball geometry must not mix raw pixels with compensated players")

    def test_optical_flow_accumulation_maps_current_pixels_back_to_the_original_reference(self):
        rng = np.random.default_rng(19)
        source = cv2.cvtColor(rng.integers(0, 256, (480, 640), dtype=np.uint8), cv2.COLOR_GRAY2BGR)
        guard = CameraMotionGuard()
        reference = guard.update(source, [], 0)
        self.assertTrue(reference["compensation_valid"])
        probes = np.array([[120, 120, 1], [300, 200, 1], [450, 350, 1]], dtype=float)
        for i in range(1, 5):
            transform = _similarity(angle=i * .002, scale=1 + i * .001, tx=i * 2, ty=i)
            moved = cv2.warpAffine(source, transform[:2], (640, 480))
            result = guard.update(moved, [], i * 40)
            self.assertTrue(result["compensation_valid"])
            current = probes @ transform.T
            recovered = current @ np.array(result["matrix_to_reference"]).T
            np.testing.assert_allclose(recovered, probes[:, :2], atol=.4)

    def test_fallback_reference_reset_never_reuses_old_camera_coordinates_or_epoch(self):
        rng = np.random.default_rng(21)
        source = cv2.cvtColor(rng.integers(0, 256, (240, 320), dtype=np.uint8), cv2.COLOR_GRAY2BGR)
        moved = cv2.warpAffine(source, np.float32([[1, 0, 4], [0, 1, 2]]), (320, 240))
        guard = CameraMotionGuard()
        first = guard.update(source, [], 0)
        before_reset = guard.update(moved, [], 40)
        self.assertTrue(before_reset["compensation_valid"])
        self.assertNotAlmostEqual(before_reset["matrix_to_reference"][0][2], 0, delta=.1)
        # The pipeline invokes this boundary whenever source time falls back.
        guard.reset_reference()
        resumed = guard.update(moved, [], 120)
        self.assertEqual(resumed["status"], "reference")
        self.assertGreater(resumed["reference_epoch"], before_reset["reference_epoch"])
        np.testing.assert_array_equal(resumed["matrix_to_reference"], np.eye(3)[:2])
        guard.reset_reference()
        resumed_again = guard.update(moved, [], 200)
        self.assertGreater(resumed_again["reference_epoch"], resumed["reference_epoch"])
        self.assertEqual(len({first["reference_epoch"], resumed["reference_epoch"], resumed_again["reference_epoch"]}), 3)

    def test_epoch_only_optional_status_sequence_splits_without_crossing_reference_change(self):
        sequence = _motion_sequence()
        boundary = len(sequence.timestamp_ms) // 2
        sequence = replace(sequence, camera_motion_status=None,
                           camera_reference_epochs=tuple(int(i >= boundary) for i in range(len(sequence.timestamp_ms))))
        events = detect_pose_events(sequence, source_id="epoch-only-regression")
        boundary_ms = int(sequence.timestamp_ms[boundary])
        self.assertIsInstance(events, list)
        self.assertFalse(any(event["start_ms"] < boundary_ms < event["end_ms"] for event in events))


if __name__ == "__main__":
    unittest.main()
