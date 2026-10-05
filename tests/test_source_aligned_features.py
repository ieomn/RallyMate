from __future__ import annotations

import copy
import json
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_events.associations import SuccessorEventIndex
from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import JOINTS, locate_source_preload
from rallymate_scoring.source_aligned_features import (
    clear_source_aligned_feature_cache, compute_source_aligned_features, prepare_source_aligned_context,
)


def event(identifier="split", code="FS01", start=0, end=800, person=1, video="video-a", **phases):
    return {"video_id": video, "event_id": identifier, "person_track_id": person, "event_code": code,
            "start_ms": start, "end_ms": end, "key_phases_ms": phases, "quality_flags": [],
            "track_diagnostics": {"primary_identity_ambiguous": False, "source_track_switch_candidate_count": 0,
                                  "source_track_ids": [person]}}


def fixture():
    times = np.arange(0, 1640, 40)
    count = len(times)
    dip = np.zeros(count)
    dip[:9] = [.01, 0, .01, .025, .04, .025, .01, 0, 0]
    xy = {name: np.empty((count, 2)) for name in JOINTS}
    for index, depth in enumerate(dip):
        hip_y = .45 + depth
        for side, sign in (("left", -1), ("right", 1)):
            xy[f"{side}_shoulder"][index] = [.5 + sign * .2, hip_y - .25]
            xy[f"{side}_hip"][index] = [.5 + sign * .1, hip_y]
            xy[f"{side}_knee"][index] = [.5 + sign * (.2 + depth), (hip_y + .9) / 2]
            xy[f"{side}_ankle"][index] = [.5 + sign * .3, .9]
    sequence = PoseSequence(times, np.arange(count), xy, {name: np.full(count, .95) for name in JOINTS},
                            camera_motion_status=("stationary",) * count, camera_reference_epochs=(0,) * count)
    events = [event(preload_ms=640, takeoff_proxy_ms=320, landing_proxy_ms=480),
              event("launch", "FS02", 800, 920),
              event("stop", "FS09", 920, 1200, stable_control_onset_ms=960),
              event("other-person", "FS02", 1240, 1480, person=2),
              event("next-launch", "FS02", 1400, 1560)]
    timeline = [{"source_frame_index": int(index), "timestamp_ms": int(time), "primary_player_id": 1,
                 "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False}
                for index, time in enumerate(times)]
    return sequence, events, timeline


def indicator(result, code):
    return next(item for item in result["indicators"] if item["indicator_id"] == code)


def feature(result, code, name):
    return next(row for row in indicator(result, code)["source_measurements"] if row["feature_name"] == name)


class SourceAlignedFeatureTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        self.addCleanup(clear_source_aligned_feature_cache)

    def test_reversed_preload_repaired_from_observed_turn_without_mutating_event(self):
        sequence, events, timeline = fixture()
        original = copy.deepcopy(events)
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
        row = feature(result, "FS01-M02", "preload_hip_height_drop_body")
        self.assertEqual(row["status"], "measured")
        self.assertAlmostEqual(row["value"], .16)
        phase = row["evidence"]["source_phase"]
        self.assertEqual(phase["original_preload_ms"], 640)
        self.assertEqual(phase["source_preload_ms"], 160)
        self.assertEqual(phase["source_preload_start_ms"], 40)
        self.assertEqual(phase["repair_reason"], "original_preload_after_takeoff_replaced_by_observed_turn")
        self.assertGreater(feature(result, "FS01-M02", "left_preload_knee_flexion_change_deg")["value"], 0)
        self.assertGreater(feature(result, "FS01-M02", "preload_body_speed_change_rate_body_s2")["value"], 0)
        self.assertEqual(events, original)
        self.assertIsNone(result["grade"])
        self.assertFalse(indicator(result, "FS01-M02")["source_rule_gate"]["complete_source_rule_verified"])
        json.dumps(result, allow_nan=False)

    def test_descent_already_in_progress_at_first_event_frame_is_censored(self):
        sequence, events, _ = fixture()
        for name in ("left_hip", "right_hip"):
            sequence.keypoints_xy[name][0, 1] = .44
        context = prepare_source_aligned_context(sequence, events)
        phase = locate_source_preload(context, events[0])
        self.assertEqual(phase["repair_reason"], "source_preload_start_boundary_censored")
        self.assertIsNone(phase["source_preload_start_ms"])
        result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
        self.assertTrue(all(row["value"] is None for row in indicator(result, "FS01-M02")["source_measurements"]))
        json.dumps(result, allow_nan=False)

    def test_true_hip_width_ratio_and_zero_are_not_missing(self):
        sequence, events, _ = fixture()
        result = compute_source_aligned_features(sequence, events[0], events=events)
        row = feature(result, "FS01-M04", "post_slowdown_ankle_width_to_hip_width_ratio")
        self.assertEqual(row["unit"], "ratio")
        self.assertAlmostEqual(row["value"], 3.)
        self.assertNotIn("post_slowdown_stance_width_body", [item["feature_name"] for item in indicator(result, "FS01-M04")["source_measurements"]])
        sequence.keypoints_xy["left_ankle"][:, 0] = .5
        sequence.keypoints_xy["right_ankle"][:, 0] = .5
        clear_source_aligned_feature_cache()
        row = feature(compute_source_aligned_features(sequence, events[0], events=events), "FS01-M04", "post_slowdown_ankle_width_to_hip_width_ratio")
        self.assertEqual((row["status"], row["value"]), ("measured", 0.))

    def test_source_joint_70_percent_is_conservative_simultaneous_mask(self):
        sequence, events, _ = fixture()
        events[0]["end_ms"] = 360  # Exactly ten frames in the source window.
        for name in ("left_hip", "right_hip"):
            sequence.confidence[name][7:10] = .1
        context = prepare_source_aligned_context(sequence, events)
        result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
        coverage = indicator(result, "FS01-M02")["source_rule_gate"]["pose_coverage"]
        self.assertEqual((coverage["valid_frame_count"], coverage["total_frame_count"]), (7, 10))
        self.assertTrue(coverage["passes"])
        for name in ("left_knee", "right_knee"):
            sequence.confidence[name][:3] = .1
        clear_source_aligned_feature_cache()
        result = compute_source_aligned_features(sequence, events[0], events=events)
        coverage = indicator(result, "FS01-M02")["source_rule_gate"]["pose_coverage"]
        self.assertTrue(all(group["meets_70_percent"] for group in coverage["groups"]))
        self.assertEqual(coverage["fraction"], .4)
        self.assertFalse(coverage["passes"])

    def test_no_turn_boundary_peak_or_single_spike_is_not_repaired(self):
        for values in ([.45] * 9, [.53, .52, .51, .50, .49, .48, .47, .46, .45],
                       [.45, .45, .45, .45, .5, .45, .45, .45, .45]):
            with self.subTest(values=values):
                sequence, events, _ = fixture()
                for name in ("left_hip", "right_hip"):
                    sequence.keypoints_xy[name][:9, 1] = values
                context = prepare_source_aligned_context(sequence, events)
                phase = locate_source_preload(context, events[0])
                self.assertEqual(phase["status"], "unavailable")
                self.assertIsNone(phase["source_preload_ms"])

    def test_phase_or_window_gap_cannot_bridge_missing_observations(self):
        sequence, events, _ = fixture()
        sequence.keypoints_xy["left_hip"][3] = np.nan
        context = prepare_source_aligned_context(sequence, events)
        self.assertEqual(locate_source_preload(context, events[0])["status"], "unavailable")
        sequence, events, _ = fixture()
        sequence = replace(sequence, camera_reference_epochs=(0,) * 4 + (1,) * (len(sequence.timestamp_ms) - 4))
        result = compute_source_aligned_features(sequence, events[0], events=events)
        self.assertEqual(feature(result, "FS01-M02", "preload_hip_height_drop_body")["reason"], "camera_reference_discontinuity")

    def test_successor_same_person_video_and_continuity(self):
        sequence, events, timeline = fixture()
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        split = compute_source_aligned_features(sequence, events[0], events=events, context=context)
        self.assertEqual(feature(split, "FS01-M05", "landing_proxy_to_next_fs02_ms")["value"], 320)
        stop = compute_source_aligned_features(sequence, events[2], events=events, context=context)
        association = indicator(stop, "FS09-M05")["event_association"]
        self.assertEqual(association["successor_event_id"], "next-launch")
        self.assertEqual(feature(stop, "FS09-M05", "stable_control_proxy_to_next_fs10_or_fs02_ms")["value"], 440)
        self.assertFalse(association["new_events_generated"])
        unverified = compute_source_aligned_features(sequence, events[2], events=events)
        self.assertEqual(feature(unverified, "FS09-M05", "stable_control_proxy_to_next_fs10_or_fs02_ms")["status"], "unavailable")

    def test_intervening_identity_switch_rejects_link_even_when_endpoints_match(self):
        sequence, events, timeline = fixture()
        timeline[32]["source_track_id"] = 7
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        result = compute_source_aligned_features(sequence, events[2], events=events, context=context)
        row = feature(result, "FS09-M05", "stable_control_proxy_to_next_fs10_or_fs02_ms")
        self.assertEqual(row["reason"], "between_event_subject_continuity_unverified")
        self.assertIsNone(row["value"])

    def test_conflicting_timeline_rows_and_successor_tracks_do_not_claim_verified_identity(self):
        for conflict in ("timeline", "successor"):
            with self.subTest(conflict=conflict):
                sequence, events, timeline = fixture()
                if conflict == "timeline":
                    timeline.append({**timeline[32], "source_track_id": 7})
                    timeline.append(dict(timeline[32]))
                else:
                    events[-1]["track_diagnostics"]["source_track_ids"] = [7]
                context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
                result = compute_source_aligned_features(sequence, events[2], events=events, context=context)
                association = indicator(result, "FS09-M05")["event_association"]
                self.assertEqual(association["subject_continuity"], "unavailable")
                self.assertEqual(association["link_measurement_status"], "unavailable")
                json.dumps(result, allow_nan=False)

    def test_zero_time_is_observed_when_both_candidates_share_the_exact_frame(self):
        sequence, events, timeline = fixture()
        events[1]["start_ms"] = 480
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
        row = feature(result, "FS01-M05", "landing_proxy_to_next_fs02_ms")
        self.assertEqual((row["status"], row["value"]), ("measured", 0.))
        self.assertEqual(row["source_frames"], [12])
        self.assertIsNone(result["grade"])

    def test_degenerate_hip_width_invalid_confidence_and_unverified_frames_are_unavailable(self):
        for invalid in ("hip_width", "confidence", "camera", "frame_gap", "endpoint"):
            with self.subTest(invalid=invalid):
                sequence, events, timeline = fixture()
                if invalid == "hip_width":
                    sequence.keypoints_xy["right_hip"][:] = sequence.keypoints_xy["left_hip"]
                elif invalid == "confidence":
                    sequence.confidence["left_hip"][:] = 1.1
                elif invalid == "camera":
                    sequence = replace(sequence, camera_motion_status=None)
                elif invalid == "frame_gap":
                    sequence.source_frames[15:] += 1
                else:
                    events[0]["key_phases_ms"]["landing_proxy_ms"] = 481
                context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
                result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
                code, name = ("FS01-M05", "landing_proxy_to_next_fs02_ms") if invalid == "endpoint" else ("FS01-M04", "post_slowdown_ankle_width_to_hip_width_ratio")
                self.assertEqual(feature(result, code, name)["status"], "unavailable")
                json.dumps(result, allow_nan=False)

    def test_fs10_only_associated_when_explicitly_supplied_and_no_successor_is_missing(self):
        sequence, events, timeline = fixture()
        events.append(event("explicit-return", "FS10", 1320, 1480))
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        result = compute_source_aligned_features(sequence, events[2], events=events, context=context)
        self.assertEqual(indicator(result, "FS09-M05")["event_association"]["successor_event_code"], "FS10")
        events = events[:4]
        context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
        result = compute_source_aligned_features(sequence, events[2], events=events, context=context)
        self.assertEqual(indicator(result, "FS09-M05")["event_association"]["status"], "not_observed")
        self.assertIsNone(feature(result, "FS09-M05", "stable_control_proxy_to_next_fs10_or_fs02_ms")["value"])

    def test_preparation_is_reused_and_full_sequence_geometry_not_repeated(self):
        sequence, events, _ = fixture()
        from rallymate_features.source_alignment import body_scale
        with patch("rallymate_features.source_alignment.body_scale", wraps=body_scale) as scale:
            first = prepare_source_aligned_context(sequence, events)
            for observed_event in events:
                compute_source_aligned_features(sequence, observed_event, events=events)
            self.assertIs(first, prepare_source_aligned_context(sequence, events))
            self.assertEqual(scale.call_count, 1)


class SuccessorIndexTests(unittest.TestCase):
    def test_conflicts_ties_wrong_video_and_wrong_person_not_arbitrarily_selected(self):
        origin = event()
        wrong_video = event("foreign", "FS02", 800, 900, video="video-b")
        wrong_person = event("person-two", "FS02", 800, 900, person=2)
        a, b = event("a", "FS02", 840, 960), event("b", "FS02", 840, 960)
        index = SuccessorEventIndex([origin, wrong_video, wrong_person, a, b])
        self.assertEqual(index.next_event(origin, after_ms=800, allowed_codes=("FS02",))["reason"], "simultaneous_successor_candidates_ambiguous")
        changed = {**origin, "end_ms": 840}
        index = SuccessorEventIndex([origin, changed, a])
        self.assertEqual(index.next_event(origin, after_ms=800, allowed_codes=("FS02",))["reason"], "source_event_conflicting_or_not_in_snapshot")


if __name__ == "__main__":
    unittest.main()
