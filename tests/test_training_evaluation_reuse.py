from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.artifact_evidence import EvidencePoolWriter, POOL_FILENAME
from rallymate_service import training_evaluation
from rallymate_service.indicator_records import IndicatorRecordError, IndicatorRecords
from rallymate_service.user_demo import (
    UserDemoResultError, build_user_demo_result, open_indicator_feature_records,
)
from tests import test_training_evaluation as evaluation_fixtures
from tests import test_user_demo as demo_fixtures


class TrainingEvaluationReuseTests(unittest.TestCase):
    def test_joint_entry_still_checks_complete_conflicts_and_event_codes(self):
        kept = evaluation_fixtures.TrainingEvaluationTests._record("kept")
        conflict = evaluation_fixtures.TrainingEvaluationTests._record("conflicted")
        changed = deepcopy(conflict)
        changed["unknown_proof"] = {"source_binding": "different"}
        wrong = evaluation_fixtures.TrainingEvaluationTests._record("wrong-event")
        wrong["event_code"] = "FS02"
        rows = [kept, deepcopy(kept), conflict, changed, wrong]
        with patch.object(training_evaluation, "validated_training_records",
                          wraps=training_evaluation.validated_training_records) as validate:
            evaluation, validated = training_evaluation.build_training_evaluation_with_records(iter(rows))
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(validated, [kept])
        self.assertIs(validated[0], kept)
        self.assertEqual(evaluation["input_validation"], {
            "duplicate_record_count": 1, "conflicting_record_count": 2,
            "event_code_mismatch_record_count": 1, "unidentified_record_count": 0,
        })
        self.assertEqual(evaluation, training_evaluation.build_training_evaluation(rows))

    def test_user_demo_reuses_validation_only_within_the_current_build(self):
        first = evaluation_fixtures.TrainingEvaluationTests._record("event")
        rows = [first, deepcopy(first)]
        summary = demo_fixtures.UserDemoResultTests()._summary()
        with patch.object(training_evaluation, "validated_training_records",
                          wraps=training_evaluation.validated_training_records) as validate:
            initial = build_user_demo_result(summary, rows, include_derived_assessments=False)
            self.assertEqual(validate.call_count, 1)
            self.assertTrue(initial["training_evaluation"]["available"])
            rows[1]["unknown_proof"] = {"different": True}
            changed = build_user_demo_result(summary, rows, include_derived_assessments=False)
            self.assertEqual(validate.call_count, 2)
        self.assertFalse(changed["training_evaluation"]["available"])
        self.assertEqual(changed["training_evaluation"]["input_validation"]["conflicting_record_count"], 2)
        self.assertTrue(all(not action["amplitudes"] for action in changed["actions"]))

    def test_lazy_result_keeps_bounded_handles_and_scalar_revalidation_needs_no_resolve(self):
        rows = [evaluation_fixtures.TrainingEvaluationTests._record(f"event-{i}") for i in range(12)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            with IndicatorRecords(path) as records:
                with patch.object(records.reader, "resolve_record", wraps=records.reader.resolve_record) as resolve:
                    evaluation, validated = training_evaluation.build_training_evaluation_with_records(records)
                    before_redundant_pass = resolve.call_count
                    # This was the second validation in build_user_demo_result.
                    repeated, validation = training_evaluation.validated_training_records(records)
                    # The scalar identity index now also makes a subsequent
                    # non-conflicting validation pass free of proof expansion.
                    self.assertEqual(resolve.call_count - before_redundant_pass, 0)
                self.assertEqual(len(validated), len(rows))
                self.assertTrue(all(not isinstance(row, dict) for row in validated))
                self.assertEqual([row.index for row in validated], [row.index for row in repeated])
                self.assertEqual(evaluation["input_validation"], validation)
                self.assertLessEqual(len(records.cache), 8)
                self.assertLessEqual(records.cache_bytes, 16 * 1024 * 1024)
            with self.assertRaisesRegex(IndicatorRecordError, "closed"):
                validated[0]["event_id"]

    def test_unused_tampered_proof_is_rejected_before_joint_evaluation(self):
        rows = [evaluation_fixtures.TrainingEvaluationTests._record("valid"), {
            "video_id": "test-video", "indicator_id": "unrecognized", "event_id": "ignored",
            "source_window": {"source_frames": list(range(1500))},
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "indicator-features.jsonl"
            with EvidencePoolWriter(root, video_id="test-video", source_id="job") as writer:
                writer.write_jsonl(path, rows)
            with closing(sqlite3.connect(root / POOL_FILENAME)) as connection:
                self.assertGreater(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
                connection.execute("UPDATE evidence SET payload=?", (b'{"tampered":true}',))
                connection.commit()
            with self.assertRaisesRegex(UserDemoResultError, "content hash mismatch"):
                with open_indicator_feature_records(path) as records:
                    training_evaluation.build_training_evaluation_with_records(records)

    def test_joint_evaluation_preserves_reader_cancellation_checks(self):
        cancelled = False
        callbacks = 0

        def check():
            nonlocal callbacks
            callbacks += 1
            if cancelled:
                raise RuntimeError("report cancelled")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indicator-features.jsonl"
            path.write_text(json.dumps(evaluation_fixtures.TrainingEvaluationTests._record("event")), encoding="utf-8")
            with open_indicator_feature_records(path, cancellation_callback=check) as records:
                opened_callbacks = callbacks
                cancelled = True
                with self.assertRaisesRegex(RuntimeError, "report cancelled"):
                    training_evaluation.build_training_evaluation_with_records(records)
                self.assertGreater(callbacks, opened_callbacks)


if __name__ == "__main__":
    unittest.main()
