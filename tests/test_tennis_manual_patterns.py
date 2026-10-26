from copy import deepcopy
import unittest

from scripts.validate_tennis_manual_patterns import extract_window


class ManualPatternWindowTests(unittest.TestCase):
    def fixture(self):
        rows = [{"time": i * 20., "frame": i, "track": 1, "reference": (0, 0), "subject_id": "subject-1"} for i in range(51)]
        annotation = {"start_ms": 0, "prep_end_ms": 350, "peak_ms": 600, "end_ms": 1000,
                      "person_track_id": 1, "native_selection_epoch": 0, "camera_reference_epoch": 0, "subject_id": "subject-1"}
        return rows, annotation

    def test_manual_times_are_quantized_only_to_actual_observations(self):
        rows, annotation = self.fixture()
        selected, reason = extract_window([rows], annotation)
        self.assertIsNone(reason)
        observed, prep, peak, resolution = selected
        self.assertEqual(observed[prep - 1]["time"], 340)
        self.assertEqual(observed[peak]["time"], 600)
        self.assertEqual(resolution, 20)
        self.assertEqual(len(observed), 51)

    def test_fragmented_window_cannot_be_stitched_across_a_subject_break(self):
        rows, annotation = self.fixture()
        self.assertIsNone(extract_window([rows[:30], rows[30:]], annotation)[0])
        for field, value in (("person_track_id", 2), ("native_selection_epoch", 1), ("camera_reference_epoch", 1), ("subject_id", "other")):
            changed = {**annotation, field: value}
            self.assertIsNone(extract_window([rows], changed)[0])

    def test_incomplete_and_duplicate_manual_windows_are_missing(self):
        rows, annotation = self.fixture()
        self.assertIsNone(extract_window([rows[:-5]], annotation)[0])
        self.assertEqual(extract_window([rows, deepcopy(rows)], annotation)[1], "manual_window_has_ambiguous_source_segments")

    def test_manual_window_does_not_expand_to_find_a_better_pattern(self):
        rows, annotation = self.fixture()
        annotation.update(start_ms=100, prep_end_ms=350, end_ms=900)
        selected, reason = extract_window([rows], annotation)
        self.assertIsNone(reason)
        self.assertEqual((selected[0][0]["time"], selected[0][-1]["time"]), (100, 900))


if __name__ == "__main__":
    unittest.main()
