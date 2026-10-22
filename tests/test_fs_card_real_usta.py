"""Real numeric replay; media/artifact verification ran separately before export.

The adapter test substitutes only the already-verified sample preparation step.
It does not pretend that the 23-frame fixture is the complete source video.
"""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule
from rallymate_service.fs_card_windows import compute_fs_card_windows


class RealUstaManualCardWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / "fixtures/fs-card-real-usta-manual-v1.json").read_text(encoding="utf-8"))

    def evaluate(self, case, sample_rows=None):
        return evaluate_card_window(get_card_rule(case["indicator_id"]),
            [SimpleNamespace(**row) for row in (case["samples"] if sample_rows is None else sample_rows)],
            case["window"], case["timeline"])

    def test_measured_values_and_short_window_are_not_relabelled(self):
        expected_values = [
            [None],
            [1.0, 1.8166969546549694, 1.4268890535434235],
            [2.5891164183774587, 44.22961946362683, 49.80140699073314],
        ]
        for case, values, frame_count in zip(self.fixture["cases"], expected_values, (6, 6, 11)):
            with self.subTest(card=case["indicator_id"]):
                actual = self.evaluate(case)
                self.assertEqual({key: actual[key] for key in case["numeric_core_expected"]}, case["numeric_core_expected"])
                self.assertEqual(actual["quality"]["total_frame_count"], frame_count)
                self.assertEqual(actual["quality"]["valid_frame_count"], frame_count)
                self.assertFalse(actual["quality"]["visibility_verified"])
                self.assertIsNone(actual["original_grade"])
                self.assertEqual(len(actual["conditions"]), len(values))
                for condition, value in zip(actual["conditions"], values):
                    if value is None:
                        self.assertIsNone(condition["value"])
                        self.assertIn("continuous_endpoint_window_missing", condition["evidence"]["reason_codes"])
                    else:
                        self.assertAlmostEqual(condition["value"], value, places=9)

    def test_original_quality_blocks_both_numerically_complete_windows(self):
        cases = self.fixture["cases"]
        samples = [SimpleNamespace(**row) for case in cases for row in case["samples"]]
        timeline = sorted([row for case in cases for row in case["timeline"]], key=lambda row: row["frame_index"])
        decoded = sorted([row for case in cases for row in case["expected"]["source_window_evidence"]["source_pts"]],
            key=lambda row: row["frame_index"])
        # These objects were exported after the real full-video decoder and
        # source-artifact checks. CI tests the downstream adapter, not those IO gates.
        with patch("rallymate_service.fs_card_windows._prepare_samples", return_value=({2: samples}, timeline)):
            actual = compute_fs_card_windows([], [], self.fixture["source_events"], decoded,
                self.fixture["annotation_document"], source_job_id=self.fixture["source_closure"]["source_run_id"])
        instances = {row["event_id"]: row for card in actual["cards"] for row in card["instances"]}
        self.assertEqual(len(instances), 3)
        for case, expected_status in zip(cases, ("unavailable", "review_required", "review_required")):
            with self.subTest(card=case["indicator_id"]):
                result = instances[case["annotation_id"]]
                self.assertEqual(result, case["expected"])
                self.assertEqual(result["status"], expected_status)
                self.assertIsNone(result["score"])
                self.assertIsNone(result["proxy_grade"])
                self.assertIsNone(result["original_grade"])
                self.assertFalse(result["source_grade_supported"])
                self.assertIn("fs_window_original_quality_requires_review", result["reason_codes"])
                self.assertTrue(all(c["passed"] is None for c in result["conditions"]))
                self.assertFalse(result["source_window_evidence"]["source_behavior_or_contact_verified"])
                self.assertFalse(result["source_window_evidence"]["step_cycle_bound"])
        self.assertEqual(actual["coverage"]["instance_status_counts"], {"unavailable": 1, "review_required": 2})
        self.assertIsNone(actual["overall_score"])
        self.assertFalse(actual["input"]["artifact_files_verified"])

    def test_every_frozen_frame_has_same_video_pts_identity_and_png_hash(self):
        self.assertEqual(self.fixture["dataset_role"], "previously_observed_development_not_independent_validation")
        video_sha = self.fixture["source_closure"]["video_sha256"]
        for case in self.fixture["cases"]:
            samples = {row["frame_index"]: row for row in case["samples"]}
            visual = {row["frame_index"]: row for row in case["visual_observation"]["source_frame_references"]}
            frames = [row["frame_index"] for row in case["timeline"]]
            self.assertEqual(frames, list(range(frames[0], frames[-1] + 1)))
            self.assertEqual(set(frames), set(samples))
            self.assertEqual(set(frames), set(visual))
            self.assertTrue(case["visual_observation"]["candidate_only"])
            for row in case["timeline"]:
                binding = samples[row["frame_index"]]["source_binding"]
                self.assertEqual(row["selected_person_track_id"], 2)
                self.assertEqual(binding["video_sha256"], video_sha)
                for key in ("pts", "time_base_num", "time_base_den", "selection_epoch", "camera_reference_epoch"):
                    self.assertEqual(binding[key], row[key])
                for key in ("pts", "time_base_num", "time_base_den"):
                    self.assertEqual(visual[row["frame_index"]][key], row[key])
                self.assertLessEqual(abs(float(Fraction(row["pts"] * row["time_base_num"] * 1000, row["time_base_den"])) - row["timestamp_ms"]), .51)
                self.assertRegex(visual[row["frame_index"]]["original_png_sha256"], r"^[a-f0-9]{64}$")

    def test_synthetic_joint_gap_does_not_shrink_real_denominator(self):
        case = self.fixture["cases"][1]
        rows = deepcopy(case["samples"])
        rows[0]["point_confidences"]["left_hip"] = 0
        rows[1]["point_confidences"]["right_hip"] = 0
        actual = self.evaluate(case, rows)
        self.assertEqual(actual["quality"]["total_frame_count"], 6)
        self.assertEqual(actual["quality"]["valid_frame_count"], 4)
        self.assertIn("joint_union_geometry_below_70_percent", actual["reason_codes"])
        self.assertEqual(actual["status"], "unavailable")
        self.assertIsNone(actual["score"])


if __name__ == "__main__":
    unittest.main()
