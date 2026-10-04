from __future__ import annotations

from copy import deepcopy
import math
import json
import unittest

from rallymate_scoring.stroke_candidates import detect_stroke_candidates, _sample, _associate_rackets
from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION, _episode, _classification, racket_hand_evidence, build_motion_analysis
from rallymate_scoring.rotation_analysis import analyze_rotation


def make_frame(index, *, right=(0.9, 0.55), left=(-0.9, 0.55), track=1, offset=0):
    points = {"left_shoulder": (-0.3, 0), "right_shoulder": (0.3, 0),
              "left_hip": (-0.25, 1), "right_hip": (0.25, 1),
              "left_elbow": (-0.5, 0.3), "right_elbow": (0.5, 0.3),
              "left_wrist": left, "right_wrist": right}
    pose = {"person_track_id": track, "keypoints": [
        {"name": name, "x_px": 300 + offset + p[0] * 100, "y_px": 250 + p[1] * 100, "confidence": 0.95}
        for name, p in points.items()]}
    x, y = 300 + offset + right[0] * 100, 250 + right[1] * 100
    return {"frame": {"index": index, "processed_index": index, "timestamp_ms": index * 50},
            "poses": [pose], "detections": [{"class_name": "racket", "confidence": 0.9, "bbox_px": [x - 3, y - 3, x + 15, y + 30]}]}


def swing(kind="forehand"):
    result = []
    for i in range(60):
        progress = min(1, max(0, (i - 20) / 16))
        x = 1.15 * math.cos(math.pi * progress)
        if kind != "forehand":
            x *= -1
        left = (x - 0.22, 0.56) if kind == "two_handed_backhand" and 20 <= i <= 36 else (-1.5, 0.7)
        # Idle context supplies separated-hand racket evidence before/after a
        # two-hand grip, without fabricating a second swinging hand.
        if kind == "two_handed_backhand" and (i < 16 or i > 40):
            x, left = 0.9, (-0.9, 0.55)
        result.append(make_frame(i, right=(x, 0.55), left=left))
    return result


def serve():
    result = []
    for i in range(52):
        time = i * 50
        y = 0.8 if time <= 800 else 0.8 - 2 * (time - 800) / 600 if time <= 1400 else -1.2 + 2 * min(1, (time - 1400) / 500)
        result.append(make_frame(i, right=(0.65, y), left=(-0.55, -1.0 if 400 <= time <= 1150 else 0.6)))
    return result


def mirrored(frames, *, swap_anatomy=False):
    result = deepcopy(frames)
    for item in result:
        for point in item["poses"][0]["keypoints"]:
            point["x_px"] = 600 - point["x_px"]
            if swap_anatomy:
                point["name"] = point["name"].replace("left", "temp").replace("right", "left").replace("temp", "right")
        for detection in item["detections"]:
            x1, y1, x2, y2 = detection["bbox_px"]
            detection["bbox_px"] = [600 - x2, y1, 600 - x1, y2]
    return result


def motion(frames, timeline=None):
    return detect_stroke_candidates(frames, primary_timeline=timeline)["motion_analysis"]


def rotation_samples(times=None, *, shoulder_speed=40, hip_speed=20):
    """Known independent image-plane axes, angles in deg and speed in deg/s."""
    times = times if times is not None else list(range(0, 1001, 50))
    result = []
    for index, time in enumerate(times):
        frame = make_frame(index)
        frame["frame"]["timestamp_ms"] = time
        for point in frame["poses"][0]["keypoints"]:
            joint = "shoulder" if "shoulder" in point["name"] else "hip" if "hip" in point["name"] else None
            if joint is None:
                continue
            angle = math.radians((15 + shoulder_speed * time / 1000) if joint == "shoulder" else (5 + hip_speed * time / 1000))
            sign = -1 if point["name"].startswith("left") else 1
            radius = 30 if joint == "shoulder" else 25
            point["x_px"] = 300 + sign * radius * math.cos(angle)
            point["y_px"] = (250 if joint == "shoulder" else 350) + sign * radius * math.sin(angle)
        result.append(_sample(frame["poses"][0], frame["frame"]))
    return result


class RotationAnalysisTests(unittest.TestCase):
    def test_known_independent_axes_use_real_timestamps_and_keep_score_unavailable(self):
        samples = rotation_samples([0, 40, 105, 155, 220, 260, 325, 400, 450, 510, 580])
        result = analyze_rotation(samples)
        self.assertEqual(result["status"], "measured_2d")
        self.assertEqual(result["metrics"], {
            "shoulder_line_change_deg": 23.2, "hip_line_change_deg": 11.6,
            "shoulder_hip_separation_change_deg": 11.6, "shoulder_hip_separation_max_deg": 21.6,
            "peak_shoulder_angular_speed_deg_s": 40.0, "peak_hip_angular_speed_deg_s": 20.0,
        })
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_status"], "calibration_required")
        self.assertFalse(result["is_formal_coach_score"])
        self.assertFalse(result["is_3d_rotation"])
        self.assertEqual(result["series"][2]["timestamp_ms"], 105)
        self.assertEqual(result["series"][2]["shoulder_angular_velocity_deg_s"], 40)
        self.assertEqual(result["series"][2]["separation_angular_velocity_deg_s"], 20)
        self.assertIsNone(result["series"][0]["shoulder_angular_velocity_deg_s"])
        evidence = result["metric_evidence"]["hip_line_change_deg"]
        self.assertEqual((evidence["coverage_fraction"], evidence["start_ms"], evidence["end_ms"]), (1, 0, 580))
        json.dumps(result, allow_nan=False)

    def test_static_axes_are_observed_zero_motion_and_not_missing_measurements(self):
        result = analyze_rotation(rotation_samples(shoulder_speed=0, hip_speed=0))
        self.assertEqual(result["status"], "measured_2d")
        self.assertEqual(result["metrics"]["shoulder_line_change_deg"], 0)
        self.assertEqual(result["metrics"]["peak_hip_angular_speed_deg_s"], 0)
        self.assertEqual(result["metrics"]["shoulder_hip_separation_max_deg"], 10)

    def test_independent_endpoint_flip_and_crossing_angle_wrap_do_not_make_spikes(self):
        samples = rotation_samples(shoulder_speed=100, hip_speed=80)
        expected = analyze_rotation(samples)["metrics"]
        for sample in samples[10:]:
            sample.points["left_shoulder"], sample.points["right_shoulder"] = sample.points["right_shoulder"], sample.points["left_shoulder"]
        result = analyze_rotation(samples)
        self.assertEqual(result["metrics"], expected)
        self.assertEqual(result["metrics"]["shoulder_line_change_deg"], 100)
        self.assertEqual(result["metrics"]["peak_shoulder_angular_speed_deg_s"], 100)

    def test_one_foreshortened_axis_does_not_invalidate_other_axis_or_fabricate_separation(self):
        samples = rotation_samples()
        for sample in samples:
            for side in ("left", "right"):
                x, y = sample.points[f"{side}_shoulder"]
                sample.points[f"{side}_shoulder"] = (x * 0.1, y * 0.1)
        result = analyze_rotation(samples)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["metrics"]["shoulder_line_change_deg"])
        self.assertIsNone(result["metrics"]["shoulder_hip_separation_max_deg"])
        self.assertEqual(result["metrics"]["hip_line_change_deg"], 20)
        self.assertTrue(all(row["shoulder_line_angle_deg"] is None for row in result["series"]))

    def test_short_missing_axis_in_middle_is_never_stitched_into_a_full_rotation(self):
        samples = rotation_samples()
        del samples[10].points["left_shoulder"]
        result = analyze_rotation(samples)
        self.assertIsNone(result["metrics"]["shoulder_line_change_deg"])
        self.assertEqual(result["metrics"]["hip_line_change_deg"], 20)
        evidence = result["metric_evidence"]["shoulder_line_change_deg"]
        self.assertEqual(evidence["segment_count"], 2)
        self.assertEqual(evidence["valid_samples"], 20)
        self.assertEqual(evidence["total_samples"], 21)
        self.assertIsNone(evidence["start_ms"])

    def test_missing_torso_sample_and_frame_index_hole_split_despite_short_timestamp_gap(self):
        for mode in ("missing_sample", "frame_index"):
            samples = rotation_samples()
            if mode == "missing_sample":
                del samples[10]
            else:
                for sample in samples[10:]:
                    sample.frame_index += 1
            result = analyze_rotation(samples)
            self.assertEqual(result["status"], "unavailable", mode)
            self.assertEqual(result["score_status"], "insufficient_evidence")
            self.assertEqual(result["quality"]["temporal_break_count"], 1)
            self.assertTrue(all(value is None for value in result["metrics"].values()))

    def test_identity_change_timestamp_reversal_and_scale_jump_cannot_be_bridged(self):
        for mode in ("track", "selection_epoch", "timestamp", "scale"):
            samples = rotation_samples()
            if mode == "track":
                samples[10].track = 2
            elif mode == "selection_epoch":
                samples[10].selection_epoch = 1
            elif mode == "timestamp":
                samples[10].time = samples[9].time
            else:
                samples[10].scale *= 2
            result = analyze_rotation(samples)
            self.assertEqual(result["status"], "unavailable", mode)
            self.assertGreaterEqual(result["quality"]["temporal_break_count"], 1)

    def test_equal_shoulder_and_hip_pose_spike_does_not_cancel_into_valid_separation(self):
        samples = rotation_samples(shoulder_speed=0, hip_speed=0)
        for joint, center_y in (("shoulder", 0), ("hip", 1)):
            for side in ("left", "right"):
                x, y = samples[10].points[f"{side}_{joint}"]
                samples[10].points[f"{side}_{joint}"] = (-(y - center_y), center_y + x)
        result = analyze_rotation(samples)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["metrics"]["shoulder_hip_separation_change_deg"])

    def test_mirror_preserves_magnitudes_and_reverses_signed_series(self):
        samples = rotation_samples()
        normal = analyze_rotation(samples)
        for sample in samples:
            sample.points = {name: (-x, y) for name, (x, y) in sample.points.items()}
        mirrored_result = analyze_rotation(samples)
        self.assertEqual(normal["metrics"], mirrored_result["metrics"])
        self.assertEqual(normal["series"][5]["shoulder_hip_separation_deg"],
                         -mirrored_result["series"][5]["shoulder_hip_separation_deg"])

    def test_empty_short_and_nonfinite_evidence_preserve_null_not_zero(self):
        for samples in ([], rotation_samples([0, 10, 20, 30, 40, 50, 60]), rotation_samples()[:4]):
            result = analyze_rotation(samples)
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(all(value is None for value in result["metrics"].values()))
            json.dumps(result, allow_nan=False)
        samples = rotation_samples()
        for sample in samples:
            sample.points["left_shoulder"] = (float("nan"), 0)
        result = analyze_rotation(samples)
        self.assertIsNone(result["metrics"]["shoulder_line_change_deg"])
        json.dumps(result, allow_nan=False)


class StrokeMotionAnalysisTests(unittest.TestCase):
    def test_forehand_is_measured_with_ordered_phases_and_no_contact_or_score(self):
        result = motion(swing())
        self.assertEqual(result["analysis_version"], ANALYSIS_VERSION)
        episodes = result["families"]["baseline"]["episodes"]
        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertEqual(episode["classification"]["label"], "forehand")
        self.assertEqual(episode["classification"]["status"], "rule_inferred")
        self.assertFalse(episode["contact_confirmed"])
        self.assertNotIn("score", episode)
        self.assertGreater(episode["metrics"]["peak_wrist_speed_torso_per_s"], 1.5)
        self.assertGreater(episode["metrics"]["wrist_path_torso"], 0.9)
        self.assertEqual([p["phase"] for p in episode["phases"]], ["preparation", "acceleration", "follow_through"])
        for phase in episode["phases"]:
            self.assertLess(phase["start_ms"], phase["end_ms"])
        self.assertEqual(episode["phases"][0]["start_ms"], episode["start_ms"])
        self.assertEqual(episode["phases"][-1]["end_ms"], episode["end_ms"])
        self.assertEqual(episode["rotation_analysis"]["status"], "measured_2d")
        self.assertIsNone(episode["rotation_analysis"]["score"])
        for key, value in episode["rotation_analysis"]["metrics"].items():
            self.assertEqual(episode["metrics"][key], value)

    def test_image_mirroring_and_left_handed_player_do_not_invert_forehand_label(self):
        for frames in (mirrored(swing()), mirrored(swing(), swap_anatomy=True)):
            episodes = motion(frames)["families"]["baseline"]["episodes"]
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["classification"]["label"], "forehand")

    def test_single_and_two_hand_backhand_use_hand_and_wrist_relationships(self):
        for kind in ("backhand", "two_handed_backhand"):
            episodes = motion(swing(kind))["families"]["baseline"]["episodes"]
            self.assertTrue(any(e["classification"]["label"] == kind for e in episodes), episodes)

    def test_serve_has_measured_phases_but_no_confirmation_of_ball_contact(self):
        episodes = motion(serve())["families"]["serve"]["episodes"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["classification"]["label"], "serve_motion")
        self.assertFalse(episodes[0]["contact_confirmed"])
        self.assertGreater(episodes[0]["metrics"]["elbow_extension_deg"], 0)

    def test_analysis_peak_is_reestimated_independently_of_a_late_candidate_peak(self):
        frames = swing()
        candidate = detect_stroke_candidates(frames)["candidates"][0]
        samples = []
        for frame in frames:
            sample = _sample(frame["poses"][0], frame["frame"])
            _associate_rackets([sample], frame["detections"])
            samples.append(sample)
        shifted = {**candidate, "peak_ms": candidate["end_ms"] - 50}
        episode = _episode(shifted, samples, racket_hand_evidence(samples))
        self.assertEqual(episode["candidate_peak_ms"], shifted["peak_ms"])
        self.assertLess(episode["peak_ms"], shifted["peak_ms"])
        self.assertEqual(episode["phase_timing_status"], "estimated_from_2d_motion")
        self.assertFalse(episode["contact_confirmed"])

    def test_clipped_motion_does_not_invent_completed_follow_through(self):
        for frames, family in ((serve()[:37], "serve"), (swing()[:34], "baseline")):
            episodes = motion(frames)["families"][family]["episodes"]
            self.assertTrue(episodes)
            episode = episodes[0]
            self.assertEqual(episode["analysis_status"], "partial")
            follow = next(phase for phase in episode["phases"] if phase["phase"] == "follow_through")
            self.assertEqual(follow["status"], "unavailable")
            self.assertIsNone(follow["start_ms"])
            self.assertIsNone(follow["end_ms"])
            self.assertTrue(episode["metric_notes_zh"])

    def test_trimmed_empty_or_short_classification_window_is_unclassified(self):
        sample = _sample(make_frame(0)["poses"][0], make_frame(0)["frame"])
        for samples in ([], [sample], [sample] * 4):
            classification = _classification(samples, "right", {"hand": "right"}, "baseline")
            self.assertEqual(classification["label"], "unclassified")
            self.assertIn("未被连续观测覆盖", classification["reason_zh"])

    def test_recentered_preparation_and_recovery_do_not_double_count_one_motion(self):
        frames = swing()
        candidate = detect_stroke_candidates(frames)["candidates"][0]
        samples = []
        for frame in frames:
            sample = _sample(frame["poses"][0], frame["frame"])
            _associate_rackets([sample], frame["detections"])
            samples.append(sample)
        second = {**candidate, "candidate_id": "second-local-peak", "peak_ms": candidate["peak_ms"] + 100}
        result = build_motion_analysis([candidate, second], {1: samples})
        self.assertEqual(result["families"]["baseline"]["summary"]["analyzed_count"], 1)
        merged = result["families"]["baseline"]["episodes"][0]["evidence"]["merged_candidate_ids"]
        self.assertEqual(set(merged), {candidate["candidate_id"], "second-local-peak"})

    def test_foreshortened_body_axis_remains_unclassified(self):
        frames = swing()
        for item in frames:
            for point in item["poses"][0]["keypoints"]:
                if "shoulder" in point["name"] or "hip" in point["name"]:
                    point["x_px"] = 300 + (point["x_px"] - 300) * 0.15
        episodes = motion(frames)["families"]["baseline"]["episodes"]
        self.assertTrue(episodes)
        self.assertTrue(all(e["classification"]["label"] == "unclassified" for e in episodes))

    def test_shoulder_endpoint_order_flip_is_not_a_180_degree_body_rotation(self):
        frames = swing()
        for item in frames[28:]:
            for point in item["poses"][0]["keypoints"]:
                if "shoulder" in point["name"] or "hip" in point["name"]:
                    point["x_px"] = 600 - point["x_px"]
        episode = motion(frames)["families"]["baseline"]["episodes"][0]
        self.assertEqual(episode["classification"]["label"], "unclassified")
        self.assertEqual(episode["metrics"]["shoulder_line_change_deg"], 0)

    def test_projected_arm_collapse_withholds_elbow_angle(self):
        frames = swing()
        for item in frames:
            points = {point["name"]: point for point in item["poses"][0]["keypoints"]}
            for axis in ("x_px", "y_px"):
                points["right_elbow"][axis] = points["right_shoulder"][axis]
        episode = motion(frames)["families"]["baseline"]["episodes"][0]
        self.assertIsNone(episode["metrics"]["elbow_extension_deg"])
        self.assertTrue(episode["metric_notes_zh"])

    def test_uniform_video_scaling_preserves_normalized_motion_metrics(self):
        frames = swing()
        enlarged = deepcopy(frames)
        for item in enlarged:
            for point in item["poses"][0]["keypoints"]:
                point["x_px"] *= 2
                point["y_px"] *= 2
            for detection in item["detections"]:
                detection["bbox_px"] = [value * 2 for value in detection["bbox_px"]]
        normal = motion(frames)["families"]["baseline"]["episodes"][0]
        larger = motion(enlarged)["families"]["baseline"]["episodes"][0]
        self.assertEqual(normal["metrics"], larger["metrics"])

    def test_primary_identity_change_cannot_be_bridged_even_for_one_frame(self):
        frames = swing()
        timeline = [{"processed_index": i, "source_track_id": 1 if i != 28 else 2, "selection_status": "selected"} for i in range(len(frames))]
        self.assertEqual(motion(frames, timeline)["families"]["baseline"]["episodes"], [])

    def test_low_confidence_torso_frame_does_not_become_a_continuous_rotation_measurement(self):
        frames = swing()
        for point in frames[28]["poses"][0]["keypoints"]:
            if point["name"] == "left_shoulder":
                point["confidence"] = 0.1
        episode = motion(frames)["families"]["baseline"]["episodes"][0]
        rotation = episode["rotation_analysis"]
        self.assertEqual(rotation["status"], "unavailable")
        self.assertEqual(rotation["quality"]["temporal_break_count"], 1)
        self.assertEqual(rotation["score_status"], "insufficient_evidence")
        self.assertTrue(all(value is None for value in rotation["metrics"].values()))

    def test_gap_and_pose_spike_do_not_create_measured_episodes(self):
        frames = [make_frame(i) for i in range(60)]
        frames[30] = make_frame(30, right=(-1.5, 0.55))
        self.assertEqual(motion(frames)["families"]["baseline"]["episodes"], [])
        frames = swing()
        for item in frames[24:34]:
            item["poses"] = []
        self.assertEqual(motion(frames)["families"]["baseline"]["episodes"], [])

    def test_no_return_inferred_from_swing_without_opponent_and_ball_context(self):
        result = motion(swing())
        row = result["families"]["return"]
        self.assertEqual(row["episodes"], [])
        self.assertIn("对手发球", row["reason_zh"])
        self.assertIn("不代表", row["reason_zh"])

    def _return_frames(self):
        server = serve()
        receiver = swing()
        frames = []
        for i in range(90):
            item = make_frame(i, track=2)
            if i < len(server):
                item["poses"] = deepcopy(server[i]["poses"])
                item["poses"][0]["person_track_id"] = 2
                item["detections"] = deepcopy(server[i]["detections"])
            receiver_i = max(0, min(len(receiver) - 1, i - 26))
            receiver_pose = deepcopy(receiver[receiver_i]["poses"][0])
            for point in receiver_pose["keypoints"]:
                point["x_px"] += 800
            item["poses"].append(receiver_pose)
            racket = deepcopy(receiver[receiver_i]["detections"][0])
            racket["bbox_px"][0] += 800
            racket["bbox_px"][2] += 800
            item["detections"].append(racket)
            if 30 <= i <= 54:
                x = 350 + (i - 30) / 24 * 700
                item["detections"].append({"class_name": "ball", "track_id": 99, "confidence": 0.9,
                                           "bbox_px": [x - 3, 280, x + 3, 286]})
            frames.append(item)
        timeline = [{"processed_index": i, "source_track_id": 1, "selection_status": "selected"} for i in range(len(frames))]
        return frames, timeline

    def test_return_requires_opponent_serve_and_same_ball_track_approaching_receiver(self):
        frames, timeline = self._return_frames()
        episodes = motion(frames, timeline)["families"]["return"]["episodes"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["evidence"]["return_context"]["server_track_id"], 2)
        self.assertEqual(episodes[0]["person_track_id"], 1)
        self.assertFalse(episodes[0]["contact_confirmed"])

    def test_ball_track_switch_or_outgoing_ball_cannot_impute_return(self):
        for mode in ("switch", "outgoing", "absent"):
            frames, timeline = self._return_frames()
            for item in frames:
                for ball in item["detections"]:
                    if ball["class_name"] != "ball":
                        continue
                    if mode == "switch":
                        ball["track_id"] = item["frame"]["index"]
                    elif mode == "outgoing":
                        x1, y1, x2, y2 = ball["bbox_px"]
                        ball["bbox_px"] = [1400 - x2, y1, 1400 - x1, y2]
                    else:
                        ball["confidence"] = 0.1
            self.assertEqual(motion(frames, timeline)["families"]["return"]["episodes"], [], mode)


if __name__ == "__main__":
    unittest.main()
