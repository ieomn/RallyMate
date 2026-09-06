from __future__ import annotations

import unittest

import numpy as np

from rallymate_evaluation.event_bounded_pose_gaps import (
    interpolate_event_bounded_pose_gaps,
)
from rallymate_features.schemas import EventInterval, PoseSequence


def _sequence() -> PoseSequence:
    timestamps = np.asarray([0, 40, 85, 125, 170, 220], dtype=np.int64)
    values = np.asarray(
        [[0.0, 0.0], [np.nan, np.nan], [0.4, 0.8], [np.nan, np.nan], [np.nan, np.nan], [1.0, 2.0]],
        dtype=np.float64,
    )
    confidence = np.asarray([0.8, np.nan, 0.7, np.nan, np.nan, 0.9], dtype=np.float64)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(6, dtype=np.int64),
        keypoints_xy={"left_ankle": values},
        confidence={"left_ankle": confidence},
    )


class EventBoundedPoseGapTests(unittest.TestCase):
    def test_only_short_internal_gap_is_interpolated_with_timestamp(self) -> None:
        sequence, audit = interpolate_event_bounded_pose_gaps(
            _sequence(),
            EventInterval("event-1", "FS01", 0, 220),
            ["left_ankle"],
            max_gap_ms=100,
        )
        np.testing.assert_allclose(sequence.keypoints_xy["left_ankle"][1], [0.1882352941, 0.3764705882])
        self.assertEqual(0.7, sequence.confidence["left_ankle"][1])
        self.assertTrue(np.isnan(sequence.keypoints_xy["left_ankle"][3:5]).all())
        self.assertEqual(1, audit["interpolated_joint_observation_count"])
        self.assertEqual(1, len(audit["interpolated_points"]))
        self.assertEqual(0, audit["interpolated_points"][0]["left_boundary_source_frame"])
        self.assertEqual(2, audit["interpolated_points"][0]["right_boundary_source_frame"])
        self.assertEqual(85, audit["interpolated_points"][0]["bounding_observation_span_ms"])
        self.assertFalse(audit["safety"]["production_scoring_allowed"])

    def test_leading_and_trailing_gaps_never_extrapolate(self) -> None:
        sequence, audit = interpolate_event_bounded_pose_gaps(
            _sequence(),
            EventInterval("event-2", "FS01", 40, 170),
            ["left_ankle"],
            max_gap_ms=160,
        )
        self.assertTrue(np.isnan(sequence.keypoints_xy["left_ankle"][1]).all())
        self.assertTrue(np.isnan(sequence.keypoints_xy["left_ankle"][3:5]).all())
        kinds = [item["kind"] for item in audit["joint_audits"][0]["runs"]]
        self.assertEqual(["leading_boundary", "trailing_boundary"], kinds)
        self.assertEqual(0, audit["interpolated_joint_observation_count"])

    def test_missing_topology_is_reported_not_created(self) -> None:
        sequence, audit = interpolate_event_bounded_pose_gaps(
            _sequence(),
            EventInterval("event-3", "FS01", 0, 220),
            ["right_ankle"],
        )
        self.assertNotIn("right_ankle", sequence.keypoints_xy)
        self.assertFalse(audit["joint_audits"][0]["topology_available"])

    def test_invalid_contracts_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            interpolate_event_bounded_pose_gaps(
                _sequence(), EventInterval("event-4", "FS01", 0, 220), []
            )
        with self.assertRaises(ValueError):
            interpolate_event_bounded_pose_gaps(
                _sequence(),
                EventInterval("event-4", "FS01", 0, 220),
                ["left_ankle"],
                max_gap_ms=0,
            )
