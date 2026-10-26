from __future__ import annotations

import copy
from dataclasses import replace
import json
import unittest

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import (
    HEAD, JOINTS, clear_source_aligned_feature_cache, compute_source_aligned_features,
    prepare_source_aligned_context,
)


NAMES = ("preload_hip_reference_dy_px", "preload_hip_reference_dy_torso")


def fixture(*, model=True, step_ticks=1200):
    count = 11
    pts = np.arange(count) * step_ticks + 2250000
    times = np.rint((pts - pts[0]) / 30).astype(int)
    depth = np.array([.01, 0, .01, .025, .04, .025, .01, 0, 0, 0, 0])
    xy = {}
    for side, sign in (("left", -1), ("right", 1)):
        xy[side + "_hip"] = np.column_stack([np.full(count, .5 + sign * .1), .45 + depth])
        xy[side + "_shoulder"] = np.column_stack([np.full(count, .5 + sign * .2), .20 + depth])
        xy[side + "_knee"] = np.column_stack([.5 + sign * (.2 + depth), (.45 + depth + .9) / 2])
        xy[side + "_ankle"] = np.tile([.5 + sign * .3, .9], (count, 1))
    for name, x in (("nose", .5), ("left_ear", .46), ("right_ear", .54)):
        xy[name] = np.tile([x, .1], (count, 1))
    bindings = tuple({"video_sha256": "a" * 64, "person_track_id": 1, "selection_epoch": 0,
        "camera_reference_epoch": 0, "frame_index": i, "processed_index": i,
        "pts": int(p), "time_base_num": 1, "time_base_den": 30000,
        "timestamp_ms": round(p / 30)} for i, p in enumerate(pts))
    metadata = {"contract_version": "isotropic-frame-long-edge-v1.1.0",
        "coordinate_system": "camera_reference_xy_divided_by_source_long_edge",
        "camera_compensation": {"applied_source_frames": list(range(count))}}
    if not model:
        metadata["timestamp_source"] = "decoder_pts"
    sequence = PoseSequence(times, np.arange(count), xy,
        {name: np.full(count, np.nan if model else .95) for name in xy},
        frame_dimensions_px=np.tile([1000., 1000.], (count, 1)), coordinate_metadata=metadata,
        camera_motion_status=("stationary",) * count, camera_reference_epochs=(0,) * count,
        frame_transforms_to_reference=np.tile(np.eye(2, 3), (count, 1, 1)),
        model_estimate_masks={name: np.ones(count, dtype=bool) for name in xy} if model else {},
        model_estimate_bindings=bindings if model else None)
    event = {"video_id": "video-a", "event_id": "split", "person_track_id": 1, "event_code": "FS01",
        "start_ms": int(times[0]), "end_ms": int(times[-1]), "quality_flags": [],
        "key_phases_ms": {"preload_ms": int(times[0]), "takeoff_proxy_ms": int(times[8])},
        "track_diagnostics": {"primary_identity_ambiguous": False, "source_track_switch_candidate_count": 0,
                              "source_track_ids": [1]}}
    timeline = [{"source_frame_index": i, "timestamp_ms": int(t), "primary_player_id": 1,
                 "source_track_id": 1, "selection_status": "selected", "identity_ambiguous": False}
                for i, t in enumerate(times)]
    return sequence, [event], timeline


def run(sequence, events, timeline):
    clear_source_aligned_feature_cache()
    context = prepare_source_aligned_context(sequence, events, primary_timeline=timeline)
    result = compute_source_aligned_features(sequence, events[0], events=events, context=context)
    return result, {r["feature_name"]: r for i in result["indicators"] if i["indicator_id"] == "FS01-M02"
                    for r in i["source_measurements"]}


class SourcePreloadTranslationTests(unittest.TestCase):
    def tearDown(self):
        clear_source_aligned_feature_cache()

    def test_model_geometry_has_fixed_reference_pixels_and_independent_torso_units(self):
        sequence, events, timeline = fixture()
        original = copy.deepcopy(events)
        result, rows = run(sequence, events, timeline)
        px, torso = (rows[name] for name in NAMES)
        self.assertAlmostEqual(px["value"], 40.)
        self.assertAlmostEqual(torso["value"], .16)
        self.assertEqual((px["unit"], torso["unit"]), ("px", "torso"))
        self.assertEqual(px["source_frames"], [1, 2, 3, 4])
        self.assertEqual(px["window_ms"], {"start_ms": 40., "end_ms": 160.})
        self.assertEqual(px["source_window"]["source_timestamps_ms"], [75040., 75080., 75120., 75160.])
        self.assertEqual(px["evidence"]["fixed_torso_scale_px"], 250.)
        self.assertEqual(px["source_ref"], "CARD-FS:word/document.xml:P0115")
        self.assertFalse(px["measurement_provenance"]["visibility_verified"])
        self.assertFalse(px["evidence"]["source_clause_complete"])
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["technical_score_0_to_100"])
        self.assertEqual(events, original)
        self.assertNotIn("CARD-GS", json.dumps(px))
        json.dumps(result, allow_nan=False)

    def test_one_median_scale_is_used_not_per_frame_recentering(self):
        sequence, events, timeline = fixture()
        lengths = [.20, .25, .30, .40]
        for side in ("left", "right"):
            sequence.keypoints_xy[side + "_shoulder"][1:5, 1] = sequence.keypoints_xy[side + "_hip"][1:5, 1] - lengths
        _, rows = run(sequence, events, timeline)
        self.assertAlmostEqual(rows[NAMES[0]]["value"], 40.)
        self.assertAlmostEqual(rows[NAMES[1]]["value"], 40. / 275.)

    def test_already_compensated_coordinates_are_not_transformed_twice(self):
        sequence, events, timeline = fixture()
        sequence.frame_transforms_to_reference[:, 1, 2] = -np.arange(11) * 5.
        _, rows = run(sequence, events, timeline)
        self.assertAlmostEqual(rows[NAMES[0]]["value"], 40.)

    def test_true_pts_duration_controls_80ms_gate_not_rounded_playback(self):
        for ticks, measured in ((800, True), (798, False), (600, False)):
            sequence, events, timeline = fixture(step_ticks=ticks)
            _, rows = run(sequence, events, timeline)
            for name in NAMES:
                self.assertEqual(rows[name]["valid"], measured, (ticks, name))
                if not measured:
                    self.assertEqual(rows[name]["reason"], "reference_translation_window_below_80_ms")
                    self.assertIsNone(rows[name]["value"])

    def test_missing_joint_does_not_join_endpoints_across_a_hole(self):
        sequence, events, timeline = fixture()
        sequence.model_estimate_masks["left_shoulder"][2] = False
        _, rows = run(sequence, events, timeline)
        self.assertGreaterEqual(rows[NAMES[0]]["coverage"]["fraction"], .7)
        self.assertEqual(rows[NAMES[0]]["reason"], "reference_translation_required_joints_not_continuous")
        self.assertIsNone(rows[NAMES[0]]["value"])

    def test_pixel_contract_transform_dimensions_and_scale_are_required(self):
        for defect in ("contract", "transform", "applied", "dimensions", "scale"):
            sequence, events, timeline = fixture()
            if defect == "contract": sequence.coordinate_metadata.pop("coordinate_system")
            elif defect == "transform": sequence.frame_transforms_to_reference[2] = [[2, 1, 0], [0, 1, 0]]
            elif defect == "applied": sequence.coordinate_metadata["camera_compensation"]["applied_source_frames"].remove(2)
            elif defect == "dimensions": sequence.frame_dimensions_px[2] = [2000, 1000]
            else:
                for side in ("left", "right"):
                    sequence.keypoints_xy[side + "_shoulder"][2, 1] = sequence.keypoints_xy[side + "_hip"][2, 1] - .005
            _, rows = run(sequence, events, timeline)
            self.assertTrue(all(rows[name]["value"] is None for name in NAMES), defect)

    def test_identity_epoch_camera_and_frame_breaks_are_not_joined(self):
        for defect in ("identity", "selection", "camera", "frame", "pts"):
            sequence, events, timeline = fixture()
            if defect == "identity": timeline[2]["source_track_id"] = 2
            elif defect == "selection": sequence.model_estimate_bindings[2]["selection_epoch"] = 1
            elif defect == "camera": sequence = replace(sequence, camera_reference_epochs=(0, 0, 1) + (0,) * 8)
            elif defect == "frame": sequence.source_frames[2] = 99
            else: sequence.model_estimate_bindings[2]["pts"] += 10000
            _, rows = run(sequence, events, timeline)
            self.assertTrue(all(rows[name]["value"] is None for name in NAMES), defect)

    def test_no_candidate_turn_does_not_substitute_whole_event(self):
        sequence, events, timeline = fixture()
        events[0]["key_phases_ms"].pop("takeoff_proxy_ms")
        _, rows = run(sequence, events, timeline)
        self.assertTrue(all(rows[name]["value"] is None for name in NAMES))

    def test_legacy_values_are_unchanged_by_new_reference_requirements(self):
        sequence, events, timeline = fixture()
        _, first = run(sequence, events, timeline)
        sequence = replace(sequence, frame_dimensions_px=None, frame_transforms_to_reference=None)
        _, second = run(sequence, events, timeline)
        self.assertEqual({k: v for k, v in first.items() if k not in NAMES},
                         {k: v for k, v in second.items() if k not in NAMES})
        self.assertAlmostEqual(first["preload_hip_height_drop_body"]["value"], .16)
        self.assertEqual(first["preload_hip_height_drop_body"]["unit"], "body")

    def test_confidence_path_cannot_claim_pts_from_global_label_or_rounded_ms(self):
        sequence, events, timeline = fixture(model=False)
        _, rows = run(sequence, events, timeline)
        self.assertEqual(rows[NAMES[0]]["reason"], "decoder_pts_provenance_unavailable_for_reference_translation")
        self.assertIsNone(rows[NAMES[0]]["value"])
        sequence.coordinate_metadata.pop("timestamp_source")
        _, rows = run(sequence, events, timeline)
        self.assertEqual(rows[NAMES[0]]["reason"], "decoder_pts_provenance_unavailable_for_reference_translation")
        self.assertIsNone(rows[NAMES[0]]["value"])


if __name__ == "__main__":
    unittest.main()
