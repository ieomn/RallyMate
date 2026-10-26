from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from rallymate_scoring.stroke_review_fallback import build_review_fallback_candidates

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("card_video_validation", ROOT / "scripts/replay_card_video_validation.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class CardVideoValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / "tests/fixtures/card-video-observations.json").read_text(encoding="utf-8"))["cases"]

    def test_duplicate_predictions_cannot_inflate_recall(self):
        truth = [{"id": "one", "start_ms": 100, "end_ms": 300, "anchor_ms": 200, "action_type": "forehand"}]
        candidates = [{"candidate_id": str(i), "start_ms": 100, "peak_ms": 200, "end_ms": 300} for i in range(2)]
        result = validation.match_action_windows(candidates, truth)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["false_candidate_count"], 1)
        self.assertEqual(result["precision"], .5)
        self.assertEqual(result["unknown_classification_count"], 1)

    def test_assignment_prefers_exact_swing_over_overlapping_preparation(self):
        truth = [{"id": "one", "start_ms": 200, "end_ms": 400, "anchor_ms": 300, "action_type": "forehand"}]
        candidates = [{"candidate_id": "accurate", "start_ms": 200, "peak_ms": 300, "end_ms": 400},
                      {"candidate_id": "preparation", "start_ms": 0, "peak_ms": 100, "end_ms": 350}]
        result = validation.match_action_windows(candidates, truth)
        self.assertEqual(result["matches"][0]["candidate_id"], "accurate")
        self.assertEqual(result["false_candidates"][0]["candidate_id"], "preparation")

    def test_assignment_preserves_maximum_matches_when_windows_overlap(self):
        truth = [{"id": str(i), "start_ms": i * 200, "end_ms": i * 200 + 100,
                  "anchor_ms": i * 200 + 50, "action_type": "forehand"} for i in range(2)]
        candidates = [{"candidate_id": "wide", "start_ms": 0, "peak_ms": 50, "end_ms": 300},
                      {"candidate_id": "only-first", "start_ms": 0, "peak_ms": 40, "end_ms": 100}]
        result = validation.match_action_windows(candidates, truth)
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["false_candidate_count"], 0)

    def test_actual_public_video_off_axis_windows_are_recovered_for_review(self):
        expected = {"ocw-pe.710-2-forehand-220k": [7820, 10820],
                    "ocw-pe.710-1hand_backhand-220k": [],
                    "ocw-pe.710-2-hand_backhand-220k": [9554, 13220]}
        for case in self.cases:
            with self.subTest(case=case["case_id"]):
                samples = [SimpleNamespace(**item) for item in case["samples"]]
                tracks = {track: [s for s in samples if s.track == track] for track in {s.track for s in samples}}
                result = build_review_fallback_candidates(tracks, case["primary_candidates"])
                self.assertEqual([r["peak_ms"] for r in result], expected[case["case_id"]])
                for row in result:
                    self.assertEqual(row["status"], "manual_review_required")
                    self.assertFalse(row["automatic_scoring_allowed"])
                    self.assertFalse(row["contact_confirmed"])
                    self.assertNotIn("score", row)

    def test_source_time_or_identity_break_cannot_be_bridged(self):
        case = self.cases[-1]
        for attribute in ("selection_epoch", "camera_reference_epoch", "track"):
            samples = [SimpleNamespace(**deepcopy(item)) for item in case["samples"]]
            for i, sample in enumerate(samples):
                setattr(sample, attribute, i)
            result = build_review_fallback_candidates({samples[0].track: samples}, case["primary_candidates"])
            self.assertEqual(result, [], attribute)

    def test_missing_racket_is_not_recovered_as_a_swing(self):
        case = self.cases[-1]
        samples = [SimpleNamespace(**deepcopy(item)) for item in case["samples"]]
        for sample in samples:
            sample.racket_sides = set()
        self.assertEqual(build_review_fallback_candidates({samples[0].track: samples}), [])

    def test_primary_suppression_requires_original_frames_and_same_reference(self):
        case = self.cases[-1]
        samples = [SimpleNamespace(**deepcopy(item)) for item in case["samples"]]
        track = samples[0].track
        first = build_review_fallback_candidates({track: samples})[0]
        primary = {key: first[key] for key in ("person_track_id", "start_frame", "peak_frame", "end_frame", "start_ms", "peak_ms", "end_ms")}
        self.assertEqual([r["peak_ms"] for r in build_review_fallback_candidates({track: samples}, [primary])], [13220])
        invalid = {**primary, "start_frame": primary["start_frame"] + 99999}
        self.assertEqual([r["peak_ms"] for r in build_review_fallback_candidates({track: samples}, [invalid])], [9554, 13220])
        # A nearby previous selection has the same track number and valid
        # source anchors, but must not suppress a new selection/camera/source.
        original = [s for s in samples if primary["start_ms"] <= s.time <= primary["end_ms"]]
        for boundary in ("selection_epoch", "camera_reference_epoch", "video_sha256"):
            previous = deepcopy(original)
            for sample in previous:
                sample.time -= 600
                sample.frame_index -= 100
                sample.racket_sides = set()
                if boundary == "video_sha256":
                    sample.source_binding = {"video_sha256": "a" * 64}
                else:
                    setattr(sample, boundary, getattr(sample, boundary) + 100)
            prior = {**primary, **{f"{part}_ms": primary[f"{part}_ms"] - 600 for part in ("start", "peak", "end")},
                     **{f"{part}_frame": primary[f"{part}_frame"] - 100 for part in ("start", "peak", "end")}}
            result = build_review_fallback_candidates({track: previous + samples}, [prior])
            self.assertEqual([r["peak_ms"] for r in result], [9554, 13220], boundary)


if __name__ == "__main__":
    unittest.main()
