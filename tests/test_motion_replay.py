from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from rallymate_vision.motion_replay import (
    MotionReplayError,
    _find_ffmpeg,
    build_motion_replay,
    export_motion_replay,
    export_perturbed_motion_replay,
    render_motion_frame,
    select_motion_replay_window,
)


def write_source(directory: Path, count: int = 160, *, moving: bool = True):
    rows, primary = [], []
    base = {"head": (320, 45), "nose": (320, 62), "neck": (320, 94), "hip": (320, 210)}
    for side, sign in (("left", -1), ("right", 1)):
        base.update({f"{side}_shoulder": (320 + sign * 36, 105),
                     f"{side}_elbow": (320 + sign * 55, 152),
                     f"{side}_wrist": (320 + sign * 70, 192),
                     f"{side}_hip": (320 + sign * 24, 210),
                     f"{side}_knee": (320 + sign * 30, 265),
                     f"{side}_ankle": (320 + sign * 35, 320),
                     f"{side}_heel": (320 + sign * 35, 326),
                     f"{side}_big_toe": (320 + sign * 45, 328),
                     f"{side}_small_toe": (320 + sign * 43, 332)})
    for index in range(count):
        dx = math.sin(index / 10) * 25 if moving else 0
        points = [{"name": name, "x_px": x + dx, "y_px": y, "confidence": .95}
                  for name, (x, y) in base.items()]
        rows.append({"frame": {"index": index, "processed_index": index, "timestamp_ms": index * 40,
                                "timestamp_source": "decoder_pts", "width": 640, "height": 360},
                     "poses": [{"person_track_id": 1, "coordinate_space": "original_frame",
                                "keypoint_format": "halpe26", "keypoints": points}]})
        primary.append({"processed_index": index, "source_frame_index": index, "timestamp_ms": index * 40,
                        "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False})
    frames_path, primary_path = directory / "frames.jsonl", directory / "primary-player.jsonl"
    for path, values in ((frames_path, rows), (primary_path, primary)):
        path.write_text("".join(json.dumps(row) + "\n" for row in values), encoding="utf-8")
    return frames_path, primary_path, rows, primary


def rewrite(path, values):
    path.write_text("".join(json.dumps(row) + "\n" for row in values), encoding="utf-8")


class MotionReplayTests(unittest.TestCase):
    def test_fixed_transform_preserves_identity_timing_and_joint_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, _, _ = write_source(Path(temporary))
            result = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            self.assertEqual(result["render"]["frame_count"], 48)
            self.assertEqual(result["source"]["person_track_id"], 1)
            self.assertEqual(result["quality"]["core_joint_coverage_fraction"], 1)
            self.assertLessEqual(result["quality"]["max_raster_rounding_error_px"], math.sqrt(.5))
            transform = result["render"]["transform"]
            for frame in result["frames"]:
                self.assertLessEqual(frame["source_timestamp_ms"], frame["requested_source_timestamp_ms"])
                self.assertLess(frame["observation_age_ms"], 40.01)
                for point in frame["keypoints"]:
                    self.assertAlmostEqual(point["x_px"], point["x"] * 640 * transform["scale"] + transform["offset_x_px"], places=5)
                    self.assertAlmostEqual(point["y_px"], point["y"] * 360 * transform["scale"] + transform["offset_y_px"], places=5)
            pixels = render_motion_frame(result["frames"][0], result["render"])
            self.assertEqual(pixels.shape, (720, 1280, 3))
            self.assertGreater(int(pixels.max()), 230)

    def test_low_confidence_joint_stays_missing_without_temporal_borrowing(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, rows, _ = write_source(Path(temporary))
            next(p for p in rows[25]["poses"][0]["keypoints"] if p["name"] == "right_wrist")["confidence"] = .1
            rewrite(frames, rows)
            result = build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            names = {p["name"] for p in result["frames"][24]["keypoints"]}
            self.assertNotIn("right_wrist", names)
            self.assertEqual(result["quality"]["missing_core_joint_frames"]["right_wrist"], 1)

    def test_unverified_time_or_identity_transition_fail_before_encoding(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, rows, timeline = write_source(Path(temporary))
            rows[2]["frame"].pop("timestamp_source")
            rewrite(frames, rows)
            with self.assertRaisesRegex(MotionReplayError, "timestamp_unverified"):
                build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            rows[2]["frame"]["timestamp_source"] = "decoder_pts"
            rows[10]["poses"][0]["person_track_id"] = 2
            timeline[10]["source_track_id"] = 2
            rewrite(frames, rows)
            rewrite(primary, timeline)
            with self.assertRaisesRegex(MotionReplayError, "continuous_identity"):
                build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)

    def test_selector_ignores_bad_prefix_and_requires_observed_motion(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, primary, rows, _ = write_source(directory)
            for row in rows[:30]:
                row["frame"]["timestamp_source"] = "fps_fallback"
            rewrite(frames, rows)
            result = select_motion_replay_window(frames, primary, duration_ms=2000)
            self.assertGreaterEqual(result["start_ms"], 1200)
            self.assertEqual(result["core_joint_coverage_fraction"], 1)
            build_motion_replay(frames, primary_timeline_path=primary, **{k: result[k] for k in ("start_ms", "duration_ms")})
            frames, primary, _, _ = write_source(directory, moving=False)
            with self.assertRaisesRegex(MotionReplayError, "no_continuous_active_window"):
                select_motion_replay_window(frames, primary, duration_ms=2000)

    def test_missing_subject_and_invalid_dimensions_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            frames, primary, rows, _ = write_source(Path(temporary))
            for row in rows:
                row["poses"] = []
            rewrite(frames, rows)
            with self.assertRaises(MotionReplayError):
                build_motion_replay(frames, primary_timeline_path=primary, duration_ms=2000)
            with self.assertRaises(MotionReplayError):
                build_motion_replay(frames, width=1279)

    def test_real_h264_export_and_synthetic_control_preserve_original(self):
        try:
            _find_ffmpeg(None)
        except MotionReplayError:
            self.skipTest("H.264 encoder is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames, primary, _, _ = write_source(directory)
            source_hash = hashlib.sha256(frames.read_bytes()).hexdigest()
            result = export_motion_replay(frames, directory / "a", primary_timeline_path=primary,
                                           duration_ms=2000, width=512, height=288)
            self.assertEqual(result["video"]["codec"], "h264")
            self.assertEqual(result["video"]["frame_count"], 48)
            self.assertNotIn(temporary, json.dumps(result))
            for name, expected in result["artifact_checksums"].items():
                self.assertEqual(hashlib.sha256((directory / "a" / name).read_bytes()).hexdigest(), expected)
            with self.assertRaisesRegex(MotionReplayError, "already_exists"):
                export_motion_replay(frames, directory / "a", primary_timeline_path=primary,
                                      duration_ms=2000, width=512, height=288)
            baseline = directory / "a" / "trajectory.json"
            before = baseline.read_bytes()
            changed = export_perturbed_motion_replay(baseline, directory / "b", start_ms=500,
                                                     duration_ms=300, offset_x_px=20)
            self.assertEqual(changed["result_kind"], "synthetic_motion_reference_test")
            self.assertFalse(changed["synthetic_perturbation"]["is_personal_motion_record"])
            a = json.loads(before)
            b = json.loads((directory / "b" / "trajectory.json").read_text(encoding="utf-8"))
            self.assertEqual(a["render"], b["render"])
            differences = 0
            for af, bf in zip(a["frames"], b["frames"]):
                self.assertEqual(af["source_timestamp_ms"], bf["source_timestamp_ms"])
                for ap, bp in zip(af["keypoints"], bf["keypoints"]):
                    if ap != bp:
                        differences += 1
                        self.assertEqual(ap["name"], "right_wrist")
                        self.assertTrue(500 < af["source_timestamp_ms"] < 800)
                        self.assertEqual(ap["y_px"], bp["y_px"])
            self.assertGreater(differences, 3)
            self.assertEqual(before, baseline.read_bytes())
            self.assertEqual(source_hash, hashlib.sha256(frames.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
