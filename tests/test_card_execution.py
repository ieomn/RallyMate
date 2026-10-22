from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.source_alignment import clear_source_aligned_feature_cache
import rallymate_service.card_execution as execution


def real_fixture():
    payload = json.loads((Path(__file__).parent / "fixtures" / "temporal-card-real-0a3.json").read_text(encoding="utf-8"))
    data = payload["sequence"]
    for key in ("timestamp_ms", "source_frames", "frame_dimensions_px", "frame_transforms_to_reference"):
        data[key] = np.asarray(data[key])
    for key in ("keypoints_xy", "confidence", "model_estimate_masks"):
        data[key] = {name: np.asarray(value, dtype=bool if key == "model_estimate_masks" else float)
                     for name, value in data[key].items()}
    for key in ("camera_motion_status", "camera_reference_epochs", "model_estimate_bindings"):
        data[key] = tuple(data[key])
    data["source_time_bindings"] = {int(key): value for key, value in data["source_time_bindings"].items()}
    data["coordinate_metadata"]["source_run_id"] = payload["provenance"]["run_id"]
    return PoseSequence(**data), payload["events"], payload["timeline"]


def rows(report):
    return [row for card in report["cards"] for row in card["instances"]]


class CardExecutionTests(unittest.TestCase):
    def setUp(self):
        clear_source_aligned_feature_cache()
        execution.clear_card_execution_cache()
        self.addCleanup(clear_source_aligned_feature_cache)
        self.addCleanup(execution.clear_card_execution_cache)

    def test_real_source_replay_covers_catalogue_without_claiming_298_calculators(self):
        sequence, events, timeline = real_fixture()
        with patch.object(execution, "prepare_source_aligned_context", wraps=execution.prepare_source_aligned_context) as prepare:
            report = execution.build_card_execution_from_sequence(sequence, events, timeline)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(len(report["cards"]), 298)
        self.assertEqual((report["coverage"]["fs_card_count"], report["coverage"]["gs_card_count"]), (50, 248))
        self.assertEqual(report["coverage"]["wired_temporal_card_count"], 5)
        self.assertEqual(report["coverage"]["computed_card_instance_count"], 7)
        self.assertEqual(report["summary"]["channels"]["visual_proxy"]["scored_cards"], 4)
        self.assertEqual(report["summary"]["channels"]["visual_proxy"]["coverage"], 4 / 298)
        self.assertEqual(report["observed_type_summary"]["channels"]["visual_proxy"]["applicable_cards"], 15)
        self.assertEqual(report["summary"]["channels"]["original"]["scored_cards"], 0)
        self.assertTrue(all(row["original_grade"] is None for row in rows(report)))
        self.assertIsNone(report["overall_score"])
        self.assertIn("not_executed", report["coverage"]["run_card_status_counts"])
        json.dumps(report, allow_nan=False)

    def test_real_local_dip_review_never_scores_even_with_available_measurements(self):
        sequence, events, timeline = real_fixture()
        report = execution.build_card_execution_from_sequence(sequence, events, timeline)
        negative = next(row for row in rows(report) if row["indicator_id"] == "FS01-M02" and row["event_id"] == events[0]["event_id"])
        self.assertTrue(negative["measurements"])
        self.assertEqual(negative["status"], "review_required")
        self.assertIsNone(negative["score"])
        self.assertIsNone(negative["proxy_grade"])
        self.assertTrue(all(c["passed"] is None for c in negative["conditions"]))
        self.assertEqual(negative["reason_codes"], ["fallback_local_dip_action_type_unverified"])

    def test_saved_event_grades_and_pass_bits_cannot_change_recomputed_output(self):
        sequence, events, timeline = real_fixture()
        baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        forged = deepcopy(events)
        for event in forged:
            event.update(score=100, original_grade="A", proxy_grade="A", status="proxy_scored",
                         conditions=[{"passed": True}], measurements={"hip_drop_torso": 10000})
        changed = execution.build_card_execution_from_sequence(sequence, forged, timeline)
        self.assertEqual(rows(baseline), rows(changed))

    def test_conflicting_event_snapshot_is_not_resolved_by_last_write_wins(self):
        sequence, events, timeline = real_fixture()
        stop = next(event for event in events if event["event_code"] == "FS09")
        conflicting = deepcopy(stop)
        conflicting["end_ms"] -= 33
        events = [*events, conflicting]
        report = execution.build_card_execution_from_sequence(sequence, events, timeline)
        stop_rows = [row for row in rows(report) if row["event_id"] == stop["event_id"]]
        self.assertEqual(len(stop_rows), 3)
        self.assertTrue(all(row["score"] is None for row in stop_rows))
        self.assertTrue(all("event_conflicting_or_not_in_snapshot" in row["reason_codes"] for row in stop_rows))

    def test_identical_duplicate_events_do_not_multiply_score_weight(self):
        sequence, events, timeline = real_fixture()
        baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        repeated = execution.build_card_execution_from_sequence(sequence, events + deepcopy(events), timeline)
        self.assertEqual(rows(baseline), rows(repeated))
        self.assertEqual(baseline["summary"], repeated["summary"])

    def test_foreign_video_and_run_ids_cannot_relabel_real_trajectory(self):
        sequence, events, timeline = real_fixture()
        with self.assertRaisesRegex(ValueError, "card_sequence_run_binding_mismatch"):
            execution.build_card_execution_from_sequence(sequence, events, timeline, source_run_id="another-run")
        events[-1]["video_id"] = "another-video"
        with self.assertRaisesRegex(ValueError, "card_event_video_run_binding_mismatch"):
            execution.build_card_execution_from_sequence(sequence, events, timeline)
        sequence, events, timeline = real_fixture()
        timeline[-1]["video_id"] = "another-video"
        with self.assertRaisesRegex(ValueError, "card_timeline_video_run_binding_mismatch"):
            execution.build_card_execution_from_sequence(sequence, events, timeline)
        sequence.coordinate_metadata.pop("source_run_id")
        with self.assertRaisesRegex(ValueError, "card_source_run_binding_required"):
            execution.build_card_execution_from_sequence(sequence, events, timeline)

    def test_record_run_binding_is_checked_before_frame_conversion(self):
        with patch.object(execution, "pose_sequence_from_records") as convert:
            for records, run_id, reason in (([{}], "run-a", "required"),
                    ([{"job_id": "run-b"}], "run-a", "mismatch"),
                    ([{"job_id": "run-a"}, {"job_id": "run-b"}], None, "mismatch")):
                with self.subTest(records=records), self.assertRaisesRegex(ValueError, "card_frame_run_binding_" + reason):
                    execution.build_card_execution_from_records(records, [], [], source_run_id=run_id)
            convert.assert_not_called()

    def test_rule_drift_fails_closed_without_hiding_raw_evidence(self):
        sequence, events, timeline = real_fixture()
        changed = execution.load_card_rules()
        rule = next(row for row in changed["cards"] if row["indicator_id"] == "FS09-M03")
        rule["evaluation"]["conditions"][0]["value"] = 999.
        with patch.object(execution, "load_card_rules", return_value=changed):
            report = execution.build_card_execution_from_sequence(sequence, events, timeline)
        row = next(row for row in rows(report) if row["indicator_id"] == "FS09-M03")
        self.assertEqual(row["reason_codes"], ["catalogue_temporal_conditions_mismatch"])
        self.assertTrue(row["measurements"])
        self.assertIsNone(row["score"])

    def test_geometry_for_a_different_subject_cannot_use_the_primary_timeline(self):
        sequence, events, timeline = real_fixture()
        for binding in sequence.model_estimate_bindings:
            if binding is not None:
                binding["person_track_id"] = 2
        report = execution.build_card_execution_from_sequence(sequence, events, timeline)
        self.assertTrue(all(row["score"] is None for row in rows(report)))
        self.assertTrue(any("model_geometry_subject_binding_mismatch" in row["reason_codes"] for row in rows(report)))

    def test_file_cache_reads_once_invalidates_changed_input_and_isolates_returned_copies(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            for name in ("frames.jsonl", "events.jsonl", "primary-player.jsonl"):
                (directory / name).write_text("", encoding="utf-8")
            # An old fabricated report is not among the input files.
            (directory / "scores.jsonl").write_text('{"score":100,"grade":"A"}\n', encoding="utf-8")
            with patch.object(execution, "_read_records", wraps=execution._read_records) as read, \
                 patch.object(execution, "pose_sequence_from_records", wraps=execution.pose_sequence_from_records) as sequence:
                first = execution.build_card_execution_report(directory)
                first["cards"][0]["run_status"] = "mutated"
                second = execution.build_card_execution_report(directory)
                self.assertEqual(read.call_count, 3)
                self.assertEqual(sequence.call_count, 1)
                self.assertEqual(second["cards"][0]["run_status"], "not_observed")
                self.assertIsNone(second["summary"]["channels"]["visual_proxy"]["score"])
                (directory / "frames.jsonl").write_text("\n", encoding="utf-8")
                execution.build_card_execution_report(directory)
                self.assertEqual(read.call_count, 6)
                self.assertEqual(sequence.call_count, 2)

    def test_artifact_reader_rejects_duplicate_fields_nonfinite_values_and_oversized_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            for content in ('{"frame":1,"frame":2}\n', '{"score":NaN}\n', '[1,2]\n'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    execution._read_records(path, 1000, 10)
            path.write_text('{"frame":1}\n', encoding="utf-8")
            with patch.object(execution, "MAX_RECORD_BYTES", 4), self.assertRaises(ValueError):
                execution._read_records(path, 1000, 10)


if __name__ == "__main__":
    unittest.main()
