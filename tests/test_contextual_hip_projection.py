"""Independent synthetic wire fixtures; no producer/helper execution or raw video."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from fractions import Fraction

from rallymate_scoring.source_assessment import ARTIFACT_VERSION, source_binding
from rallymate_service.source_assessment import SEMANTICS, project_source_assessment


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fixture():
    frames, pts = [100, 101, 102], [2000, 2020, 2040]
    times = [round(Fraction(p * 1000, 600)) for p in pts]
    source_times = [float(Fraction(p * 1000, 600)) for p in pts]
    bindings = [dict(video_sha256="A" * 64, frame_index=f, processed_index=f,
        pts=p, time_base_num=1, time_base_den=600, timestamp_ms=t,
        person_track_id=8, selection_epoch=2, camera_reference_epoch=3)
        for f, p, t in zip(frames, pts, times)]
    window = dict(start_ms=times[0], end_ms=times[-1], source_frames=frames,
        video_timestamps_ms=times, source_timestamps_ms=source_times, source_bindings=bindings,
        video_sha256="A" * 64, person_track_id=8, selection_epoch=2, camera_reference_epoch=3)
    event = dict(event_id="event-1", event_code="FS02", video_id="display-job", person_track_id=1,
        start_ms=times[0], end_ms=times[-1], provenance={"source_id": "source-job", "primary_player_id": 1},
        source_window=window, measurement_provenance={"pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
            "assessment_status": "measurement_only", "scoring_allowed": False,
            "confidence_available": False, "visibility_verified": False})
    normalized = copy.deepcopy(window)
    normalized["video_sha256"] = "a" * 64
    for binding in normalized["source_bindings"]:
        binding["video_sha256"] = "a" * 64
    helper_bindings = [{"frame_index": f, "processed_index": f, "video_sha256": "a" * 64,
        "pts": p, "time_base_num": 1, "time_base_den": 600, "source_timestamp_ms": t,
        "video_timestamp_ms": t, "timestamp_source": "decoder_pts", "primary_player_id": 1,
        "person_track_id": 8, "selection_epoch": 2, "camera_reference_epoch": 3}
        for f, p, t in zip(frames, pts, times)]
    item = dict(type="hip_path_ratio_2d", version="hip-contextual-measurement-v1.0.0",
        helper_version="bounded-hip-path-ratio-v1.0.0", event_id="event-1", event_code="FS02",
        video_id="display-job", source_id="source-job", video_sha256="a" * 64, person_track_id=1,
        parent_window_ms={"start_ms": times[0], "end_ms": times[-1]},
        measurement_window_video_ms={"start_ms": times[0], "end_ms": times[-1]},
        source_frame_bounds={"start_source_frame": 100, "end_source_frame": 102}, source_window=normalized,
        source_bindings=helper_bindings, source_reference={"video_sha256": "a" * 64,
            "primary_player_id": 1, "person_track_id": 8, "selection_epoch": 2, "camera_reference_epoch": 3},
        status="measured", reason="bounded_image_plane_geometry_only", hip_path_length_px=70.,
        hip_net_displacement_px=50., hip_path_over_net_ratio=1.4, score=None, grade=None,
        formal_event_verified=False, scoring_allowed=False, assessment_status="measurement_only",
        coordinate_system="stable_camera_reference_image_pixels", sampling_protocol="original_source_frames",
        adapter={"version": "native-sam-primary-epochs-v1.0.0",
            "provenance": "unique_same_frame_selected_track_SAM_request_and_pose_binding_and_camera",
            "source_files_changed": False}, input_sha256={"frames": "1" * 64, "primary_timeline": "2" * 64, "events": "3" * 64},
        window_input_sha256={"records": "4" * 64, "original_primary": "5" * 64,
            "adapted_primary": "6" * 64, "event": digest(event)})
    record = dict(event_id="event-1", event_code="FS02", video_id="display-job", person_track_id=1,
        indicators=[{"indicator_id": "FS02-M01", "source_rule_gate": {"pose_coverage": {}},
            "source_measurements": [], "limitations_zh": []}], contextual_measurements=[item])
    artifact = dict(version=ARTIFACT_VERSION, video_id="display-job", source_id="source-job", video_sha256="a" * 64,
        frames_sha256="1" * 64, primary_timeline_sha256="2" * 64, events_sha256="3" * 64,
        source_binding=source_binding(), score_semantics=SEMANTICS, technical_grade=None,
        technical_score_0_to_100=None, records=[record])
    return artifact, [event], item


def project(artifact, events):
    return project_source_assessment(artifact, events, video_id="display-job", duration_ms=4000, video_sha256="a" * 64)


class ContextualHipProjectionTests(unittest.TestCase):
    def test_complete_measurement_preserves_legacy_result_and_does_not_mutate(self):
        artifact, events, item = fixture()
        original = copy.deepcopy((artifact, events))
        old = copy.deepcopy(artifact)
        del old["records"][0]["contextual_measurements"]
        before, actual = project(old, events), project(artifact, events)
        contextual = actual.pop("contextual_measurements")
        before.pop("contextual_measurements")
        self.assertEqual(actual, before)
        self.assertEqual(len(contextual), 1)
        self.assertEqual(contextual[0]["hip_path_over_net_ratio"], 1.4)
        self.assertEqual(contextual[0]["source_bindings"], item["source_bindings"])
        self.assertEqual(contextual[0]["person_track_id"], 1)
        self.assertEqual(contextual[0]["source_reference"]["person_track_id"], 8)
        self.assertIsNone(contextual[0]["grade"])
        self.assertFalse(contextual[0]["formal_event_verified"])
        self.assertEqual((artifact, events), original)

    def test_whitelist_and_digest_case_do_not_rewrite_original_event(self):
        artifact, events, item = fixture()
        item.update(secret_token="do-not-project", path="private-path", score_explanation="not-trusted")
        item["source_reference"]["video_sha256"] = "A" * 64
        item["source_bindings"][0]["video_sha256"] = "A" * 64
        item["video_sha256"] = "A" * 64
        row = project(artifact, events)["contextual_measurements"][0]
        self.assertFalse(set(row) & {"secret_token", "path", "score_explanation"})
        self.assertEqual(row["video_sha256"], "a" * 64)
        self.assertEqual(row["source_window"]["video_sha256"], "a" * 64)
        self.assertEqual(events[0]["source_window"]["video_sha256"], "A" * 64)

    def test_wrong_identity_versions_input_hashes_and_event_pin_are_rejected(self):
        cases = [(k, "wrong") for k in ("type", "version", "helper_version", "event_id", "event_code", "video_id", "source_id")]
        cases += [("person_track_id", True), ("video_sha256", "b" * 64)]
        for key, value in cases:
            with self.subTest(key=key):
                artifact, events, item = fixture(); item[key] = value
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])
        for key in ("frames", "primary_timeline", "events"):
            with self.subTest(input=key):
                artifact, events, item = fixture(); item["input_sha256"][key] = "f" * 64
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])
        for defect in ("artifact_source", "event_source", "event_digest", "bad_hex", "adapter", "missing_hash"):
            with self.subTest(defect=defect):
                artifact, events, item = fixture()
                if defect == "artifact_source": artifact["source_id"] = "other"
                elif defect == "event_source": events[0]["provenance"]["source_id"] = "other"
                elif defect == "event_digest": item["window_input_sha256"]["event"] = "f" * 64
                elif defect == "bad_hex": item["window_input_sha256"]["records"] = "z" * 64
                elif defect == "adapter": item["adapter"]["source_files_changed"] = True
                else: del artifact["events_sha256"]
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_complete_window_cannot_be_cropped_or_changed(self):
        for defect in ("subset", "frames", "bounds", "parent", "measurement", "epoch", "camera", "video", "optional_video_time"):
            with self.subTest(defect=defect):
                artifact, events, item = fixture(); w = item["source_window"]
                if defect == "subset":
                    for k in ("source_frames", "video_timestamps_ms", "source_timestamps_ms", "source_bindings"): w[k] = w[k][1:]
                    w["start_ms"] = w["video_timestamps_ms"][0]
                elif defect == "frames": w["source_frames"][1] += 2
                elif defect == "bounds": item["source_frame_bounds"]["end_source_frame"] -= 1
                elif defect == "parent": item["parent_window_ms"]["start_ms"] += 1
                elif defect == "measurement": item["measurement_window_video_ms"]["end_ms"] -= 1
                elif defect == "epoch": w["source_bindings"][1]["selection_epoch"] += 1
                elif defect == "camera": w["camera_reference_epoch"] += 1
                elif defect == "video": w["video_sha256"] = "b" * 64
                else: w["source_bindings"][0]["video_timestamp_ms"] = False
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_each_helper_binding_and_reference_match_parent_exactly(self):
        for key in ("frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                    "person_track_id", "selection_epoch", "camera_reference_epoch", "primary_player_id",
                    "source_timestamp_ms", "video_timestamp_ms", "timestamp_source", "video_sha256"):
            with self.subTest(key=key):
                artifact, events, item = fixture(); b = item["source_bindings"][1]
                b[key] = "wrong" if isinstance(b[key], str) else b[key] + 1
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])
        for key in ("primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch", "video_sha256"):
            with self.subTest(reference=key):
                artifact, events, item = fixture(); ref = item["source_reference"]
                ref[key] = "b" * 64 if key == "video_sha256" else ref[key] + 1
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])
        artifact, events, item = fixture(); item["source_bindings"] = item["source_bindings"][:-1]
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_inconsistent_common_video_clock_is_rejected_even_with_matching_copies(self):
        artifact, events, item = fixture()
        events[0]["source_window"]["video_timestamps_ms"][1] += 2
        item["source_window"]["video_timestamps_ms"][1] += 2
        item["source_bindings"][1]["video_timestamp_ms"] += 2
        item["window_input_sha256"]["event"] = digest(events[0])
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_neutral_constants_and_numeric_relationship_are_strict(self):
        for key, value in [("score", 0), ("grade", "A"), ("formal_event_verified", 0), ("scoring_allowed", True),
            ("assessment_status", "graded"), ("coordinate_system", "meters"), ("sampling_protocol", "resampled"),
            ("hip_path_length_px", -1), ("hip_path_length_px", 20), ("hip_net_displacement_px", 0),
            ("hip_path_over_net_ratio", 2), ("hip_path_over_net_ratio", True), ("hip_path_over_net_ratio", float("inf"))]:
            with self.subTest(key=key, value=value):
                artifact, events, item = fixture(); item[key] = value
                self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_zero_net_keeps_lengths_without_ratio_and_no_threshold_cap(self):
        artifact, events, item = fixture()
        item.update(status="unavailable", reason="zero_net_displacement", hip_path_length_px=60.,
            hip_net_displacement_px=0., hip_path_over_net_ratio=None)
        row = project(artifact, events)["contextual_measurements"][0]
        self.assertEqual((row["hip_path_length_px"], row["hip_net_displacement_px"], row["hip_path_over_net_ratio"]), (60., 0., None))
        artifact, events, item = fixture()
        item.update(hip_path_length_px=60., hip_net_displacement_px=1e-10, hip_path_over_net_ratio=6e11)
        self.assertEqual(project(artifact, events)["contextual_measurements"][0]["hip_path_over_net_ratio"], 6e11)

    def test_helper_and_prehelper_unavailable_reasons_remain_null(self):
        for reason in ("hip_geometry_unavailable", "nonfinite_ratio", "contextual_source_window_incomplete"):
            with self.subTest(reason=reason):
                artifact, events, item = fixture()
                item.update(status="unavailable", reason=reason, hip_path_length_px=None,
                    hip_net_displacement_px=None, hip_path_over_net_ratio=None)
                if reason != "nonfinite_ratio": item["source_reference"] = None
                if reason.startswith("contextual_"):
                    item["source_bindings"] = []
                    item["window_input_sha256"]["adapted_primary"] = None
                row = project(artifact, events)["contextual_measurements"][0]
                self.assertEqual(row["reason"], reason)
                self.assertIsNone(row["hip_path_over_net_ratio"])
        artifact, events, item = fixture(); item.update(status="unavailable", reason="invented_reason")
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_missing_source_window_unavailable_is_retained_but_cannot_carry_geometry(self):
        artifact, events, item = fixture(); del events[0]["source_window"]
        item.update(status="unavailable", reason="contextual_event_source_window_unavailable",
            source_window=None, source_frame_bounds=None, source_reference=None, source_bindings=[],
            measurement_window_video_ms=None, hip_path_length_px=None, hip_net_displacement_px=None, hip_path_over_net_ratio=None)
        item["window_input_sha256"] = dict(records=None, original_primary=None, adapted_primary=None, event=digest(events[0]))
        self.assertEqual(project(artifact, events)["contextual_measurements"][0]["reason"], item["reason"])
        item["hip_path_length_px"] = 0
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_duplicate_context_is_not_arbitrarily_selected(self):
        artifact, events, item = fixture()
        artifact["records"][0]["contextual_measurements"].append(copy.deepcopy(item))
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_optional_compute_failure_keeps_bound_parent_but_no_unproven_inputs(self):
        artifact, events, item = fixture()
        item.update(status="unavailable", reason="contextual_measurement_failed", source_bindings=[],
            source_reference=None, hip_path_length_px=None, hip_net_displacement_px=None,
            hip_path_over_net_ratio=None)
        item["window_input_sha256"].update(records=None, original_primary=None, adapted_primary=None)
        row = project(artifact, events)["contextual_measurements"][0]
        self.assertEqual(row["reason"], "contextual_measurement_failed")
        self.assertEqual(row["source_window"], item["source_window"])
        self.assertEqual(row["source_frame_bounds"], item["source_frame_bounds"])
        self.assertEqual(row["input_sha256"], item["input_sha256"])
        for defect in ("fabricated_records", "value", "original_hash", "event_digest", "source_identity"):
            with self.subTest(defect=defect):
                bad = copy.deepcopy(artifact)
                changed = bad["records"][0]["contextual_measurements"][0]
                if defect == "fabricated_records": changed["window_input_sha256"]["records"] = "4" * 64
                elif defect == "value": changed["hip_path_length_px"] = 0
                elif defect == "original_hash": changed["input_sha256"]["frames"] = "f" * 64
                elif defect == "event_digest": changed["window_input_sha256"]["event"] = "f" * 64
                else: changed["source_id"] = "wrong-source"
                self.assertEqual(project(bad, events)["contextual_measurements"], [])

    def test_optional_compute_failure_without_valid_window_stays_explicitly_unavailable(self):
        artifact, events, item = fixture()
        del events[0]["source_window"]
        item.update(status="unavailable", reason="contextual_measurement_failed", source_window=None,
            measurement_window_video_ms=None, source_frame_bounds=None, source_bindings=[], source_reference=None,
            hip_path_length_px=None, hip_net_displacement_px=None, hip_path_over_net_ratio=None)
        item["window_input_sha256"] = dict(records=None, original_primary=None, adapted_primary=None, event=digest(events[0]))
        row = project(artifact, events)["contextual_measurements"][0]
        self.assertEqual(row["reason"], "contextual_measurement_failed")
        self.assertIsNone(row["source_window"])
        self.assertEqual(row["source_bindings"], [])
        self.assertIsNone(row["hip_path_over_net_ratio"])
        item["source_frame_bounds"] = {"start_source_frame": 100, "end_source_frame": 102}
        self.assertEqual(project(artifact, events)["contextual_measurements"], [])

    def test_old_report_without_context_or_input_hashes_still_projects(self):
        from tests.test_source_assessment import fixture as legacy_fixture
        artifact, events = legacy_fixture()
        report = project_source_assessment(artifact, events, video_id="video", duration_ms=100)
        self.assertEqual(report["contextual_measurements"], [])
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["indicators"][0]["windows"][0]["measurements"][0]["value"], .12)


if __name__ == "__main__":
    unittest.main()
