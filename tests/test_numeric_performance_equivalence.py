from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from rallymate_events import detect_pose_events
from rallymate_events.rules import _phase_signals
from rallymate_features.kinematics import irregular_derivative
from rallymate_features.schemas import FeatureResult
from rallymate_features.smoothing import time_weighted_smooth
from tests.test_events import _motion_sequence


def _reference_smooth(times, values, radius, minimum):
    """Original scalar definition, independent of the batched implementation."""
    times = np.asarray(times, dtype=np.int64)
    result = np.full_like(values, np.nan, dtype=float)
    source, target = values.reshape(len(times), -1), result.reshape(len(times), -1)
    for i, timestamp in enumerate(times):
        left = np.searchsorted(times, timestamp - radius, side="left")
        right = np.searchsorted(times, timestamp + radius, side="right")
        weights = np.maximum(1 - np.abs(times[left:right] - timestamp) / max(radius, 1), 1e-6)
        for column in range(source.shape[1]):
            local = source[left:right, column]
            finite = np.isfinite(local)
            if finite.sum() >= minimum and weights[finite].sum() > 0:
                target[i, column] = (local[finite] * weights[finite]).sum() / weights[finite].sum()
    return result


def _reference_derivative(times, values):
    times = np.asarray(times, dtype=float) / 1000
    result = np.full_like(values, np.nan, dtype=float)
    source, target = values.reshape(len(times), -1), result.reshape(len(times), -1)
    for column in range(source.shape[1]):
        finite = np.flatnonzero(np.isfinite(source[:, column]))
        for i, index in enumerate(finite):
            left, right = finite[max(i - 1, 0)], finite[min(i + 1, len(finite) - 1)]
            dt = times[right] - times[left]
            if right != left and dt > 0:
                target[index, column] = (source[right, column] - source[left, column]) / dt
    return result


class NumericPerformanceEquivalenceTests(unittest.TestCase):
    def test_batched_smoothing_matches_irregular_finite_weight_definition(self):
        rng = np.random.default_rng(420)
        for count in (1, 2, 17, 4600):
            times = np.cumsum(rng.integers(1, 80, count))
            values = rng.normal(size=(count, 2, 3))
            values[rng.random(values.shape) < 0.25] = np.nan
            values[rng.random(values.shape) < 0.02] = np.inf
            for radius, minimum in ((0, 1), (80, 2), (300, 3), (-1, 2)):
                with self.subTest(count=count, radius=radius, minimum=minimum):
                    expected = _reference_smooth(times, values, radius, minimum)
                    actual = time_weighted_smooth(times, values, radius_ms=radius, min_samples=minimum)
                    np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-13, equal_nan=True)

    def test_duplicate_and_reverse_time_preserve_existing_primitives(self):
        for times in (np.array([0, 40, 40, 100, 180]), np.array([180, 100, 40, 40, 0])):
            values = np.array([[0, np.nan], [1, 3], [4, np.inf], [9, 7], [16, 2]], dtype=float)
            np.testing.assert_allclose(
                time_weighted_smooth(times, values), _reference_smooth(times, values, 100, 2),
                rtol=1e-13, atol=1e-13, equal_nan=True,
            )
            np.testing.assert_array_equal(irregular_derivative(times, values), _reference_derivative(times, values))

    def test_derivative_keeps_column_specific_gaps_and_endpoint_stencil(self):
        rng = np.random.default_rng(3)
        for count in (1, 2, 1000):
            times = np.cumsum(rng.integers(1, 130, count))
            values = rng.normal(size=(count, 2, 3))
            values[rng.random(values.shape) < 0.3] = np.nan
            values[:, 0, 0] = np.nan
            values[-1, 0, 0] = 1
            np.testing.assert_array_equal(irregular_derivative(times, values), _reference_derivative(times, values))

    def test_empty_smoothing_and_derivative_keep_shape(self):
        for shape in ((0,), (0, 2)):
            values = np.empty(shape)
            self.assertEqual(time_weighted_smooth(np.array([]), values).shape, shape)
            self.assertEqual(irregular_derivative(np.array([]), values).shape, shape)

    def test_phase_signals_are_computed_once_for_multiple_bouts(self):
        source = _motion_sequence()
        count = len(source.timestamp_ms) * 3
        sequence = replace(source, timestamp_ms=np.arange(count) * 40, source_frames=np.arange(count),
                           keypoints_xy={name: np.vstack([points + [i * 0.18, 0] for i in range(3)])
                                         for name, points in source.keypoints_xy.items()},
                           confidence={name: np.tile(values, 3) for name, values in source.confidence.items()})
        with patch("rallymate_events.rules._phase_signals", wraps=_phase_signals) as prepare:
            events = detect_pose_events(sequence, source_id="repeat-motion")
        self.assertGreaterEqual(len(events), 9)
        self.assertEqual(prepare.call_count, 1)
        base = _phase_signals(sequence, scale=1)
        for scale in (0.17, 0.5, 2.1):
            expected = _phase_signals(sequence, scale=scale)
            for name, values in base.items():
                scaled = values if name == "hip_support_redistribution_speed_body_s" else values / scale
                np.testing.assert_allclose(scaled, expected[name], rtol=1e-10, atol=1e-12, equal_nan=True)

    def test_read_only_export_retains_content_and_isolates_mutable_layers(self):
        result = FeatureResult("feature", "v1", 1.0, "unit", 0.12345678, True, "valid", [1],
                               [{"value": 1}], [{"value": 2}], {"coordinate_metadata": {"frames": [1, 2, 3]}})
        independent = result.to_dict()
        shared = result.to_dict(copy_evidence=False)
        self.assertEqual(shared, independent)
        shared["provenance"]["event_id"] = "event"
        self.assertNotIn("event_id", result.provenance)
        independent["provenance"]["coordinate_metadata"]["frames"].append(4)
        self.assertEqual(result.provenance["coordinate_metadata"]["frames"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
