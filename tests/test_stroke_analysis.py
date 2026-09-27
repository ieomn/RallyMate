from __future__ import annotations

from copy import deepcopy
import math
import unittest

from rallymate_scoring.stroke_candidates import detect_stroke_candidates, _sample, _associate_rackets
from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION, _episode, _classification, racket_hand_evidence, build_motion_analysis


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
