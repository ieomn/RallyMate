"""Public tests use analytic geometry only; real source snippets stay private."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import math
import unittest

import numpy as np

from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION
from rallymate_features.hip_path_ratio import compute_hip_path_ratio, SAMPLING_PROTOCOL
from rallymate_features.schemas import PoseSequence


def fixture(centers=((300., 400.), (330., 400.), (330., 440.)), *, model=True, pts=None):
    centers = np.asarray(centers, dtype=float)
    count = len(centers)
    frames = np.arange(100, 100 + count)
    pts = pts if pts is not None else [2000 + 20 * i for i in range(count)]
    times = np.asarray([round(p * 1000 / 600) for p in pts])
    xy = {"left_hip": (centers - [10, 0]) / 1000, "right_hip": (centers + [10, 0]) / 1000}
    bindings = {int(frame): dict(frame_index=int(frame), processed_index=int(frame), video_sha256="a" * 64,
        pts=int(p), time_base_num=1, time_base_den=600, source_timestamp_ms=round(p * 1000 / 600),
        video_timestamp_ms=int(time), timestamp_source="decoder_pts", primary_player_id=1,
        person_track_id=8, selection_epoch=2, camera_reference_epoch=3)
        for frame, p, time in zip(frames, pts, times)}
    timeline = [dict(source_frame_index=int(frame), processed_index=int(frame), timestamp_ms=int(time),
        primary_player_id=1, source_track_id=8, selection_status="selected", identity_ambiguous=False,
        selection_epoch=2, camera_reference_epoch=3) for frame, time in zip(frames, times)]
    model_keys = ("frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den",
                  "person_track_id", "selection_epoch", "camera_reference_epoch")
    model_bindings = tuple({**{key: binding[key] for key in model_keys}, "timestamp_ms": binding["source_timestamp_ms"]}
        for binding in bindings.values()) if model else None
    sequence = PoseSequence(times, frames, xy, {name: np.full(count, np.nan if model else .9) for name in xy},
        frame_dimensions_px=np.tile([1000, 800], (count, 1)),
        coordinate_metadata={"contract_version": COORDINATE_CONTRACT_VERSION,
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge", "axis_units": "same_for_x_and_y",
            "camera_compensation": {"applied_source_frames": frames.tolist()}},
        camera_motion_status=("stationary",) * count, camera_reference_epochs=(3,) * count,
        frame_transforms_to_reference=np.tile([[1., 0., 0.], [0., 1., 0.]], (count, 1, 1)),
        source_time_bindings=bindings, model_estimate_bindings=model_bindings,
        model_estimate_masks={name: np.ones(count, dtype=bool) for name in xy} if model else {})
    return sequence, timeline


def measure(sequence, timeline, **kwargs):
    return compute_hip_path_ratio(sequence, primary_timeline=timeline,
        **dict(start_source_frame=100, end_source_frame=100 + len(sequence.timestamp_ms) - 1,
               sampling_protocol=SAMPLING_PROTOCOL, **kwargs))


class HipPathRatioTests(unittest.TestCase):
    def assert_unavailable(self, result):
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["hip_path_over_net_ratio"])
        self.assertFalse(result["formal_event_verified"])
        self.assertIsNone(result["grade"])
        json.dumps(result, allow_nan=False)

    def test_known_bent_path_and_binding(self):
        sequence, timeline = fixture()
        before = copy.deepcopy(sequence)
        result = measure(sequence, timeline)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["hip_path_length_px"], 70)
        self.assertAlmostEqual(result["hip_net_displacement_px"], 50)
        self.assertAlmostEqual(result["hip_path_over_net_ratio"], 1.4)
        self.assertEqual(result["source_frames"], [100, 101, 102])
        self.assertEqual(result["source_duration_ms_exact"], {"numerator": 200, "denominator": 3})
        self.assertFalse(result["measurement_provenance"]["confidence_available"])
        self.assertFalse(result["scoring_allowed"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["grade"])
        for name in sequence.keypoints_xy:
            np.testing.assert_array_equal(sequence.keypoints_xy[name], before.keypoints_xy[name])
            self.assertTrue(np.isnan(sequence.confidence[name]).all())
        json.dumps(result, allow_nan=False)

    def test_whole_body_translation_is_preserved_and_straight_path_is_one(self):
        sequence, timeline = fixture(((300, 400), (330, 400), (370, 400)))
        result = measure(sequence, timeline)
        self.assertAlmostEqual(result["hip_path_length_px"], 70)
        self.assertAlmostEqual(result["hip_net_displacement_px"], 70)
        self.assertAlmostEqual(result["hip_path_over_net_ratio"], 1)

    def test_common_translation_rotation_and_fixed_scale_are_ratio_invariant(self):
        for scale, angle, translation in ((1, 0, (50, -30)), (1, .7, (20, 30)), (1.5, .4, (-20, 10))):
            sequence, timeline = fixture()
            original = measure(sequence, timeline)
            c, s = math.cos(angle), math.sin(angle)
            linear = scale * np.array([[c, -s], [s, c]])
            for name in sequence.keypoints_xy:
                sequence.keypoints_xy[name][:] = (sequence.keypoints_xy[name] * 1000 @ linear.T + translation) / 1000
            sequence.frame_transforms_to_reference[:] = np.column_stack((linear, translation))
            result = measure(sequence, timeline)
            self.assertAlmostEqual(result["hip_path_over_net_ratio"], 1.4)
            self.assertAlmostEqual(result["hip_path_length_px"], original["hip_path_length_px"] * scale)
            self.assertAlmostEqual(result["hip_net_displacement_px"], original["hip_net_displacement_px"] * scale)

    def test_zero_displacement_keeps_lengths_and_has_no_infinity(self):
        for centers, expected_length in ((((300, 400), (330, 400), (300, 400)), 60), (((300, 400),) * 3, 0)):
            sequence, timeline = fixture(centers)
            result = measure(sequence, timeline)
            self.assert_unavailable(result)
            self.assertEqual(result["reason"], "zero_net_displacement")
            self.assertAlmostEqual(result["hip_path_length_px"], expected_length)
            self.assertEqual(result["hip_net_displacement_px"], 0)

    def test_near_zero_net_is_not_capped_or_mapped_to_a_grade(self):
        sequence, timeline = fixture(((300, 400), (330, 400), (300 + 1e-8, 400)))
        result = measure(sequence, timeline)
        self.assertGreater(result["hip_path_over_net_ratio"], 1e9)
        self.assertFalse(result["sensitivity"]["threshold_applied"])
        self.assertIn("uncertainty", result["sensitivity"]["warning"])
        self.assertIsNone(result["grade"])

    def test_exact_window_does_not_sort_crop_or_bridge(self):
        sequence, timeline = fixture()
        for start, end in ((True, 102), (100, 100), (99, 102), (100, 103), (101, 102)):
            result = compute_hip_path_ratio(sequence, primary_timeline=timeline, start_source_frame=start,
                end_source_frame=end, sampling_protocol=SAMPLING_PROTOCOL)
            self.assert_unavailable(result)
        for frames in ([100, 102, 103], [100, 101, 101], [102, 101, 100]):
            broken = copy.deepcopy(sequence)
            broken.source_frames[:] = frames
            self.assert_unavailable(measure(broken, timeline))

    def test_sampling_declaration_and_explicit_resampling_are_required(self):
        sequence, timeline = fixture()
        for protocol in (None, "resampled_30fps", "interpolated"):
            result = compute_hip_path_ratio(sequence, primary_timeline=timeline, start_source_frame=100,
                end_source_frame=102, sampling_protocol=protocol)
            self.assert_unavailable(result)
        for marker in ("resampled", "interpolated", "smoothed"):
            broken = copy.deepcopy(sequence)
            broken.coordinate_metadata[marker] = True
            self.assert_unavailable(measure(broken, timeline))

    def test_missing_hips_masks_and_fabricated_sam_confidence_reject_window(self):
        for defect in ("missing", "nan", "mask", "numeric_confidence", "wrong_shape", "boolean_coordinates"):
            sequence, timeline = fixture()
            if defect == "missing": del sequence.keypoints_xy["left_hip"]
            elif defect == "nan": sequence.keypoints_xy["left_hip"][1, 0] = np.nan
            elif defect == "mask": sequence.model_estimate_masks["left_hip"][1] = False
            elif defect == "numeric_confidence": sequence.confidence["left_hip"][1] = 1
            elif defect == "wrong_shape": sequence.keypoints_xy["left_hip"] = np.zeros((2, 2))
            else: sequence.keypoints_xy["left_hip"] = np.ones((3, 2), dtype=bool)
            self.assert_unavailable(measure(sequence, timeline))

    def test_illegal_affine_and_missing_camera_evidence_reject(self):
        for matrix in ([[1, .5, 0], [0, 1, 0]], [[-1, 0, 0], [0, 1, 0]],
                       [[0, 0, 0], [0, 0, 0]], [[1, 0, np.nan], [0, 1, 0]]):
            sequence, timeline = fixture()
            sequence.frame_transforms_to_reference[1] = matrix
            self.assert_unavailable(measure(sequence, timeline))
        sequence, timeline = fixture()
        sequence.coordinate_metadata["camera_compensation"]["applied_source_frames"].remove(101)
        self.assert_unavailable(measure(sequence, timeline))
        sequence, timeline = fixture()
        self.assert_unavailable(measure(replace(sequence, camera_motion_status=("stationary", "unavailable", "stationary")), timeline))

    def test_bound_identity_reference_and_processing_changes_reject(self):
        for field, value in (("video_sha256", "b"*64), ("person_track_id", 9), ("selection_epoch", 3),
                             ("camera_reference_epoch", 4), ("processed_index", 103), ("time_base_den", 1200)):
            sequence, timeline = fixture()
            sequence.source_time_bindings[101][field] = value
            self.assert_unavailable(measure(sequence, timeline))
        sequence, timeline = fixture()
        del sequence.source_time_bindings[101]
        self.assert_unavailable(measure(sequence, timeline))

    def test_nonmonotonic_source_pts_and_global_clock_drift_reject(self):
        sequence, timeline = fixture()
        for value in (2000, 1990):
            broken = copy.deepcopy(sequence)
            binding = broken.source_time_bindings[101]
            binding["pts"] = value
            binding["source_timestamp_ms"] = round(value*1000/600)
            self.assert_unavailable(measure(broken, timeline))
        sequence, timeline = fixture(pts=[2000, 2001, 2002])
        # Every adjacent difference is within the shared millisecond rounding
        # allowance, but the whole window must have one common clock origin.
        for i, frame in enumerate(sequence.source_frames):
            sequence.timestamp_ms[i] += i
            sequence.source_time_bindings[int(frame)]["video_timestamp_ms"] += i
            timeline[i]["timestamp_ms"] += i
        result = measure(sequence, timeline)
        self.assert_unavailable(result)
        self.assertEqual(result["reason"], "source_video_clock_origin_changed")

    def test_variable_native_pts_is_allowed_without_resampling(self):
        sequence, timeline = fixture(pts=[2000, 2007, 2030])
        result = measure(sequence, timeline)
        self.assertAlmostEqual(result["hip_path_over_net_ratio"], 1.4)
        self.assertEqual(result["source_duration_ms_exact"], {"numerator": 50, "denominator": 1})

    def test_independent_selection_and_model_binding_must_agree(self):
        for defect in ("ambiguous", "reordered", "model_pts", "model_boolean", "clock"):
            sequence, timeline = fixture()
            if defect == "ambiguous": timeline[1]["identity_ambiguous"] = True
            elif defect == "reordered": timeline.reverse()
            elif defect == "model_pts": sequence.model_estimate_bindings[1]["pts"] += 1
            elif defect == "model_boolean": sequence.model_estimate_bindings[1]["frame_index"] = True
            else: timeline[1]["source_timestamp_ms"] = 0
            self.assert_unavailable(measure(sequence, timeline))

    def test_nonmodel_confidence_path_uses_existing_cutoff_and_upper_bound(self):
        sequence, timeline = fixture(model=False)
        self.assertAlmostEqual(measure(sequence, timeline)["hip_path_over_net_ratio"], 1.4)
        for value in (.1, 1.1, np.nan):
            sequence.confidence["left_hip"][1] = value
            self.assert_unavailable(measure(sequence, timeline))

    def test_body_normalized_contract_and_changing_dimensions_are_rejected(self):
        sequence, timeline = fixture()
        sequence.coordinate_metadata["coordinate_system"] = "body_centered_torso_normalized"
        self.assert_unavailable(measure(sequence, timeline))
        sequence, timeline = fixture()
        sequence.frame_dimensions_px[1, 0] = 500
        self.assert_unavailable(measure(sequence, timeline))

    def test_shallow_mutation_cannot_change_timestamp_shape_or_finiteness(self):
        for shape in ((3, 1), (3, 2)):
            sequence, timeline = fixture()
            sequence.timestamp_ms.resize(shape, refcheck=False)
            result = measure(sequence, timeline)
            self.assert_unavailable(result)
            self.assertEqual(result["reason"], "invalid_video_time_array")
        sequence, timeline = fixture()
        sequence = replace(sequence, timestamp_ms=sequence.timestamp_ms.astype(float))
        sequence.timestamp_ms[1] = np.nan
        self.assert_unavailable(measure(sequence, timeline))

    def test_original_bounds_rechecked_but_reference_outside_image_is_allowed(self):
        sequence, timeline = fixture()
        sequence.keypoints_xy["left_hip"][1] = [2, .4]
        result = measure(sequence, timeline)
        self.assert_unavailable(result)
        self.assertEqual(result["reason"], "hip_outside_original_frame")
        sequence, timeline = fixture()
        for name in sequence.keypoints_xy:
            sequence.keypoints_xy[name][:] -= [1, 1]
        sequence.frame_transforms_to_reference[:, :, 2] = [-1000, -1000]
        result = measure(sequence, timeline)
        self.assertAlmostEqual(result["hip_path_over_net_ratio"], 1.4)
        self.assertTrue(all(x < 0 and y < 0 for x, y in result["hip_centers_reference_px"]))


if __name__ == "__main__":
    unittest.main()
