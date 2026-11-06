from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_features import foot_crossing
from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import (
    clear_source_aligned_feature_cache, compute_source_aligned_features,
    prepare_source_aligned_context,
)
from rallymate_scoring import source_assessment as assessment
from rallymate_vision.postprocess_resources import PostprocessResourceLimit
from tests.test_contextual_hip_assessment import (
    contextual_fixture, seed_inputs as seed_contextual_inputs, VIDEO_ID, VIDEO_SHA, SOURCE_ID,
)
from tests.test_foot_crossing_candidates import crossing_fixture
from tests.test_source_aligned_features import fixture
from tests.test_source_assessment_crossing_integration import seed_inputs


class SourceAssessmentCancellationTests(unittest.TestCase):
    def tearDown(self):
        clear_source_aligned_feature_cache()

    def test_streaming_json_and_digest_keep_exact_legacy_bytes(self):
        value = {"z": [{"汉字": "网球\n", "numbers": [0, -0.0, 1.2e-15, None, True]}] * 3000,
                 "a": "\U0001f3be"}
        calls = []
        encoded = b"".join(assessment._json_chunks(value, cancellation_callback=lambda: calls.append(1)))
        expected = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.assertEqual(encoded, expected)
        expected_digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(assessment.contextual_input_digest(value), expected_digest)
        self.assertGreater(len(calls), 10)

    def test_callback_does_not_change_artifact_bytes_or_manifest(self):
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            before = assessment.write_source_aligned_assessment(
                directory, sequence, events, video_id="video-a", primary_timeline=timeline)
            paths = [directory / assessment.ARTIFACT_NAME, directory / assessment.CROSSING_ARTIFACT_NAME]
            original = [path.read_bytes() for path in paths]
            calls = []
            after = assessment.write_source_aligned_assessment(directory, sequence, events, video_id="video-a",
                primary_timeline=timeline, cancellation_callback=lambda: calls.append(1))
            self.assertEqual(before, after)
            self.assertEqual(original, [path.read_bytes() for path in paths])
            for raw in original:
                self.assertEqual(raw, (json.dumps(json.loads(raw), ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
            self.assertGreater(len(calls), len(events))

    def test_indicator_loop_is_interruptible(self):
        sequence, events, timeline = fixture()
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        calls = 0
        error = RuntimeError("caller control")

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise error

        with self.assertRaises(RuntimeError) as caught:
            compute_source_aligned_features(sequence, events[0], events=events, context=context,
                                             cancellation_callback=cancel)
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, 3)

    def test_adjacent_frame_scan_is_interruptible(self):
        sequence, timeline = crossing_fixture()
        seen = []
        original_pair = foot_crossing._Context.pair
        error = RuntimeError("stop frame scan")

        def pair(context, before):
            seen.append(before)
            return original_pair(context, before)

        def cancel():
            if seen:
                raise error

        with patch.object(foot_crossing._Context, "pair", pair), self.assertRaises(RuntimeError) as caught:
            foot_crossing.compute_foot_crossing_candidates(sequence, primary_timeline=timeline,
                cancellation_callback=cancel)
        self.assertIs(caught.exception, error)
        self.assertEqual(seen, [0])

    def test_callback_errors_and_resource_limits_escape_optional_boundaries(self):
        records, timeline, event = contextual_fixture()
        sequence = pose_sequence_from_records(records, timeline)
        for helper in ("compute_contextual_hip_measurement", "compute_foot_crossing_candidates"):
            for error in (RuntimeError("custom callback"), PostprocessResourceLimit("budget")):
                with self.subTest(helper=helper, error=type(error).__name__), tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    seed_contextual_inputs(directory, records, timeline, [event])
                    entered = False
                    original = getattr(assessment, helper)

                    def compute(*args, **kwargs):
                        nonlocal entered
                        entered = True
                        return original(*args, **kwargs)

                    def cancel():
                        if entered:
                            raise error

                    with patch.object(assessment, helper, compute), self.assertRaises(type(error)) as caught:
                        assessment.write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                            video_sha256=VIDEO_SHA, primary_timeline=timeline, raw_records=records,
                            source_id=SOURCE_ID, cancellation_callback=cancel)
                    self.assertIs(caught.exception, error)

    def test_resource_limit_raised_by_optional_helper_is_not_downgraded(self):
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            error = PostprocessResourceLimit("budget")
            with patch.object(assessment, "compute_foot_crossing_candidates", side_effect=error), \
                    self.assertRaises(PostprocessResourceLimit) as caught:
                assessment.write_source_aligned_assessment(directory, sequence, events, video_id="video-a",
                    primary_timeline=timeline)
            self.assertIs(caught.exception, error)

    def test_cancelled_json_write_cleans_temporary_and_preserves_complete_artifact(self):
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            artifact = directory / assessment.ARTIFACT_NAME
            artifact.write_bytes(b"previous complete artifact")
            original_write = assessment._write_json
            writing = False
            error = PostprocessResourceLimit("cancel JSON")

            def write(*args, **kwargs):
                nonlocal writing
                writing = True
                return original_write(*args, **kwargs)

            def cancel():
                if writing:
                    raise error

            with patch.object(assessment, "_write_json", write), self.assertRaises(PostprocessResourceLimit) as caught:
                assessment.write_source_aligned_assessment(directory, sequence, events, video_id="video-a",
                    primary_timeline=timeline, cancellation_callback=cancel)
            self.assertIs(caught.exception, error)
            self.assertEqual(artifact.read_bytes(), b"previous complete artifact")
            self.assertFalse((directory / (assessment.ARTIFACT_NAME + ".tmp")).exists())


if __name__ == "__main__":
    unittest.main()
