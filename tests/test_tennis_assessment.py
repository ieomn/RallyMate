from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_service.tennis_assessment import analyze_tennis_assessment, build_tennis_assessment, _segments, _bound_windows, _score_window, _local_speed, _smooth, _angular_continuity, _time_mean, _leg_extension_continuity, _follow_deceleration, _follow_active_fraction
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
            self.assertEqual(actual["candidate_accounting"]["rejected_count"], 1)
            self.assertEqual(actual["candidate_accounting"]["rejection_counts"], {"candidate_source_or_complete_phase_unverified": 1})
            self.assertEqual({k: v for k, v in actual.items() if k != "candidate_accounting"},
                             {k: v for k, v in expected.items() if k != "candidate_accounting"})

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

    def test_stage_integration_weights_elapsed_pts_and_does_not_bridge_missing_data(self):
        # The first interval is nine times longer; counting samples would give
        # 66.7 instead of the observed-time trapezoidal mean of 55.
        self.assertAlmostEqual(_time_mean([0., 100., 100.], [0., 900., 1000.]), 55.)
        self.assertEqual(_time_mean([20., 20., 20.], [12., 43., 98.]), 20.)
        self.assertIsNone(_time_mean([100., np.nan, 100.], [0., 50., 100.]))
        self.assertIsNone(_time_mean([100., 100., 100.], [0., 50., 50.]))

    def test_same_leg_continuity_distinguishes_static_crouch_and_repeated_extension(self):
        times = np.arange(25) * 40.
        smooth = np.r_[np.full(11, 55.), np.linspace(52., 20., 14)]
        rows = [{"left_knee_flexion": float(k), "right_knee_flexion": 55.} for k in smooth]
        expected = _leg_extension_continuity(rows, times, 11, 23)
        self.assertAlmostEqual(expected, 1.)
        # Early preparation bouncing is outside the 200 ms lead-in and should
        # not be mistaken for stop/start motion during the forward swing.
        early = deepcopy(rows)
        for i in range(5):
            early[i]["left_knee_flexion"] = 100. if i % 2 else 5.
        self.assertEqual(_leg_extension_continuity(early, times, 11, 23), expected)
        repeated = deepcopy(rows)
        for i in (15, 16, 17):
            repeated[i]["left_knee_flexion"] += 22.
        self.assertLess(_leg_extension_continuity(repeated, times, 11, 23), .8)
        static = [{"left_knee_flexion": 65., "right_knee_flexion": 65.} for _ in rows]
        self.assertEqual(_leg_extension_continuity(static, times, 11, 23), 0.)
        for row in rows:
            row["left_knee_flexion"] = row["right_knee_flexion"] = None
        self.assertIsNone(_leg_extension_continuity(rows, times, 11, 23))

    def test_follow_deceleration_does_not_fabricate_constant_speed_measurement(self):
        times = np.arange(30) * 40.
        wrist = np.column_stack((times / 1000, times * 0, times * 0))
        self.assertIsNone(_follow_deceleration(wrist, times, 8))
        # A decreasing trace without an observed recovery boundary cannot
        # establish a complete deceleration measurement, even if it is smooth.
        for clock in (times, np.r_[0., np.cumsum(np.tile([31., 49.], 15))]):
            seconds = clock / 1000
            path = np.column_stack((4 * seconds - seconds ** 2, seconds * 0, seconds * 0))
            self.assertIsNone(_follow_deceleration(path, clock, 8))

    def test_first_natural_recovery_is_not_penalized_as_late_follow_through(self):
        times = np.arange(-200., 601., 20.)
        speed = np.interp(times, [-200, 0, 80, 160, 240, 320, 400, 600], [5, 10, 7, 3, 6, 4, 1.5, 1])
        x = np.r_[0., np.cumsum((speed[1:] + speed[:-1]) / 2 * np.diff(times) / 1000)]
        wrist = np.column_stack((x, x * 0, x * 0))
        # The later deliberate recovery ends at a lower speed. Using the global
        # 450 ms minimum incorrectly reduced this motion to 87.8 points.
        reasons = []
        self.assertAlmostEqual(_follow_deceleration(wrist, times, 10, reasons), 1.)
        self.assertEqual(reasons, [])
        clipped = []
        self.assertIsNone(_follow_deceleration(wrist[:19], times[:19], 10, clipped))
        self.assertTrue(clipped)

    def test_serve_position_anchor_is_not_assumed_to_be_a_wrist_speed_peak(self):
        times = np.arange(-200., 601., 20.)
        speed = np.interp(times, [-200, 0, 80, 160, 240, 320, 400, 600], [5, 10, 13, 10, 7, 4, 2, 1])
        x = np.r_[0., np.cumsum((speed[1:] + speed[:-1]) / 2 * np.diff(times) / 1000)]
        wrist = np.column_stack((x, x * 0, x * 0))
        reasons = []
        self.assertIsNone(_follow_deceleration(wrist, times, 10, reasons))
        self.assertEqual(reasons, ["follow_deceleration_anchor_not_wrist_speed_peak"])

    def test_follow_activity_uses_elapsed_phase_boundary_with_variable_frame_rate(self):
        # Same path: still for 30 ms, then moving at 0.5 torso/s. The early VFR
        # frames are dense, but must not dominate either the boundary or mean.
        for times in (np.arange(0., 451., 15.), np.array([0., 10., 20., 30., 150., 300., 450.])):
            positions = np.maximum(times - 30., 0.) * .5 / 1000
            self.assertAlmostEqual(_follow_active_fraction(np.diff(positions), times, 0), .9)

    def test_public_result_contains_no_internal_reference_document_identifiers(self):
        for result in (calculate(*inputs()), build_tennis_assessment({})):
            serialized = json.dumps(result)
            self.assertNotIn("internal_sources", serialized)
            self.assertNotIn("VIS-SV", serialized)
            self.assertNotIn("P0030", serialized)

    def test_unscored_motion_candidates_are_accounted_for_without_fake_scores(self):
        timeline, records = inputs()
        for record in records:
            record["keypoints_3d"] = deepcopy(records[0]["keypoints_3d"])
        report = calculate(timeline, records)
        self.assertIsNone(report["overall_score"])
        self.assertEqual(report["candidate_accounting"], {
            "proposed_count": 3, "accepted_count": 0, "rejected_count": 3,
            "rejection_counts": {"candidate_lacks_continuous_complete_motion": 3}})
        self.assertIn("3", report["reason_zh"])
        self.assertEqual(report["improvements"], [])


if __name__ == "__main__":
    unittest.main()
