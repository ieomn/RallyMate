from __future__ import annotations

import copy
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.source_assessment import (
    ARTIFACT_NAME, CROSSING_ARTIFACT_NAME, file_sha, write_foot_crossing_candidates, write_source_aligned_assessment,
)
from test_foot_crossing_candidates import crossing_fixture
from test_source_aligned_features import fixture


def seed_inputs(directory, timeline, events):
    (directory / "frames.jsonl").write_text("[]\n", encoding="utf-8")
    for name, rows in (("primary-player.jsonl", timeline), ("events.jsonl", events)):
        (directory / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class CrossingArtifactIntegrationTests(unittest.TestCase):
    def assert_legacy_unchanged(self, directory, result, baseline, legacy_bytes):
        self.assertEqual((directory / ARTIFACT_NAME).read_bytes(), legacy_bytes)
        self.assertEqual({key: value for key, value in result.items() if key != "foot_crossing_candidates"},
                         {key: value for key, value in baseline.items() if key != "foot_crossing_candidates"})
        optional = result["foot_crossing_candidates"]
        self.assertEqual(optional["status"], "unavailable")
        self.assertEqual(optional["reason_code"], "optional_candidate_generation_failed")
        self.assertIsNone(optional["path"])
        self.assertIsNone(optional["sha256"])
        self.assertIsNone(optional["candidate_count"])
        self.assertFalse(optional["scoring_allowed"])
        self.assertTrue(optional["existing_candidate_file_is_not_this_attempt"])
        self.assertLess(len(json.dumps(optional)), 700)

    def test_existing_entry_writes_independent_sidecar_and_keeps_legacy_bytes(self):
        sequence, events, timeline = fixture()
        original_events = copy.deepcopy(events)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            first = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
            legacy_bytes = (directory / ARTIFACT_NAME).read_bytes()
            artifact = json.loads(legacy_bytes)
            self.assertEqual(first["event_count"], 5)
            self.assertEqual(first["indicator_window_count"], sum(len(row["indicators"]) for row in artifact["records"]))
            self.assertIsNone(artifact["technical_grade"])
            self.assertIsNone(artifact["technical_score_0_to_100"])
            self.assertNotIn("foot_crossing_candidates", artifact)
            self.assertEqual(first["foot_crossing_candidates"]["path"], CROSSING_ARTIFACT_NAME)
            sidecar = json.loads((directory / CROSSING_ARTIFACT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["counts"]["candidates"], 0)
            self.assertEqual(sidecar["counts"]["excluded"], len(sequence.timestamp_ms) - 1)
            # Change only the independent computation; existing measurement bytes
            # and counts must remain unchanged even when its outcome differs.
            changed_candidate = {"version": "test-independent-result", "counts": {"candidates": 9},
                                 "candidates": [], "scoring_allowed": False}
            with patch("rallymate_scoring.source_assessment.compute_foot_crossing_candidates", return_value=changed_candidate):
                second = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
            self.assertEqual((directory / ARTIFACT_NAME).read_bytes(), legacy_bytes)
            self.assertEqual(second["sha256"], first["sha256"])
            self.assertEqual(second["event_count"], first["event_count"])
            self.assertEqual(second["foot_crossing_candidates"]["candidate_count"], 9)
            self.assertEqual(events, original_events)

    def test_geometry_only_writer_hashes_inputs_without_running_legacy_scoring(self):
        sequence, timeline = crossing_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, [])
            with patch("rallymate_scoring.source_assessment.compute_source_aligned_features", side_effect=AssertionError("legacy scoring invoked")):
                result = write_foot_crossing_candidates(directory, sequence, [], video_id="v", video_sha256="A" * 64,
                                                        primary_timeline=timeline)
            artifact = json.loads((directory / CROSSING_ARTIFACT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["sha256"], file_sha(directory / CROSSING_ARTIFACT_NAME))
            self.assertEqual(artifact["video_sha256"], "a" * 64)
            self.assertEqual(artifact["frames_sha256"], file_sha(directory / "frames.jsonl"))
            self.assertEqual(artifact["events_sha256"], file_sha(directory / "events.jsonl"))
            self.assertEqual(artifact["primary_timeline_sha256"], file_sha(directory / "primary-player.jsonl"))
            self.assertFalse((directory / ARTIFACT_NAME).exists())

    def test_writer_rejects_conflicting_or_malformed_video_identity(self):
        sequence, timeline = crossing_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, [])
            for digest in ("b" * 64, "not-a-sha"):
                with self.assertRaises(ValueError):
                    write_foot_crossing_candidates(directory, sequence, [], video_id="v", video_sha256=digest,
                                                   primary_timeline=timeline)
                self.assertFalse((directory / CROSSING_ARTIFACT_NAME).exists())

    def test_optional_compute_and_serialization_errors_preserve_legacy_and_do_not_publish_stale_file(self):
        # FS03-REVIEW-01's RuntimeError counterexample, now through the actual
        # writer with a real synthetic legacy fixture and downstream sentinel.
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            baseline = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
            legacy_bytes = (directory / ARTIFACT_NAME).read_bytes()
            previous_candidate = (directory / CROSSING_ARTIFACT_NAME).read_bytes()
            for failure in ("compute", "serialization"):
                with self.subTest(failure=failure):
                    kwargs = ({"side_effect": RuntimeError("private diagnostic must not leak " + "x" * 5000)}
                              if failure == "compute" else {"return_value": {"nonfinite": float("nan")}})
                    downstream = []
                    with patch("rallymate_scoring.source_assessment.compute_foot_crossing_candidates", **kwargs):
                        result = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
                        downstream.append("continued")
                    self.assertEqual(downstream, ["continued"])
                    self.assert_legacy_unchanged(directory, result, baseline, legacy_bytes)
                    self.assertEqual((directory / CROSSING_ARTIFACT_NAME).read_bytes(), previous_candidate)
                    self.assertNotIn("private diagnostic", json.dumps(result["foot_crossing_candidates"]))
                    self.assertEqual(list(directory.glob(CROSSING_ARTIFACT_NAME + ".*.tmp")), [])

    def test_optional_partial_write_and_replace_errors_clean_only_this_attempts_temporary(self):
        sequence, events, timeline = fixture()
        for failure in ("write", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                seed_inputs(directory, timeline, events)
                baseline = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
                legacy_bytes = (directory / ARTIFACT_NAME).read_bytes()
                previous_candidate = (directory / CROSSING_ARTIFACT_NAME).read_bytes()
                unrelated = directory / (CROSSING_ARTIFACT_NAME + ".another-attempt.tmp")
                unrelated.write_bytes(b"do not delete another attempt")
                original_write, original_replace = Path.write_bytes, Path.replace

                def write(path, data):
                    if path.name.startswith(CROSSING_ARTIFACT_NAME + ".") and path.suffix == ".tmp":
                        original_write(path, data[:16])
                        raise OSError("simulated partial candidate write")
                    return original_write(path, data)

                def replace(path, target):
                    if Path(target).name == CROSSING_ARTIFACT_NAME:
                        raise PermissionError("simulated candidate atomic replacement error")
                    return original_replace(path, target)

                with patch.object(Path, "write_bytes", write if failure == "write" else original_write), \
                     patch.object(Path, "replace", replace if failure == "replace" else original_replace):
                    result = write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
                    downstream = "continued"
                self.assertEqual(downstream, "continued")
                self.assert_legacy_unchanged(directory, result, baseline, legacy_bytes)
                self.assertEqual((directory / CROSSING_ARTIFACT_NAME).read_bytes(), previous_candidate)
                self.assertEqual(list(directory.glob(CROSSING_ARTIFACT_NAME + ".*.tmp")), [unrelated])
                self.assertEqual(unrelated.read_bytes(), b"do not delete another attempt")

    def test_real_worker_and_standard_cancellation_propagate_instead_of_becoming_unavailable(self):
        # Use the actual worker control types, not a lookalike RuntimeError.
        from rallymate_service.worker import JobCancelled, JobLeaseLost
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            for error in (JobCancelled("stop"), JobLeaseLost("lost"), FutureCancelledError(),
                          asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(), MemoryError()):
                with self.subTest(error=type(error).__name__), \
                     patch("rallymate_scoring.source_assessment.compute_foot_crossing_candidates", side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
                    self.assertIs(caught.exception, error)
            original_replace = Path.replace
            cancellation = JobCancelled("cancel during replacement")

            def replace(path, target):
                if Path(target).name == CROSSING_ARTIFACT_NAME:
                    raise cancellation
                return original_replace(path, target)

            with patch.object(Path, "replace", replace), self.assertRaises(JobCancelled) as caught:
                write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
            self.assertIs(caught.exception, cancellation)
            self.assertEqual(list(directory.glob(CROSSING_ARTIFACT_NAME + ".*.tmp")), [])

    def test_legacy_measurement_errors_are_not_swallowed_by_optional_boundary(self):
        sequence, events, timeline = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, timeline, events)
            with patch("rallymate_scoring.source_assessment.compute_source_aligned_features", side_effect=RuntimeError("legacy failed")), \
                 patch("rallymate_scoring.source_assessment.compute_foot_crossing_candidates") as candidate:
                with self.assertRaisesRegex(RuntimeError, "legacy failed"):
                    write_source_aligned_assessment(directory, sequence, events, video_id="video-a", primary_timeline=timeline)
                candidate.assert_not_called()
            self.assertFalse((directory / ARTIFACT_NAME).exists())


if __name__ == "__main__":
    unittest.main()
