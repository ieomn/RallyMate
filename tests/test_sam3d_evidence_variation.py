import unittest

from scripts.audit_sam3d_evidence_variation import compare_scores, frame_distance


class EvidenceVariationTests(unittest.TestCase):
    def test_inserted_frame_does_not_create_positional_shift(self):
        self.assertEqual(frame_distance([189, 212], [189, 198, 212]), 9)
        self.assertEqual(frame_distance([189, 212], [212, 189]), 0)
        self.assertEqual(frame_distance([], []), 0)
        self.assertIsNone(frame_distance([1], []))
        with self.assertRaises(ValueError):
            frame_distance([True], [1])

    def test_frame_set_change_and_numeric_value_units_are_separate(self):
        key = ("event", 1, "indicator")
        def row(frames, value):
            return {key: {"feature": {"items": [{"feature_name": "velocity", "source_frames": frames,
                        "value": value, "unit": "deg/s"}]}, "evidence": [{"source_frames": frames}]}}
        report = compare_scores(row([1, 5], 20), row([1, 6], 20.02))
        self.assertEqual(report["feature_frame_set_changes"], 1)
        self.assertEqual(report["feature_frame_distance_counts"], {"1": 1})
        self.assertAlmostEqual(report["value_changes_by_unit"]["deg/s"]["largest"]["abs_delta"], .02)
        reordered = compare_scores(row([1, 5], 20), row([5, 1], 20))
        self.assertEqual(reordered["feature_frame_list_changes"], 1)
        self.assertEqual(reordered["feature_frame_set_changes"], 0)


if __name__ == "__main__":
    unittest.main()
