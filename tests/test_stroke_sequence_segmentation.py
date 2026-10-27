"""Counterfactual motion sequences, independent of player identity or scores."""
from __future__ import annotations

from copy import deepcopy
import unittest

from rallymate_scoring.stroke_candidates import detect_stroke_candidates
from tests.test_stroke_candidates import frame, swing


def interpolate(t, anchors):
    if t <= anchors[0][0]:
        return anchors[0][1]
    for (a, p), (b, q) in zip(anchors, anchors[1:]):
        if t <= b:
            f = (t - a) / (b - a)
            return tuple(x + f * (y - x) for x, y in zip(p, q))
    return anchors[-1][1]


def counter_sweep(*, strong=True, gap=False):
    # Slow low takeback, an observed hold, then a faster upward forward sweep.
    anchors = [(0, (.9, .7)), (400, (.9, .7)), (1000, (-1, .7)),
               (1950, (-1, .7)), (2250 if strong else 2550, (1, -.2)), (3300, (1, -.2))]
    frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(67)]
    if gap:
        for item in frames[23:27]:
            item["poses"] = []
    return frames


class StrokeSequenceSegmentationTests(unittest.TestCase):
    def _prediction(self, frames, candidate, hand=None, votes=None):
        from rallymate_scoring.stroke_candidates import _sample
        from rallymate_scoring.temporal_recognition import RuleTemporalBackend, build_temporal_window
        samples = [_sample(f["poses"][0], f["frame"]) for f in frames]
        return RuleTemporalBackend().recognize(build_temporal_window(samples), candidate, {"hand": hand, "separated_wrist_racket_votes": votes or {}})

    def test_separated_competing_arm_motion_with_unknown_racket_hand_has_no_complete_phases(self):
        frames = []
        for i in range(35):
            t = i * 50
            left = interpolate(t, [(0, (-1, .5)), (300, (-1, .5)), (600, (1, .5))])
            right = interpolate(t, [(0, (1, .5)), (650, (1, .5)), (850, (-1, .5))])
            frames.append(frame(i, right=right, left=left))
        candidate = {"family": "baseline", "racket_hand_candidate": "left", "start_ms": 300,
                     "peak_ms": 500, "end_ms": 650, "evidence": {}}
        result = self._prediction(frames, candidate)
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["phase_ambiguity"]["reason_code"], "unresolved_racket_hand_with_competing_arm_sequence")
        self.assertTrue(all(p["status"] == "unavailable" for p in result["phases"]))
        # Independent racket ownership resolves the ambiguity; the opposite
        # arm's later movement cannot erase the identified wrist's measurement.
        known = self._prediction(frames, candidate, hand="left")
        self.assertIsNone(known["phase_ambiguity"])
        self.assertEqual(known["analysis_status"], "complete")

        # Sparse but independently separated racket boxes support this wrist's
        # chronology while leaving the stricter eight-vote taxonomy unknown.
        supported = self._prediction(frames, candidate, votes={"left": 3, "right": 1})
        self.assertIsNone(supported["phase_ambiguity"])
        self.assertEqual(supported["analysis_status"], "complete")
        self.assertEqual(supported["classification"]["label"], "unclassified")
        conflicting = self._prediction(frames, candidate, votes={"left": 3, "right": 2})
        self.assertEqual(conflicting["analysis_status"], "partial")

    def test_external_preparatory_lift_cannot_borrow_a_later_cross_body_sweep(self):
        anchors = [(0, (.1, .8)), (300, (.1, .8)), (400, (-.25, .3)),
                   (600, (-.25, .3)), (1200, (.9, .3)), (2200, (.9, .3))]
        frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(45)]
        candidate = {"family": "baseline", "racket_hand_candidate": "right", "start_ms": 600,
                     "peak_ms": 900, "end_ms": 1200, "evidence": {}}
        result = self._prediction(frames, candidate, hand="right")
        self.assertLess(result["peak_ms"], candidate["start_ms"])
        self.assertEqual(result["analysis_status"], "partial")
        ambiguity = result["phase_ambiguity"]
        self.assertEqual(ambiguity["reason_code"], "external_peak_motion_geometry_unverified")
        self.assertEqual(ambiguity["candidate_motion_peak_ms"], result["peak_ms"])
        self.assertLess(ambiguity["local_sweep_geometry"]["horizontal_excursion_torso_units"], .9)
        self.assertTrue(all(p["status"] == "unavailable" for p in result["phases"]))

        # Merely touching endpoints is still a separate interval. Genuine
        # overlap retains the original candidate's own observed evidence.
        touching = {**candidate, "start_ms": result["end_ms"]}
        self.assertEqual(self._prediction(frames, touching, hand="right")["analysis_status"], "partial")
        overlapping = {**candidate, "start_ms": result["end_ms"] - 50}
        retained = self._prediction(frames, overlapping, hand="right")
        self.assertLess(retained["peak_ms"], overlapping["start_ms"])
        self.assertEqual(retained["analysis_status"], "complete")
        self.assertIsNone(retained["phase_ambiguity"])

    def test_external_real_low_sweep_retains_its_own_geometry_without_new_racket_gate(self):
        anchors = [(0, (.9, .4)), (300, (.9, .4)), (600, (-.9, .7)),
                   (750, (-.9, .7)), (1500, (.9, .7)), (2200, (.9, .7))]
        frames = [frame(i, right=interpolate(i * 50, anchors), racket=False) for i in range(45)]
        candidate = {"family": "baseline", "racket_hand_candidate": "right", "start_ms": 750,
                     "peak_ms": 1150, "end_ms": 1500, "evidence": {}}
        result = self._prediction(frames, candidate, hand="right")
        self.assertLess(result["peak_ms"], candidate["start_ms"])
        self.assertEqual(result["analysis_status"], "complete")
        self.assertIsNone(result["phase_ambiguity"])

    def test_observed_reset_keeps_a_larger_previous_peak_out_of_the_next_action(self):
        anchors = [(0, (.9, .5)), (550, (.9, .5)), (650, (-.9, .5)),
                   (750, (.9, .5)), (1200, (.9, .5)), (1600, (-.9, .5)), (2300, (-.9, .5))]
        frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(9, 47)]
        candidate = {"family": "baseline", "racket_hand_candidate": "right", "start_ms": 1100,
                     "peak_ms": 1400, "end_ms": 1650, "evidence": {}}
        result = self._prediction(frames, candidate, hand="right")
        self.assertGreaterEqual(result["peak_ms"], 1200)
        self.assertLessEqual(result["peak_ms"], 1600)

    def test_longer_observed_window_keeps_racket_evidence_lost_in_fast_sweep(self):
        frames = swing()
        for item in frames:
            if item["frame"]["timestamp_ms"] not in {300, 350}:
                item["detections"] = []
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertLessEqual(candidate["start_ms"], 300)
        self.assertGreater(candidate["end_ms"], candidate["peak_ms"])
        self.assertEqual(candidate["evidence"]["racket_associated_frames"], 2)
        # One isolated box is still insufficient; no evidence threshold changed.
        frames[6]["detections"] = []
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_raise_toss_and_low_retrieval_are_not_horizontal_sweeps(self):
        for anchors in (
            [(0, (.7, .7)), (500, (.7, .7)), (900, (.7, -1.3)), (1500, (.7, -1.3))],
            [(0, (.5, .8)), (500, (.5, .8)), (900, (.6, -1.2)), (1400, (.6, .8))],
            [(0, (.6, .7)), (500, (.6, .7)), (1100, (.7, 1.8)), (1700, (.6, .7))],
        ):
            with self.subTest(anchors=anchors):
                frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(45)]
                self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_rigid_low_arm_body_turn_cannot_borrow_an_earlier_racket_box(self):
        import math
        frames = []
        for i in range(36):
            lateral = .55 * math.cos(math.pi * min(1, max(0, (i - 6) / 16)))
            item = frame(i, right=(lateral + .05, .85), racket=i in {6, 7})
            # The arm hangs at the same shoulder-relative offset while the
            # projected body axis turns. This is not independent arm motion.
            for point in item["poses"][0]["keypoints"]:
                if point["name"] in {"left_shoulder", "right_shoulder", "left_hip", "right_hip"}:
                    sign = 1 if point["name"].startswith("right") else -1
                    point["x_px"] = 300 + sign * lateral * 100
            frames.append(item)
        self.assertEqual(detect_stroke_candidates(frames)["candidate_count"], 0)

    def test_low_cut_with_visible_arm_excursion_keeps_earlier_racket_support(self):
        frames = swing()
        for item in frames:
            for point in item["poses"][0]["keypoints"]:
                if point["name"] == "right_wrist":
                    point["y_px"] += 25
            for box in item["detections"]:
                box["bbox_px"][1] += 25
                box["bbox_px"][3] += 25
            if item["frame"]["timestamp_ms"] not in {300, 350}:
                item["detections"] = []
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["candidate_count"], 1)
        self.assertLessEqual(result["candidates"][0]["start_ms"], 300)

    def test_fast_serve_keeps_observed_toss_onset_and_early_racket_evidence(self):
        frames = []
        for i in range(45):
            t = i * 50
            right = interpolate(t, [(0, (.65, .8)), (1000, (.65, .8)),
                                    (1200, (.65, -1.2)), (1400, (.65, .8))])
            left = interpolate(t, [(0, (-.55, .8)), (300, (-.55, .8)),
                                   (650, (-.55, -1)), (950, (-.55, -1)), (1200, (-.55, .6))])
            frames.append(frame(i, right=right, left=left, racket=t <= 600))
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["by_family"]["serve"], 1)
        candidate = next(c for c in result["candidates"] if c["family"] == "serve")
        self.assertLess(candidate["start_ms"], 650)
        self.assertGreaterEqual(candidate["peak_ms"], 1200)
        self.assertGreater(candidate["end_ms"], 1200)
        self.assertGreaterEqual(candidate["evidence"]["racket_associated_frames"], 2)
        # Identical pose sequence without racket evidence remains insufficient.
        without_racket = deepcopy(frames)
        for item in without_racket:
            item["detections"] = []
        self.assertEqual(detect_stroke_candidates(without_racket)["by_family"]["serve"], 0)

    def test_slower_low_takeback_is_context_not_an_extra_stroke(self):
        result = detect_stroke_candidates(counter_sweep())
        self.assertEqual(result["candidate_count"], 1)
        evidence = result["candidates"][0]["evidence"]
        linked = evidence["supporting_motion_candidates"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["relation"], "preparation")
        self.assertFalse(linked[0]["automatic_scoring_allowed"])
        episode = result["motion_analysis"]["families"]["baseline"]["episodes"][0]
        self.assertLessEqual(episode["start_ms"], linked[0]["start_ms"])
        self.assertLess(episode["phases"][0]["end_ms"], episode["peak_ms"])

    def test_similar_speed_opposite_sweeps_remain_separate(self):
        result = detect_stroke_candidates(counter_sweep(strong=False))
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(all(not c["evidence"].get("supporting_motion_candidates") for c in result["candidates"]))

    def test_no_grouping_across_missing_observations(self):
        result = detect_stroke_candidates(counter_sweep(gap=True))
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(all(not c["evidence"].get("supporting_motion_candidates") for c in result["candidates"]))

    def test_low_stroke_with_recovery_and_new_preparation_is_not_takeback(self):
        anchors = [(0, (.9, .7)), (400, (.9, .7)), (1000, (-1, .7)),
                   (1100, (-1, .7)), (1400, (.5, .7)), (1800, (-1, .7)),
                   (1950, (-1, .7)), (2250, (1, -.2)), (3300, (1, -.2))]
        frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(67)]
        result = detect_stroke_candidates(frames)
        self.assertTrue(any(c["peak_ms"] <= 1000 for c in result["candidates"]))
        self.assertTrue(any(c["peak_ms"] >= 1950 for c in result["candidates"]))
        self.assertTrue(all(not c["evidence"].get("supporting_motion_candidates") for c in result["candidates"]))

    def test_raised_preparation_before_horizontal_forward_sweep_is_one_motion(self):
        anchors = [(0, (-.55, .7)), (400, (-.55, .7)), (1100, (.5, -.2)),
                   (1900, (.5, -.2)), (2200, (-1, -.2)), (3100, (-1, -.2))]
        frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(64)]
        result = detect_stroke_candidates(frames)
        self.assertEqual(result["candidate_count"], 1)
        linked = result["candidates"][0]["evidence"]["supporting_motion_candidates"]
        self.assertEqual(linked[0]["reason_code"], "raised_preparation_before_faster_counter_sweep")
        self.assertLess(linked[0]["peak_ms"], result["candidates"][0]["peak_ms"])

    def test_two_real_sweeps_with_lower_reset_after_high_follow_remain_separate(self):
        anchors = [(0, (-.55, .7)), (400, (-.55, .7)), (1100, (.5, -.2)),
                   (1250, (.5, -.2)), (1550, (.1, .8)), (1900, (.5, -.2)),
                   (2200, (.5, -.2)), (2500, (-1, -.2)), (3400, (-1, -.2))]
        frames = [frame(i, right=interpolate(i * 50, anchors)) for i in range(70)]
        result = detect_stroke_candidates(frames)
        self.assertTrue(any(c["peak_ms"] <= 1100 for c in result["candidates"]))
        self.assertTrue(any(c["peak_ms"] >= 2200 for c in result["candidates"]))
        self.assertTrue(all(not c["evidence"].get("supporting_motion_candidates") for c in result["candidates"]))

    def test_independently_supported_different_racket_hands_remain_separate(self):
        # Exercise the sequence stage after candidate ownership has been
        # established. Both events remain exactly as supplied by detection.
        from rallymate_scoring.stroke_candidates import _group_supporting_sweeps, _sample, _associate_rackets
        from unittest.mock import patch
        frames = counter_sweep()
        samples = []
        for item in frames:
            sample = _sample(item["poses"][0], item["frame"])
            _associate_rackets([sample], item["detections"])
            samples.append(sample)
        with patch("rallymate_scoring.stroke_candidates._group_supporting_sweeps", side_effect=lambda candidates, _: candidates):
            candidates = detect_stroke_candidates(frames)["candidates"]
        self.assertEqual(len(candidates), 2)
        candidates[0]["racket_hand_candidate"] = "left"
        candidates[0]["evidence"]["hand_consistency_before_suppression"] = {
            "status": "matched", "hand_evidence": {"hand": "left"}}
        grouped = _group_supporting_sweeps(candidates, {1: samples})
        self.assertEqual(len(grouped), 2)


if __name__ == "__main__":
    unittest.main()
