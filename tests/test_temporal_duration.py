from __future__ import annotations

import unittest

import numpy as np

from rallymate_features.event_features import stability_duration_from_series
from rallymate_features.fs01_fs02_features import _longest_true_run as first_step_run
from rallymate_features.fs09_features import double_support_low_motion_proxy
from rallymate_features.temporal import longest_contiguous_duration


class TemporalDurationTests(unittest.TestCase):
    def test_longest_means_elapsed_time_instead_of_sample_count_in_all_proxies(self) -> None:
        timestamps = np.asarray([0, 10, 20, 30, 40, 100, 220, 340])
        mask = np.asarray([True, True, True, True, False, True, True, True])
        indexes = np.arange(timestamps.size)
        duration, evidence, _ = longest_contiguous_duration(timestamps, indexes, mask)
        self.assertEqual((duration, evidence), (250, (5, 7)))
        self.assertEqual(first_step_run(timestamps, indexes, mask), (250, (5, 7)))
        values = np.where(mask, 1.0, 100.0)
        duration, evidence, _, _ = double_support_low_motion_proxy(timestamps, values, values, indexes)
        self.assertEqual((duration, evidence), (250, (5, 7)))
        duration, _, diagnostics = stability_duration_from_series(timestamps, values, values)
        self.assertEqual(duration, 250)
        self.assertEqual(diagnostics['longest_run_local_indexes'], [5, 7])

    def test_long_observation_gap_is_not_added_to_stability_or_support_duration(self) -> None:
        timestamps = np.asarray([0, 40, 80, 1000, 1040, 1080])
        values = np.ones(timestamps.size)
        indexes = np.arange(timestamps.size)
        mask = np.ones(timestamps.size, dtype=bool)
        self.assertEqual(first_step_run(timestamps, indexes, mask), (120, (0, 2)))
        duration, evidence, _, diagnostics = double_support_low_motion_proxy(timestamps, values, values, indexes)
        self.assertEqual((duration, evidence), (120, (0, 2)))
        self.assertEqual(diagnostics['continuity_break_count'], 1)
        duration, _, diagnostics = stability_duration_from_series(timestamps, values, values)
        self.assertEqual(duration, 120)
        self.assertEqual(diagnostics['max_contiguous_gap_ms'], 160)
        self.assertEqual(diagnostics['median_dt_ms'], 40)

    def test_missing_event_indexes_and_missing_measurements_break_runs(self) -> None:
        timestamps = np.asarray([0, 40, 80, 120, 160])
        duration, evidence, _ = longest_contiguous_duration(timestamps, np.asarray([0, 1, 3, 4]), np.ones(5, dtype=bool))
        self.assertEqual((duration, evidence), (80, (0, 1)))
        values = np.asarray([1.0, 1.0, np.nan, 1.0, 1.0])
        duration, stable, diagnostics = stability_duration_from_series(timestamps, values, values)
        self.assertEqual(duration, 80)
        self.assertFalse(stable[2])
        self.assertEqual(diagnostics['longest_run_local_indexes'], [0, 1])

    def test_gap_threshold_is_inclusive_and_cannot_inflate_endpoint_cadence(self) -> None:
        for gap, expected in [(160, 270), (161, 40)]:
            timestamps = np.asarray([0, 20, 20 + gap])
            duration, _, _ = longest_contiguous_duration(timestamps, np.arange(3), np.ones(3, dtype=bool))
            # Endpoint support uses only observed adjacent intervals <=160 ms.
            self.assertEqual(duration, expected)
        duration, _, diagnostics = longest_contiguous_duration(np.asarray([0, 1000, 2000]), np.arange(3), np.ones(3, dtype=bool))
        self.assertEqual(duration, 0)
        self.assertEqual(diagnostics['median_dt_ms'], 0)


if __name__ == '__main__':
    unittest.main()
