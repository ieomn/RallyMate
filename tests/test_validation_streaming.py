"""Disk-backed joins retain full validation semantics without collecting evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from rallymate_vision import validation
from tests.test_sam_feature_validation import _write_bundle


class ValidationStreamingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        _write_bundle(self.path)

    def validate(self, **kwargs):
        return validation._validate_scoring_artifacts(
            self.path, frame_count=4, frame_timestamps={i: i * 40 for i in range(4)},
            frame_processed_indexes=range(4), frames_path=self.path / "frames.jsonl", **kwargs,
        )

    def update_hash(self, filename):
        summary_path = self.path / "scoring-loop-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["artifact_sha256"][filename] = hashlib.sha256(
            (self.path / filename).read_bytes()).hexdigest()
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

    def test_native_bundle_never_calls_full_jsonl_loader(self):
        with patch.object(validation, "_load_jsonl", side_effect=AssertionError("unbounded JSONL load")):
            result = self.validate()
        self.assertEqual(result["validated_features"], 1)
        self.assertEqual(result["validated_scores"], 1)

    def test_duplicate_join_keys_still_rejected_in_every_stream(self):
        for name, message in (
            ("events.jsonl", "duplicate event_id"),
            ("features.jsonl", "duplicate event feature"),
            ("indicator-features.jsonl", "duplicate indicator event record"),
            ("scores.jsonl", "duplicate score event record"),
        ):
            with self.subTest(stream=name):
                path = self.path / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original * 2)
                    self.update_hash(name)
                    with self.assertRaisesRegex(validation.OutputValidationError, message):
                        self.validate()
                finally:
                    path.write_bytes(original)
                    self.update_hash(name)

    def test_missing_score_is_not_hidden_by_disk_index(self):
        (self.path / "scores.jsonl").write_bytes(b"")
        self.update_hash("scores.jsonl")
        with self.assertRaisesRegex(validation.OutputValidationError, "indicator/scores record count mismatch"):
            self.validate()

    def test_hash_validation_still_binds_original_artifact_bytes(self):
        with (self.path / "features.jsonl").open("ab") as handle:
            handle.write(b"\n")
        with self.assertRaisesRegex(validation.OutputValidationError, "SHA256 mismatch: features.jsonl"):
            self.validate()

    def pool_bundle(self, *, source_id="structural-fixture"):
        from rallymate_scoring.artifact_evidence import EvidencePoolWriter

        filenames = ("events.jsonl", "features.jsonl", "indicator-features.jsonl", "scores.jsonl")
        with EvidencePoolWriter(self.path, video_id="fixture-video", source_id=source_id) as writer:
            for filename in filenames:
                rows = [json.loads(line) for line in (self.path / filename).read_text(encoding="utf-8").splitlines()]
                writer.write_jsonl(self.path / filename, rows)
        summary_path = self.path / "scoring-loop-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["evidence_storage"] = writer.manifest
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        for filename in filenames:
            self.update_hash(filename)

    def test_pooled_bundle_is_validated_with_the_same_source_and_feature_checks(self):
        native = self.validate()
        self.pool_bundle()
        self.assertEqual(self.validate(), native)

    def test_missing_or_tampered_pool_manifest_is_rejected(self):
        self.pool_bundle()
        summary_path = self.path / "scoring-loop-summary.json"
        original = summary_path.read_text(encoding="utf-8")
        for mutation in ("missing", "hash", "video"):
            with self.subTest(mutation=mutation):
                summary = json.loads(original)
                if mutation == "missing":
                    summary.pop("evidence_storage")
                elif mutation == "hash":
                    summary["evidence_storage"]["sha256"] = "0" * 64
                else:
                    summary["evidence_storage"]["video_id"] = "another-video"
                summary_path.write_text(json.dumps(summary), encoding="utf-8")
                with self.assertRaisesRegex(validation.OutputValidationError, "evidence manifest"):
                    self.validate()

    def test_pool_is_bound_to_event_source_not_only_video_id(self):
        self.pool_bundle(source_id="different-task-same-video")
        with self.assertRaisesRegex(validation.OutputValidationError, "event source differs"):
            self.validate()

    def write_features(self, count):
        path = self.path / "features.jsonl"
        with path.open(encoding="utf-8") as handle:
            base = json.loads(handle.readline())
        original_name = base["feature_name"]
        # Mimic irrelevant repeated full-video provenance. Values/proofs used by
        # validation are real fixture records; no source check is mocked out.
        base["provenance"] = {"coordinate_metadata": {"source_ledger": "x" * (256 * 1024)}}
        with path.open("w", encoding="utf-8") as handle:
            for index in range(count):
                base["feature_name"] = original_name if index == 0 else f"extra-feature-{index}"
                handle.write(json.dumps(base) + "\n")
        self.update_hash("features.jsonl")

    def test_peak_allocations_do_not_scale_with_repeated_feature_provenance(self):
        # Warm imports/fixtures before measuring the validator itself.
        self.validate()
        peaks = []
        for count in (8, 160):
            self.write_features(count)
            tracemalloc.start()
            try:
                result = self.validate()
                peaks.append(tracemalloc.get_traced_memory()[1])
            finally:
                tracemalloc.stop()
            self.assertEqual(result["validated_features"], count)
        # The input grows by ~38 MiB. A list/index retaining complete feature
        # objects would grow by that amount; the disk join remains near-flat.
        self.assertLess(peaks[1] - peaks[0], 8 * 1024 * 1024, peaks)
        self.assertLess(peaks[1], 16 * 1024 * 1024, peaks)

    def test_cancellation_propagates_and_removes_temporary_index(self):
        self.write_features(80)
        created = []
        real_temporary_directory = tempfile.TemporaryDirectory

        def capture_directory(*args, **kwargs):
            value = real_temporary_directory(*args, **kwargs)
            created.append(Path(value.name))
            return value

        class Cancelled(RuntimeError):
            pass

        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 60:
                raise Cancelled("operator stopped validation")

        with patch.object(validation.tempfile, "TemporaryDirectory", capture_directory):
            with self.assertRaisesRegex(Cancelled, "operator stopped"):
                self.validate(cancellation_callback=cancel)
        self.assertTrue(created)
        self.assertTrue(all(not path.exists() for path in created))


if __name__ == "__main__":
    unittest.main()
