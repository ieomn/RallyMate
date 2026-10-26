from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import JOINTS, clear_source_aligned_feature_cache
from rallymate_scoring.temporal_card_evidence import (
    CARD_CONDITIONS, SOURCE_BINDING_EVIDENCE_VERSION, compute_temporal_card_evidence,
)
from rallymate_scoring.quality_policy import evaluate_indicator_event_quality


def event(code="FS01", start=0, end=800, identifier="split", **phases):
    return {"video_id": "test-video", "event_id": identifier, "event_code": code,
            "person_track_id": 1, "start_ms": start, "end_ms": end, "key_phases_ms": phases,
            "quality_flags": [], "track_diagnostics": {"primary_identity_ambiguous": False,
                "source_track_switch_candidate_count": 0, "source_track_ids": [1]}}


def fixture():
    times = np.arange(0, 1640, 40)
    n = len(times)
    depth = np.zeros(n)
    depth[:9] = [.01, 0, .01, .025, .04, .025, .01, 0, 0]
    xy = {name: np.empty((n, 2)) for name in JOINTS}
    for i, dip in enumerate(depth):
        hip_y = .45 + dip
        for side, sign in (("left", -1), ("right", 1)):
            xy[f"{side}_shoulder"][i] = [.5 + sign * .2, hip_y - .25]
            xy[f"{side}_hip"][i] = [.5 + sign * .1, hip_y]
            xy[f"{side}_knee"][i] = [.5 + sign * (.2 + dip), (hip_y + .9) / 2]
            xy[f"{side}_ankle"][i] = [.5 + sign * .3, .9]
    sequence = PoseSequence(times, np.arange(n), xy, {name: np.full(n, .95) for name in JOINTS},
        camera_motion_status=("stationary",) * n, camera_reference_epochs=(0,) * n,
        frame_dimensions_px=np.tile([1280, 720], (n, 1)),
        frame_transforms_to_reference=np.tile([[1., 0., 0.], [0., 1., 0.]], (n, 1, 1)),
        source_time_bindings={i: {"frame_index": i, "processed_index": i, "video_sha256": "a" * 64,
            "pts": int(t), "time_base_num": 1, "time_base_den": 1000, "source_timestamp_ms": int(t),
            "video_timestamp_ms": int(t), "timestamp_source": "decoder_pts", "primary_player_id": 1,
            "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0} for i, t in enumerate(times)},
        coordinate_metadata={"contract_version": "isotropic-frame-long-edge-v1.1.0",
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge",
            "camera_compensation": {"applied_source_frames": list(range(n))}})
    events = [event(preload_ms=640, takeoff_proxy_ms=320, landing_proxy_ms=480),
              event("FS02", 800, 920, "launch"),
              event("FS09", 920, 1200, "stop", peak_speed_ms=920,
                    deceleration_peak_ms=960, restabilization_onset_ms=1080, stable_control_onset_ms=1080),
              event("FS02", 1400, 1560, "next-launch")]
    timeline = [{"source_frame_index": i, "timestamp_ms": int(t), "primary_player_id": 1,
                 "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False}
                for i, t in enumerate(times)]
    return sequence, events, timeline


def card(result, identifier):
    return next(row for row in result["cards"] if row["indicator_id"] == identifier)


class TemporalCardEvidenceTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        self.addCleanup(clear_source_aligned_feature_cache)

    def test_stability_axis_endpoint_reversal_cannot_manufacture_angular_braking(self):
        sequence, events, timeline = fixture()
        before = card(compute_temporal_card_evidence(sequence, events[2], events=events,
                      primary_timeline=timeline), "FS09-M04")
        # Same image-plane line and centre, opposite endpoint labels in the
        # first source frame. The old directed unwrap invented 4500 deg/s.
        index = int(np.flatnonzero(sequence.timestamp_ms == events[2]["key_phases_ms"]["deceleration_peak_ms"])[0])
        for name in ("shoulder", "hip"):
            left, right = sequence.keypoints_xy[f"left_{name}"], sequence.keypoints_xy[f"right_{name}"]
            left[index], right[index] = right[index].copy(), left[index].copy()
        clear_source_aligned_feature_cache()
        after = card(compute_temporal_card_evidence(sequence, events[2], events=events,
                     primary_timeline=timeline), "FS09-M04")
        self.assertEqual(before["status"], "measured_visual_proxy")
        self.assertEqual(after["status"], "measured_visual_proxy")
        self.assertAlmostEqual(before["measurements"]["shoulder_hip_angular_speed_drop_deg_s"], 0)
        self.assertAlmostEqual(after["measurements"]["shoulder_hip_angular_speed_drop_deg_s"], 0)
        self.assertEqual([c["passed"] for c in before["conditions"]], [c["passed"] for c in after["conditions"]])

    def test_real_axis_rotation_and_180_degree_wrap_preserve_observed_angular_drop(self):
        for offset in (0, 80):
            with self.subTest(offset=offset):
                sequence, events, timeline = fixture()
                for index, degrees in zip(range(24, 31), (0, 20, 35, 45, 50, 54, 57)):
                    center = (sequence.keypoints_xy["left_shoulder"][index] + sequence.keypoints_xy["right_shoulder"][index]) / 2
                    radians = np.deg2rad(offset + degrees)
                    half_axis = .2 * np.array([np.cos(radians), np.sin(radians)])
                    sequence.keypoints_xy["left_shoulder"][index] = center - half_axis
                    sequence.keypoints_xy["right_shoulder"][index] = center + half_axis
                clear_source_aligned_feature_cache()
                row = card(compute_temporal_card_evidence(sequence, events[2], events=events,
                           primary_timeline=timeline), "FS09-M04")
                self.assertEqual(row["status"], "measured_visual_proxy")
                self.assertAlmostEqual(row["measurements"]["shoulder_hip_angular_speed_drop_deg_s"], 425)

    def test_interval_thresholds_survive_report_evidence_transport(self):
        from rallymate_service.report_evidence_transport import pack_report_evidence, unpack_report_evidence

        sequence, events, timeline = fixture()
        for source_event, identifier in ((events[0], "FS01-M05"), (events[2], "FS09-M05")):
            with self.subTest(indicator=identifier):
                row = card(compute_temporal_card_evidence(
                    sequence, source_event, events=events, primary_timeline=timeline), identifier)
                report = {"card_execution": {"cards": [{"indicator_id": identifier, "instances": [row]}]}}
                wire_report = json.loads(json.dumps(report, allow_nan=False))
                self.assertEqual(unpack_report_evidence(pack_report_evidence(report)), wire_report)

    def test_joint_preload_uses_repaired_continuous_window_and_fixed_reference(self):
        sequence, events, timeline = fixture()
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M02")
        self.assertEqual(row["status"], "measured_visual_proxy")
        self.assertEqual(row["evidence"]["requested_window_ms"], {"start_ms": 40., "end_ms": 160.})
        self.assertAlmostEqual(row["measurements"]["hip_drop_torso"], .16)
        self.assertEqual(row["measurements"]["joint_descent_fraction"], 1.)
        self.assertEqual(row["evidence"]["series"]["pair_source_frames"], [[1, 2], [2, 3], [3, 4]])
        self.assertIsNone(row["original_grade"])
        self.assertFalse(row["source_grade_supported"])
        self.assertEqual(events[0]["key_phases_ms"]["preload_ms"], 640)
        json.dumps(row, allow_nan=False)

    def test_ankle_motion_does_not_change_reference_hip_drop(self):
        sequence, events, timeline = fixture()
        baseline = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M02")
        clear_source_aligned_feature_cache()
        for side in ("left", "right"):
            sequence.keypoints_xy[f"{side}_ankle"][1:5, 1] += np.arange(4) * .02
        changed = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M02")
        self.assertEqual(baseline["measurements"]["hip_drop_torso"], changed["measurements"]["hip_drop_torso"])

    def test_time_separated_knee_and_hip_actions_do_not_pass_joint_descent(self):
        sequence, events, timeline = fixture()
        from rallymate_features.source_alignment import prepare_source_aligned_context
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        # Inject independently measured angle series to isolate temporal joining:
        # both knees increase only during one of three downward intervals.
        context.knees["left"][1:5] = [10, 30, 30, 30]
        context.knees["right"][1:5] = [10, 30, 30, 30]
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, context=context), "FS01-M02")
        self.assertGreater(row["measurements"]["left_knee_flexion_change_deg"], 4)
        self.assertAlmostEqual(row["measurements"]["joint_descent_fraction"], 1 / 3)
        joint = next(c for c in row["conditions"] if c["metric"] == "joint_descent_fraction")
        self.assertFalse(joint["passed"])

    def test_visibility_gap_retains_full_denominator_and_never_becomes_failed_grade(self):
        sequence, events, timeline = fixture()
        sequence.confidence["left_knee"][2] = .1
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M02")
        coverage = row["evidence"]["coverage"]
        self.assertEqual((coverage["valid_frame_count"], coverage["total_frame_count"]), (3, 4))
        self.assertTrue(coverage["passes"])
        self.assertEqual(row["reason"], "required_joint_gap_inside_continuous_window")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
        self.assertIsNone(row["original_grade"])

    def test_successor_requires_identity_across_the_gap(self):
        sequence, events, timeline = fixture()
        timeline[33]["source_track_id"] = 99  # 1320ms, between stop and next launch.
        row = card(compute_temporal_card_evidence(sequence, events[2], events=events, primary_timeline=timeline), "FS09-M05")
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(row["reason"], "measurement_window_subject_continuity_unverified")
        self.assertNotIn("successor_interval_ms", row["measurements"])
        proof = row["evidence"]["successor"]
        self.assertEqual([r["frame_index"] for r in proof["source_frame_bindings"]], proof["source_frames"])
        mismatched = next(r for r in proof["source_frame_bindings"] if r["frame_index"] == 33)
        self.assertEqual(mismatched["primary_selection"]["source_track_id"], 99)
        self.assertEqual(mismatched["person_track_id"], 1)

    def test_all_five_cards_preserve_original_rawtrack_and_decoder_binding_per_frame(self):
        sequence, events, timeline = fixture()
        baseline = {row["indicator_id"]: row["measurements"] for event_row in (events[0], events[2])
                    for row in compute_temporal_card_evidence(sequence, event_row, events=events,
                        primary_timeline=timeline)["cards"]}
        clear_source_aligned_feature_cache()
        for binding in sequence.source_time_bindings.values(): binding["person_track_id"] = 7
        for event_row in events: event_row["track_diagnostics"]["source_track_ids"] = [7]
        for selected in timeline: selected["source_track_id"] = 7
        original_bindings, original_timeline = deepcopy(sequence.source_time_bindings), deepcopy(timeline)
        rows = [row for event_row in (events[0], events[2]) for row in
                compute_temporal_card_evidence(sequence, event_row, events=events, primary_timeline=timeline)["cards"]]
        self.assertEqual({row["indicator_id"] for row in rows}, set(CARD_CONDITIONS))
        for row in rows:
            evidence = row["evidence"]
            self.assertEqual(row["measurements"], baseline[row["indicator_id"]])
            self.assertEqual(evidence["source_frame_binding_version"], SOURCE_BINDING_EVIDENCE_VERSION)
            self.assertEqual(evidence["source_track_ids"], [7])
            self.assertEqual([r["frame_index"] for r in evidence["source_frame_bindings"]], evidence["source_frames"])
            for binding in evidence["source_frame_bindings"]:
                index = binding["frame_index"]
                self.assertEqual({k: binding[k] for k in original_bindings[index]}, original_bindings[index])
                self.assertEqual(binding["status"], "verified_decoder_binding")
                self.assertEqual(binding["primary_selection"], original_timeline[index])
                self.assertEqual(binding["primary_player_id"], 1)
                self.assertEqual(binding["person_track_id"], 7)
                self.assertIsNone(binding["model_geometry_source"])
            if "successor" in evidence:
                successor = evidence["successor"]
                self.assertEqual(successor["source_track_ids"], [7])
                self.assertEqual(successor["successor_source_track_ids"], [7])
                self.assertEqual([r["frame_index"] for r in successor["source_frame_bindings"]], successor["source_frames"])
        rows[0]["evidence"]["source_frame_bindings"][0]["primary_selection"]["source_track_id"] = 99
        self.assertEqual(timeline, original_timeline)
        self.assertEqual(sequence.source_time_bindings, original_bindings)

    def test_binding_proof_keeps_separate_source_and_video_clocks(self):
        sequence, events, timeline = fixture()
        for binding in sequence.source_time_bindings.values():
            binding["pts"] += 9000
            binding["source_timestamp_ms"] += 9000
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events,
            primary_timeline=timeline), "FS01-M05")
        self.assertEqual(row["status"], "measured_visual_proxy")
        for binding, video_ms, source_ms in zip(row["evidence"]["source_frame_bindings"],
                row["evidence"]["timestamps_ms"], row["evidence"]["source_timestamps_ms"]):
            self.assertEqual(binding["video_timestamp_ms"], video_ms)
            self.assertEqual(binding["source_timestamp_ms"], source_ms)
            self.assertEqual(source_ms - video_ms, 9000)
            self.assertNotIn("timestamp_ms", binding)
        self.assertEqual(row["measurements"]["successor_interval_ms"], 320)

    def test_missing_clock_evidence_retains_original_frame_and_does_not_invent_binding(self):
        sequence, events, timeline = fixture()
        del sequence.source_time_bindings[13]
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events,
            primary_timeline=timeline), "FS01-M05")
        self.assertEqual(row["status"], "needs_review")
        self.assertTrue(all(condition["passed"] is None for condition in row["conditions"]))
        proof = row["evidence"]["source_frame_bindings"]
        missing = next(binding for binding in proof if binding["frame_index"] == 13)
        self.assertEqual(missing["status"], "unavailable")
        self.assertNotIn("pts", missing)
        self.assertEqual(missing["primary_selection"], timeline[13])
        self.assertEqual(len(proof), len(row["evidence"]["source_frames"]))

    def test_no_successor_is_missing_evidence_not_zero_or_e(self):
        sequence, events, timeline = fixture()
        events = events[:1]
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M05")
        self.assertEqual(row["status"], "needs_review")
        self.assertEqual(row["reason"], "same_player_successor_not_observed")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_low_foot_motion_does_not_establish_support_when_hip_outside_interval(self):
        sequence, events, timeline = fixture()
        for side in ("left", "right"):
            sequence.keypoints_xy[f"{side}_hip"][23:, 0] += 1
            sequence.keypoints_xy[f"{side}_shoulder"][23:, 0] += 1
        row = card(compute_temporal_card_evidence(sequence, events[2], events=events, primary_timeline=timeline), "FS09-M04")
        self.assertEqual(row["measurements"]["supported_low_motion_duration_ms"], 0)
        self.assertEqual(row["measurements"]["hip_inside_support_fraction"], 0)
        self.assertFalse(row["evidence"]["phase_selection"]["physical_contact_confirmed"])

    def test_braking_window_is_phase_local_not_whole_event_quartiles(self):
        sequence, events, timeline = fixture()
        row = card(compute_temporal_card_evidence(sequence, events[2], events=events, primary_timeline=timeline), "FS09-M03")
        self.assertEqual(row["evidence"]["requested_window_ms"], {"start_ms": 920., "end_ms": 1080.})
        self.assertEqual(row["evidence"]["source_frames"], [23, 24, 25, 26, 27])
        self.assertIsNone(row["measurements"]["hip_speed_drop_fraction"])
        self.assertEqual(row["status"], "needs_review")

    def test_camera_epoch_change_frame_gap_or_unverified_coordinates_block_measurement(self):
        for defect in ("camera", "frames", "coordinates", "timeline"):
            sequence, events, timeline = fixture()
            if defect == "camera":
                sequence = replace(sequence, source_time_bindings={}, camera_reference_epochs=(0,) * 3 + (1,) * (len(sequence.timestamp_ms) - 3))
            elif defect == "frames":
                sequence.source_time_bindings.clear()
                sequence.source_frames[3] = 1000
            elif defect == "coordinates":
                sequence = replace(sequence, coordinate_metadata={})
            else:
                timeline = None
            row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M02")
            self.assertEqual(row["status"], "needs_review", defect)
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]), defect)
            clear_source_aligned_feature_cache()

    def test_contract_is_complete_for_five_cards_and_unknown_types_stay_empty(self):
        sequence, events, timeline = fixture()
        self.assertEqual(len(CARD_CONDITIONS), 5)
        self.assertEqual(compute_temporal_card_evidence(sequence, events[1], events=events, primary_timeline=timeline)["cards"], [])

    def test_real_clip_fallback_keeps_unverified_action_semantics_but_exposes_complete_conditions(self):
        payload = json.loads((Path(__file__).parent / "fixtures" / "temporal-card-real-0a3.json").read_text(encoding="utf-8"))
        data = payload["sequence"]
        for key in ("timestamp_ms", "source_frames", "frame_dimensions_px", "frame_transforms_to_reference"):
            data[key] = np.asarray(data[key])
        for key in ("keypoints_xy", "confidence", "model_estimate_masks"):
            data[key] = {name: np.asarray(value, dtype=bool if key == "model_estimate_masks" else float)
                         for name, value in data[key].items()}
        for key in ("camera_motion_status", "camera_reference_epochs", "model_estimate_bindings"):
            data[key] = tuple(data[key])
        data["source_time_bindings"] = {int(k): v for k, v in data["source_time_bindings"].items()}
        sequence = PoseSequence(**data)
        events, timeline = payload["events"], payload["timeline"]
        before = card(compute_temporal_card_evidence(sequence, events[0], events=events,
            primary_timeline=timeline, allow_phase_fallback=False), "FS01-M02")
        after = card(compute_temporal_card_evidence(sequence, events[0], events=events,
            primary_timeline=timeline), "FS01-M02")
        self.assertEqual(before["reason"], "pre_takeoff_window_too_short_for_turn_evidence")
        self.assertIsNone(after["reason"])
        self.assertEqual(after["advisory_reason_codes"], ["fallback_local_dip_action_type_unverified"])
        self.assertEqual(after["evidence"]["requested_window_ms"], {"start_ms": 1467., "end_ms": 1633.})
        self.assertTrue(after["measurements"])
        self.assertFalse(after["evidence"]["coverage"]["visibility_verified"])
        self.assertIsNone(after["evidence"]["coverage"]["source_visibility_70_percent_verified"])
        self.assertTrue(all(type(row["passed"]) is bool for row in after["conditions"]))
        self.assertEqual(after["status"], "measured_visual_proxy")
        proof = after["evidence"]["fallback"]["proof"]
        self.assertEqual(proof["version"], "temporal-local-dip-proof-v1.0.0")
        self.assertEqual(proof["measurement_window_ms"], after["evidence"]["requested_window_ms"])
        self.assertEqual(proof["parent_event"]["event_id"], events[0]["event_id"])
        self.assertEqual(proof["parent_event"]["key_phases_ms"], events[0]["key_phases_ms"])
        self.assertEqual(proof["search_end_basis"], "landing_proxy_ms")
        self.assertEqual(proof["search_end_ms"], events[0]["key_phases_ms"]["landing_proxy_ms"])
        self.assertFalse(proof["takeoff_inferred"])
        rows = [r for r in proof["rows"] if 1467 <= r["timestamp_ms"] <= 1633]
        self.assertEqual([r["frame_index"] for r in rows], after["evidence"]["source_frames"])
        hips = np.asarray([(np.asarray(r["points"]["left_hip"]) + r["points"]["right_hip"]) / 2 for r in rows])
        shoulders = np.asarray([(np.asarray(r["points"]["left_shoulder"]) + r["points"]["right_shoulder"]) / 2 for r in rows])
        scale = np.median(np.linalg.norm(shoulders - hips, axis=1))
        self.assertAlmostEqual((hips[-1, 1] - hips[0, 1]) / scale, after["measurements"]["hip_drop_torso"])
        for r in rows:
            self.assertEqual(r["source_binding"]["frame_index"], r["frame_index"])
            self.assertEqual(r["primary_selection"]["source_track_id"], proof["source_track_ids"][0])
            original_index = list(sequence.source_frames).index(r["frame_index"])
            self.assertEqual(r["model_geometry_source"]["source_binding"], sequence.model_estimate_bindings[original_index])
            self.assertEqual(r["model_geometry_source"]["observation_semantics"], "monocular_model_estimate")
            self.assertFalse(r["model_geometry_source"]["confidence_available"])
        self.assertIsNone(after["original_grade"])
        self.assertEqual(events[0]["key_phases_ms"]["takeoff_proxy_ms"], 1133)
        # The next actual FS09 candidate has a complete geometric window. The
        # source-bound model remains a visual proxy even when every point exists.
        stop = next(row for row in events if row["event_code"] == "FS09")
        braking = card(compute_temporal_card_evidence(sequence, stop, events=events,
            primary_timeline=timeline), "FS09-M03")
        self.assertEqual(braking["status"], "measured_visual_proxy")
        self.assertEqual(braking["evidence"]["requested_window_ms"], {"start_ms": 2500., "end_ms": 2933.})
        self.assertFalse(braking["evidence"]["coverage"]["visibility_verified"])
        self.assertIsNone(braking["original_grade"])
        seen = set()
        by_frame = {int(frame): i for i, frame in enumerate(sequence.source_frames)}
        primary_by_frame = {row["source_frame_index"]: row for row in timeline}
        for source_event in events:
            for measured in compute_temporal_card_evidence(sequence, source_event, events=events,
                    primary_timeline=timeline)["cards"]:
                evidence = measured["evidence"]
                if not evidence.get("source_frames"):
                    continue
                seen.add(measured["indicator_id"])
                self.assertEqual([r["frame_index"] for r in evidence["source_frame_bindings"]], evidence["source_frames"])
                for bound in evidence["source_frame_bindings"]:
                    index = by_frame[bound["frame_index"]]
                    self.assertEqual(bound["status"], "verified_decoder_binding")
                    self.assertEqual(bound["primary_selection"], primary_by_frame[bound["frame_index"]])
                    self.assertEqual(bound["model_geometry_source"]["source_binding"], sequence.model_estimate_bindings[index])
                    self.assertEqual(bound["person_track_id"], evidence["source_track_ids"][0])
        self.assertEqual(seen, set(CARD_CONDITIONS))
        json.dumps([before, after, braking], allow_nan=False)

    def test_quality_hard_failure_and_context_from_other_snapshot_are_rejected(self):
        sequence, events, timeline = fixture()
        events[0]["quality_flags"] = ["confirmed_id_switch_present"]
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M05")
        self.assertEqual(row["reason"], "event_quality_hard_fail")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
        from rallymate_features.source_alignment import prepare_source_aligned_context
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        with self.assertRaises(ValueError):
            compute_temporal_card_evidence(sequence, events[0], events=list(events), context=context)

    def test_support_card_does_not_require_unrelated_knees(self):
        sequence, events, timeline = fixture()
        sequence.confidence["left_knee"][:] = 0.
        sequence.confidence["right_knee"][:] = 0.
        result = compute_temporal_card_evidence(sequence, events[2], events=events, primary_timeline=timeline)
        support = card(result, "FS09-M04")
        self.assertEqual(support["status"], "measured_visual_proxy")
        self.assertNotIn("left_knee", support["evidence"]["coverage"]["required_joint_names"])
        self.assertEqual(card(result, "FS09-M03")["status"], "needs_review")
        json.dumps(result, allow_nan=False)

    def test_minimum_duration_uses_decoder_pts_not_rounded_playback_time(self):
        sequence, events, timeline = fixture()
        events[0]["end_ms"] = 560
        events[1].update(start_ms=560, end_ms=680)
        bindings = {}
        for index, original in sequence.source_time_bindings.items():
            pts = original["pts"] * 10 - (1 if index == 13 else 2 if index == 14 else 0)
            bindings[index] = {**original, "pts": pts, "time_base_den": 10000, "source_timestamp_ms": pts / 10}
        sequence = replace(sequence, source_time_bindings=bindings)
        row = card(compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline), "FS01-M05")
        self.assertEqual(row["evidence"]["requested_window_ms"], {"start_ms": 480., "end_ms": 560.})
        self.assertAlmostEqual(row["evidence"]["source_duration_ms"], 79.8)
        self.assertEqual(row["reason"], "continuous_window_below_80_ms")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_local_quality_gate_does_not_depend_on_legacy_policy_relaxation(self):
        sequence, events, timeline = fixture()
        events[0]["quality_flags"] = ["pose_kinematic_coverage_low"]
        outcomes = []
        for relaxed in (False, True):
            def legacy(indicator, flags):
                result = evaluate_indicator_event_quality(indicator, flags)
                result["scoring_block_flags"] = [] if relaxed else ["pose_kinematic_coverage_low"]
                result["scoring_allowed"] = relaxed
                return result
            with patch("rallymate_scoring.temporal_card_evidence.evaluate_indicator_event_quality", side_effect=legacy):
                outcomes.append(card(compute_temporal_card_evidence(sequence, events[0], events=events,
                    primary_timeline=timeline), "FS01-M05"))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertIsNone(outcomes[0]["reason"])
        self.assertEqual(outcomes[0]["evidence"]["event_quality"]["raw_input_quality_flags"], ["pose_kinematic_coverage_low"])
        self.assertTrue(all(type(c["passed"]) is bool for c in outcomes[0]["conditions"]))

    def test_pose_jump_diagnostics_are_scoped_to_actual_card_required_joints(self):
        sequence, events, timeline = fixture()
        events[2]["quality_flags"] = ["keypoint_jump_candidates_present", "left_right_swap_candidates_present"]
        diagnostics = events[2]["track_diagnostics"]
        diagnostics["keypoint_jump_candidate_joints"] = ["left_knee"]
        diagnostics["left_right_swap_candidate_joint_pairs"] = [{"left_joint": "left_knee", "right_joint": "right_knee"}]
        result = compute_temporal_card_evidence(sequence, events[2], events=events, primary_timeline=timeline)
        support = card(result, "FS09-M04")
        self.assertEqual(support["status"], "measured_visual_proxy")
        self.assertEqual(len(support["evidence"]["event_quality"]["irrelevant_joint_flags"]), 2)
        braking = card(result, "FS09-M03")
        self.assertEqual(braking["reason"], "required_condition_unavailable")
        self.assertEqual(len(braking["evidence"]["event_quality"]["advisory_flags"]), 2)
        self.assertIsNone(next(c["passed"] for c in braking["conditions"] if c["metric"] == "hip_speed_drop_fraction"))

    def test_all_seven_soft_flags_keep_complete_temporal_observations(self):
        from rallymate_scoring.card_scoring_policy import ADVISORY_QUALITY_FLAGS
        for flag in ADVISORY_QUALITY_FLAGS:
            with self.subTest(flag=flag):
                sequence, events, timeline = fixture()
                events[0]["quality_flags"] = [flag]
                events[0]["track_diagnostics"].update(primary_identity_ambiguous=True,
                    source_track_switch_candidate_count=2)
                for row in timeline:
                    row["identity_ambiguous"] = True
                result = compute_temporal_card_evidence(sequence, events[0], events=events, primary_timeline=timeline)
                for row in result["cards"]:
                    self.assertEqual(row["status"], "measured_visual_proxy", row["reason"])
                    self.assertEqual(row["evidence"]["event_quality"]["blocking_flags"], [])
                    self.assertIn(flag, row["evidence"]["event_quality"]["advisory_flags"])

    def test_advisory_identity_never_waives_missing_raw_subject_or_epoch(self):
        for defect in ("track", "epoch", "missing", "confirmed"):
            sequence, events, timeline = fixture()
            events[0]["quality_flags"] = ["primary_identity_ambiguous"]
            events[0]["track_diagnostics"]["primary_identity_ambiguous"] = True
            if defect == "track":
                events[0]["track_diagnostics"]["source_track_ids"] = [1, 2]
            elif defect == "epoch":
                sequence.source_time_bindings[3]["selection_epoch"] += 1
            elif defect == "missing":
                timeline[3].clear()
            else:
                events[0]["track_diagnostics"]["confirmed_id_switch_count"] = 1
            row = card(compute_temporal_card_evidence(sequence, events[0], events=events,
                primary_timeline=timeline), "FS01-M02")
            self.assertEqual(row["status"], "needs_review", defect)
            self.assertTrue(all(c["passed"] is None for c in row["conditions"]), defect)

    def test_preload_validator_defaults_preserve_legacy_identity_policy(self):
        from copy import deepcopy
        from rallymate_features.source_alignment import locate_source_preload, prepare_source_aligned_context
        from rallymate_scoring.temporal_card_evidence import _identity_reason, _subject_window_reason
        sequence, events, timeline = fixture()
        events[0]["track_diagnostics"].update(primary_identity_ambiguous=True,
            source_track_switch_candidate_count=1)
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        before = deepcopy(events)
        legacy = locate_source_preload(context, events[0])
        current = locate_source_preload(context, events[0], identity_validator=_identity_reason,
                                        subject_validator=_subject_window_reason)
        self.assertEqual(legacy["status"], "unavailable")
        self.assertEqual(legacy["repair_reason"], "subject_identity_continuity_unverified")
        self.assertEqual(current["status"], "observed_candidate")
        self.assertEqual(events, before)

    def test_absent_model_binding_does_not_erase_existing_detector_clock_binding(self):
        from rallymate_features.source_alignment import prepare_source_aligned_context
        from rallymate_scoring.temporal_card_evidence import _subject_window_reason
        sequence, events, timeline = fixture()
        sequence = replace(sequence, model_estimate_bindings=(None,) * len(sequence.timestamp_ms))
        for row in timeline:
            row["selection_epoch"] = sequence.source_time_bindings[row["source_frame_index"]]["selection_epoch"]
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        indexes = context.indexes(40, 160)
        self.assertIsNone(_subject_window_reason(context, indexes, events[0]))
        # The sequence-wide mixed-source geometry contract is not supplied by
        # this local subject check; retain the existing window-level rejection.
        self.assertEqual(context.window_reason(indexes, 40, 160), "source_model_binding_discontinuity")


if __name__ == "__main__":
    unittest.main()
