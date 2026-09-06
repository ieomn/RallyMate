from __future__ import annotations

import unittest

import numpy as np

from rallymate_features.coordinates import point_series, pose_sequence_from_records
from rallymate_features.event_features import (
    compute_event_features,
    stability_duration_from_series,
)
from rallymate_features.kinematics import irregular_derivative
from rallymate_features.schemas import EventInterval, PoseSequence


NAMES = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def _sequence(missing_left_ankle_at: int | None = None) -> PoseSequence:
    timestamps = np.asarray([0, 40, 95, 165, 250, 350, 470], dtype=np.int64)
    points = {name: [] for name in NAMES}
    confidence = {name: [] for name in NAMES}
    for index, timestamp in enumerate(timestamps):
        shift = timestamp / 1000.0 * 0.1
        coordinates = {
            "left_shoulder": (0.40 + shift, 0.30),
            "right_shoulder": (0.60 + shift, 0.30),
            "left_hip": (0.45 + shift, 0.55),
            "right_hip": (0.55 + shift, 0.55),
            "left_knee": (0.44 + shift, 0.72),
            "right_knee": (0.56 + shift, 0.72),
            "left_ankle": (0.42 + shift, 0.90),
            "right_ankle": (0.58 + shift, 0.90),
        }
        for name in NAMES:
            if name == "left_ankle" and index == missing_left_ankle_at:
                points[name].append([np.nan, np.nan])
                confidence[name].append(np.nan)
            else:
                points[name].append(coordinates[name])
                confidence[name].append(0.9)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size),
        keypoints_xy={name: np.asarray(values) for name, values in points.items()},
        confidence={name: np.asarray(values) for name, values in confidence.items()},
    )


def _fine_foot_sequence() -> PoseSequence:
    sequence = _sequence()
    points = {name: values.copy() for name, values in sequence.keypoints_xy.items()}
    confidence = {name: values.copy() for name, values in sequence.confidence.items()}
    for side, x in (("left", 0.42), ("right", 0.58)):
        points[f"{side}_big_toe"] = np.asarray([[x + 0.04, 0.92]] * 7)
        points[f"{side}_small_toe"] = np.asarray([[x + 0.02, 0.93]] * 7)
        confidence[f"{side}_big_toe"] = np.full(7, 0.9)
        confidence[f"{side}_small_toe"] = np.full(7, 0.9)
    return PoseSequence(
        timestamp_ms=sequence.timestamp_ms,
        source_frames=sequence.source_frames,
        keypoints_xy=points,
        confidence=confidence,
    )


class FeatureLibraryTests(unittest.TestCase):
    def test_stability_envelope_uses_irregular_timestamps_and_aligned_series(self) -> None:
        timestamps = np.asarray([0, 40, 100, 180, 300], dtype=np.int64)
        duration, stable, diagnostics = stability_duration_from_series(
            timestamps,
            np.asarray([4.0, 3.0, 0.5, 0.4, 2.0]),
            np.asarray([20.0, 15.0, 1.0, 1.0, 10.0]),
        )

        self.assertEqual(duration, 150)
        self.assertEqual(stable.tolist(), [False, False, True, True, False])
        self.assertEqual(diagnostics["median_dt_ms"], 70)
        self.assertEqual(diagnostics["longest_run_local_indexes"], [2, 3])

    def test_irregular_derivative_uses_timestamp_not_fixed_fps(self) -> None:
        timestamps = np.asarray([0, 100, 350, 900])
        values = (timestamps / 1000.0 * 2.0)[:, None]
        derivative = irregular_derivative(timestamps, values)[:, 0]
        np.testing.assert_allclose(derivative, np.full(4, 2.0), atol=1e-9)

    def test_ten_features_emit_version_unit_validity_and_evidence(self) -> None:
        sequence = _sequence()
        event = EventInterval("event-1", "FS09", 0, 470)
        results = compute_event_features(sequence, event)
        self.assertEqual(len(results), 10)
        by_name = {result.feature_name: result for result in results}
        self.assertEqual(by_name["body_center_speed_body_s"].unit, "body/s")
        self.assertTrue(by_name["body_center_speed_body_s"].valid)
        self.assertGreater(by_name["body_center_speed_body_s"].value, 0.0)
        self.assertTrue(by_name["left_knee_flexion_deg"].source_frames)
        self.assertEqual(by_name["stability_duration_ms"].unit, "ms")
        self.assertIn(
            "provisional_event_adaptive",
            by_name["stability_duration_ms"].reason,
        )
        stability = by_name["stability_duration_ms"]
        self.assertEqual(
            stability.feature_version,
            "0.2.0-provisional-envelope-evidence",
        )
        self.assertEqual(
            set(stability.raw_value),
            {"speed", "absolute_angular_velocity"},
        )
        self.assertEqual(
            set(stability.smoothed_value["series"]),
            {"speed", "absolute_angular_velocity"},
        )
        self.assertEqual(
            stability.provenance["evidence_contract"],
            "raw_pre_extra_smoothing_and_smoothed_series_v1",
        )
        for result in results:
            payload = result.to_dict()
            self.assertIn("raw_value", payload)
            self.assertIn("smoothed_value", payload)
            self.assertIn("library_version", payload["provenance"])

    def test_missing_point_is_null_not_zero_and_can_pass_short_gap_interpolation(self) -> None:
        sequence = _sequence(missing_left_ankle_at=3)
        event = EventInterval("event-1", "FS01", 0, 470)
        results = compute_event_features(
            sequence, event, ["left_knee_flexion_deg", "stance_width_body"]
        )
        for result in results:
            self.assertTrue(result.valid)
            raw_missing = [item for item in result.raw_value if item["source_frame"] == 3]
            self.assertEqual(raw_missing[0]["value"], None)
            self.assertIsNotNone(result.smoothed_value[3]["value"])

    def test_fine_foot_points_enable_ankle_angle_while_coco17_is_unavailable(self) -> None:
        event = EventInterval("event-1", "FS01", 0, 470)
        name = "left_ankle_shank_foot_angle_deg"
        coco = compute_event_features(_sequence(), event, [name])[0]
        fine_foot = compute_event_features(_fine_foot_sequence(), event, [name])[0]
        self.assertFalse(coco.valid)
        self.assertEqual(coco.reason, "required_keypoints_unavailable")
        self.assertTrue(fine_foot.valid)
        self.assertEqual(fine_foot.unit, "deg")
        self.assertEqual(fine_foot.feature_version, "0.2.0-fine-foot-diagnostic")

    def test_halpe26_and_wholebody133_records_share_registered_fine_foot_feature(self) -> None:
        event = EventInterval("event-1", "FS01", 0, 240)
        timeline = [
            {"processed_index": index, "source_track_id": 10}
            for index in range(7)
        ]
        for topology in ("halpe26", "coco_wholebody133"):
            records = []
            for index in range(7):
                shift = index * 0.002
                point_values = {
                    "left_shoulder": (0.40 + shift, 0.30),
                    "right_shoulder": (0.60 + shift, 0.30),
                    "left_hip": (0.45 + shift, 0.55),
                    "right_hip": (0.55 + shift, 0.55),
                    "left_knee": (0.44 + shift, 0.72),
                    "left_ankle": (0.42 + shift, 0.90),
                    "left_big_toe": (0.46 + shift, 0.92),
                    "left_small_toe": (0.44 + shift, 0.93),
                }
                records.append(
                    {
                        "frame": {
                            "processed_index": index,
                            "index": 100 + index,
                            "timestamp_ms": index * 40,
                        },
                        "poses": [
                            {
                                "person_track_id": 10,
                                "keypoint_format": topology,
                                "keypoints": [
                                    {
                                        "name": name,
                                        "x_normalized": xy[0],
                                        "y_normalized": xy[1],
                                        "confidence": 0.9,
                                    }
                                    for name, xy in point_values.items()
                                ],
                            }
                        ],
                    }
                )
            sequence = pose_sequence_from_records(records, timeline)
            result = compute_event_features(
                sequence,
                event,
                ["left_ankle_shank_foot_angle_deg"],
            )[0]
            self.assertTrue(result.valid, topology)
            self.assertEqual(result.feature_version, "0.2.0-fine-foot-diagnostic")

    def test_out_of_frame_point_is_missing_even_when_confidence_is_high(self) -> None:
        records = [
            {
                "frame": {"processed_index": 0, "index": 930, "timestamp_ms": 31000},
                "poses": [
                    {
                        "person_track_id": 90,
                        "keypoint_format": "coco_wholebody133",
                        "keypoints": [
                            {
                                "name": "left_big_toe",
                                "x_normalized": -0.01,
                                "y_normalized": 0.9,
                                "confidence": 0.99,
                                "in_frame": False,
                            }
                        ],
                    }
                ],
            }
        ]
        sequence = pose_sequence_from_records(
            records,
            [{"processed_index": 0, "source_track_id": 90}],
        )
        values, valid = point_series(sequence, "left_big_toe")
        self.assertFalse(valid[0])
        self.assertTrue(np.isnan(values[0]).all())


if __name__ == "__main__":
    unittest.main()
