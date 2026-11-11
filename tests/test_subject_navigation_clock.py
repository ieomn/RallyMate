import unittest
from rallymate_service.tennis_subjects import select_report_subject


class SubjectNavigationClockTests(unittest.TestCase):
    def test_fractional_pts_residual_is_preserved_but_navigation_starts_at_zero(self):
        rows = [{"subject_id": "z-first", "identity_basis": "target_lock", "time": -.067, "track": 1},
                {"subject_id": "a-later", "identity_basis": "target_lock", "time": -.01, "track": 2},
                {"subject_id": "z-first", "identity_basis": "target_lock", "time": 33.3, "track": 1}]
        report = select_report_subject([rows])
        self.assertEqual(report["selected_subject_id"], "z-first")
        self.assertEqual(report["subjects"][0]["start_ms"], 0)
        self.assertEqual(report["subjects"][0]["source_relative_start_ms"], -.067)
        self.assertEqual(rows[0]["time"], -.067)

    def test_a_large_negative_clock_error_is_not_silently_corrected(self):
        report = select_report_subject([[{"subject_id": "bad", "identity_basis": "target_lock", "time": -100, "track": 1}]])
        self.assertEqual(report["subjects"][0]["start_ms"], -100)


if __name__ == "__main__":
    unittest.main()
