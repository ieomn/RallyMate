from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from rallymate_vision.pose_playback import build_pose_playback
from rallymate_vision.motion_replay import build_motion_replay, select_motion_replay_window
from tests.test_motion_replay import write_source, rewrite


class DisplayContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.frames, self.primary, self.rows, self.timeline = write_source(Path(self.temporary.name), count=300)
        for row in self.timeline:
            row["primary_player_id"] = 1

    def preview(self, **kwargs):
        rewrite(self.frames, self.rows)
        rewrite(self.primary, self.timeline)
        return build_pose_playback(self.frames, primary_timeline_path=self.primary, **kwargs)

    def hide(self, index, name="right_wrist"):
        next(point for point in self.rows[index]["poses"][0]["keypoints"] if point["name"] == name)["confidence"] = .1

    def test_short_joint_gap_is_separate_provenance_labelled_and_does_not_change_raw(self):
        self.hide(10)
        self.hide(11)
        result = self.preview()
        digest = hashlib.sha256(self.frames.read_bytes()).hexdigest()
        raw, display = result["frames"][10], result["display_frames"][10]
        self.assertNotIn("right_wrist", {p["name"] for p in raw["keypoints"]})
        point = next(p for p in display["keypoints"] if p["name"] == "right_wrist")
        self.assertEqual(point["source"], "interpolated")
        self.assertEqual((point["anchors"]["before_frame_index"], point["anchors"]["after_frame_index"]), (9, 12))
        self.assertEqual(point["anchors"]["after_timestamp_ms"] - point["anchors"]["before_timestamp_ms"], 120)
        self.assertEqual(result["display_continuity"]["interpolated_keypoints"], 2)
        self.assertEqual(hashlib.sha256(self.frames.read_bytes()).hexdigest(), digest)
        trajectory = build_motion_replay(self.frames, primary_timeline_path=self.primary, duration_ms=2000)
        self.assertLess(trajectory["quality"]["raw_core_joint_coverage_fraction"], 1)
        self.assertEqual(trajectory["quality"]["display_core_joint_coverage_fraction"], 1)
        self.assertNotIn("right_wrist", {p["name"] for p in trajectory["observations"][10]["keypoints"]})

    def test_joint_fill_rejects_long_time_index_dimension_identity_and_unknown_time_boundaries(self):
        original_rows, original_timeline = copy.deepcopy(self.rows), copy.deepcopy(self.timeline)
        for condition in ("long", "index", "processed", "dimensions", "identity", "unknown_time", "empty_unknown"):
            with self.subTest(condition=condition):
                self.rows, self.timeline = copy.deepcopy(original_rows), copy.deepcopy(original_timeline)
                self.hide(10)
                if condition == "long":
                    self.hide(11)
                    self.hide(12)
                elif condition in {"index", "processed"}:
                    key = "index" if condition == "index" else "processed_index"
                    for row, primary in zip(self.rows[10:], self.timeline[10:]):
                        row["frame"][key] += 1
                        primary["source_frame_index" if key == "index" else key] += 1
                elif condition == "dimensions":
                    self.rows[10]["frame"]["height"] += 2
                elif condition == "identity":
                    self.rows[10]["poses"][0]["person_track_id"] = 2
                    self.timeline[10]["source_track_id"] = 2
                    # Actual target still detected, so this is not a target dropout.
                    self.rows[10]["detections"] = [{"class_name": "player", "track_id": 1}]
                elif condition == "unknown_time":
                    self.rows[10]["frame"]["timestamp_source"] = "fps_fallback"
                elif condition == "empty_unknown":
                    self.rows[10]["poses"] = []
                    self.timeline[10].update(selection_status="missing", source_track_id=None)
                result = self.preview()
                self.assertFalse(any(p["source"] == "interpolated" for p in result["display_frames"][10]["keypoints"]))

    def test_display_context_covers_both_sides_of_a_window_boundary(self):
        self.hide(249)
        self.hide(250)
        result = self.preview(start_ms=10000, duration_ms=200)
        first = result["display_frames"][0]
        self.assertEqual(first["frame_index"], 250)
        point = next(p for p in first["keypoints"] if p["name"] == "right_wrist")
        self.assertEqual(point["anchors"]["before_timestamp_ms"], 9920)
        self.assertEqual(point["anchors"]["after_timestamp_ms"], 10040)
        self.assertEqual(first["valid_until_ms"], 10040)

    def test_short_target_detection_dropout_uses_target_anchors_never_background_points(self):
        self.rows[10]["poses"][0]["person_track_id"] = 2
        for point in self.rows[10]["poses"][0]["keypoints"]:
            point["x_px"] = 600
        self.timeline[10]["source_track_id"] = 2
        result = self.preview()
        raw, display = result["frames"][10], result["display_frames"][10]
        self.assertEqual((raw["person_track_id"], display["person_track_id"], display["observed_person_track_id"]), (2, 1, 2))
        self.assertEqual(display["selection_epoch"], raw["selection_epoch"])
        self.assertTrue(all(p["source"] == "interpolated" and p["x"] < .8 for p in display["keypoints"]))
        self.assertEqual(result["display_continuity"]["repaired_identity_frames"], 1)
        build_motion_replay(self.frames, primary_timeline_path=self.primary, duration_ms=2000)
        self.rows[10]["detections"] = [{"class_name": "player", "track_id": 1}]
        self.assertNotIn("identity_repair", self.preview()["display_frames"][10])

    def test_known_primary_pose_missing_or_unusable_can_bridge_two_frames_but_conflicts_cannot(self):
        original_rows = copy.deepcopy(self.rows)
        for mode in ("missing", "unusable", "duplicate"):
            with self.subTest(mode=mode):
                self.rows = copy.deepcopy(original_rows)
                for index in (10, 11):
                    self.rows[index]["detections"] = [{"class_name": "player", "track_id": 1}]
                    if mode == "missing":
                        self.rows[index]["poses"] = []
                    elif mode == "unusable":
                        for p in self.rows[index]["poses"][0]["keypoints"]:
                            p["confidence"] = .1
                    else:
                        self.rows[index]["poses"].append(copy.deepcopy(self.rows[index]["poses"][0]))
                result = self.preview()
                self.assertEqual(result["frames"][10]["keypoints"], [])
                if mode == "duplicate":
                    self.assertEqual(result["display_frames"][10]["keypoints"], [])
                else:
                    self.assertTrue(result["display_frames"][10]["keypoints"])
                    self.assertTrue(result["display_frames"][11]["keypoints"])
                    self.assertEqual(result["display_frames"][10]["identity_repair"]["dropout_kind"], "pose_unavailable")

    def test_selector_uses_bounded_display_policy_instead_of_rejecting_every_short_gap(self):
        for index in range(5, len(self.rows), 10):
            self.hide(index)
        self.preview()
        selected = select_motion_replay_window(self.frames, self.primary, duration_ms=2000)
        self.assertEqual(selected["display_core_joint_coverage_fraction"], 1)
        self.assertLess(selected["core_joint_coverage_fraction"], 1)
        build_motion_replay(self.frames, primary_timeline_path=self.primary,
                            start_ms=selected["start_ms"], duration_ms=selected["duration_ms"])

    def test_integer_millisecond_pts_do_not_duplicate_same_rate_output_frames(self):
        for index, (row, primary) in enumerate(zip(self.rows, self.timeline)):
            row["frame"]["timestamp_ms"] = primary["timestamp_ms"] = round(index * 1000 / 24)
        result = self.preview()
        self.assertEqual(result["source"]["timestamp_rounding_tolerance_ms"], .5)
        trajectory = build_motion_replay(self.frames, primary_timeline_path=self.primary, duration_ms=6000)
        self.assertEqual(len({row["source_frame_index"] for row in trajectory["frames"]}), 144)
        self.assertTrue(all(abs(row["source_timestamp_offset_ms"]) <= .5 for row in trajectory["frames"]))
        self.assertTrue(all(row["observation_age_ms"] >= 0 for row in trajectory["frames"]))


if __name__ == "__main__":
    unittest.main()
