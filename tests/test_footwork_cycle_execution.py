from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from rallymate_features.source_alignment import clear_source_aligned_feature_cache, prepare_source_aligned_context
from rallymate_service.footwork_cycle_execution import compute_footwork_cycle_execution
from tests.test_footwork_cycle_card_evidence import real_fixture, synthetic_cycle


def report(fixture, bindings=True):
    sequence, event, timeline, binding = fixture
    return compute_footwork_cycle_execution(sequence, [event], timeline,
        [binding] if bindings else [], source_run_id=event["video_id"])


def instance(data, identifier):
    return next(row for row in data["cards"] if row["indicator_id"] == identifier)["instances"][0]


class FootworkCycleExecutionTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        self.addCleanup(clear_source_aligned_feature_cache)

    def test_complete_synthetic_cycle_maps_only_fresh_conditions_to_proxy(self):
        fixture = synthetic_cycle()
        fixture[1].update(score=0, proxy_grade="E", grade="E", conditions=[{"passed":False}])
        with patch("rallymate_service.footwork_cycle_execution.prepare_source_aligned_context",
                   wraps=prepare_source_aligned_context) as prepare:
            data = report(fixture)
        prepare.assert_called_once()
        row = instance(data, "FS04-M04")
        self.assertEqual((row["status"], row["proxy_grade"], row["score"]), ("proxy_scored", "A", 100))
        self.assertIsNone(row["original_grade"])
        self.assertEqual(row["semantics"], "engineering_visual_condition_completion_not_original_technical_grade")
        self.assertEqual(row["measurement_version"], "footwork-cycle-card-evidence-v1.0.0")
        self.assertEqual(row["quality"], row["evidence"]["coverage"])
        self.assertEqual(len(row["source"]["rubric_sha256"]), 64)
        self.assertEqual(len(row["evidence"]["source_frame_bindings"]), 22)
        self.assertEqual(len(data["cards"]), 10)
        self.assertEqual(next(c for c in data["cards"] if c["indicator_id"] == "FS04-M04")["run_status"], "proxy_scored")
        self.assertEqual(data["coverage"]["source_fs_card_count"], 50)
        self.assertIsNone(data["overall_score"])
        self.assertFalse(data["input"]["artifact_files_verified"])
        json.dumps(data, allow_nan=False)

    def test_real_candidate_keeps_all_raw_values_and_review_causes_without_grade(self):
        data = report(real_fixture())
        row = instance(data, "FS03-M03")
        self.assertEqual(row["status"], "review_required")
        self.assertAlmostEqual(row["measurements"]["hip_displacement_torso"], 2.81227098454173, places=8)
        self.assertIn("complete_visual_motion_cycle_not_observed", row["reason_codes"])
        self.assertIsNone(row["proxy_grade"])
        self.assertIsNone(row["score"])
        self.assertTrue(all(c["passed"] is None for c in row["conditions"]))
        self.assertEqual(data["coverage"]["instance_status_counts"], {"review_required": 5})

    def test_manual_window_missing_never_becomes_e_or_zero(self):
        data = report(synthetic_cycle(), bindings=False)
        row = instance(data, "FS04-M04")
        self.assertEqual(row["status"], "review_required")
        self.assertIn("source_step_window_binding_required", row["reason_codes"])
        self.assertIsNone(row["score"])

    def test_identical_duplicates_are_idempotent_conflicts_reject(self):
        sequence, event, timeline, binding = synthetic_cycle()
        data = compute_footwork_cycle_execution(sequence, [event, deepcopy(event)], timeline,
            [binding, deepcopy(binding)], source_run_id=event["video_id"])
        self.assertEqual(data["coverage"]["computed_card_instance_count"], 5)
        changed = {**event, "end_ms":800}
        with self.assertRaisesRegex(ValueError, "duplicate_event_conflict"):
            compute_footwork_cycle_execution(sequence, [event, changed], timeline, [binding], source_run_id=event["video_id"])
        changed_binding = {**binding, "end_source_frame":20}
        with self.assertRaisesRegex(ValueError, "duplicate_binding_conflict"):
            compute_footwork_cycle_execution(sequence, [event], timeline, [binding, changed_binding], source_run_id=event["video_id"])

    def test_cross_job_timeline_event_sequence_and_binding_cannot_exchange(self):
        for target in ("timeline", "event", "sequence", "binding"):
            with self.subTest(target=target):
                sequence, event, timeline, binding = synthetic_cycle()
                if target == "timeline": timeline[5]["job_id"] = "another-run"
                if target == "event": event["video_id"] = "another-run"
                if target == "sequence": sequence.coordinate_metadata["source_run_id"] = "another-run"
                if target == "binding": binding["video_id"] = "another-run"
                with self.assertRaises(ValueError):
                    compute_footwork_cycle_execution(sequence, [event], timeline, [binding], source_run_id="controlled-cycle")

    def test_wrong_video_and_unregistered_detector_keep_scores_null(self):
        for mutation in ({"video_sha256":"b"*64}, {"method":"versioned_detector","version":"caller-claims-v1"}):
            with self.subTest(mutation=mutation):
                fixture = synthetic_cycle()
                fixture[3].update(mutation)
                row = instance(report(fixture), "FS04-M04")
                self.assertEqual(row["status"], "review_required")
                self.assertIsNone(row["score"])


if __name__ == "__main__":
    unittest.main()
