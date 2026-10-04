from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_vision.pose_playback import PosePlaybackError, build_pose_playback
from tests import test_trajectory_api as fixture


def point(name="left_shoulder", x=160, y=90, confidence=.9, **changes):
    return {"name": name, "x_px": x, "y_px": y, "confidence": confidence, **changes}


def pose(track=1, points=None, **changes):
    return {"person_track_id": track, "keypoint_format": "halpe26", "coordinate_space": "original_frame",
            "keypoints": points if points is not None else [point(), point("right_shoulder", 320, 90)], **changes}


def frame(index, *, timestamp=None, poses=None, **changes):
    return {"frame": {"index": index, "processed_index": index,
                      "timestamp_ms": index * 40 if timestamp is None else timestamp,
                      "timestamp_source": "decoder_pts", "width": 640, "height": 360, **changes},
            "poses": [pose()] if poses is None else poses}


def primary(index, track=1, **changes):
    return {"processed_index": index, "source_frame_index": index, "timestamp_ms": index * 40,
            "source_track_id": track, "selection_status": "selected", "identity_ambiguous": False, **changes}


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class PosePlaybackTests(unittest.TestCase):
    def build(self, rows, *, timeline=None, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            write_rows(path, rows)
            primary_path = Path(directory) / "primary-player.jsonl"
            if timeline is not None:
                write_rows(primary_path, timeline)
            return build_pose_playback(path, primary_timeline_path=primary_path, **options)

    def test_pixel_coordinates_use_each_source_dimension_and_never_boxes(self):
        result = self.build([frame(0), frame(1, width=320, height=720)])
        first, second = result["frames"]
        self.assertEqual((first["keypoints"][0]["x"], first["keypoints"][0]["y"]), (.25, .25))
        self.assertEqual((second["keypoints"][0]["x"], second["keypoints"][0]["y"]), (.5, .125))
        self.assertIsNone(result["source"]["width"])
        self.assertEqual(result["coordinate_space"], "normalized_frame_0_1")
        self.assertEqual(len(result["keypoint_names"]), 26)
        self.assertEqual(result["keypoint_names"][25], "right_heel")
        self.assertNotIn("bbox", json.dumps(result))
        self.assertEqual(result["interpolation"], "none")

    def test_low_confidence_invalid_and_offscreen_points_are_missing_not_clamped(self):
        points = [point(), point("right_shoulder", confidence=.3499), point("left_elbow", confidence=.35),
                  point("right_elbow", in_frame=False), point("left_wrist", x=-1, x_normalized=.5),
                  point("right_wrist", confidence=True), point("left_hip", confidence=1.01),
                  point("right_hip", confidence_in_range=False)]
        result = self.build([frame(0, poses=[pose(points=points)])])
        self.assertEqual([item["name"] for item in result["frames"][0]["keypoints"]], ["left_shoulder", "left_elbow"])

    def test_missing_joint_and_conflicting_duplicate_never_create_a_joint(self):
        points = [point(), point(x=300), point("left_knee")]
        result = self.build([frame(0, poses=[pose(points=points)])])
        self.assertEqual([item["name"] for item in result["frames"][0]["keypoints"]], ["left_knee"])

    def test_primary_selects_one_exact_identity_and_ambiguous_frames_clear(self):
        rows = [frame(index, poses=[pose(1), pose(2, points=[point(x=480)])]) for index in range(3)]
        result = self.build(rows, timeline=[primary(0, 2), primary(1, 1, identity_ambiguous=True), primary(2, 1)])
        first, gap, third = result["frames"]
        self.assertEqual(first["person_track_id"], 2)
        self.assertEqual(first["keypoints"][0]["x"], .75)
        self.assertEqual(gap["keypoints"], [])
        self.assertIsNone(gap["person_track_id"])
        self.assertGreater(third["selection_epoch"], first["selection_epoch"])
        self.assertEqual(result["source"]["selection_source"], "primary_player_timeline")

    def test_primary_binding_and_duplicate_pose_fail_closed_without_another_player(self):
        for timeline in [[primary(0, 1, timestamp_ms=1)], [primary(0, 1, source_frame_index=True)], []]:
            with self.subTest(timeline=timeline):
                result = self.build([frame(0)], timeline=timeline)
                self.assertEqual(result["frames"][0]["keypoints"], [])
        result = self.build([frame(0, poses=[pose(), pose()])], timeline=[primary(0)])
        self.assertEqual(result["frames"][0]["keypoints"], [])

    def test_missing_primary_allows_only_one_visible_person_and_splits_track_changes(self):
        result = self.build([frame(0), frame(1, poses=[pose(1), pose(2)]), frame(2, poses=[pose(2)])])
        self.assertEqual(result["frames"][0]["selection_status"], "single_visible_person")
        self.assertEqual(result["frames"][1]["keypoints"], [])
        self.assertNotEqual(result["frames"][0]["selection_epoch"], result["frames"][2]["selection_epoch"])

    def test_sampling_keeps_original_times_and_does_not_extend_across_dropped_frames(self):
        rows = [frame(index) for index in range(20)]
        result = self.build(rows, sample_limit=3)
        self.assertEqual(len(result["frames"]), 3)
        self.assertTrue(result["source"]["is_sampled"])
        self.assertEqual(result["source"]["observed_frames_in_window"], 20)
        self.assertEqual(result["frames"][0]["timestamp_ms"], 0)
        self.assertEqual(result["frames"][0]["valid_until_ms"], 40)
        self.assertEqual(result["frames"][-1]["timestamp_ms"], 760)
        self.assertLess(result["frames"][0]["valid_until_ms"], result["frames"][1]["timestamp_ms"])

    def test_long_time_gap_and_empty_pose_stop_previous_display(self):
        result = self.build([frame(0), frame(1, poses=[]), frame(2, timestamp=900)])
        self.assertEqual(result["frames"][0]["valid_until_ms"], 40)
        self.assertEqual(result["frames"][1]["keypoints"], [])
        self.assertEqual(result["frames"][1]["valid_until_ms"], 140)
        self.assertGreater(result["frames"][2]["selection_epoch"], result["frames"][0]["selection_epoch"])

    def test_window_and_epoch_are_stable_across_paging(self):
        rows = [frame(index, poses=[pose(1 if index < 5 else 2)]) for index in range(20)]
        full = self.build(rows)
        window = self.build(rows, start_ms=400, duration_ms=200)
        expected = [item for item in full["frames"] if 400 <= item["timestamp_ms"] < 600]
        self.assertEqual(window["frames"], expected)
        self.assertEqual(window["source"]["requested_end_ms"], 600)

    def test_window_keeps_only_a_still_valid_preceding_observation(self):
        result = self.build([frame(0, timestamp=9980), frame(1, timestamp=10013), frame(2, timestamp=10046)],
                            start_ms=10000, duration_ms=50)
        self.assertTrue(result["source"]["includes_preceding_observation"])
        self.assertEqual(result["frames"][0]["timestamp_ms"], 9980)
        self.assertEqual(result["frames"][0]["valid_until_ms"], 10013)
        self.assertEqual(result["source"]["observed_frames_in_window"], 2)
        self.assertEqual(result["source"]["returned_frames"], 3)

    def test_preceding_context_cannot_revive_a_missing_pose_or_cross_person_change(self):
        for first, second in [([pose(1)], []), ([], [pose(1)]), ([pose(1)], [pose(2)])]:
            with self.subTest(first=first, second=second):
                result = self.build([frame(0, timestamp=9980, poses=first), frame(1, timestamp=10013, poses=second)],
                                    start_ms=10000, duration_ms=50)
                self.assertFalse(result["source"]["includes_preceding_observation"])
                self.assertEqual(result["frames"][0]["timestamp_ms"], 10013)

    def test_unverified_time_and_missing_pose_do_not_borrow_a_prior_joint(self):
        result = self.build([frame(0, timestamp=9980), frame(1, timestamp=10013, timestamp_source="fps_fallback")],
                            start_ms=10000, duration_ms=50)
        self.assertFalse(result["source"]["includes_preceding_observation"])
        self.assertEqual(result["frames"][0]["keypoints"], [])

    def test_unknown_source_time_dimensions_or_crop_coordinates_do_not_overlay(self):
        for row in [frame(0, timestamp_source="fps_fallback"), frame(0, width=0),
                    frame(0, poses=[pose(coordinate_space="crop")])]:
            with self.subTest(row=row):
                self.assertEqual(self.build([row])["frames"][0]["keypoints"], [])

    def test_nonmonotonic_frames_and_conflicting_primary_rows_are_rejected(self):
        for rows, timeline in [([frame(0), frame(1, timestamp=0)], None),
                               ([frame(0), frame(0)], None),
                               ([frame(0), frame(1)], [primary(0), primary(0, 2)])]:
            with self.subTest(rows=rows), self.assertRaises(PosePlaybackError):
                self.build(rows, timeline=timeline)

    def test_read_limits_and_incomplete_tail_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            write_rows(path, [frame(0)])
            with path.open("ab") as stream:
                stream.write(b'{"frame":')
            result = build_pose_playback(path, allow_partial=True)
            self.assertTrue(result["source"]["is_partial"])
            self.assertEqual(len(result["frames"]), 1)
            with self.assertRaises(PosePlaybackError):
                build_pose_playback(path)
            with patch("rallymate_vision.pose_playback.MAX_ARTIFACT_BYTES", 1), self.assertRaises(PosePlaybackError):
                build_pose_playback(path, allow_partial=True)


class PosePlaybackApiTests(unittest.TestCase):
    def test_existing_job_endpoint_is_authenticated_read_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory), api_key="secret")
            job_id = helper._succeeded_job(settings, database)
            frames = settings.runs_dir / job_id / "frames.jsonl"
            write_rows(frames, [frame(index) for index in range(10)])
            before = hashlib.sha256(frames.read_bytes()).hexdigest()
            with TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/pose-preview"
                self.assertEqual(client.get(url).status_code, 401)
                headers = {"Authorization": "Bearer secret"}
                result = client.get(url + "?start_ms=80&duration_ms=100&sample_limit=2", headers=headers)
                self.assertEqual(result.status_code, 200, result.text)
                payload = result.json()
                self.assertEqual(payload["job_id"], job_id)
                self.assertEqual(len(payload["frames"]), 2)
                self.assertNotIn(directory, json.dumps(payload))
                self.assertIn("no-store", result.headers["cache-control"])
                self.assertEqual(client.get(f"/v1/jobs/{job_id}", headers=headers).json()["pose_preview_url"], url)
                for params in ["sample_limit=601", "duration_ms=10001", "start_ms=-1", "sample_limit=0"]:
                    self.assertEqual(client.get(url + "?" + params, headers=headers).status_code, 422)
            self.assertEqual(hashlib.sha256(frames.read_bytes()).hexdigest(), before)

    def test_running_cache_invalidates_for_frames_and_primary_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database, running=True)
            directory = settings.runs_dir / job_id
            frames, timeline = directory / "frames.jsonl", directory / "primary-player.jsonl"
            write_rows(frames, [frame(0, poses=[pose(1), pose(2)])])
            with TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/pose-preview"
                self.assertEqual(client.get(url).json()["frames"][0]["keypoints"], [])
                write_rows(timeline, [primary(0, 2)])
                selected = client.get(url).json()
                self.assertEqual(selected["frames"][0]["person_track_id"], 2)
                self.assertTrue(selected["source"]["is_partial"])
                write_rows(frames, [frame(0), frame(1)])
                write_rows(timeline, [primary(0), primary(1)])
                self.assertEqual(len(client.get(url).json()["frames"]), 2)
                database.mark_succeeded(job_id, {"status": "completed", "job_id": job_id})
                self.assertFalse(client.get(url).json()["source"]["is_partial"])

    def test_missing_corrupt_and_inactive_jobs_fail_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = fixture.TrajectoryApiTests()
            app, settings, database = helper._app(Path(directory))
            job_id = helper._succeeded_job(settings, database, running=True)
            frames = settings.runs_dir / job_id / "frames.jsonl"
            with TestClient(app) as client:
                url = f"/v1/jobs/{job_id}/pose-preview"
                frames.unlink()
                self.assertEqual(client.get(url).status_code, 409)
                frames.write_text('{"broken":}\n', encoding="utf-8")
                self.assertEqual(client.get(url).status_code, 422)
                database.mark_failed(job_id, "test failure")
                self.assertEqual(client.get(url).status_code, 409)


if __name__ == "__main__":
    unittest.main()
