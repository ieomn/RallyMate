from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rallymate_vision.motion_reconstruction_reference import (
    ReconstructionReferenceError, build_reference_plan, export_motion_reconstruction_reference,
)


def trajectory() -> dict:
    rows = []
    for index in range(144):
        rows.append({"output_frame_index": index, "output_timestamp_ms": index * 1000 / 24,
                     "source_frame_index": 48 + index, "source_timestamp_ms": (48 + index) * 1000 / 24,
                     "person_track_id": 2,
                     "keypoints": [{"name": "head", "x": (120 + index / 20) / 320, "y": .2, "x_px": 9000},
                                   {"name": "left_wrist", "x": .28, "y": .4},
                                   {"name": "right_wrist", "x": .72, "y": .4},
                                   {"name": "left_ankle", "x": .4, "y": .8}]})
    return {"result_kind": "deterministic_2d_motion_replay",
            "source": {"width": 320, "height": 288, "start_ms": 2000, "duration_ms": 6000,
                       "person_track_id": 2, "timestamp_source": "decoder_pts"},
            "render": {"fps": 24}, "display_frames": rows, "frames": []}


def plan(data: dict, **kwargs) -> dict:
    return build_reference_plan(data, source_width=320, source_height=288, source_fps=24, source_frame_count=192, **kwargs)


class ReconstructionReferencePlanTests(unittest.TestCase):
    def test_fixed_crop_covers_entire_window_without_using_rendered_pixel_coordinates(self):
        result = plan(trajectory())
        crop = result["crop"]
        self.assertEqual(result["mapping_source"], "display_frames")
        self.assertEqual(result["frame_count"], 144)
        self.assertEqual([row["source_frame_index"] for row in result["frame_map"]], list(range(48, 192)))
        self.assertEqual(crop["mode"], "fixed_entire_window")
        self.assertTrue(crop["all_supplied_target_landmarks_inside"])
        self.assertFalse(crop["racket_extent_verified"])
        self.assertFalse(crop["other_people_excluded"])
        self.assertLessEqual(crop["x"] + crop["width"], 320)
        self.assertLessEqual(crop["y"] + crop["height"], 288)
        self.assertEqual(crop["width"] % 2, 0)
        self.assertEqual(crop["height"] % 2, 0)

    def test_missing_duplicate_skipped_or_reversed_frames_cannot_change_speed_silently(self):
        for source_index in (None, 48, 50, -1):
            with self.subTest(source_index=source_index):
                data = trajectory()
                data["display_frames"][1]["source_frame_index"] = source_index
                with self.assertRaises(ReconstructionReferenceError):
                    plan(data)
        data = trajectory()
        data["display_frames"].pop()
        with self.assertRaises(ReconstructionReferenceError):
            plan(data)

    def test_changed_identity_synthetic_trajectory_or_missing_bounds_are_rejected(self):
        variants = []
        switched = trajectory(); switched["display_frames"][40]["person_track_id"] = 3; variants.append(switched)
        synthetic = trajectory(); synthetic["synthetic_perturbation"] = {}; variants.append(synthetic)
        missing = trajectory(); missing["display_frames"][40]["keypoints"] = []; variants.append(missing)
        invalid = trajectory(); invalid["display_frames"][40]["keypoints"][0]["x"] = float("nan"); variants.append(invalid)
        for data in variants:
            with self.assertRaises(ReconstructionReferenceError):
                plan(data)

    def test_crop_rejects_cut_limbs_and_unsupported_geometry(self):
        data = trajectory()
        data["display_frames"][40]["keypoints"][0]["x"] = .9
        for crop in ((0, 0, 256, 256), (-1, 0, 320, 288), (0, 0, 321, 288), (0, 0, 320, 289)):
            with self.subTest(crop=crop), self.assertRaises(ReconstructionReferenceError):
                plan(data, crop=crop)

    def test_unverified_or_different_source_timebase_is_rejected(self):
        for mutation in (lambda data: data["source"].update(timestamp_source="fps_fallback"),
                         lambda data: data["render"].update(fps=25),
                         lambda data: data["source"].update(width=640),
                         lambda data: data["display_frames"][0].update(source_timestamp_ms=2005)):
            data = trajectory(); mutation(data)
            with self.assertRaises(ReconstructionReferenceError):
                plan(data)

    def test_fixed_crop_expands_short_axis_to_fit_provider_aspect_limits(self):
        for width, height in ((1280, 720), (720, 1280)):
            with self.subTest(source_dimensions=(width, height)):
                data = trajectory()
                data["source"].update(width=width, height=height)
                for row in data["display_frames"]:
                    row["keypoints"] = [{"x": .01, "y": .48}, {"x": .99, "y": .52}] if width > height else [
                        {"x": .48, "y": .01}, {"x": .52, "y": .99}]
                result = build_reference_plan(data, source_width=width, source_height=height,
                                              source_fps=24, source_frame_count=192)
                crop = result["crop"]
                self.assertGreaterEqual(crop["width"] / crop["height"], .4)
                self.assertLessEqual(crop["width"] / crop["height"], 2.5)
                self.assertEqual(crop["width"] if width > height else crop["height"], 1280)
                self.assertEqual(crop["height"] if width > height else crop["width"], 512)
                self.assertTrue(crop["all_supplied_target_landmarks_inside"])
                self.assertLessEqual(crop["x"] + crop["width"], width)
                self.assertLessEqual(crop["y"] + crop["height"], height)

    def test_impossible_aspect_or_oversized_crop_is_rejected_without_resizing(self):
        for width, height in ((1280, 270), (270, 1280), (6000, 4000), (4000, 6000)):
            data = trajectory()
            data["source"].update(width=width, height=height)
            for row in data["display_frames"]:
                row["keypoints"] = [{"x": .01, "y": .01}, {"x": .99, "y": .99}]
            for crop in (None, (0, 0, width, height)):
                with self.subTest(source_dimensions=(width, height), crop=crop):
                    with self.assertRaises(ReconstructionReferenceError) as error:
                        build_reference_plan(data, source_width=width, source_height=height,
                                             source_fps=24, source_frame_count=192, crop=crop)
                    self.assertEqual(error.exception.code, "reference_geometry_unsupported")

    def test_provider_geometry_exact_boundaries_remain_supported(self):
        for width, height in ((640, 256), (256, 640), (5760, 2304), (2304, 5760)):
            with self.subTest(dimensions=(width, height)):
                data = trajectory()
                data["source"].update(width=width, height=height)
                result = build_reference_plan(data, source_width=width, source_height=height,
                                              source_fps=24, source_frame_count=192, crop=(0, 0, width, height))
                self.assertEqual(result["crop"]["width"], width)
                self.assertEqual(result["crop"]["height"], height)


class ReconstructionReferenceExportTests(unittest.TestCase):
    def test_real_export_preserves_source_pixels_and_records_frame_crop_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 24, (320, 288))
            self.assertTrue(writer.isOpened())
            for index in range(192):
                pixels = np.zeros((288, 320, 3), np.uint8)
                pixels[:] = (index, index // 2, 240 - index)
                cv2.rectangle(pixels, (90 + index // 4, 60), (130 + index // 4, 220), (240, 220, 180), -1)
                writer.write(pixels)
            writer.release()
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            data = trajectory()
            saved = copy.deepcopy(data)
            reference = root / "trajectory.json"
            reference.write_text(json.dumps(data), encoding="utf-8")
            output = root / "output"
            result = export_motion_reconstruction_reference(source, reference, output, expected_source_sha256=digest)
            self.assertEqual(result["video"]["frame_count"], 144)
            self.assertEqual(result["video"]["duration_ms"], 6000)
            self.assertFalse(result["video"]["audio_included"])
            self.assertFalse(result["video"]["visual_interpolation"])
            self.assertFalse(result["video"]["geometric_resize"])
            self.assertEqual(result["source"]["sha256"], digest)
            self.assertEqual(json.loads(reference.read_text(encoding="utf-8")), saved)
            capture = cv2.VideoCapture(str(source))
            for index in range(192):
                ok, pixels = capture.read()
                self.assertTrue(ok)
                if index == 48:
                    x, y, w, h = (result["crop"][key] for key in ("x", "y", "width", "height"))
                    expected_first = pixels[y:y + h, x:x + w]
            capture.release()
            observed_first = cv2.imdecode(np.frombuffer((output / "first.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(observed_first, expected_first)
            for filename, artifact_digest in result["artifact_checksums"].items():
                self.assertEqual(hashlib.sha256((output / filename).read_bytes()).hexdigest(), artifact_digest)
            original_output = (output / "reference.mp4").read_bytes()
            with self.assertRaises(ReconstructionReferenceError) as error:
                export_motion_reconstruction_reference(source, reference, output, expected_source_sha256=digest)
            self.assertEqual(error.exception.code, "reference_output_already_exists")
            self.assertEqual((output / "reference.mp4").read_bytes(), original_output)
            with self.assertRaises(ReconstructionReferenceError) as error:
                export_motion_reconstruction_reference(source, reference, root / "wrong-source", expected_source_sha256="0" * 64)
            self.assertEqual(error.exception.code, "reference_source_digest_mismatch")


if __name__ == "__main__":
    unittest.main()
