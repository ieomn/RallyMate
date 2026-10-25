from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_service.tennis_assessment import analyze_tennis_assessment, build_tennis_assessment, _segments, _bound_windows, _score_window, _local_speed, _smooth, _angular_continuity
from tests.test_sam3d_evaluation import _frame


def inputs():
    records = []
    for i in range(121):
        row = _frame(i)
        t = i / 30
        turn = .35 * (1 + math.sin(t * math.pi * 2))
        swing = 1.3 * math.sin(t * math.pi * 2 - .4)
        knee = .18 + .1 * math.sin(t * math.pi * 2 + .5)
        p = row["keypoints_3d"]
        for side, sign in (("left", -1), ("right", 1)):
            p[f"{side}_hip"] = [sign * .3, 0, 0]
            p[f"{side}_shoulder"] = [sign * .5 * math.cos(turn), 1, sign * .5 * math.sin(turn)]
            p[f"{side}_knee"] = [sign * .4, -.6, knee]
            p[f"{side}_ankle"] = [sign * .5, -1.25, 0]
            p[f"{side}_ear"] = [sign * .1, 1.4, 0]
        p["right_wrist"] = [.6 * math.cos(swing), .8, .9 * math.sin(swing)]
        p["right_elbow"] = [.6, .7, .2]
        p["left_wrist"] = [-.5, .7, .1]
        p["left_elbow"] = [-.6, .8, .05]
        p["nose"] = [0, 1.4, .1]
        records.append(row)
    timeline = [{"source_frame_index": r["frame_index"], "timestamp_ms": r["timestamp_ms"],
                 "selection_status": "selected", "primary_player_id": 8, "source_track_id": 1,
                 "identity_ambiguous": False} for r in records]
    return timeline, records


def calculate(timeline, records):
    return analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, source_run_id="test-run", motion_episodes=episodes())


def episodes():
    return [{"episode_id": f"swing-{i}", "start_ms": start, "peak_ms": start + 600, "end_ms": start + 1120,
             "family": "baseline", "person_track_id": 1,
             "continuity": {"pose_source": "sam3d", "video_sha256": "a" * 64, "source_selection_epoch": 0, "camera_reference_epoch": 0},
             "phases": [{"phase": phase, "status": "measured", "start_ms": a, "end_ms": b}
                        for phase, a, b in (("preparation", start, start + 400), ("acceleration", start + 400, start + 600),
                                            ("follow_through", start + 600, start + 1120))]}
            for i, start in enumerate((40, 1240, 2840))]


class TennisAssessmentTests(unittest.TestCase):
    def test_real_geometry_produces_five_dimensions_and_exact_aggregation(self):
        report = calculate(*inputs())
        self.assertEqual(report["status"], "scored")
        self.assertGreater(report["observation_count"], 0)
        self.assertEqual(len(report["dimensions"]), 5)
        for dimension in report["dimensions"]:
            measured = [m for m in dimension["metrics"] if m["score"] is not None]
            self.assertAlmostEqual(dimension["score"], sum(m["score"] * m["weight"] for m in measured) / sum(m["weight"] for m in measured), delta=.01)
            self.assertGreater(dimension["observation_count"], 0)
        self.assertAlmostEqual(report["overall_score"], sum(d["score"] for d in report["dimensions"]) / 5, delta=.01)
        self.assertEqual(len(report["improvements"]), 2)
        self.assertFalse(report["rubric"]["identity_labels_used"])
        self.assertFalse(report["rubric"]["confidence_used_in_score"])
        for e in report["episodes"]:
            self.assertLess(e["start_ms"], e["peak_ms"])
            self.assertLess(e["peak_ms"], e["end_ms"])
            self.assertEqual(e["anchor_semantics"], "body_relative_arm_motion_peak_not_ball_contact")

    def test_time_varying_camera_rigid_transform_and_scale_do_not_change_score(self):
        timeline, records = inputs()
        expected = calculate(timeline, records)
        for i, row in enumerate(records):
            a, b = i * .08, i * .035
            ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
            rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
            row["keypoints_3d"] = {k: (2.5 * ry @ rx @ np.array(p) + np.array([i, -2 * i, .5 * i])).tolist()
                                    for k, p in row["keypoints_3d"].items()}
        actual = calculate(timeline, records)
        self.assertEqual(actual["observation_count"], expected["observation_count"])
        self.assertAlmostEqual(actual["overall_score"], expected["overall_score"], places=2)
        self.assertEqual(actual["dimensions"], expected["dimensions"])

    def test_static_pose_and_common_camera_motion_never_get_a_score(self):
        timeline, records = inputs()
        fixed = deepcopy(records[0]["keypoints_3d"])
        for i, row in enumerate(records):
            a = i * .1
            rotation = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
            row["keypoints_3d"] = {k: (rotation @ np.array(v) + np.array([i, 0, i])).tolist() for k, v in fixed.items()}
        report = calculate(timeline, records)
        self.assertIsNone(report["overall_score"])
        self.assertEqual(report["observation_count"], 0)

    def test_arm_motion_without_independent_complete_action_is_not_a_swing(self):
        timeline, records = inputs()
        report = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64)
        self.assertIsNone(report["overall_score"])
        candidates = episodes()
        candidates[0]["phases"] = candidates[0]["phases"][:2]
        valid = calculate(timeline, records)
        partial = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)
        self.assertEqual(partial["observation_count"], valid["observation_count"] - 1)

    def test_repeated_candidate_does_not_double_count_an_action(self):
        timeline, records = inputs()
        expected = calculate(timeline, records)
        candidates = episodes() + deepcopy(episodes())
        for i, e in enumerate(candidates):
            e["episode_id"] = f"candidate-{i}"
        actual = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)
        self.assertEqual(actual["observation_count"], expected["observation_count"])
        self.assertEqual(actual["dimensions"], expected["dimensions"])

    def test_invalid_candidate_cannot_hide_valid_action_or_clip_its_context(self):
        timeline, records = inputs()
        expected = calculate(timeline, records)
        for kind in ("source", "phase", "person"):
            bad = deepcopy(episodes()[0])
            bad["episode_id"] = "a-invalid"
            if kind == "source":
                bad["continuity"]["video_sha256"] = "b" * 64
            elif kind == "phase":
                bad["phases"] = []
            else:
                bad["person_track_id"] = 999
            actual = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, source_run_id="test-run", motion_episodes=[bad] + episodes())
            self.assertEqual(actual, expected)

    def test_nonzero_source_pts_origin_preserves_media_time_and_score(self):
        timeline, records = inputs()
        expected = calculate(timeline, records)
        for row in records:
            row["relative_timestamp_ms"] = row["timestamp_ms"]
            row["timestamp_ms"] += 12000
            row["pts"] += 12000
        actual = calculate(timeline, records)
        self.assertEqual(actual, expected)

    def test_opposite_leg_flexion_does_not_cancel_loaded_leg_extension(self):
        timeline, records = inputs()
        segments, _ = _segments(timeline, records, "a" * 64)
        for row in segments[0]:
            row["left_knee_flexion"] = max(10., 50 - row["time"] / 20)
            row["right_knee_flexion"] = 60 - row["left_knee_flexion"]
            row["knee"] = 30.
            row["separation"] = max(5., 40 - max(0, row["time"] - 100) / 20)
        segment, window = _bound_windows(segments, episodes()[:1], "a" * 64)[0]
        score = _score_window(segment, window)
        self.assertGreater(score["metrics"]["release_sequence"][1], 90)

    def test_identity_labels_and_pose_confidence_never_change_score(self):
        timeline, records = inputs()
        expected = calculate(timeline, records)
        for row in timeline:
            row.update(professional=True, player_name="famous player", keypoint_valid_fraction=0., selection_prior_score=.01)
        for row in records:
            row["baseline"]["keypoint_scores"] = {k: .001 for k in row["baseline"]["keypoint_scores"]}
        self.assertEqual(calculate(timeline, records), expected)

    def test_missing_joint_affects_only_related_subcriteria(self):
        timeline, records = inputs()
        for row in records:
            for name in ("left_ear", "right_ear", "nose"):
                row["keypoints_3d"].pop(name, None)
        report = calculate(timeline, records)
        self.assertEqual(report["status"], "scored")
        striking = next(d for d in report["dimensions"] if d["id"] == "striking")
        self.assertIsNone(striking["metrics"][0]["score"])
        self.assertIsNotNone(striking["metrics"][1]["score"])
        self.assertEqual(striking["score"], striking["metrics"][1]["score"])

    def test_ambiguous_window_does_not_erase_independent_valid_windows(self):
        timeline, records = inputs()
        for row in timeline[45:55]:
            row["identity_ambiguous"] = True
        report = calculate(timeline, records)
        self.assertIsNotNone(report["overall_score"])
        self.assertEqual(report["quality_reason_counts"]["primary_subject_unverified"], 10)
        for episode in report["episodes"]:
            self.assertFalse(episode["start_ms"] < 1800 < episode["end_ms"])

    def test_camera_epoch_track_and_time_gaps_split_windows(self):
        timeline, records = inputs()
        for r in records[60:]:
            r["camera_reference_epoch"] = 1
        report = calculate(timeline, records)
        for episode in report["episodes"]:
            self.assertFalse(episode["start_ms"] < 2400 < episode["end_ms"])
        self.assertGreater(report["quality_reason_counts"]["source_continuity_break"], 0)

    def test_wrong_hash_duplicate_native_and_invalid_clock_cannot_score(self):
        for mutation in (lambda r: r.update(video_sha256="b" * 64), lambda r: r.update(pts=r["pts"] + 10)):
            timeline, records = inputs()
            for row in records:
                mutation(row)
            self.assertIsNone(calculate(timeline, records)["overall_score"])
        timeline, records = inputs()
        self.assertIsNone(calculate(timeline, records + deepcopy(records))["overall_score"])

    def test_cached_file_builder_uses_raw_records_and_returns_independent_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "test-run"
            directory.mkdir()
            timeline, records = inputs()
            for filename, data in (("primary-player.jsonl", timeline), ("sam3d_frames.jsonl", records)):
                (directory / filename).write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf8")
            summary = {"job_id": "test-run", "minimum_scoring_loop": {"provenance": {"video_sha256": "a" * 64}},
                       "overall_score": 999, "professional": True}
            candidates = episodes()
            for row in candidates:
                row["evidence"] = {"process_continuity": row.pop("continuity")}
            summary["action_recognition"] = {"motion_analysis": {"families": {"baseline": {"episodes": candidates}}}}
            report = build_tennis_assessment(summary, directory)
            self.assertEqual(report["status"], "scored")
            report["dimensions"][0]["score"] = -1
            self.assertGreaterEqual(build_tennis_assessment(summary, directory)["dimensions"][0]["score"], 0)
            self.assertIsNone(build_tennis_assessment(summary, directory, job_id="wrong")["overall_score"])
            target = directory / "primary-player.jsonl"
            before = target.stat()
            target.write_bytes(target.read_bytes().replace(b'"identity_ambiguous": false', b'"identity_ambiguous": true '))
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(target.stat().st_size, before.st_size)
            self.assertIsNone(build_tennis_assessment(summary, directory)["overall_score"])

    def test_unavailable_builder_preserves_five_null_dimensions(self):
        for summary, run_dir in (({}, None), ({"job_id": "bad"}, Path("missing")),
                                 ({"job_id": "bad", "minimum_scoring_loop": {"provenance": {"video_sha256": "a" * 64}}}, Path("missing"))):
            result = build_tennis_assessment(summary, run_dir)
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["overall_score"])
            self.assertEqual(len(result["dimensions"]), 5)
            self.assertTrue(all(d["score"] is None and d["observation_count"] == 0 for d in result["dimensions"]))

    def test_serve_uses_declared_source_bound_hand_and_profile_metrics(self):
        timeline, records = inputs()
        candidates = episodes()
        for row in candidates:
            row.update(family="serve", racket_hand="right")
        result = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)
        self.assertEqual(result["status"], "scored")
        self.assertTrue(all(e["active_arm"] == "right" for e in result["episodes"]))
        self.assertEqual([e["peak_ms"] for e in result["episodes"]], [e["peak_ms"] for e in candidates])
        ids = {m["id"] for d in result["dimensions"] for m in d["metrics"]}
        self.assertIn("serve_trophy", ids)
        self.assertNotIn("coil", ids)
        for row in candidates:
            row["racket_hand"] = None
        self.assertIsNone(analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)["overall_score"])

    def test_fast_serve_follow_through_does_not_move_overhead_anchor(self):
        timeline, records = inputs()
        candidates = episodes()[:1]
        candidates[0].update(family="serve", racket_hand="right")
        for i, row in enumerate(records):
            if 700 <= row["timestamp_ms"] <= 950:
                row["keypoints_3d"]["right_wrist"][1] -= .8 * (i % 2)
        report = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)
        self.assertEqual(len(report["episodes"]), 1)
        self.assertEqual(report["episodes"][0]["peak_ms"], candidates[0]["peak_ms"])
        self.assertEqual(report["episodes"][0]["anchor_semantics"], "source_bound_overhead_extension_not_ball_contact")

    def test_mixed_families_preserve_equal_action_weight_in_every_dimension(self):
        timeline, records = inputs()
        candidates = episodes()
        candidates[0].update(family="serve", racket_hand="right")
        result = analyze_tennis_assessment(timeline, records, video_sha256="a" * 64, motion_episodes=candidates)
        self.assertEqual(result["status"], "scored")
        for dimension in result["dimensions"]:
            keys = {m["id"] for m in dimension["metrics"]}
            episode_scores = []
            for episode in result["episodes"]:
                measured = [v["score"] for k, v in episode["metrics"].items() if k in keys]
                if measured:
                    episode_scores.append(sum(measured) / len(measured))
            self.assertAlmostEqual(dimension["score"], sum(episode_scores) / len(episode_scores), delta=.02)
            measured = [m for m in dimension["metrics"] if m["score"] is not None]
            self.assertAlmostEqual(sum(m["weight"] for m in measured), 1, places=6)

    def test_complete_native_context_corrects_short_proposal_wrong_arm_and_peak(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "tennis-native-phase-refinement.json").read_text(encoding="utf8"))
        original = deepcopy(data)
        self.assertEqual(data["episodes"][0]["racket_hand"], "left")
        report = analyze_tennis_assessment(data["timeline"], data["records"], video_sha256=data["video_sha256"],
                                          source_run_id=data["source_run_id"], motion_episodes=data["episodes"])
        self.assertEqual(report["observation_count"], 1)
        episode = report["episodes"][0]
        self.assertEqual(episode["active_arm"], "right")
        self.assertAlmostEqual(episode["peak_ms"], 16449.767, places=2)
        self.assertGreater(episode["end_ms"], 17000)
        self.assertLess(episode["start_ms"], 15600)
        self.assertEqual(data, original)

    def test_supported_velocity_uses_irregular_actual_pts_and_rejects_one_frame_peak(self):
        times = np.array([0, 31, 79, 121, 162, 199, 248, 282, 326], dtype=float)
        points = np.column_stack((times / 1000, 2 * times / 1000, -.5 * times / 1000))
        expected = math.sqrt(1 + 4 + .25)
        self.assertTrue(np.allclose(_local_speed(points, times), expected, atol=1e-6))
        noisy = points.copy()
        noisy[4, 0] += .7
        raw_peak = np.max(np.linalg.norm(np.diff(noisy, axis=0), axis=1) / (np.diff(times) / 1000))
        supported = _local_speed(_smooth(noisy), times)
        self.assertLess(float(np.max(supported)), raw_peak / 3)
        self.assertTrue(np.isfinite(supported).all())

    def test_native_joint_spikes_do_not_replace_forward_swing_with_follow_through(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "tennis-native-peak-support.json").read_text(encoding="utf8"))
        report = analyze_tennis_assessment(data["timeline"], data["records"], video_sha256=data["video_sha256"],
                                          source_run_id=data["source_run_id"], motion_episodes=data["episodes"])
        self.assertEqual(len(report["episodes"]), 2)
        self.assertEqual([e["active_arm"] for e in report["episodes"]], ["right", "left"])
        self.assertEqual([e["peak_ms"] for e in report["episodes"]], [8400., 20120.])
        self.assertEqual(report["rubric"]["phase_velocity_estimator"], "centered_five_observation_linear_fit_actual_decoder_pts")
        self.assertIn("preparation_start_clipped_by_source_boundary", report["episodes"][0]["quality_reason_codes"])
        self.assertNotIn("preparation_sync", report["episodes"][0]["metrics"])

    def test_smooth_recovery_turn_is_not_instability_but_repeated_reversals_are(self):
        times = np.array([i * 40. for i in range(31)])
        linear_turn = np.linspace(-35, 55, len(times))
        smooth = _angular_continuity(linear_turn, times)
        larger = _angular_continuity(linear_turn * 2, times)
        self.assertEqual(smooth[1], 100.)
        self.assertEqual(larger[1], 100.)
        oscillating = linear_turn + 18 * np.sin(np.linspace(0, 10 * math.pi, len(times)))
        noisy = _angular_continuity(oscillating, times)
        self.assertLess(noisy[1], smooth[1])
        self.assertGreater(noisy[0], smooth[0])
        # Axis wrap is representational: a smooth turn through +90/-90 stays smooth.
        wrapping = (np.linspace(70, 115, len(times)) + 90) % 180 - 90
        self.assertAlmostEqual(_angular_continuity(wrapping, times)[1], 100.)

    def test_preparation_participation_does_not_penalize_or_reward_extra_knee_depth(self):
        scores, values = [], []
        for depth in (.25, .8):
            timeline, records = inputs()
            for row in records:
                for side, sign in (("left", -1), ("right", 1)):
                    row["keypoints_3d"][side + "_knee"] = [sign * .4, -.6, depth]
            report = calculate(timeline, records)
            metric = next(m for d in report["dimensions"] for m in d["metrics"] if m["id"] == "knee_load")
            scores.append(metric["score"])
            values.append(metric["value"])
        self.assertGreater(values[1], values[0])
        self.assertEqual(scores, [100., 100.])

    def test_ready_stance_has_only_a_narrowness_floor_and_is_not_hip_balance(self):
        measured = []
        for half_width, lean in ((.1, 0), (.5, 0), (1.5, 0), (.5, .6)):
            timeline, records = inputs()
            for row in records:
                for side, sign in (("left", -1), ("right", 1)):
                    row["keypoints_3d"][side + "_ankle"][0] = sign * half_width
                for name, point in row["keypoints_3d"].items():
                    if point[1] > 0:
                        point[2] += lean * point[1]
            report = calculate(timeline, records)
            metric = next(m for d in report["dimensions"] for m in d["metrics"] if m["id"] == "support_control")
            measured.append(metric)
        self.assertLess(measured[0]["score"], measured[1]["score"])
        self.assertEqual([m["score"] for m in measured[1:]], [100., 100., 100.])
        self.assertAlmostEqual(measured[1]["value"], measured[3]["value"], places=5)
        self.assertEqual(measured[1]["unit"], "hip_width")

    def test_unobserved_preparation_onsets_are_missing_not_failed_synchrony(self):
        timeline, records = inputs()
        for row in records:
            for side, sign in (("left", -1), ("right", 1)):
                row["keypoints_3d"][side + "_shoulder"] = [sign * .5 * math.cos(.4), 1, sign * .5 * math.sin(.4)]
        report = calculate(timeline, records)
        prep = next(d for d in report["dimensions"] if d["id"] == "preparation")
        metrics = {m["id"]: m for m in prep["metrics"]}
        self.assertIsNone(metrics["preparation_sync"]["score"])
        self.assertEqual(metrics["preparation_sync"]["observation_count"], 0)
        self.assertIsNotNone(metrics["coil"]["score"])
        self.assertIsNotNone(report["overall_score"])


if __name__ == "__main__":
    unittest.main()
