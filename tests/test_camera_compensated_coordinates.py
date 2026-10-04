from __future__ import annotations

import copy
import math
import unittest

import numpy as np

from rallymate_evaluation.ground_truth import apply_keypoint_corrections
from rallymate_features.coordinates import validated_camera_transform, point_series
from rallymate_features.event_features import compute_event_features, clear_feature_cache
from rallymate_features.schemas import EventInterval
from test_isotropic_coordinates import _records, _sequence


def _camera_records(mode: str):
    records = _records()
    center = np.asarray([960., 540.])
    for index, record in enumerate(records):
        angle = index * .01 if mode in {"rotation", "combined"} else 0
        scale = 1 + index * .01 if mode in {"scale", "combined"} else 1
        translation = np.asarray([index * 5., index * -3.]) if mode in {"pan", "combined"} else np.zeros(2)
        linear = scale * np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        shift = center + translation - linear @ center
        inverse = np.linalg.inv(linear)
        matrix = np.column_stack([inverse, -inverse @ shift])
        for point in record["poses"][0]["keypoints"]:
            xy = linear @ np.asarray([point["x_px"], point["y_px"]]) + shift
            point.update(x_px=float(xy[0]), y_px=float(xy[1]),
                         x_normalized=float(xy[0] / 1920), y_normalized=float(xy[1] / 1080))
        record["camera_motion"] = {"status": "reference" if index == 0 else "moving",
                                   "compensation_valid": True, "matrix_to_reference": matrix.tolist(),
                                   "reference_epoch": 0}
    return records


class CameraCompensatedCoordinateTests(unittest.TestCase):
    def tearDown(self):
        clear_feature_cache()

    def test_pan_rotation_and_scale_preserve_reference_geometry_and_motion(self):
        reference = _sequence(_records())
        event = EventInterval("motion", "FS02", 0, 480)
        names = ["left_knee_flexion_deg", "torso_lean_deg", "hip_center_y_body", "stance_width_body",
                 "body_center_speed_body_s", "hip_center_speed_body_s"]
        reference_features = compute_event_features(reference, event, names)
        for mode in ["pan", "rotation", "scale", "combined"]:
            with self.subTest(mode=mode):
                sequence = _sequence(_camera_records(mode))
                self.assertEqual(sequence.camera_motion_status, ("reference",) + ("compensated",) * 12)
                self.assertEqual(sequence.camera_reference_epochs, (0,) * 13)
                for name in reference.keypoints_xy:
                    np.testing.assert_allclose(sequence.keypoints_xy[name], reference.keypoints_xy[name], atol=1e-12)
                actual = compute_event_features(sequence, event, names)
                self.assertTrue(all(item.valid for item in actual))
                np.testing.assert_allclose([item.value for item in actual], [item.value for item in reference_features], atol=1e-7)
                self.assertFalse(sequence.coordinate_metadata["is_metric_or_3d"])

    def test_invalid_declared_transforms_fail_closed_without_using_raw_points(self):
        matrices = [None, [[1, 0], [0, 1]], [[-1, 0, 0], [0, 1, 0]],
                    [[1, 1, 0], [0, 1, 0]], [[.2, 0, 0], [0, .2, 0]],
                    [[3, 0, 0], [0, 3, 0]], [[1, 0, float("nan")], [0, 1, 0]],
                    [["1", 0, 0], [0, "1", 0]], [[True, False, False], [False, True, False]]]
        for matrix in matrices:
            with self.subTest(matrix=matrix):
                records = _camera_records("pan")
                records[3]["camera_motion"]["matrix_to_reference"] = matrix
                sequence = _sequence(records)
                self.assertEqual(sequence.camera_motion_status[3], "unavailable")
                self.assertTrue(np.isnan(sequence.frame_transforms_to_reference[3]).all())
                self.assertTrue(all(np.isnan(values[3]).all() for values in sequence.keypoints_xy.values()))
        for change in [{"compensation_valid": False}, {"reference_epoch": None}, {"reference_epoch": True}]:
            records = _camera_records("pan")
            records[3]["camera_motion"].update(change)
            self.assertEqual(_sequence(records).camera_motion_status[3], "unavailable")

    def test_valid_reference_points_are_not_clamped_to_current_frame(self):
        records = _camera_records("pan")
        records[0]["camera_motion"]["matrix_to_reference"] = [[1, 0, -2000], [0, 1, 0]]
        sequence = _sequence(records)
        values, valid = point_series(sequence, "left_shoulder")
        self.assertTrue(valid[0])
        self.assertLess(values[0, 0], 0)

    def test_manual_annotations_receive_exactly_the_same_reference_transform(self):
        records = _camera_records("combined")
        sequence = _sequence(records)
        point = records[5]["poses"][0]["keypoints"][0]
        annotation = {"schema_version": "1.0.0", "video_id": "synthetic-camera",
                      "source_frame_index": 5, "timestamp_ms": 200, "primary_player_id": 1,
                      "annotator_id": "test", "view_group": "synthetic",
                      "joints": {point["name"]: {"visible": True, "x_normalized": point["x_normalized"],
                                                "y_normalized": point["y_normalized"]}}}
        truth = apply_keypoint_corrections(sequence, [annotation])
        np.testing.assert_allclose(truth.keypoints_xy[point["name"]][5], sequence.keypoints_xy[point["name"]][5], atol=1e-12)
        np.testing.assert_array_equal(truth.frame_transforms_to_reference, sequence.frame_transforms_to_reference)
        self.assertEqual(truth.camera_reference_epochs, sequence.camera_reference_epochs)
        broken = copy.deepcopy(records)
        broken[5]["camera_motion"]["compensation_valid"] = False
        with self.assertRaisesRegex(ValueError, "valid camera reference transform"):
            apply_keypoint_corrections(_sequence(broken), [annotation])

    def test_epoch_change_splits_features_even_without_an_unavailable_frame(self):
        records = _camera_records("pan")
        for record in records[6:]:
            record["camera_motion"]["reference_epoch"] = 1
        sequence = _sequence(records)
        crossing = compute_event_features(sequence, EventInterval("cross-epoch", "FS02", 0, 480), ["body_center_speed_body_s"])[0]
        self.assertFalse(crossing.valid)
        self.assertIsNone(crossing.value)
        safe = compute_event_features(sequence, EventInterval("one-epoch", "FS02", 240, 480), ["body_center_speed_body_s"])[0]
        self.assertTrue(safe.valid)
        self.assertEqual(safe.provenance["coordinate_metadata"]["analysis_window"]["reference_epoch"], 1)
        self.assertEqual(safe.provenance["coordinate_metadata"]["camera_compensation"]["applied_source_frames"], list(range(6, 13)))

    def test_transform_scale_bounds_accept_valid_similarity_endpoints(self):
        for scale in [.5, 1., 2.]:
            self.assertIsNotNone(validated_camera_transform({"compensation_valid": True,
                                                            "matrix_to_reference": [[scale, 0, 10], [0, scale, 20]]}))


if __name__ == "__main__":
    unittest.main()
