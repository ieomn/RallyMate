from copy import deepcopy
import math
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from tests import test_trajectory_api as trajectory_fixture

from rallymate_service.swing_kinematics import analyze_swing_kinematics
from tests.test_sam3d_evaluation import _frame


def inputs():
    records = [_frame(i) for i in range(11)]
    for i, row in enumerate(records):
        theta = .03 * i * i
        for side, sign in (("left", -1), ("right", 1)):
            row["keypoints_3d"][side + "_shoulder"] = [sign * .5 * math.cos(theta), 1, sign * .5 * math.sin(theta)]
        row["keypoints_3d"]["left_wrist"] = [-.5 + i * .02, .5, .5]
    timeline = [{"source_frame_index": row["frame_index"], "timestamp_ms": row["timestamp_ms"],
                 "selection_status": "selected", "primary_player_id": 8, "source_track_id": 1,
                 "identity_ambiguous": False} for row in records]
    episode = {"episode_id": "motion-one", "start_ms": 0, "end_ms": 400,
               "person_track_id": 8, "racket_hand": "left", "continuity": {
                   "pose_source": "sam3d", "video_sha256": "a" * 64,
                   "source_selection_epoch": 0, "camera_reference_epoch": 0}}
    return [episode], timeline, records


def calculate(episodes, timeline, records):
    return analyze_swing_kinematics(episodes, timeline, records, video_sha256="a" * 64, source_run_id="test-run")


class SwingKinematicsTests(unittest.TestCase):
    def test_api_reuses_saved_inputs_and_rejects_unfinished_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = trajectory_fixture.TrajectoryApiTests()
            app, settings, database = fixture._app(Path(directory))
            job_id = fixture._succeeded_job(settings, database, running=True)
            episodes, timeline, records = inputs()
            raw_episode = deepcopy(episodes[0])
            raw_episode["evidence"] = {"process_continuity": raw_episode.pop("continuity")}
            summary = {"job_id": job_id, "minimum_scoring_loop": {"provenance": {"video_sha256": "a" * 64}},
                       "action_recognition": {"motion_analysis": {"families": {"baseline": {"episodes": [raw_episode]}}}}}
            output = settings.runs_dir / job_id
            for filename, rows in (("primary-player.jsonl", timeline), ("sam3d_frames.jsonl", records)):
                (output / filename).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            before = {p.name: p.read_bytes() for p in (output / "primary-player.jsonl", output / "sam3d_frames.jsonl")}
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/v1/jobs/{job_id}/swing-kinematics").status_code, 409)
                database.mark_succeeded(job_id, summary)
                response = client.get(f"/v1/jobs/{job_id}/swing-kinematics")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["source_run_id"], job_id)
                self.assertEqual(response.json()["status"], "available")
                self.assertEqual(response.json(), client.get(f"/v1/jobs/{job_id}/swing-kinematics").json())
                self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(before, {p.name: p.read_bytes() for p in (output / "primary-player.jsonl", output / "sam3d_frames.jsonl")})

    def test_source_bound_metrics_preserve_zero_and_do_not_create_scores(self):
        report = calculate(*inputs())
        row = report["episodes"][0]
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["source_run_id"], "test-run")
        self.assertIsNone(report["score"])
        self.assertEqual(row["metrics"]["hip_angular_speed_deg_s"]["value"], 0)
        self.assertAlmostEqual(row["metrics"]["wrist_speed_torso_s"]["value"], .5)
        for metric in row["metrics"].values():
            self.assertEqual(metric["status"], "estimated")
            self.assertGreaterEqual(metric["timestamp_ms"], row["start_ms"])
            self.assertLessEqual(metric["timestamp_ms"], row["end_ms"])
            self.assertEqual(metric["source_frames"], sorted(metric["source_frames"]))

    def test_common_translation_and_uniform_scale_do_not_become_wrist_speed(self):
        episode, timeline, records = inputs()
        expected = calculate(episode, timeline, records)["episodes"][0]["metrics"]
        for i, row in enumerate(records):
            row["keypoints_3d"] = {name: [3 * p[0] + i * 100, 3 * p[1] - i * 50, 3 * p[2] + i * 20]
                                   for name, p in row["keypoints_3d"].items()}
        actual = calculate(episode, timeline, records)["episodes"][0]["metrics"]
        for key in expected:
            self.assertAlmostEqual(actual[key]["value"], expected[key]["value"], places=4)

    def test_root_translation_with_stationary_relative_wrist_remains_zero(self):
        episodes, timeline, records = inputs()
        for i, row in enumerate(records):
            row["keypoints_3d"]["left_wrist"] = [-.5, .5, .5]
            row["keypoints_3d"] = {name: [p[0] + i * 2, p[1] - i, p[2] + i * .5] for name, p in row["keypoints_3d"].items()}
        self.assertEqual(calculate(episodes, timeline, records)["episodes"][0]["metrics"]["wrist_speed_torso_s"]["value"], 0)

    def test_wrong_video_subject_epochs_clock_or_duplicate_records_never_score(self):
        mutations = [lambda r: r.update(video_sha256="b" * 64), lambda r: r.update(person_track_id=2),
                     lambda r: r.update(selection_epoch=3), lambda r: r.update(camera_reference_epoch=1),
                     lambda r: r.update(pts=r["pts"] + 1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                episodes, timeline, records = inputs()
                for row in records:
                    mutate(row)
                report = calculate(episodes, timeline, records)
                self.assertEqual(report["status"], "unavailable")
                self.assertTrue(all(m["value"] is None for m in report["episodes"][0]["metrics"].values()))
        episodes, timeline, records = inputs()
        self.assertEqual(calculate(episodes, timeline, records + deepcopy(records))["status"], "unavailable")

    def test_missing_frame_and_camera_change_do_not_bridge_derivatives(self):
        episodes, timeline, records = inputs()
        records[5]["camera_reference_epoch"] = 1
        for row in records[6:]:
            row["keypoints_3d"]["left_wrist"][0] += 100
        row = calculate(episodes, timeline, records)["episodes"][0]
        self.assertAlmostEqual(row["metrics"]["wrist_speed_torso_s"]["value"], .5)
        self.assertEqual(row["valid_frame_count"], 10)
        self.assertIn("source_clock_identity_or_reference_mismatch", row["quality_reason_counts"])
        for metric in row["metrics"].values():
            self.assertFalse(metric["window_start_ms"] < 200 < metric["window_end_ms"])

    def test_missing_wrist_does_not_erase_independent_torso_angles(self):
        episodes, timeline, records = inputs()
        for row in records:
            del row["keypoints_3d"]["left_wrist"]
        row = calculate(episodes, timeline, records)["episodes"][0]
        self.assertIsNone(row["metrics"]["wrist_speed_torso_s"]["value"])
        self.assertIsNotNone(row["metrics"]["shoulder_hip_separation_deg"]["value"])
        self.assertIn("wrist_joint_missing_or_hand_unconfirmed", row["quality_reason_counts"])

    def test_ambiguous_primary_identity_is_not_promoted_to_known_identity(self):
        episodes, timeline, records = inputs()
        for row in timeline:
            row["identity_ambiguous"] = True
        report = calculate(episodes, timeline, records)
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["episodes"][0]["quality_reason_counts"], {"selected_subject_unverified": 11})


if __name__ == "__main__":
    unittest.main()
