from __future__ import annotations

import unittest

import numpy as np

from rallymate_features.smoothing import time_weighted_smooth


class TimestampWeightedSmoothingTests(unittest.TestCase):
    def test_vectorized_columns_preserve_weighted_finite_semantics(self) -> None:
        timestamp_ms = np.asarray([0, 40, 100, 180], dtype=np.int64)
        values = np.asarray(
            [
                [1.0, np.nan, 3.0],
                [3.0, 2.0, np.nan],
                [5.0, 4.0, 7.0],
                [9.0, 8.0, 11.0],
            ]
        )
        actual = time_weighted_smooth(
            timestamp_ms, values, radius_ms=100, min_samples=2
        )

        expected = np.full_like(values, np.nan)
        for index, timestamp in enumerate(timestamp_ms):
            left = int(np.searchsorted(timestamp_ms, timestamp - 100, side="left"))
            right = int(np.searchsorted(timestamp_ms, timestamp + 100, side="right"))
            weights = np.maximum(
                1.0 - np.abs(timestamp_ms[left:right] - timestamp) / 100.0,
                1e-6,
            )
            for column in range(values.shape[1]):
                candidates = values[left:right, column]
                finite = np.isfinite(candidates)
                if int(finite.sum()) >= 2:
                    expected[index, column] = np.average(
                        candidates[finite], weights=weights[finite]
                    )
        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_one_dimensional_series_shape_is_preserved(self) -> None:
        timestamp_ms = np.asarray([0, 50, 100], dtype=np.int64)
        values = np.asarray([1.0, 3.0, 5.0])
        actual = time_weighted_smooth(timestamp_ms, values, radius_ms=100)
        self.assertEqual(actual.shape, values.shape)
        self.assertTrue(np.isfinite(actual).all())


if __name__ == "__main__":
    unittest.main()
