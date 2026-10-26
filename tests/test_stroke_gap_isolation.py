"""Candidate-local continuity regressions; synthetic contracts, not video truth."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from rallymate_scoring.process_motion import _trajectory
from rallymate_scoring.stroke_analysis import _episode, _episode_context, build_motion_analysis
from rallymate_scoring.stroke_candidates import Sample, _associate_rackets, _sample, detect_stroke_candidates
from rallymate_scoring.temporal_recognition import RuleTemporalBackend, wrist_kinematics
from rallymate_scoring.vector_kinematics import WristKinematicsUnavailable, vector_trajectory
from test_stroke_analysis import swing


def gap_case(kind, *, split_epoch=True):
    # The full context has a slower median cadence than its last nine samples.
    # Existing context selection accepts this local segment; its own measured
    # cadence exposes a gap. Neither a frame nor a wrist point may be invented.
    if kind == "time":
        times = list(range(0, 601, 50)) + [620, 640, 660, 720, 740, 760, 780, 800, 820]
        frames = list(range(len(times)))
        start, peak, end = 640, 720, 780
    else:
        times = [i * 50 for i in range(22)]
        frames = [i * 3 for i in range(13)] + [37, 38, 39, 42, 43, 44, 45, 46, 47]
        start, peak, end = 700, 800, 950
    samples = [Sample(t, f, 1, (0., 0.), 100., {
        "right_wrist": (t / 1000, .55), "left_wrist": (-.9, .55),
        "left_shoulder": (-.3, 0), "right_shoulder": (.3, 0),
        "left_hip": (-.25, 1), "right_hip": (.25, 1), "right_elbow": (.5, .3),
    }, {"right"}, selection_epoch=int(split_epoch and i >= 13), timestamp_source="decoder_pts")
        for i, (t, f) in enumerate(zip(times, frames))]
    candidate = {"candidate_id": "affected-" + kind, "family": "baseline", "person_track_id": 1,
                 "racket_hand_candidate": "right", "start_ms": start, "peak_ms": peak, "end_ms": end,
                 "evidence": {"racket_associated_frames": 9}}
    return samples, candidate


def valid_case():
    records = swing()
    for record in records:
        record["poses"][0]["person_track_id"] = 2
    detected = detect_stroke_candidates(records)
    assert len(detected["candidates"]) == 1
    samples = []
    for record in records:
        sample = _sample(record["poses"][0], record["frame"])
        _associate_rackets([sample], record["detections"])
        samples.append(sample)
    return samples, detected["candidates"][0]


class UnavailableBackend(RuleTemporalBackend):
    def recognize(self, window, candidate, hand_evidence):
        return None


class CroppingBackend(RuleTemporalBackend):
    """A contract-valid external segmenter; it does not calculate wrist speed."""
    def recognize(self, window, candidate, hand_evidence):
        samples = list(window.samples)[-9:]
        bounds = [samples[i].time for i in (0, 2, 4, 8)]
        return {
            "start_ms": bounds[0], "peak_ms": bounds[2], "end_ms": bounds[3],
            "classification": {"label": "unclassified", "status": "unclassified"},
            "contact_confirmed": False, "analysis_status": "complete",
            "phase_method": "synthetic_observed_bounds", "phase_timing_status": "synthetic_contract_only",
            "phases": [{"phase": name, "status": "measured", "start_ms": a, "end_ms": b,
                        "anchor_is_contact": False}
                       for name, a, b in zip(("preparation", "acceleration", "follow_through"), bounds, bounds[1:])],
        }


class StrokeGapIsolationTests(unittest.TestCase):
    def assert_candidate_isolated(self, kind):
        samples, candidate = gap_case(kind)
        original = deepcopy(samples)
        context = _episode_context(candidate, samples)
        self.assertEqual(len(context), 9)
        for measure in (wrist_kinematics, _trajectory):
            with self.assertRaises(WristKinematicsUnavailable):
                measure(context, "right")
        reasons = []
        self.assertIsNone(_episode(candidate, samples, unavailable_reasons=reasons))
        self.assertEqual(reasons, ["wrist_observation_discontinuity"])
        good_samples, good_candidate = valid_case()
        expected = build_motion_analysis([good_candidate], {2: good_samples})
        self.assertEqual(expected["families"]["baseline"]["summary"]["analyzed_count"], 1)
        for candidates in ([candidate, good_candidate], [good_candidate, candidate]):
            result = build_motion_analysis(candidates, {1: samples, 2: good_samples})
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["families"]["baseline"]["episodes"], expected["families"]["baseline"]["episodes"])
            self.assertIn("已跳过 1 个手腕观测不连续", result["families"]["baseline"]["reason_zh"])
            self.assertEqual(result["families"]["serve"], expected["families"]["serve"])
            self.assertEqual(result["families"]["return"], expected["families"]["return"])
            json.dumps(result, allow_nan=False)
        self.assertEqual(samples, original)

    def test_local_time_cadence_gap_cancels_only_affected_candidate(self):
        self.assert_candidate_isolated("time")

    def test_local_frame_cadence_gap_cancels_only_affected_candidate(self):
        self.assert_candidate_isolated("frame")

    def test_unavailable_external_backend_rule_fallback_is_also_isolated(self):
        samples, candidate = gap_case("time")
        reasons = []
        self.assertIsNone(_episode(candidate, samples, temporal_backend=UnavailableBackend(), unavailable_reasons=reasons))
        self.assertEqual(reasons, ["wrist_observation_discontinuity"])

    def test_final_report_crop_rechecks_its_own_cadence_without_bridging_gap(self):
        for kind in ("time", "frame"):
            with self.subTest(kind=kind):
                samples, candidate = gap_case(kind, split_epoch=False)
                context = _episode_context(candidate, samples)
                expected_count = 22 if kind == "time" else 21
                self.assertEqual(len(context), expected_count)
                # The parent context is genuinely measurable. The reported
                # nine-frame child has a stricter local median cadence.
                points, speeds = wrist_kinematics(context, "right")
                self.assertEqual(len(points), expected_count)
                self.assertTrue(all(value is not None for value in speeds))
                reasons = []
                self.assertIsNone(_episode(candidate, samples, temporal_backend=CroppingBackend(), unavailable_reasons=reasons))
                self.assertEqual(reasons, ["wrist_observation_discontinuity"])

    def test_non_continuity_errors_still_propagate_from_rule_and_final_measurement(self):
        samples, candidate = valid_case()
        for backend in (None, UnavailableBackend()):
            with self.subTest(backend=type(backend).__name__):
                with patch.object(RuleTemporalBackend, "recognize", side_effect=ValueError("unrelated rule error")):
                    with self.assertRaisesRegex(ValueError, "unrelated rule error"):
                        _episode(candidate, samples, temporal_backend=backend)
        with patch("rallymate_scoring.stroke_analysis.wrist_kinematics", side_effect=ValueError("unrelated final error")):
            with self.assertRaisesRegex(ValueError, "unrelated final error"):
                _episode(candidate, samples)
        with patch("rallymate_scoring.stroke_analysis.analyze_process_motion", side_effect=ValueError("unrelated process error")):
            with self.assertRaisesRegex(ValueError, "unrelated process error"):
                _episode(candidate, samples)

    def test_malformed_vector_inputs_are_not_reclassified_as_missing_observations(self):
        with self.assertRaises(ValueError) as error:
            vector_trajectory([0, 50], [(0, 0), (1, 1)], frame_indices=[0, -1])
        self.assertNotIsInstance(error.exception, WristKinematicsUnavailable)


if __name__ == "__main__":
    unittest.main()
