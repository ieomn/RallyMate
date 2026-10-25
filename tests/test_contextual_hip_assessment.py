"""Bounded contextual hip integration; public data is synthetic only.

The saved native 41-frame acceptance is exercised by a private runner, never by
reading the original full video, or embedding private observations in this file.
"""
from __future__ import annotations

import copy
from concurrent.futures import CancelledError as FutureCancelledError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import model_geometry_evidence
from rallymate_scoring.source_assessment import (
    ARTIFACT_NAME,
    CROSSING_ARTIFACT_NAME,
    compute_contextual_hip_measurement,
    write_source_aligned_assessment,
)
from rallymate_service.source_assessment import load_source_aligned_assessment
from tests.test_sam_footwork import sam_motion_fixture
from tests.test_source_aligned_features import fixture as legacy_fixture


VIDEO_ID = "synthetic-context-video"
SOURCE_ID = "synthetic-context-source"
VIDEO_SHA = "a" * 64
INPUT_SHA = {"frames": "b" * 64, "primary_timeline": "c" * 64, "events": "d" * 64}
NUMBERS = ("hip_path_length_px", "hip_net_displacement_px", "hip_path_over_net_ratio")


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def contextual_fixture(centers=((300., 400.), (330., 400.), (330., 440.))):
    records, timeline = sam_motion_fixture()
    records, timeline = records[:3], timeline[:3]
    for record, row, (x, y) in zip(records, timeline, centers):
        record["job_id"] = SOURCE_ID
        record["poses"][0]["source_binding"].update(
            relative_timestamp_ms=record["frame"]["timestamp_ms"], timestamp_source="decoder_pts")
        row.update(schema_version="1.0.0", selection_algorithm_version="primary-player-v0.3.0",
                   camera_reference_epoch=0)
        for point in record["poses"][0]["keypoints"]:
            if point["name"] in {"left_hip", "right_hip"}:
                px = x + (-10. if point["name"] == "left_hip" else 10.)
                point.update(raw_x_px=px, x_px=px, raw_y_px=y, y_px=y, in_frame=True)
    proof_sequence = pose_sequence_from_records(records, timeline)
    event = {"event_id": "synthetic-existing-fs02", "event_code": "FS02", "video_id": VIDEO_ID,
             "person_track_id": 1, "start_ms": 0, "end_ms": 80, "confidence": .6,
             "key_phases_ms": {}, "quality_flags": [],
             "provenance": {"source_id": SOURCE_ID, "primary_player_id": 1},
             **model_geometry_evidence(proof_sequence, range(3))}
    for row in timeline:
        row.pop("selection_epoch")
        row.pop("camera_reference_epoch")
    return records, timeline, event


def seed_inputs(directory, records, timeline, events):
    for name, rows in (("frames.jsonl", records), ("primary-player.jsonl", timeline), ("events.jsonl", events)):
        (directory / name).write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                                              for row in rows), encoding="utf-8")


def without_context(artifact):
    result = copy.deepcopy(artifact)
    result.pop("source_id", None)
    for row in result["records"]:
        row.pop("contextual_measurements", None)
    return result


class ContextualHipAssessmentTests(unittest.TestCase):
    def compute(self, records, timeline, event, **overrides):
        kwargs = dict(video_id=VIDEO_ID, source_id=SOURCE_ID, video_sha256=VIDEO_SHA,
                      input_sha256=INPUT_SHA)
        kwargs.update(overrides)
        return compute_contextual_hip_measurement(records, timeline, event, **kwargs)

    def assert_neutral(self, value):
        self.assertIsNone(value["score"])
        self.assertIsNone(value["grade"])
        self.assertIs(value["formal_event_verified"], False)
        self.assertIs(value["scoring_allowed"], False)
        self.assertEqual(value["assessment_status"], "measurement_only")
        self.assertEqual(value["coordinate_system"], "stable_camera_reference_image_pixels")
        self.assertEqual(value["sampling_protocol"], "original_source_frames")
        json.dumps(value, allow_nan=False)

    def assert_unavailable(self, value):
        self.assertEqual(value["status"], "unavailable")
        self.assertIsInstance(value["reason"], str)
        self.assertTrue(value["reason"])
        for key in NUMBERS:
            self.assertIsNone(value[key], key)
        self.assert_neutral(value)

    def test_bent_native_geometry_is_additive_and_source_bound(self):
        records, timeline, event = contextual_fixture()
        before = copy.deepcopy((records, timeline, event))
        result = self.compute(records, timeline, event)
        self.assertEqual(result["version"], "hip-contextual-measurement-v1.0.0")
        self.assertEqual(result["type"], "hip_path_ratio_2d")
        self.assertEqual(result["helper_version"], "bounded-hip-path-ratio-v1.0.0")
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["reason"], "bounded_image_plane_geometry_only")
        for key, expected in zip(NUMBERS, (70., 50., 1.4)):
            self.assertAlmostEqual(result[key], expected)
        for key in ("event_id", "event_code", "video_id", "person_track_id"):
            self.assertEqual(result[key], event[key])
        self.assertEqual(result["source_id"], SOURCE_ID)
        self.assertEqual(result["video_sha256"], VIDEO_SHA)
        self.assertEqual(result["parent_window_ms"], {"start_ms": 0, "end_ms": 80})
        self.assertEqual(result["measurement_window_video_ms"], result["parent_window_ms"])
        self.assertEqual(result["source_frame_bounds"], {"start_source_frame": 0, "end_source_frame": 2})
        self.assertEqual([row["pts"] for row in result["source_bindings"]], [0, 40, 80])
        self.assertEqual(result["input_sha256"], INPUT_SHA)
        self.assertEqual(result["window_input_sha256"]["records"], canonical_sha(records))
        self.assertEqual(result["window_input_sha256"]["original_primary"], canonical_sha(timeline))
        self.assertEqual(result["window_input_sha256"]["event"], canonical_sha(event))
        self.assertEqual(result["adapter"]["version"], "native-sam-primary-epochs-v1.0.0")
        self.assertIs(result["adapter"]["source_files_changed"], False)
        self.assertEqual((records, timeline, event), before)
        self.assert_neutral(result)

    def test_zero_net_preserves_finite_lengths_without_ratio_or_grade(self):
        records, timeline, event = contextual_fixture(((300., 400.), (330., 400.), (300., 400.)))
        result = self.compute(records, timeline, event)
        self.assertEqual((result["status"], result["reason"]), ("unavailable", "zero_net_displacement"))
        self.assertAlmostEqual(result["hip_path_length_px"], 60.)
        self.assertEqual(result["hip_net_displacement_px"], 0.)
        self.assertIsNone(result["hip_path_over_net_ratio"])
        self.assert_neutral(result)

    def test_missing_native_evidence_does_not_fill_or_crop_a_window(self):
        for defect in ("request", "pose", "request_epoch", "binding_epoch"):
            with self.subTest(defect=defect):
                records, timeline, event = contextual_fixture()
                if defect == "request": records[1]["sam3d_requests"] = []
                elif defect == "pose": records[1]["poses"] = []
                elif defect == "request_epoch": records[1]["sam3d_requests"][0].pop("selection_epoch")
                else: records[1]["poses"][0]["source_binding"].pop("selection_epoch")
                before = copy.deepcopy((records, timeline, event))
                self.assert_unavailable(self.compute(records, timeline, event))
                self.assertEqual((records, timeline, event), before)

    def test_null_boolean_or_conflicting_primary_epochs_are_unavailable(self):
        for field, value in (("selection_epoch", None), ("selection_epoch", True), ("selection_epoch", 99),
                             ("camera_reference_epoch", None), ("camera_reference_epoch", False),
                             ("camera_reference_epoch", 1)):
            with self.subTest(field=field, value=value):
                records, timeline, event = contextual_fixture()
                timeline[1][field] = value
                self.assert_unavailable(self.compute(records, timeline, event))

    def test_full_window_missing_duplicate_reversed_or_extra_interval_not_repaired(self):
        for defect in ("raw_missing", "primary_missing", "raw_duplicate", "raw_reverse", "primary_reverse"):
            with self.subTest(defect=defect):
                records, timeline, event = contextual_fixture()
                if defect == "raw_missing": records.pop(1)
                elif defect == "primary_missing": timeline.pop(1)
                elif defect == "raw_duplicate": records[1] = copy.deepcopy(records[0])
                elif defect == "raw_reverse": records.reverse()
                else: timeline.reverse()
                self.assert_unavailable(self.compute(records, timeline, event))

    def test_wrong_event_video_source_and_raw_job_binding_are_unavailable(self):
        for defect in ("event_id", "video", "source", "raw_job", "video_sha", "event_window"):
            with self.subTest(defect=defect):
                records, timeline, event = contextual_fixture()
                if defect == "event_id": event["event_id"] = ""
                elif defect == "video": event["video_id"] = "different-video"
                elif defect == "source": event["provenance"]["source_id"] = "different-source"
                elif defect == "raw_job": records[1]["job_id"] = "different-source"
                elif defect == "video_sha": event["source_window"]["video_sha256"] = "f" * 64
                else: event["end_ms"] = 40
                self.assert_unavailable(self.compute(records, timeline, event))

    def test_decoder_or_source_binding_conflicts_do_not_become_measurements(self):
        for field, value in (("pts", 41), ("time_base_den", 600), ("frame_index", 2),
                             ("person_track_id", 9), ("camera_reference_epoch", 2)):
            with self.subTest(field=field):
                records, timeline, event = contextual_fixture()
                records[1]["poses"][0]["source_binding"][field] = value
                self.assert_unavailable(self.compute(records, timeline, event))

    def test_existing_event_proof_cannot_claim_partial_or_different_source_frames(self):
        for defect in ("missing", "partial", "pts", "epoch"):
            with self.subTest(defect=defect):
                records, timeline, event = contextual_fixture()
                if defect == "missing": event.pop("source_window")
                elif defect == "partial": event["source_window"]["source_frames"].pop(1)
                elif defect == "pts": event["source_window"]["source_bindings"][1]["pts"] += 1
                else: event["source_window"]["selection_epoch"] += 1
                self.assert_unavailable(self.compute(records, timeline, event))

    def test_writer_and_public_projection_preserve_legacy_evidence_and_inputs(self):
        records, timeline, event = contextual_fixture()
        original = copy.deepcopy((records, timeline, event))
        sequence = pose_sequence_from_records(records, timeline)
        sequence_before = copy.deepcopy(sequence)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, records, timeline, [event])
            input_bytes = {p.name: p.read_bytes() for p in directory.iterdir()}
            first = write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                       video_sha256=VIDEO_SHA, primary_timeline=timeline)
            baseline = json.loads((directory / ARTIFACT_NAME).read_bytes())
            crossing_before = (directory / CROSSING_ARTIFACT_NAME).read_bytes()
            first_view = load_source_aligned_assessment(directory, video_id=VIDEO_ID, duration_ms=1000,
                                                       video_sha256=VIDEO_SHA)
            second = write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                       video_sha256=VIDEO_SHA, primary_timeline=timeline, raw_records=records, source_id=SOURCE_ID)
            current = json.loads((directory / ARTIFACT_NAME).read_bytes())
            view = load_source_aligned_assessment(directory, video_id=VIDEO_ID, duration_ms=1000,
                                                 video_sha256=VIDEO_SHA)
            self.assertEqual(without_context(current), without_context(baseline))
            for key in ("event_count", "indicator_window_count", "foot_crossing_candidates"):
                self.assertEqual(second[key], first[key], key)
            self.assertEqual((directory / CROSSING_ARTIFACT_NAME).read_bytes(), crossing_before)
            self.assertEqual(view["source_id"], SOURCE_ID)
            self.assertIsNone(first_view["source_id"])
            self.assertEqual({k: v for k, v in view.items() if k not in {"contextual_measurements", "source_id"}},
                             {k: v for k, v in first_view.items() if k not in {"contextual_measurements", "source_id"}})
            contextual = current["records"][0]["contextual_measurements"]
            self.assertEqual(len(contextual), 1)
            self.assertEqual(contextual[0]["status"], "measured")
            self.assertEqual(contextual[0]["person_track_id"], 1)
            self.assertEqual(contextual[0]["source_reference"]["primary_player_id"], 1)
            self.assertEqual(contextual[0]["source_reference"]["person_track_id"], 7)
            self.assertEqual(len(view["contextual_measurements"]), 1)
            self.assertEqual(view["contextual_measurements"][0]["person_track_id"], 1)
            self.assertEqual(view["contextual_measurements"][0]["source_reference"]["person_track_id"], 7)
            for key in NUMBERS:
                self.assertEqual(view["contextual_measurements"][0][key], contextual[0][key])
            for name, raw in input_bytes.items():
                self.assertEqual((directory / name).read_bytes(), raw)
        self.assertEqual((records, timeline, event), original)
        self.assertEqual(sequence.source_time_bindings, sequence_before.source_time_bindings)
        for name in sequence.keypoints_xy:
            np.testing.assert_array_equal(sequence.keypoints_xy[name], sequence_before.keypoints_xy[name])
        np.testing.assert_array_equal(sequence.timestamp_ms, sequence_before.timestamp_ms)

    def test_missing_context_inputs_leave_all_legacy_five_event_results_unchanged(self):
        sequence, events, timeline = legacy_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, [], timeline, events)
            first = write_source_aligned_assessment(directory, sequence, events, video_id="video-a",
                                                    primary_timeline=timeline)
            baseline = json.loads((directory / ARTIFACT_NAME).read_bytes())
            second = write_source_aligned_assessment(directory, sequence, events, video_id="video-a",
                        primary_timeline=timeline, raw_records=[], source_id=SOURCE_ID)
            current = json.loads((directory / ARTIFACT_NAME).read_bytes())
            self.assertEqual(first["event_count"], 5)
            self.assertEqual(without_context(current), without_context(baseline))
            for key in ("event_count", "indicator_window_count", "foot_crossing_candidates"):
                self.assertEqual(second[key], first[key])
            self.assertIsNone(current["technical_grade"])
            self.assertIsNone(current["technical_score_0_to_100"])

    def test_optional_context_failure_preserves_legacy_and_emits_neutral_unavailable(self):
        records, timeline, event = contextual_fixture()
        original = copy.deepcopy((records, timeline, event))
        sequence = pose_sequence_from_records(records, timeline)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            seed_inputs(directory, records, timeline, [event])
            input_bytes = {p.name: p.read_bytes() for p in directory.iterdir()}
            first = write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                        video_sha256=VIDEO_SHA, primary_timeline=timeline)
            baseline = json.loads((directory / ARTIFACT_NAME).read_bytes())
            crossing_before = (directory / CROSSING_ARTIFACT_NAME).read_bytes()
            with patch("rallymate_scoring.source_assessment.compute_contextual_hip_measurement",
                       side_effect=RuntimeError("synthetic-private-error-must-not-leak")):
                second = write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                           video_sha256=VIDEO_SHA, primary_timeline=timeline,
                           raw_records=records, source_id=SOURCE_ID)
            artifact_bytes = (directory / ARTIFACT_NAME).read_bytes()
            current = json.loads(artifact_bytes)
            self.assertEqual(without_context(current), without_context(baseline))
            self.assertNotIn(b"synthetic-private-error-must-not-leak", artifact_bytes)
            self.assertEqual((directory / CROSSING_ARTIFACT_NAME).read_bytes(), crossing_before)
            for key in ("event_count", "indicator_window_count", "foot_crossing_candidates"):
                self.assertEqual(second[key], first[key], key)
            value = current["records"][0]["contextual_measurements"][0]
            self.assert_unavailable(value)
            self.assertEqual(value["reason"], "contextual_measurement_failed")
            for key in ("event_id", "event_code", "video_id", "person_track_id"):
                self.assertEqual(value[key], event[key])
            self.assertEqual(value["source_id"], SOURCE_ID)
            self.assertEqual(value["video_sha256"], VIDEO_SHA)
            for key, name in (("frames", "frames.jsonl"), ("primary_timeline", "primary-player.jsonl"),
                              ("events", "events.jsonl")):
                self.assertEqual(value["input_sha256"][key], hashlib.sha256(input_bytes[name]).hexdigest())
            for key in ("records", "original_primary", "adapted_primary"):
                self.assertIsNone(value["window_input_sha256"][key])
            self.assertEqual(value["window_input_sha256"]["event"], canonical_sha(event))
            for name, raw in input_bytes.items():
                self.assertEqual((directory / name).read_bytes(), raw)
        self.assertEqual((records, timeline, event), original)

    def test_context_cancellation_and_resource_failure_propagate(self):
        for error_type in (FutureCancelledError, MemoryError):
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as temporary:
                records, timeline, event = contextual_fixture()
                directory = Path(temporary)
                seed_inputs(directory, records, timeline, [event])
                input_bytes = {p.name: p.read_bytes() for p in directory.iterdir()}
                sequence = pose_sequence_from_records(records, timeline)
                with patch("rallymate_scoring.source_assessment.compute_contextual_hip_measurement",
                           side_effect=error_type("control-flow")):
                    with self.assertRaises(error_type):
                        write_source_aligned_assessment(directory, sequence, [event], video_id=VIDEO_ID,
                            video_sha256=VIDEO_SHA, primary_timeline=timeline,
                            raw_records=records, source_id=SOURCE_ID)
                for name, raw in input_bytes.items():
                    self.assertEqual((directory / name).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
