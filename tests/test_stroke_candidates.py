from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest

from rallymate_scoring.stroke_candidates import detect_stroke_candidates, recognize_strokes_from_artifacts
from rallymate_scoring.technique_assessment import build_technique_assessment


def frame(index, right=(0.8, 0.6), left=(-0.6, 0.6), *, track=1, racket=True, offset=0):
    # Coordinates relative to shoulder midpoint, scaled by the 100-pixel torso.
    positions = {"left_shoulder": (-0.3, 0), "right_shoulder": (0.3, 0),
                 "left_hip": (-0.25, 1), "right_hip": (0.25, 1),
                 "left_elbow": (-0.5, 0.3), "right_elbow": (0.5, 0.3),
                 "left_wrist": left, "right_wrist": right}
    pose = {"person_track_id": track, "keypoints": [
        {"name": name, "x_px": 300 + offset + p[0] * 100, "y_px": 250 + p[1] * 100,
         "confidence": 0.95} for name, p in positions.items()]}
    x, y = 300 + offset + right[0] * 100, 250 + right[1] * 100
    detections = [{"class_name": "racket", "confidence": 0.9, "bbox_px": [x - 5, y - 5, x + 30, y + 50]}] if racket else []
    return {"frame": {"index": index, "processed_index": index, "timestamp_ms": index * 50},
            "poses": [pose], "detections": detections}


def swing(*, racket=True):
    return [frame(i, right=(1.15 * math.cos(math.pi * min(1, max(0, (i - 6) / 16))), 0.55), racket=racket)
            for i in range(36)]


def serve(*, opposite_lift=True, follow_through=True, racket=True):
    result = []
    for i in range(52):
        t = i * 50
        y = 0.8 if t <= 800 else 0.8 - 2 * (t - 800) / 600 if t <= 1400 else -1.2 + 2 * min(1, (t - 1400) / 500)
        if not follow_through and t > 1400:
            y = -1.2
        left_y = -1.0 if opposite_lift and 400 <= t <= 1150 else 0.6
        result.append(frame(i, right=(0.65, y), left=(-0.55, left_y), racket=racket))
    return result


class StrokeCandidateTests(unittest.TestCase):
    def test_cross_body_swing_has_one_reviewable_interval_not_hit_count(self):
        result = detect_stroke_candidates(swing())
        self.assertEqual(result["by_family"], {"baseline": 1, "serve": 0})
        self.assertIsNone(result["confirmed_contact_count"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["status"], "candidate")
        self.assertFalse(candidate["contact_confirmed"])
        self.assertNotIn("event_code", candidate)
        self.assertLess(candidate["start_ms"], candidate["peak_ms"])
        self.assertLess(candidate["peak_ms"], candidate["end_ms"])
        self.assertGreaterEqual(candidate["evidence"]["racket_associated_frames"], 2)

    def test_ordered_overhead_motion_has_serve_candidate(self):
        result = detect_stroke_candidates(serve())
        self.assertEqual(result["by_family"], {"baseline": 0, "serve": 1})
        candidate = result["candidates"][0]
        self.assertLess(candidate["evidence"]["opposite_arm_lift_ms"], candidate["peak_ms"])
        self.assertFalse(candidate["evidence"]["ball_toss_confirmed"])

    def test_arm_raise_without_toss_or_follow_through_is_not_serve(self):
        for frames in (serve(opposite_lift=False), serve(follow_through=False), serve(racket=False)):
            with self.subTest():
                self.assertEqual(detect_stroke_candidates(frames)["by_family"]["serve"], 0)

    def test_no_racket_no_swing_and_static_or_translated_body_not_motion(self):
        self.assertEqual(detect_stroke_candidates(swing(racket=False))["candidate_count"], 0)
        for frames in ([frame(i) for i in range(40)], [frame(i, offset=i * 4) for i in range(40)]):
            self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_one_pose_spike_does_not_create_a_swing(self):
        frames = [frame(i) for i in range(40)]
        frames[20] = frame(20, right=(-1.8, 0.4))
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_identity_change_or_missing_pose_cannot_join_a_swing(self):
        frames = swing()
        for item in frames[14:]:
            item["poses"][0]["person_track_id"] = 2
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)
        frames = swing()
        for item in frames[12:20]:
            item["poses"] = []
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_background_players_and_their_rackets_are_not_primary_evidence(self):
        frames = swing()
        for i, item in enumerate(frames):
            other = frame(i, track=2, offset=600, racket=False)
            item["poses"].extend(other["poses"])
        timeline = [{"processed_index": i, "source_track_id": 2, "selection_status": "selected"} for i in range(len(frames))]
        self.assertEqual(detect_stroke_candidates(frames, primary_timeline=timeline)["candidate_count"], 0)
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 1)

    def test_ambiguous_overlapping_people_do_not_share_one_racket(self):
        frames = swing()
        for item in frames:
            other = deepcopy(item["poses"][0])
            other["person_track_id"] = 2
            item["poses"].append(other)
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_repeated_motion_after_recovery_produces_separate_intervals(self):
        frames = swing()
        second = deepcopy(swing())
        for item in second:
            item["frame"]["timestamp_ms"] += 2500
            item["frame"]["index"] += 50
            item["frame"]["processed_index"] += 50
        result = detect_stroke_candidates(frames + second)
        self.assertEqual(result["candidate_count"], 2)
        self.assertLess(result["candidates"][0]["end_ms"], result["candidates"][1]["start_ms"])

    def test_low_confidence_or_out_of_frame_wrists_are_not_used(self):
        for field, value in (("confidence", 0.1), ("in_frame", False)):
            frames = swing()
            for item in frames:
                next(k for k in item["poses"][0]["keypoints"] if k["name"] == "right_wrist")[field] = value
            self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_candidate_summary_cannot_impute_confirmed_registry_techniques(self):
        recognition = detect_stroke_candidates(serve())
        assessment = build_technique_assessment({"action_recognition": recognition})
        item = next(t for t in assessment["techniques"] if t["technique_id"] == "serve")
        self.assertFalse(item["observed"])
        self.assertIsNone(item["score_0_to_100"])
        self.assertEqual(item["recognition_status"], "candidate")
        self.assertEqual(assessment["family_summary"]["serve"]["candidate_count"], 1)
        self.assertEqual(assessment["family_summary"]["serve"]["observed_count"], 0)
        self.assertEqual(assessment["family_summary"]["serve"]["recognition_status"], "motion_analyzed")
        self.assertEqual(assessment["family_summary"]["serve"]["motion_episode_count"], 1)
        self.assertEqual(assessment["action_recognition"]["motion_analysis"]["families"]["serve"]["summary"]["analyzed_count"], 1)
        self.assertIn("未确认", assessment["family_summary"]["serve"]["recognition_reason_zh"])

    def test_baseline_family_explains_unclassified_motion_without_imputing_subtypes(self):
        assessment = build_technique_assessment({"action_recognition": detect_stroke_candidates(swing())})
        baseline = assessment["family_summary"]["baseline"]
        self.assertEqual(baseline["recognition_status"], "motion_analyzed")
        self.assertEqual(baseline["motion_episode_count"], 1)
        self.assertEqual(assessment["action_recognition"]["motion_analysis"]["families"]["baseline"]["summary"]["analyzed_count"], 1)
        self.assertEqual(baseline["observed_count"], 0)
        self.assertEqual(baseline["candidate_count"], 1)
        for item in assessment["techniques"]:
            if item["family"] == "baseline":
                self.assertFalse(item["observed"])
                self.assertIsNone(item["score_0_to_100"])
                self.assertTrue(all(phase["status"] == "unavailable" for phase in item["phase_statuses"]))

    def test_no_motion_distinguishes_unavailable_classifier_from_no_stroke(self):
        recognition = detect_stroke_candidates([frame(i) for i in range(40)])
        assessment = build_technique_assessment({"action_recognition": recognition})
        for family in ("baseline", "serve"):
            row = assessment["family_summary"][family]
            self.assertEqual(row["recognition_status"], "motion_unavailable")
            self.assertIn("不代表", row["recognition_reason_zh"])

    def test_explicit_classified_event_is_not_overridden_by_candidates(self):
        assessment = build_technique_assessment({
            "event_counts": {"GS01": 1},
            "action_recognition": detect_stroke_candidates(swing()),
        })
        self.assertEqual(assessment["family_summary"]["baseline"]["recognition_status"], "observed")
        self.assertEqual(assessment["family_summary"]["baseline"]["observed_count"], 1)

    def test_left_handed_mirror_is_not_silently_dropped(self):
        frames = serve()
        for item in frames:
            for point in item["poses"][0]["keypoints"]:
                point["name"] = point["name"].replace("left", "temporary").replace("right", "left").replace("temporary", "right")
                point["x_px"] = 600 - point["x_px"]
            for detection in item["detections"]:
                x1, y1, x2, y2 = detection["bbox_px"]
                detection["bbox_px"] = [600 - x2, y1, 600 - x1, y2]
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["by_family"]["serve"], 1)
        self.assertEqual(result["candidates"][0]["racket_hand_candidate"], "left")

    def test_opposite_arm_lift_after_overhead_swing_is_not_serve(self):
        frames = serve(opposite_lift=False)
        for item in frames[32:40]:
            next(k for k in item["poses"][0]["keypoints"] if k["name"] == "left_wrist")["y_px"] = 150
        self.assertEqual(detect_stroke_candidates(frames)["by_family"]["serve"], 0)

    def test_ambiguous_primary_identity_is_excluded(self):
        frames = serve()
        timeline = [{"processed_index": i, "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": True}
                    for i in range(len(frames))]
        result = detect_stroke_candidates(frames, primary_timeline=timeline)
        self.assertEqual(result["status"], "insufficient_pose")
        self.assertEqual(result["candidate_count"], 0)

    def test_candidate_labels_in_legacy_containers_cannot_become_observed(self):
        candidate = {"status": "candidate", "event_code": "GS06", "technique_id": "serve", "phase": "strike"}
        result = build_technique_assessment({"predictions": [candidate], "technique_events": {"GS06": candidate}}, [candidate])
        item = next(t for t in result["techniques"] if t["technique_id"] == "serve")
        self.assertFalse(item["observed"])
        self.assertTrue(all(phase["status"] == "unavailable" for phase in item["phase_statuses"]))

    def test_missing_artifacts_mean_unavailable_and_not_zero_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            result = recognize_strokes_from_artifacts(Path(directory) / "frames.jsonl", Path(directory) / "primary.jsonl")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["candidate_count"])
        assessment = build_technique_assessment({"action_recognition": result})
        self.assertIsNone(assessment["family_summary"]["serve"]["candidate_count"])
        self.assertEqual(assessment["family_summary"]["serve"]["recognition_status"], "analysis_unavailable")


if __name__ == "__main__":
    unittest.main()
