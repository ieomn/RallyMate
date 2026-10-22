"""Source contract tests; synthetic geometry is not model accuracy evidence."""
from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from jsonschema import Draft202012Validator

from rallymate_events.rules import detect_pose_events
from rallymate_features.coordinates import pose_sequence_from_records, point_series, body_scale
from rallymate_features.event_features import compute_event_features
from rallymate_features.schemas import EventInterval, model_geometry_evidence
from rallymate_features.source_alignment import prepare_source_aligned_context
from rallymate_scoring.model_measurement_contract import validated_model_window, validated_geometry_mask
from rallymate_scoring.scoring import score_indicator
from rallymate_vision.pose.metadata import keypoint_schema
from tests.test_events import _motion_sequence


def sam_motion_fixture(*, source_offset_ms=0):
    sequence = _motion_sequence()
    records, timeline = [], []
    for i, timestamp in enumerate(sequence.timestamp_ms):
        timestamp = int(timestamp)
        binding = dict(video_sha256="a"*64, frame_index=i, processed_index=i, pts=timestamp+source_offset_ms,
                       time_base_num=1, time_base_den=1000, timestamp_ms=timestamp+source_offset_ms,
                       person_track_id=7, selection_epoch=3, camera_reference_epoch=0)
        points = []
        for definition in keypoint_schema("mhr70")["keypoints"]:
            name = definition["name"]
            xy = sequence.keypoints_xy.get(name)
            x, y = xy[i]*1000 if xy is not None else (-100., -100.)
            points.append({**definition, "raw_x_px": float(x), "raw_y_px": float(y),
                "x_px": max(0., float(x)), "y_px": max(0., float(y)), "confidence": None,
                "confidence_semantics": "not_provided_by_sam3d", "source": "sam3d",
                "observation_semantics": "model_estimate", "observed": None, "visibility": None,
                "in_frame": xy is not None})
        frame = {"index": i, "processed_index": i, "timestamp_ms": timestamp, "source_timestamp_ms": binding["timestamp_ms"],
                 "timestamp_source": "decoder_pts", "width": 1000, "height": 1000,
                 **{key: binding[key] for key in ("video_sha256", "pts", "time_base_num", "time_base_den")}}
        records.append({"frame": frame, "poses": [{"person_track_id": 7, "source": "sam3d", "keypoint_format": "mhr70",
             "confidence": None, "confidence_semantics": "not_provided_by_sam3d", "status": "ok",
             "observation_semantics": "monocular_model_estimate", "coordinate_space": "original_frame",
             "source_binding": binding, "keypoints": points}],
             "detections": [{"class_name": "player", "track_id": 7}],
             "sam3d_requests": [{"person_track_id": 7, "selection_epoch": 3, "status": "ok"}],
             "camera_motion": {"status": "stationary", "reference_epoch": 0, "compensation_valid": True,
                               "matrix_to_reference": [[1., 0., 0.], [0., 1., 0.]]}})
        timeline.append({"processed_index": i, "source_frame_index": i, "timestamp_ms": timestamp,
                         "primary_player_id": 1, "source_track_id": 7, "selection_epoch": 3,
                         "selection_status": "selected", "identity_ambiguous": False})
    return records, timeline


def model_report_fixture():
    records, timeline = sam_motion_fixture()
    sequence = pose_sequence_from_records(records, timeline)
    events, indicators, scores, definitions = [], [], [], []
    for code, begin, end, phases in (("FS01", 0, 10, {"initiation_ms": 400}),
            ("FS02", 10, 20, {"peak_speed_ms": 640}), ("FS09", 15, 25, {"peak_speed_ms": 640})):
        proof = model_geometry_evidence(sequence, range(begin, end+1))
        event = {"event_id": code, "event_code": code, "video_id": "model-video", "person_track_id": 1,
                 "start_ms": begin*40, "end_ms": end*40, "confidence": .6, "key_phases_ms": phases,
                 "provenance": {"source_id": "fixture"}, **proof}
        feature = {"feature_name": "body_center_speed_body_s", "feature_version": "1", "value": 0., "unit": "body/s",
                   "valid": True, "confidence": None, "reason": "model_estimate", "source_frames": [begin,end], **proof}
        key = code+"-M01"
        events.append(event)
        definitions.append({"indicator_id": key, "required_features": [feature["feature_name"]], "required_events": [code+".start"], "feasibility_level": "F2"})
        indicators.append({"event_id": code, "event_code": code, "video_id": "model-video", "person_track_id": 1,
                           "indicator_id": key, "feature_status": "measured", "features": [feature],
                           "scoring_status": "unavailable", "reason_codes": ["model_estimate_measurement_only"],
                           "quality_gate": {"status": "pass"}, **proof})
        scores.append({"event_id": code, "indicator_id": key, "status": "unavailable", "grade": None,
                       "threshold_version": None, "model_versions": {}, "evidence": []})
    return {"registry": {"registry_version": "fixture", "indicators": definitions}, "events": events,
            "indicator_records": indicators, "scores": scores, "video_id": "model-video",
            "sources": {key:{"path": key, "sha256": "A"*64} for key in ("feasibility_registry", "events", "indicator_features", "scores")}}


class SamFootworkTests(unittest.TestCase):
    def test_raw_source_bound_geometry_and_unknown_confidence(self):
        records, timeline = sam_motion_fixture(source_offset_ms=5000)
        sequence = pose_sequence_from_records(records, timeline)
        self.assertTrue(point_series(sequence, "left_hip")[1].all())
        self.assertTrue(np.isnan(sequence.confidence["left_hip"]).all())
        self.assertTrue(body_scale(sequence)[1].all())
        self.assertFalse(point_series(sequence, "left_big_toe")[1].any())
        proof = model_geometry_evidence(sequence, range(4))
        self.assertEqual(proof["source_window"]["source_timestamps_ms"], [5000., 5040., 5080., 5120.])
        self.assertIsNotNone(validated_model_window(proof, person_track_id=7))
        self.assertIsNotNone(validated_geometry_mask(proof["geometry_mask"], proof["source_window"]))

    def test_independent_decoder_selection_and_request_mutations_reject(self):
        def change_video(r, t): r["frame"]["video_sha256"] = "b"*64
        def change_pts(r, t): r["frame"]["pts"] += 1
        def change_request(r, t): r["sam3d_requests"][0]["selection_epoch"] += 1
        def change_ambiguous(r, t): t["identity_ambiguous"] = True
        def change_time(r, t): t["timestamp_ms"] += 1
        def change_epoch(r, t): t["selection_epoch"] += 1
        def duplicate_pose(r, t): r["poses"].append(copy.deepcopy(r["poses"][0]))
        def missing_camera(r, t): del r["camera_motion"]["compensation_valid"]
        def wrong_primary(r, t): t["primary_player_id"] = 2
        def huge_pts(r, t): r["poses"][0]["source_binding"]["pts"] = 10**1000
        for mutate in (change_video, change_pts, change_request, change_ambiguous, change_time, change_epoch, duplicate_pose, missing_camera, wrong_primary, huge_pts):
            with self.subTest(mutation=mutate.__name__):
                records, timeline = sam_motion_fixture()
                mutate(records[12], timeline[12])
                sequence = pose_sequence_from_records(records, timeline)
                self.assertIsNone(sequence.model_estimate_bindings[12])
                self.assertFalse(point_series(sequence, "left_hip")[1][12])

    def test_out_of_frame_missing_raw_and_wrong_mapping_are_not_rescued(self):
        for defect in ("out_of_frame", "missing_raw", "nonfinite", "edge", "conf", "mapping"):
            records, timeline = sam_motion_fixture()
            pose = records[12]["poses"][0]
            point = next(p for p in pose["keypoints"] if p["name"] == "left_hip")
            if defect == "out_of_frame": point["in_frame"] = False
            if defect == "missing_raw": del point["raw_x_px"]
            if defect == "nonfinite": point["raw_x_px"] = float("nan")
            if defect == "edge": point["raw_x_px"] = 1000
            if defect == "conf": point["confidence"] = 1
            if defect == "mapping": point["downstream_joint_id"] = "wrong"
            sequence = pose_sequence_from_records(records, timeline)
            self.assertFalse(point_series(sequence, "left_hip")[1][12], defect)

    def test_selection_epoch_splits_and_preserves_source_arrays(self):
        records, timeline = sam_motion_fixture()
        for row, selection in zip(records[25:], timeline[25:]):
            row["poses"][0]["source_binding"]["selection_epoch"] = 4
            row["sam3d_requests"][0]["selection_epoch"] = 4
            selection["selection_epoch"] = 4
        sequence = pose_sequence_from_records(records, timeline)
        self.assertTrue(point_series(sequence, "left_hip")[1].all())
        for event in detect_pose_events(sequence, source_id="epoch"):
            self.assertFalse(event["start_ms"] < 1000 <= event["end_ms"])
            self.assertIsNotNone(validated_model_window(event))
        part = sequence.slice(slice(25, 30))
        self.assertEqual(len(part.model_estimate_bindings), 5)
        self.assertEqual(part.model_estimate_masks["left_hip"].shape, (5,))

    def test_existing_seventy_percent_geometry_gate_is_not_relaxed(self):
        records, timeline = sam_motion_fixture()
        for row in records[:4]:
            next(p for p in row["poses"][0]["keypoints"] if p["name"] == "left_hip")["in_frame"] = False
        sequence = pose_sequence_from_records(records, timeline)
        context = prepare_source_aligned_context(sequence, [])
        coverage = context.coverage(np.arange(10), (("left_hip", "right_hip"),))
        self.assertEqual(coverage["fraction"], .6)
        self.assertFalse(coverage["passes"])

    def test_event_feature_proof_null_confidence_and_no_grade(self):
        records, timeline = sam_motion_fixture()
        sequence = pose_sequence_from_records(records, timeline)
        events = detect_pose_events(sequence, source_id="sam-fixture", video_id="sam-video")
        self.assertTrue(events)
        for event in events:
            window = validated_model_window(event, person_track_id=7)
            self.assertIsNotNone(window)
            interval = EventInterval(event["event_id"], event["event_code"], event["start_ms"], event["end_ms"], key_phases=event["key_phases_ms"])
            feature = compute_event_features(sequence, interval, ["body_center_speed_body_s"])[0].to_dict()
            self.assertIsNone(feature["confidence"])
            self.assertIsNotNone(validated_model_window(feature, parent_window=window))
            score = score_indicator(indicator_id="FS02-M02", features=[feature], calibration=None,
                                    model_versions={}, evidence=[])
            self.assertIsNone(score["grade"])
            self.assertIsNone(score["confidence"])
            self.assertTrue(score["quality_gate"]["scoring_allowed"])
            # This integration contract is independent of the legacy/new grade
            # policy wording: unknown confidence must preserve an explicit reason.
            self.assertTrue(score["reason_codes"])

    def test_null_confidence_feature_schema_requires_model_provenance(self):
        records, timeline = sam_motion_fixture()
        sequence = pose_sequence_from_records(records, timeline)
        feature = compute_event_features(sequence, EventInterval("model", "FS02", 0, 120),
            ["body_center_speed_body_s"])[0].to_dict()
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/feature-result.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        validator.validate(feature)
        self.assertIsNone(feature["confidence"])
        del feature["measurement_provenance"]
        self.assertTrue(list(validator.iter_errors(feature)))

    def test_missing_source_frame_or_epoch_never_bridged_before_smoothing(self):
        records, timeline = sam_motion_fixture()
        records[25]["sam3d_requests"][0]["status"] = "failed"
        sequence = pose_sequence_from_records(records, timeline)
        for event in detect_pose_events(sequence, source_id="gap"):
            self.assertFalse(event["start_ms"] < 1000 < event["end_ms"])
        interval = EventInterval("crosses", "FS02", 600, 1400)
        result = compute_event_features(sequence, interval, ["body_center_speed_body_s"])[0]
        self.assertFalse(result.valid)
        self.assertIsNone(result.confidence)

    def test_source_alignment_uses_geometry_without_claiming_word_visibility(self):
        records, timeline = sam_motion_fixture()
        sequence = pose_sequence_from_records(records, timeline)
        context = prepare_source_aligned_context(sequence, [], primary_timeline=timeline)
        coverage = context.coverage(np.arange(10), (("left_hip", "right_hip"),))
        self.assertTrue(coverage["passes"])
        self.assertFalse(coverage["visibility_verified"])
        self.assertIsNone(coverage["source_visibility_70_percent_verified"])
        self.assertIsNone(coverage["point_validity"]["minimum_confidence"])

    def test_ordinary_unknown_confidence_is_not_a_geometry_authorization(self):
        source = _motion_sequence()
        sequence = replace(source, confidence={name: np.full(len(source.timestamp_ms), np.nan) for name in source.confidence})
        self.assertFalse(point_series(sequence, "left_hip")[1].any())
        self.assertEqual(detect_pose_events(sequence, source_id="unknown"), [])

    def test_external_target_reference_cannot_convert_unknown_pose_confidence(self):
        from rallymate_scoring.scoring_context import resolve_target_direction_context, compact_target_direction_alignment_feature
        records, timeline = sam_motion_fixture()
        sequence = pose_sequence_from_records(records, timeline)
        event = {"event_id": "launch", "event_code": "FS02", "start_ms": 0, "end_ms": 120}
        feature = {"value": 20., "valid": True, "confidence": None, "source_frames": [0, 3],
                   **model_geometry_evidence(sequence, range(4))}
        context = resolve_target_direction_context(event=event, launch_direction_feature=feature,
            observation={**event, "status": "accepted", "observation_id": "ref", "target_direction_deg": 20., "confidence": .9}, source_sha256="b"*64)
        self.assertEqual(context["status"], "available")
        compact = compact_target_direction_alignment_feature(context, launch_direction_feature=feature)
        self.assertIsNone(compact["confidence"])
        self.assertTrue(compact["valid"])
        self.assertEqual(compact["value"], 0.)
        self.assertEqual(compact["measurement_provenance"], feature["measurement_provenance"])

    def test_zero_stability_binds_evaluated_model_window_without_claiming_stable_interval(self):
        records, timeline = sam_motion_fixture()
        sequence = pose_sequence_from_records(records, timeline).slice(slice(0, 5))
        interval = EventInterval('no-stable-run', 'FS09', 0, 160)

        def series(_sequence, name):
            values = np.arange(5, dtype=float)
            if name == 'shoulder_hip_angular_velocity':
                values = values[::-1]
            return values, values

        with patch('rallymate_features.event_features._cached_series', side_effect=series):
            result = compute_event_features(sequence, interval, ['stability_duration_ms'])[0]
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, [0, 1, 2, 3, 4])
        self.assertEqual(result.source_frames, result.provenance['source_window']['source_frames'])
        self.assertEqual(result.provenance['source_frame_semantics'], 'evaluated_window_no_stable_run')
        self.assertIsNone(result.provenance['envelope_diagnostics']['longest_run_local_indexes'])
        self.assertFalse(any(result.smoothed_value['stable_mask']))
        self.assertFalse(result.provenance['stable_interval_observed'])
        self.assertIsNone(result.confidence)

        with patch('rallymate_features.event_features._cached_series', return_value=(np.full(5, np.nan), np.full(5, np.nan))):
            missing = compute_event_features(sequence, interval, ['stability_duration_ms'])[0]
        self.assertFalse(missing.valid)
        self.assertIsNone(missing.value)
        self.assertEqual(missing.source_frames, [])
        self.assertNotIn('source_frame_semantics', missing.provenance)

        ordinary = _motion_sequence().slice(slice(0, 5))
        with patch('rallymate_features.event_features._cached_series', side_effect=series):
            unchanged = compute_event_features(ordinary, interval, ['stability_duration_ms'])[0]
        self.assertTrue(unchanged.valid)
        self.assertEqual(unchanged.value, 0)
        self.assertEqual(unchanged.source_frames, [])
        self.assertNotIn('source_frame_semantics', unchanged.provenance)
