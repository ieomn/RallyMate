from __future__ import annotations

import copy
import unittest

import numpy as np

from rallymate_evaluation.ground_truth import apply_keypoint_corrections
from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION, pose_sequence_from_records
from rallymate_features.event_features import compute_event_features
from rallymate_features.schemas import EventInterval


POINTS = {
    "left_shoulder": (300, 200), "right_shoulder": (400, 200),
    "left_hip": (320, 350), "right_hip": (380, 350),
    "left_knee": (370, 400), "right_knee": (380, 420),
    "left_ankle": (370, 500), "right_ankle": (420, 500),
}


def _records(width=1920, height=1080, scale=1.0, *, pixels=True):
    records = []
    for index in range(13):
        points = []
        for name, (x, y) in POINTS.items():
            px = (x - 350 + index * 2) * scale + width / 2
            py = (y - 350) * scale + height / 2
            point = {"name": name, "x_normalized": px / width,
                     "y_normalized": py / height, "confidence": 0.99}
            if pixels:
                point.update(x_px=px, y_px=py)
            points.append(point)
        records.append({"frame": {"index": index, "processed_index": index,
                                  "timestamp_ms": index * 40, "width": width, "height": height},
                        "poses": [{"person_track_id": 1, "keypoints": points}]})
    return records


def _sequence(records):
    return pose_sequence_from_records(records, [{"processed_index": i, "source_track_id": 1}
                                                for i in range(len(records))])


class IsotropicCoordinateTests(unittest.TestCase):
    def test_crop_padding_and_proportional_resize_preserve_geometry_and_body_units(self):
        names = ["left_knee_flexion_deg", "right_knee_flexion_deg", "torso_lean_deg",
                 "hip_center_y_body", "stance_width_body", "body_center_speed_body_s",
                 "hip_center_relative_to_ankle_support"]
        event = EventInterval("same-motion", "FS02", 0, 480)
        reference = None
        for width, height, scale in [(1920, 1080, 1), (960, 540, .5),
                                     (1080, 1080, 1), (1080, 1920, 1)]:
            for pixels in [True, False]:
                with self.subTest(width=width, height=height, pixels=pixels):
                    sequence = _sequence(_records(width, height, scale, pixels=pixels))
                    results = compute_event_features(sequence, event, names)
                    self.assertTrue(all(result.valid for result in results))
                    values = np.asarray([result.value for result in results])
                    self.assertAlmostEqual(values[0], 45.0, places=7)
                    if reference is None:
                        reference = values
                    np.testing.assert_allclose(values, reference, atol=1e-7)
                    self.assertEqual(results[0].provenance["coordinate_metadata"]["contract_version"],
                                     COORDINATE_CONTRACT_VERSION)

    def test_pixels_are_authoritative_and_dimensions_are_traceable(self):
        records = _records()
        records[0]["poses"][0]["keypoints"][0]["x_normalized"] = 0.99
        sequence = _sequence(records)
        expected = records[0]["poses"][0]["keypoints"][0]["x_px"] / 1920
        self.assertEqual(sequence.keypoints_xy["left_shoulder"][0, 0], expected)
        self.assertEqual(sequence.coordinate_metadata["frame_dimensions"],
                         [{"width": 1920, "height": 1080, "frame_count": 13}])
        self.assertEqual(sequence.coordinate_metadata["point_source_counts"], {"original_frame_pixels": 104})

    def test_missing_or_invalid_dimensions_never_guess_aspect_ratio(self):
        for value in [None, 0, -1, True, float("nan"), float("inf"), "1920", 1920.5]:
            records = _records()
            records[3]["frame"]["width"] = value
            sequence = _sequence(records)
            with self.subTest(value=value):
                self.assertTrue(all(np.isnan(points[3]).all() for points in sequence.keypoints_xy.values()))
                self.assertEqual(sequence.coordinate_metadata["invalid_dimensions_source_frames"], [3])
                self.assertTrue(np.isfinite(sequence.keypoints_xy["left_shoulder"][2]).all())

    def test_invalid_pixel_observation_is_not_rescued_by_normalized_values(self):
        for patch in [{"x_px": -1}, {"x_px": float("nan")}, {"x_px": 2000},
                      {"confidence": 1.1}, {"confidence": True}, {"in_frame": False}]:
            records = _records()
            records[0]["poses"][0]["keypoints"][0].update(patch)
            self.assertTrue(np.isnan(_sequence(records).keypoints_xy["left_shoulder"][0]).all())
        records = _records()
        del records[0]["poses"][0]["keypoints"][0]["y_px"]
        self.assertTrue(np.isnan(_sequence(records).keypoints_xy["left_shoulder"][0]).all())

    def test_camera_status_is_preserved_without_removing_angle_observations(self):
        records = _records()
        self.assertIsNone(_sequence(records).camera_motion_status)
        records[0]["camera_motion"] = {"status": "reference"}
        records[1]["camera_motion"] = {"status": "moving"}
        sequence = _sequence(records)
        self.assertEqual(sequence.camera_motion_status[:3], ("reference", "moving", "unavailable"))
        self.assertTrue(np.isfinite(sequence.keypoints_xy["left_shoulder"]).all())

    def test_manual_truth_uses_the_same_isotropic_space(self):
        records = _records()
        sequence = _sequence(records)
        source_point = records[0]["poses"][0]["keypoints"][0]
        annotation = {"schema_version": "1.0.0", "video_id": "synthetic",
                      "source_frame_index": 0, "timestamp_ms": 0, "primary_player_id": 1,
                      "annotator_id": "test", "view_group": "synthetic",
                      "joints": {"left_shoulder": {"visible": True,
                                 "x_normalized": source_point["x_normalized"],
                                 "y_normalized": source_point["y_normalized"]}}}
        truth = apply_keypoint_corrections(sequence, [annotation])
        np.testing.assert_allclose(truth.keypoints_xy["left_shoulder"][0], sequence.keypoints_xy["left_shoulder"][0])
        self.assertTrue(np.isnan(truth.keypoints_xy["left_shoulder"][1:]).all())
        np.testing.assert_equal(truth.frame_dimensions_px, sequence.frame_dimensions_px)
        malformed = copy.deepcopy(records)
        del malformed[0]["frame"]["width"]
        with self.assertRaisesRegex(ValueError, "valid source frame dimensions"):
            apply_keypoint_corrections(_sequence(malformed), [annotation])


if __name__ == "__main__":
    unittest.main()
