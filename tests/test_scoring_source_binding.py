"""A grade's trace must describe the model geometry actually being scored."""
from copy import deepcopy
import unittest

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import model_geometry_evidence
from rallymate_scoring.scoring import score_indicator
from tests.test_direct_scoring_rules import evaluate, feature, rule
from tests.test_sam_footwork import sam_motion_fixture


class ScoringSourceBindingTests(unittest.TestCase):
    def setUp(self):
        records, timeline = sam_motion_fixture()
        proof = model_geometry_evidence(pose_sequence_from_records(records, timeline), range(4))
        self.item = {**feature(), **proof, "confidence": None}
        self.window = self.item["source_window"]

    def test_explicit_identity_must_match_features_even_with_a_single_trace(self):
        for field, values in {
            "person_track_id": (999, True, "7", None),
            "selection_epoch": (999, True, "3", None),
            "camera_reference_epoch": (999, False, "0", None),
            "video_sha256": ("b" * 64, None, ""),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    result = evaluate(self.item, rule(), evidence=[{field: value}])
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["grade"])

    def test_optional_trace_fields_can_be_omitted_without_inventing_confidence(self):
        for trace in ({}, {"event_id": "event", "video_id": "video"},
                      {key: self.window[key] for key in ("person_track_id", "selection_epoch", "camera_reference_epoch")},
                      {"video_sha256": self.window["video_sha256"].upper()}):
            with self.subTest(trace=trace):
                result = evaluate(self.item, rule(), evidence=[trace])
                self.assertEqual(result["grade"], "B")
                self.assertIsNone(result["confidence"])

    def test_trace_frames_cannot_substitute_another_window(self):
        for value in ([9999], [True], [0, 0], [], "0", None):
            with self.subTest(value=value):
                self.assertEqual(evaluate(self.item, rule(), evidence=[{"source_frames": value}])["status"], "unavailable")
        frames = self.window["source_frames"]
        self.assertEqual(evaluate(self.item, rule(), evidence=[{"source_frames": [frames[0], frames[-1]]}])["grade"], "B")

    def test_original_clock_and_nested_bindings_are_checked(self):
        original = self.window["source_bindings"][0]
        self.assertEqual(evaluate(self.item, rule(), evidence=[deepcopy(original)])["grade"], "B")
        for field in ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den", "timestamp_ms"):
            broken = {**original, field: original[field] + 1}
            for trace in (broken, {"source_bindings": [broken]}):
                with self.subTest(field=field, nested="source_bindings" in trace):
                    self.assertEqual(evaluate(self.item, rule(), evidence=[trace])["status"], "unavailable")
        self.assertEqual(evaluate(self.item, rule(), evidence=[{"pts": original["pts"]}])["status"], "unavailable")
        trace = {"source_frames": self.window["source_frames"],
                 "source_timestamps_ms": self.window["source_timestamps_ms"],
                 "video_timestamps_ms": self.window["video_timestamps_ms"]}
        self.assertEqual(evaluate(self.item, rule(), evidence=[trace])["grade"], "B")
        broken = deepcopy(trace)
        broken["source_timestamps_ms"][1] += 1
        self.assertEqual(evaluate(self.item, rule(), evidence=[broken])["status"], "unavailable")

    def test_nested_window_cannot_disagree_with_the_measured_window(self):
        self.assertEqual(evaluate(self.item, rule(), evidence=[{"source_window": self.window}])["grade"], "B")
        for field in ("start_ms", "end_ms", "selection_epoch"):
            broken = deepcopy(self.window)
            broken[field] += 1
            with self.subTest(field=field):
                self.assertEqual(evaluate(self.item, rule(), evidence=[{"source_window": broken}])["status"], "unavailable")

    def test_two_features_cannot_bind_the_same_frame_to_different_pts(self):
        other = deepcopy(self.item)
        other["feature_name"] = "other"
        window = other["source_window"]
        for index, binding in enumerate(window["source_bindings"]):
            binding["pts"] += binding["time_base_den"]
            shift = binding["time_base_num"] * 1000
            binding["timestamp_ms"] += shift
            window["source_timestamps_ms"][index] += shift
        result = score_indicator(indicator_id="FS01-M02", features=[self.item, other],
                                 calibration=rule(), model_versions={}, evidence=[])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("model_source_clock_mismatch", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
