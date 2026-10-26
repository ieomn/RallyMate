from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace

import numpy as np

from rallymate_features.foot_crossing import compute_foot_crossing_candidates, inspect_foot_crossing_pair
from rallymate_features.schemas import PoseSequence


def crossing_fixture(count=2, *, model=False, pts=None):
    frames = np.arange(170, 170 + count)
    pts = pts if pts is not None else [3400 + 20 * i for i in range(count)]
    times = np.array([round(p * 1000 / 600) for p in pts])
    centers = np.column_stack([500 + 10 * np.arange(count), np.full(count, 500)])
    left = np.column_stack([np.where(np.arange(count) % 2 == 0, 480., 525.), np.full(count, 700.)])
    right = np.column_stack([np.where(np.arange(count) % 2 == 0, 520., 515.), np.full(count, 730.)])
    xy = {"left_hip": (centers - [20, 0]) / 1000, "right_hip": (centers + [20, 0]) / 1000,
          "left_ankle": left / 1000, "right_ankle": right / 1000}
    bindings = {int(frame): {"frame_index": int(frame), "processed_index": int(frame), "video_sha256": "a" * 64,
        "pts": int(p), "time_base_num": 1, "time_base_den": 600, "source_timestamp_ms": round(p * 1000 / 600),
        "video_timestamp_ms": int(time), "timestamp_source": "decoder_pts", "primary_player_id": 1,
        "person_track_id": 8, "selection_epoch": 0, "camera_reference_epoch": 0}
        for frame, p, time in zip(frames, pts, times)}
    timeline = [{"source_frame_index": int(frame), "processed_index": int(frame), "timestamp_ms": int(time),
        "primary_player_id": 1, "source_track_id": 8, "selection_status": "selected", "identity_ambiguous": False,
        "selection_epoch": 0, "camera_reference_epoch": 0} for frame, time in zip(frames, times)]
    model_bindings = tuple({**{key: value for key, value in binding.items() if key in (
        "frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den",
        "person_track_id", "selection_epoch", "camera_reference_epoch")}, "timestamp_ms": binding["source_timestamp_ms"]}
        for binding in bindings.values()) if model else None
    sequence = PoseSequence(times, frames, xy, {name: np.full(count, np.nan if model else .9) for name in xy},
        frame_dimensions_px=np.tile([1000, 800], (count, 1)),
        coordinate_metadata={"contract_version": "isotropic-frame-long-edge-v1.1.0",
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge", "axis_units": "same_for_x_and_y",
            "camera_compensation": {"applied_source_frames": frames.tolist()}},
        camera_motion_status=("stationary",) * count, camera_reference_epochs=(0,) * count,
        frame_transforms_to_reference=np.tile([[1., 0., 0.], [0., 1., 0.]], (count, 1, 1)),
        source_time_bindings=bindings, model_estimate_bindings=model_bindings,
        model_estimate_masks={name: np.ones(count, dtype=bool) for name in xy} if model else {})
    return sequence, timeline


def pair(sequence, timeline, **kwargs):
    return inspect_foot_crossing_pair(sequence, 0, primary_timeline=timeline, **kwargs)


def parent_with_source_window(sequence):
    # The independent review's two-frame parent, including the SAM proof shape.
    bindings = list(sequence.source_time_bindings.values())
    return {"event_id": "synthetic-parent-with-source-window", "event_code": "FS02",
        "video_id": "v", "person_track_id": 1, "start_ms": 5667, "end_ms": 5700,
        "quality_flags": ["confirmed_id_switch"], "track_diagnostics": {"source_track_ids": [8]},
        "source_window": {"start_ms": 5667, "end_ms": 5700,
            "source_frames": [170, 171], "video_timestamps_ms": [5667, 5700],
            "source_timestamps_ms": [5667, 5700], "video_sha256": "a" * 64,
            "person_track_id": 8, "selection_epoch": 0, "camera_reference_epoch": 0,
            "source_bindings": [{**{key: row[key] for key in (
                "video_sha256", "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                "person_track_id", "selection_epoch", "camera_reference_epoch")},
                "timestamp_ms": row["source_timestamp_ms"]} for row in bindings]}}


class FootCrossingCandidateTests(unittest.TestCase):
    def test_original_two_frame_pts_interval_not_interpolated_time(self):
        sequence, timeline = crossing_fixture()
        result = pair(sequence, timeline)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["source_frames"], [170, 171])
        self.assertEqual([row["pts"] for row in result["source_bindings"]], [3400, 3420])
        self.assertEqual(result["source_interval_ms"][0], {"numerator": 17000, "denominator": 3, "approx_ms": 17000 / 3})
        np.testing.assert_allclose(result["signed_ankle_order_px"], [-40., 10.])
        self.assertIsNone(result["crossing_time_ms"])
        self.assertFalse(result["formal_step_verified"])
        self.assertFalse(result["scoring_allowed"])
        self.assertEqual(result["endpoint_chord_relation_2d"], "disjoint_endpoint_chords")
        self.assertFalse(result["continuous_trajectory_intersection_verified"])

    def test_screen_x_exchange_does_not_imply_local_image_axis_exchange(self):
        sequence, timeline = crossing_fixture()
        for name in ("left_hip", "right_hip"):
            sequence.keypoints_xy[name][1, 1] += .01
        sequence.keypoints_xy["left_ankle"][:] = [[.498, .71], [.502, .71]]
        sequence.keypoints_xy["right_ankle"][:] = [[.5, .7], [.5, .7]]
        result = pair(sequence, timeline)
        self.assertEqual(result["status"], "no_exchange")
        self.assertTrue(result["axis"]["fixed_within_pair"])
        self.assertLess(result["observations"]["left_minus_right_ankle_reference_x_px"][0], 0)
        self.assertGreater(result["observations"]["left_minus_right_ankle_reference_x_px"][1], 0)
        self.assertTrue(all(value > 0 for value in result["signed_ankle_order_px"]))

    def test_chord_intersection_is_only_endpoint_geometry(self):
        sequence, timeline = crossing_fixture()
        sequence.keypoints_xy["left_ankle"][:] = [[.48, .7], [.52, .74]]
        sequence.keypoints_xy["right_ankle"][:] = [[.52, .7], [.48, .74]]
        result = pair(sequence, timeline)
        self.assertEqual(result["endpoint_chord_relation_2d"], "proper_endpoint_chord_intersection")
        self.assertFalse(result["continuous_trajectory_intersection_verified"])
        self.assertFalse(result["formal_step_verified"])

    def test_zero_axis_and_machine_resolution_are_separate_from_small_motion(self):
        for dx, reason in ((0., "zero_hip_displacement_no_axis"),
                           (np.spacing(.5), "numerically_degenerate_hip_displacement_no_axis"),
                           (1e-8, "strict_projected_order_exchange")):
            sequence, timeline = crossing_fixture()
            for name in ("left_hip", "right_hip"):
                sequence.keypoints_xy[name][1] = sequence.keypoints_xy[name][0] + [dx, 0]
            result = pair(sequence, timeline)
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["axis"]["movement_direction_reliability"], "unverified")

    def test_zero_endpoint_never_bridges_minus_zero_plus(self):
        sequence, timeline = crossing_fixture(3)
        sequence.keypoints_xy["left_ankle"][:, 0] = [.48, .5, .52]
        sequence.keypoints_xy["right_ankle"][:, 0] = [.5, .5, .5]
        result = compute_foot_crossing_candidates(sequence, primary_timeline=timeline)
        self.assertEqual(result["counts"]["candidates"], 0)
        self.assertEqual(result["counts"]["reasons"], {"exact_zero_endpoint_no_strict_exchange": 2})

    def test_invalid_joint_blocks_each_neighbor_and_does_not_bridge(self):
        for model in (False, True):
            sequence, timeline = crossing_fixture(3, model=model)
            if model:
                sequence.model_estimate_masks["left_ankle"][1] = False
            else:
                sequence.confidence["left_ankle"][1] = .1
            result = compute_foot_crossing_candidates(sequence, primary_timeline=timeline)
            self.assertEqual(result["counts"]["excluded"], 2)
            self.assertEqual(result["counts"]["candidates"], 0)

    def test_original_frame_and_processed_adjacency_are_both_required(self):
        sequence, timeline = crossing_fixture(3)
        sequence = sequence.slice(slice(None, None, 2))
        self.assertEqual(pair(sequence, [timeline[0], timeline[2]])["reason"], "nonadjacent_source_frames")
        sequence, timeline = crossing_fixture()
        sequence.source_time_bindings[171]["processed_index"] = 172
        timeline[1]["processed_index"] = 172
        self.assertEqual(pair(sequence, timeline)["reason"], "nonadjacent_processed_indexes")

    def test_variable_native_pts_delta_is_accepted_and_long_span_is_not_called_missing(self):
        sequence, timeline = crossing_fixture(3, pts=[3400, 3409, 3427])
        result = compute_foot_crossing_candidates(sequence, primary_timeline=timeline)
        self.assertEqual(result["counts"]["candidates"], 2)
        sequence, timeline = crossing_fixture(2, pts=[3400, 3520])
        self.assertEqual(pair(sequence, timeline)["reason"], "temporal_span_exceeds_local_policy")

    def test_duplicate_reversed_pts_and_clock_mismatch_fail_closed(self):
        for value, reason in ((3400, "source_pts_not_increasing"), (3380, "source_pts_not_increasing"),
                              (3426, "source_video_clock_mismatch")):
            sequence, timeline = crossing_fixture()
            sequence.source_time_bindings[171]["pts"] = value
            sequence.source_time_bindings[171]["source_timestamp_ms"] = round(value * 1000 / 600)
            self.assertEqual(pair(sequence, timeline)["reason"], reason)

    def test_identity_camera_and_missing_timeline_cannot_be_rescued(self):
        for key, reason in (("person_track_id", "subject_identity_changed"),
                            ("selection_epoch", "subject_identity_changed"),
                            ("camera_reference_epoch", "camera_reference_changed")):
            sequence, timeline = crossing_fixture()
            sequence.source_time_bindings[171][key] += 1
            self.assertEqual(pair(sequence, timeline)["reason"], reason)
        sequence, timeline = crossing_fixture()
        self.assertEqual(pair(sequence, None)["reason"], "primary_timeline_missing_or_duplicated")
        self.assertEqual(pair(sequence, timeline + [timeline[0]])["reason"], "primary_timeline_missing_or_duplicated")
        timeline[1]["identity_ambiguous"] = True
        self.assertEqual(pair(sequence, timeline)["reason"], "primary_identity_unverified")

    def test_bad_reference_contract_transform_and_confidence_rejected(self):
        for defect in ("contract", "declaration", "transform", "dimension", "confidence", "nan"):
            sequence, timeline = crossing_fixture()
            if defect == "contract":
                sequence.coordinate_metadata["coordinate_system"] = "x_width_y_height"
            elif defect == "declaration":
                sequence.coordinate_metadata["camera_compensation"]["applied_source_frames"] = []
            elif defect == "transform":
                sequence.frame_transforms_to_reference[1, 0, 1] = .3
            elif defect == "dimension":
                sequence.frame_dimensions_px[1, 0] = 1200
            elif defect == "confidence":
                sequence.confidence["left_ankle"][1] = 1.1
            else:
                sequence.keypoints_xy["right_ankle"][1] = np.nan
            self.assertEqual(pair(sequence, timeline)["status"], "excluded", defect)

    def test_sam_unknown_confidence_requires_geometry_and_request_bindings(self):
        sequence, timeline = crossing_fixture(model=True)
        result = pair(sequence, timeline)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["quality"]["confidence"]["left_ankle"], [None, None])
        self.assertFalse(result["quality"]["measurement_provenance"]["visibility_verified"])
        self.assertFalse(result["quality"]["source_70_percent_visibility_verified"])
        self.assertIn("model_confidence_and_visibility_not_provided", result["risks"])
        sequence.model_estimate_bindings[1]["processed_index"] += 1
        self.assertEqual(pair(sequence, timeline)["reason"], "model_request_source_binding_mismatch")

    def test_uppercase_source_hash_is_compatible_but_different_video_is_not(self):
        sequence, timeline = crossing_fixture(model=True)
        for row in timeline:
            row["video_sha256"] = "A" * 64
        for binding in sequence.model_estimate_bindings:
            binding["video_sha256"] = "A" * 64
        self.assertEqual(pair(sequence, timeline)["status"], "candidate")
        timeline[1]["video_sha256"] = "b" * 64
        self.assertEqual(pair(sequence, timeline)["reason"], "primary_timeline_clock_mismatch")

    def test_rtm_uses_scoring_cutoff_not_coordinate_loader_or_primary_coverage(self):
        sequence, timeline = crossing_fixture()
        sequence.confidence["right_ankle"][1] = .349
        timeline[1]["keypoint_valid_fraction"] = 1.
        self.assertEqual(pair(sequence, timeline)["reason"], "joint_confidence_or_geometry_unavailable")
        sequence.confidence["right_ankle"][1] = .35
        self.assertEqual(pair(sequence, timeline)["status"], "candidate")

    def test_similarity_invariance_and_no_second_camera_transform(self):
        sequence, timeline = crossing_fixture()
        original = pair(sequence, timeline)
        angle = .71
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        for name in sequence.keypoints_xy:
            sequence.keypoints_xy[name][:] = 1.5 * sequence.keypoints_xy[name] @ rotation.T + [.1, -.2]
        sequence.frame_transforms_to_reference[1] = [[1., 0., 300.], [0., 1., -250.]]
        transformed = pair(sequence, timeline)
        self.assertEqual(transformed["status"], original["status"])
        np.testing.assert_allclose(transformed["signed_ankle_order_px"], 1.5 * np.array(original["signed_ankle_order_px"]))
        np.testing.assert_allclose(transformed["axis"]["unit_vector"], rotation @ original["axis"]["unit_vector"])
        self.assertFalse(transformed["normalization"]["camera_transform_applied_again"])

    def test_parent_candidates_preserved_without_inventing_formal_start(self):
        sequence, timeline = crossing_fixture()
        event = {"video_id": "v", "person_track_id": 1, "event_id": "fs02", "event_code": "FS02",
            "start_ms": 5300, "end_ms": 6600, "key_phases_ms": {"first_step_slowdown_proxy_ms": 6167},
            "track_diagnostics": {"source_track_ids": [8]},
            "quality_flags": ["keypoint_jump_candidates_present", "left_right_swap_candidates_present", "required_phase_missing"]}
        original = copy.deepcopy(event)
        result = pair(sequence, timeline, events=[event], video_id="v")
        self.assertEqual(result["status"], "candidate")
        self.assertIsNone(result["formal_start_ms"])
        self.assertEqual(result["parent_quality_flags"], sorted(event["quality_flags"]))
        self.assertEqual(result["parent_events"][0]["key_phases_ms"], original["key_phases_ms"])
        self.assertEqual(event, original)
        event["quality_flags"].append("confirmed_id_switch")
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["reason"], "parent_confirmed_identity_switch")
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="other-video")["status"], "candidate")

    def test_joint_risks_are_scoped_to_endpoints_and_axis_joints(self):
        sequence, timeline = crossing_fixture()
        event = {"video_id": "v", "person_track_id": 1, "start_ms": 5300, "end_ms": 6600,
            "quality_flags": ["keypoint_jump_candidates_present", "left_right_swap_candidates_present"],
            "track_diagnostics": {"source_track_ids": [8], "diagnostic_semantics": "heuristic_candidates",
                "keypoint_jump_candidate_frames_by_joint": {"right_ankle": [166, 167, 171, 173], "left_elbow": [170]},
                "left_right_swap_candidate_joint_pairs": [
                    {"left_joint": "left_hip", "right_joint": "right_hip", "frames": [180]},
                    {"left_joint": "left_wrist", "right_joint": "right_wrist", "frames": [170]}]}}
        result = pair(sequence, timeline, events=[event], video_id="v")
        self.assertEqual(result["candidate_quality_status"], "suspect")
        self.assertEqual(result["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"], {"right_ankle": [171]})
        self.assertEqual(result["local_joint_diagnostics"]["left_right_swap_candidate_joint_pairs"], [])
        self.assertNotIn("left_right_swap_candidates_present", result["risks"])
        event["track_diagnostics"]["keypoint_jump_candidate_frames_by_joint"] = {"right_ankle": [173]}
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["candidate_quality_status"], "unvalidated")
        event["track_diagnostics"]["left_right_swap_candidate_joint_pairs"][0]["frames"] = [171]
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["candidate_quality_status"], "suspect")

    def test_unmatched_parent_cannot_supply_confirmed_identity_failure(self):
        sequence, timeline = crossing_fixture()
        event = {"video_id": "v", "person_track_id": 1, "start_ms": 5300, "end_ms": 6600,
                 "quality_flags": ["confirmed_id_switch"], "track_diagnostics": {"source_track_ids": [9]}}
        result = pair(sequence, timeline, events=[event], video_id="v")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["source_track_matched_parent_count"], 0)
        self.assertEqual(result["parent_quality_flags"], ["confirmed_id_switch"])

    def test_nested_parent_identity_conflicts_cannot_veto_or_degrade_pair(self):
        sequence, timeline = crossing_fixture(model=True)
        baseline = pair(sequence, timeline)
        parent = parent_with_source_window(sequence)
        correct = pair(sequence, timeline, events=[parent], video_id="v")
        self.assertEqual(correct["reason"], "parent_confirmed_identity_switch")
        self.assertTrue(correct["parent_events"][0]["source_association"]["pair_source_bindings_verified"])
        for key, value in (("video_sha256", "b" * 64), ("selection_epoch", 1),
                           ("camera_reference_epoch", 1), ("person_track_id", 9)):
            for location in ("window_and_bindings", "window_only", "binding_only"):
                with self.subTest(key=key, location=location):
                    event = copy.deepcopy(parent)
                    if location != "binding_only":
                        event["source_window"][key] = value
                    if location != "window_only":
                        for row in event["source_window"]["source_bindings"]:
                            row[key] = value
                    event["track_diagnostics"].update({
                        "keypoint_jump_candidate_frames_by_joint": {"right_ankle": [171]},
                        "left_right_swap_candidate_joint_pairs": [
                            {"left_joint": "left_hip", "right_joint": "right_hip", "frames": [171]}]})
                    original = copy.deepcopy(event)
                    result = pair(sequence, timeline, events=[event], video_id="v")
                    self.assertEqual(result["status"], "candidate")
                    self.assertEqual(result["candidate_quality_status"], "unvalidated")
                    np.testing.assert_array_equal(result["signed_ankle_order_px"], baseline["signed_ankle_order_px"])
                    self.assertEqual(result["source_track_matched_parent_count"], 0)
                    association = result["parent_events"][0]["source_association"]
                    self.assertEqual(association["status"], "mismatch")
                    self.assertFalse(association["eligible_for_gating"])
                    self.assertFalse(association["pair_source_bindings_verified"])
                    self.assertTrue(association["mismatch_reasons"])
                    self.assertEqual(result["parent_quality_flags"], ["confirmed_id_switch"])
                    self.assertEqual(event, original)

    def test_parent_endpoint_frame_and_native_clock_conflicts_do_not_fall_back_to_track(self):
        sequence, timeline = crossing_fixture()
        parent = parent_with_source_window(sequence)
        changes = {"frame_index": 270, "processed_index": 270, "pts": 3401, "time_base_num": 2,
                   "time_base_den": 601, "timestamp_ms": 5700, "source_timestamp_ms": 5700,
                   "video_timestamp_ms": 5700, "primary_player_id": 2}
        for key, value in changes.items():
            with self.subTest(binding_field=key):
                event = copy.deepcopy(parent)
                event["source_window"]["source_bindings"][0][key] = value
                result = pair(sequence, timeline, events=[event], video_id="v")
                self.assertEqual(result["status"], "candidate")
                self.assertEqual(result["parent_events"][0]["source_track_association"], "mismatch")
        for key, value in (("source_frames", [270, 171]), ("video_timestamps_ms", [5668, 5700]),
                           ("source_timestamps_ms", [5668, 5700])):
            with self.subTest(window_field=key):
                event = copy.deepcopy(parent)
                event["source_window"][key] = value
                self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["status"], "candidate")
        event = copy.deepcopy(parent)
        event["source_bindings"] = copy.deepcopy(event["source_window"]["source_bindings"])
        event["source_bindings"][0]["selection_epoch"] = 1
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["status"], "candidate")
        event = copy.deepcopy(parent)
        event["source_window"]["source_bindings"].append(copy.deepcopy(event["source_window"]["source_bindings"][0]))
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["status"], "candidate")
        event = copy.deepcopy(parent)
        event["track_diagnostics"]["model_estimate_geometry"] = {"frame_evidence": [{
            "frame_index": 170, "processed_index": 170, "timestamp_ms": 5667, "person_track_id": 8,
            "source_binding": {**copy.deepcopy(event["source_window"]["source_bindings"][0]), "selection_epoch": 1}}]}
        self.assertEqual(pair(sequence, timeline, events=[event], video_id="v")["status"], "candidate")

    def test_parent_source_and_video_clocks_remain_distinct_with_offset(self):
        sequence, timeline = crossing_fixture()
        parent = parent_with_source_window(sequence)
        sequence.timestamp_ms[:] -= 5000
        for binding, row in zip(sequence.source_time_bindings.values(), timeline):
            binding["video_timestamp_ms"] -= 5000
            row["timestamp_ms"] -= 5000
        parent["start_ms"], parent["end_ms"] = 667, 700
        parent["source_window"].update(start_ms=667, end_ms=700, video_timestamps_ms=[667, 700])
        correct = pair(sequence, timeline, events=[parent], video_id="v")
        self.assertEqual(correct["reason"], "parent_confirmed_identity_switch")
        self.assertTrue(correct["parent_events"][0]["source_association"]["pair_source_bindings_verified"])
        parent["source_window"]["source_bindings"][0]["timestamp_ms"] = 667
        self.assertEqual(pair(sequence, timeline, events=[parent], video_id="v")["status"], "candidate")

    def test_parent_strong_weak_and_unknown_proof_keep_local_risk_scope_explicit(self):
        sequence, timeline = crossing_fixture()
        parent = parent_with_source_window(sequence)
        parent["quality_flags"] = ["keypoint_jump_candidates_present", "left_right_swap_candidates_present"]
        parent["track_diagnostics"].update({
            "keypoint_jump_candidate_frames_by_joint": {"right_ankle": [166, 167, 171, 173], "left_elbow": [170]},
            "left_right_swap_candidate_joint_pairs": [
                {"left_joint": "left_hip", "right_joint": "right_hip", "frames": [171]},
                {"left_joint": "left_wrist", "right_joint": "right_wrist", "frames": [171]}]})
        expected_jumps = {"right_ankle": [171]}
        expected_swaps = [{"left_joint": "left_hip", "right_joint": "right_hip", "frames": [171]}]
        for proof in ("complete", "partial", "absent"):
            with self.subTest(proof=proof):
                event = copy.deepcopy(parent)
                if proof == "partial":
                    del event["source_window"]["source_bindings"][0]["pts"]
                elif proof == "absent":
                    del event["source_window"]
                result = pair(sequence, timeline, events=[event], video_id="v")
                self.assertEqual(result["candidate_quality_status"], "suspect")
                association = result["parent_events"][0]["source_association"]
                self.assertEqual(association["status"], "pair_source_bindings_verified" if proof == "complete" else "weak_source_track_association")
                self.assertEqual(association["pair_source_bindings_verified"], proof == "complete")
                self.assertEqual(result["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"], expected_jumps)
                self.assertEqual(result["local_joint_diagnostics"]["left_right_swap_candidate_joint_pairs"], expected_swaps)
                self.assertFalse(result["local_joint_diagnostics"]["empty_lists_prove_no_anomaly"])
        unknown = copy.deepcopy(parent)
        del unknown["source_window"]
        del unknown["track_diagnostics"]["source_track_ids"]
        unknown["quality_flags"].append("confirmed_id_switch")
        result = pair(sequence, timeline, events=[unknown], video_id="v")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate_quality_status"], "unvalidated")
        self.assertEqual(result["parent_events"][0]["source_track_association"], "unverified")

    def test_parent_diagnostic_processed_indexes_are_mapped_to_original_frames(self):
        sequence, timeline = crossing_fixture()
        for index, frame in enumerate((170, 171)):
            sequence.source_time_bindings[frame]["processed_index"] = 20 + index
            timeline[index]["processed_index"] = 20 + index
        event = {"video_id": "v", "person_track_id": 1, "start_ms": 5300, "end_ms": 6600,
            "track_diagnostics": {"source_track_ids": [8],
                "keypoint_jump_candidate_frames_by_joint": {"right_ankle": [21], "left_hip": [171]}}}
        result = pair(sequence, timeline, events=[event], video_id="v")
        self.assertEqual(result["candidate_quality_status"], "suspect")
        self.assertEqual(result["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"], {"right_ankle": [171]})
        self.assertEqual(result["local_joint_diagnostics"]["input_frame_index_domain"], "processed_index")

    def test_parent_proof_is_bounded_copied_and_hard_failure_scans_untruncated_flags(self):
        sequence, timeline = crossing_fixture()
        event = {"video_id": "v", "person_track_id": 1, "start_ms": 5300, "end_ms": 6600,
            "quality_flags": [f"a{i:03d}" + "x" * 1000 for i in range(100)],
            "key_phases_ms": {"first_step_slowdown_proxy_ms": 6167, "huge": [1] * 10000},
            "track_diagnostics": {"source_track_ids": [8]}}
        result = pair(sequence, timeline, events=[event] * 6, video_id="v")
        self.assertEqual(len(result["parent_events"]), 4)
        self.assertEqual(len(result["parent_quality_flags"]), 32)
        self.assertTrue(all(len(value) <= 160 for value in result["parent_quality_flags"]))
        self.assertTrue(result["parent_quality_flags_truncated"])
        self.assertNotIn("huge", result["parent_events"][0]["key_phases_ms"])
        result["parent_events"][0]["key_phases_ms"]["first_step_slowdown_proxy_ms"] = 0
        self.assertEqual(event["key_phases_ms"]["first_step_slowdown_proxy_ms"], 6167)
        event["quality_flags"].append("confirmed_id_switch")
        self.assertEqual(pair(sequence, timeline, events=[event] * 6, video_id="v")["reason"], "parent_confirmed_identity_switch")

    def test_bounded_output_complete_counts_and_no_input_mutation(self):
        sequence, timeline = crossing_fixture(20)
        before = {name: values.copy() for name, values in sequence.keypoints_xy.items()}
        before_timeline = copy.deepcopy(timeline)
        result = compute_foot_crossing_candidates(sequence, primary_timeline=timeline, max_candidates=3, max_exclusions=1)
        self.assertEqual(result["counts"]["candidates"], 19)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertTrue(result["output_limits"]["candidates_truncated"])
        self.assertEqual([item["source_frames"][0] for item in result["candidates"]], [170, 171, 172])
        json.dumps(result, allow_nan=False)
        for name, values in before.items():
            np.testing.assert_array_equal(sequence.keypoints_xy[name], values)
        self.assertEqual(timeline, before_timeline)
        with self.assertRaises(ValueError):
            compute_foot_crossing_candidates(sequence, primary_timeline=timeline, max_candidates=129)


if __name__ == "__main__":
    unittest.main()
