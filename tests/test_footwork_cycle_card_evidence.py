from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.source_alignment import JOINTS, clear_source_aligned_feature_cache
from rallymate_scoring.footwork_cycle_card_evidence import compute_footwork_cycle_card_evidence


def synthetic_cycle(code="FS04", cross=False):
    """Controlled observation fixture, explicitly not a real-video benchmark."""
    times = np.arange(0, 841, 40)
    n = len(times)
    lead = np.clip((times - 160) / 240, 0, 1)
    follow = np.clip((times - 400) / 240, 0, 1)
    hip = .5 + .05 * (lead + follow)
    points = {}
    for side, sign in (("left", -1), ("right", 1)):
        points[side + "_hip"] = np.column_stack((hip + sign * .08, np.full(n, .45)))
        points[side + "_shoulder"] = np.column_stack((hip + sign * .12, np.full(n, .20)))
        points[side + "_knee"] = np.column_stack((hip + sign * .12, np.full(n, .67)))
    points["left_ankle"] = np.column_stack((.35 + .1 * follow, np.full(n, .9)))
    points["right_ankle"] = np.column_stack((.65 + .1 * lead, np.full(n, .9)))
    if cross:
        # Left foot travels through a sampled zero gap, with sustained order
        # before/after and 160ms observed stillness at both ends of its motion.
        points["left_ankle"][:, 0] = .35 + .5 * np.clip((times - 160) / 480, 0, 1)
        points["right_ankle"][:, 0] = .65
    sequence = PoseSequence(times, np.arange(n), points, {name: np.full(n, .95) for name in JOINTS},
        camera_motion_status=("stationary",) * n, camera_reference_epochs=(0,) * n,
        frame_dimensions_px=np.tile([1280, 720], (n, 1)),
        frame_transforms_to_reference=np.tile([[1., 0., 0.], [0., 1., 0.]], (n, 1, 1)),
        source_time_bindings={i: {"frame_index": i, "processed_index": i, "video_sha256": "a" * 64,
            "pts": int(t), "time_base_num": 1, "time_base_den": 1000, "source_timestamp_ms": int(t),
            "video_timestamp_ms": int(t), "timestamp_source": "decoder_pts", "primary_player_id": 1,
            "person_track_id": 2, "selection_epoch": 0, "camera_reference_epoch": 0} for i, t in enumerate(times)},
        coordinate_metadata={"contract_version": "isotropic-frame-long-edge-v1.1.0",
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge",
            "camera_compensation": {"applied_source_frames": list(range(n))}})
    event = {"video_id": "controlled-cycle", "event_id": "cycle-1", "event_code": code,
        "person_track_id": 1, "start_ms": 0, "end_ms": 840, "quality_flags": [],
        "track_diagnostics": {"primary_identity_ambiguous": False,
            "source_track_switch_candidate_count": 0, "source_track_ids": [2]}}
    timeline = [{"source_frame_index": i, "timestamp_ms": int(t), "primary_player_id": 1,
        "source_track_id": 2, "selection_status": "selected", "identity_ambiguous": False} for i, t in enumerate(times)]
    binding = {"method": "manual_anchor", "version": "controlled-test-v1", "evidence_reference": "synthetic_trajectory_not_video",
        "event_id": "cycle-1", "video_id": "controlled-cycle", "video_sha256": "a" * 64,
        "primary_player_id": 1, "start_source_frame": 0, "end_source_frame": n-1}
    return sequence, event, timeline, binding


def compute(fixture):
    sequence, event, timeline, binding = fixture
    return compute_footwork_cycle_card_evidence(sequence, event, events=[event],
        primary_timeline=timeline, cycle_binding=binding)


def card(report, number):
    return report["cards"][number-1]


def real_fixture(derive_bindings=True):
    path = Path(__file__).parent / "fixtures/footwork-cycle-real-usta-v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    records, timeline = deepcopy(data["records"]), deepcopy(data["primary_timeline"])
    if derive_bindings:
        for record, selected, source, epoch in zip(records, timeline, data["decoder_timeline"], data["derived_epoch_timeline"]):
            assert record["frame"]["index"] == source["frame_index"] == epoch["source_frame_index"]
            record["frame"].update({k: source[k] for k in ("pts", "time_base_num", "time_base_den", "video_sha256")})
            record["frame"]["source_timestamp_ms"] = source["timestamp_ms"]
            selected["selection_epoch"] = epoch["selection_epoch"]
    sequence = pose_sequence_from_records(records, timeline)
    return sequence, data["events"][0], timeline, data["bindings"][0]


class FootworkCycleCardEvidenceTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        self.addCleanup(clear_source_aligned_feature_cache)

    def test_complete_lead_follow_cycle_has_original_series_and_catalogue_values(self):
        result = compute(synthetic_cycle())
        row = card(result, 4)
        self.assertEqual(row["status"], "measured_visual_proxy")
        self.assertEqual(row["measurements"]["ankle_order_exchange_count"], 0)
        self.assertAlmostEqual(row["measurements"]["ankle_width_contraction_torso"], .4)
        pair = row["evidence"]["series"]["sequential_follow_pairs"][0]
        self.assertEqual((pair["lead_side"], pair["follow_side"], pair["onset_delay_ms"]), ("right", "left", 240))
        self.assertTrue(all(condition["passed"] for condition in row["conditions"]))
        self.assertIsNone(row["original_grade"])
        self.assertIsNone(row["score"])
        self.assertEqual(card(result, 1)["reason"], "ball_direction_and_target_evidence_required")
        json.dumps(result, allow_nan=False)

    def test_continuous_crossing_requires_observed_interior(self):
        row = card(compute(synthetic_cycle("FS03", cross=True)), 3)
        self.assertEqual(row["status"], "measured_visual_proxy")
        self.assertEqual(row["measurements"]["ankle_order_exchange_count"], 1)
        self.assertTrue(row["evidence"]["series"]["ankle_order_exchanges"][0]["near_zero_source_frames"])

    def test_static_crossed_pose_is_not_a_step(self):
        fixture = synthetic_cycle("FS03", cross=True)
        for points in fixture[0].keypoints_xy.values():
            points[:] = points[-1]
        row = card(compute(fixture), 3)
        self.assertEqual(row["reason"], "complete_visual_motion_cycle_not_observed")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_two_endpoint_crossing_with_missing_source_frames_is_not_interpolated(self):
        sequence, event, timeline, binding = synthetic_cycle("FS03", cross=True)
        keep = np.arange(len(sequence.source_frames)) != 10
        sequence = replace(sequence, timestamp_ms=sequence.timestamp_ms[keep], source_frames=sequence.source_frames[keep],
            keypoints_xy={k:v[keep] for k,v in sequence.keypoints_xy.items()},
            confidence={k:v[keep] for k,v in sequence.confidence.items()},
            camera_motion_status=tuple(v for i,v in enumerate(sequence.camera_motion_status) if keep[i]),
            camera_reference_epochs=tuple(v for i,v in enumerate(sequence.camera_reference_epochs) if keep[i]),
            frame_dimensions_px=sequence.frame_dimensions_px[keep],
            frame_transforms_to_reference=sequence.frame_transforms_to_reference[keep],
            source_time_bindings={k:v for k,v in sequence.source_time_bindings.items() if k != 10})
        row = card(compute((sequence, event, timeline, binding)), 3)
        self.assertEqual(row["status"], "needs_review")
        self.assertIsNone(row["score"])
        self.assertFalse(row["evidence"]["interpolation_used"])

    def test_clipped_motion_does_not_manufacture_a_complete_cycle(self):
        sequence, event, timeline, binding = synthetic_cycle()
        event["start_ms"] = 200
        binding["start_source_frame"] = 5
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "sequential_lead_follow_cycle_not_observed")

    def test_simultaneous_feet_translation_is_not_lead_follow(self):
        sequence, event, timeline, binding = synthetic_cycle()
        sequence.keypoints_xy["left_ankle"][:, 0] = sequence.keypoints_xy["right_ankle"][:, 0] - .3
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "sequential_lead_follow_cycle_not_observed")

    def test_single_following_foot_without_leading_step_is_incomplete(self):
        sequence, event, timeline, binding = synthetic_cycle()
        sequence.keypoints_xy["right_ankle"][:, 0] = .75
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "sequential_lead_follow_cycle_not_observed")

    def test_partial_joint_gap_keeps_denominator_and_null_conditions(self):
        sequence, event, timeline, binding = synthetic_cycle()
        sequence.confidence["left_ankle"][10] = .1
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "required_joint_gap_inside_continuous_window")
        self.assertEqual(row["evidence"]["coverage"]["total_frame_count"], 22)
        self.assertEqual(row["evidence"]["coverage"]["valid_frame_count"], 21)
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_clock_identity_camera_and_video_bindings_fail_closed(self):
        mutations = [lambda s,e,t,b: s.source_time_bindings.pop(8),
            lambda s,e,t,b: t[8].update(identity_ambiguous=True),
            lambda s,e,t,b: s.frame_transforms_to_reference.__setitem__(8, np.nan),
            lambda s,e,t,b: b.update(video_sha256="b"*64)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = synthetic_cycle()
                mutation(*fixture)
                row = card(compute(fixture), 4)
                self.assertEqual(row["status"], "needs_review")
                self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_caller_complete_bit_does_not_replace_source_binding_or_trajectory(self):
        sequence, event, timeline, binding = synthetic_cycle()
        row = card(compute((sequence, event, timeline, {"complete":True,"passed":True})), 4)
        self.assertEqual(row["reason"], "automatic_cycle_detector_not_registered")

    def test_arbitrary_detector_version_is_not_registration(self):
        sequence, event, timeline, binding = synthetic_cycle()
        binding.update(method="versioned_detector", version="claimed-v999")
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "automatic_cycle_detector_not_registered")
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))

    def test_short_window_does_not_reduce_endpoint_median_to_one_frame(self):
        sequence, event, timeline, binding = synthetic_cycle()
        event["end_ms"] = 200
        binding["end_source_frame"] = 5
        row = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(row["reason"], "continuous_endpoint_window_missing")
        self.assertEqual(row["measurements"], {})

    def test_joint_diagnostics_are_local_and_independent_of_legacy_policy(self):
        sequence, event, timeline, binding = synthetic_cycle()
        event["quality_flags"] = ["keypoint_jump_candidates_present"]
        event["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["left_knee"]
        report = compute((sequence, event, timeline, binding))
        self.assertEqual(card(report, 4)["status"], "measured_visual_proxy")
        event["track_diagnostics"]["keypoint_jump_candidate_joints"] = ["left_ankle"]
        report = compute((sequence, event, timeline, binding))
        self.assertEqual(card(report, 4)["reason"], "cycle_required_evidence_requires_review")
        self.assertEqual(card(report, 4)["evidence"]["event_quality"]["raw_input_quality_flags"], event["quality_flags"])

    def test_exact_decoder_clock_drives_motion_not_rounded_export_ms(self):
        sequence, event, timeline, binding = synthetic_cycle()
        baseline = card(compute((sequence, event, timeline, binding)), 4)
        for row in sequence.source_time_bindings.values():
            row["pts"] *= 2
            row["time_base_den"] *= 2
        changed = card(compute((sequence, event, timeline, binding)), 4)
        self.assertEqual(baseline["measurements"], changed["measurements"])

    def test_real_original_inputs_keep_missing_time_binding_unscored(self):
        row = card(compute(real_fixture(False)), 3)
        self.assertEqual(row["reason"], "source_clock_subject_binding_mismatch")
        self.assertEqual(row["measurements"], {})
        self.assertIsNone(row["score"])

    def test_real_source_subset_produces_values_without_inventing_a_complete_cycle(self):
        row = card(compute(real_fixture()), 3)
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("complete_visual_motion_cycle_not_observed", row["reason_codes"])
        self.assertAlmostEqual(row["measurements"]["hip_displacement_torso"], 2.81227098454173, places=8)
        self.assertAlmostEqual(row["measurements"]["ankle_max_displacement_torso"], 3.18877603141948, places=8)
        self.assertEqual(row["evidence"]["coverage"]["total_frame_count"], 36)
        self.assertEqual(len(row["evidence"]["series"]["pair_source_frames"]), 35)
        self.assertEqual(row["evidence"]["time_basis"], "decoder_pts")
        bindings = row["evidence"]["source_frame_bindings"]
        self.assertEqual([b["frame_index"] for b in bindings], row["evidence"]["source_frames"])
        self.assertEqual({b["selection_epoch"] for b in bindings}, {1})
        self.assertEqual({b["camera_reference_epoch"] for b in bindings}, {0})
        self.assertEqual({b["person_track_id"] for b in bindings}, {2})
        for binding, exact in zip(bindings, row["evidence"]["source_timestamps_ms"]):
            self.assertAlmostEqual(binding["pts"] * binding["time_base_num"] * 1000 / binding["time_base_den"], exact)
        self.assertIsNone(row["measurements"]["ankle_order_exchange_count"])
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
        json.dumps(row, allow_nan=False)

    def test_same_window_near_zero_order_does_not_replace_complete_step(self):
        fixture = synthetic_cycle("FS03", cross=True)
        # Preserve a complete right-foot motion earlier, but clip the crossing
        # left foot's terminal quiet interval. These disjoint facts cannot join.
        fixture[0].keypoints_xy["right_ankle"][:, 0] = .65 + .1 * np.clip((fixture[0].timestamp_ms-160)/160,0,1)
        fixture[0].keypoints_xy["left_ankle"][:, 0] = .35 + .5 * np.clip((fixture[0].timestamp_ms-400)/400,0,1)
        row = card(compute(fixture), 3)
        self.assertGreater(row["evidence"]["series"]["complete_visual_motion_cycle_count"], 0)
        self.assertIsNone(row["measurements"]["ankle_order_exchange_count"])
        self.assertEqual(row["status"], "needs_review")

    def test_unresolved_order_at_either_boundary_is_not_zero_exchanges(self):
        for affected in (slice(0,4), slice(-4,None)):
            with self.subTest(affected=affected):
                fixture = synthetic_cycle()
                left, right = (fixture[0].keypoints_xy[side + "_ankle"] for side in ("left", "right"))
                left[affected, 0] = right[affected, 0] - .01
                row = card(compute(fixture), 3)
                self.assertIsNone(row["measurements"]["ankle_order_exchange_count"])
                self.assertEqual(row["status"], "needs_review")
                self.assertTrue(any(r["reason"] == "ankle_order_not_stable_at_both_window_boundaries"
                    for r in row["evidence"]["series"]["unresolved_order_exchanges"]))

    def test_brief_reverse_order_cannot_be_filtered_into_no_exchange(self):
        fixture = synthetic_cycle()
        fixture[0].keypoints_xy["left_ankle"][11,0] = .85
        row = card(compute(fixture), 3)
        self.assertIsNone(row["measurements"]["ankle_order_exchange_count"])
        self.assertTrue(any(r["reason"] == "short_order_run_cannot_be_discarded_as_no_exchange"
            for r in row["evidence"]["series"]["unresolved_order_exchanges"]))

    def test_interior_unresolved_overlap_is_not_zero_exchanges(self):
        fixture = synthetic_cycle()
        left, right = (fixture[0].keypoints_xy[side + "_ankle"] for side in ("left", "right"))
        left[9:14,0] = right[9:14,0] - .01
        row = card(compute(fixture), 3)
        self.assertIsNone(row["measurements"]["ankle_order_exchange_count"])
        self.assertTrue(any(r["reason"] == "deadband_not_bracketed_by_resolved_opposite_orders"
            for r in row["evidence"]["series"]["unresolved_order_exchanges"]))


if __name__ == "__main__":
    unittest.main()
