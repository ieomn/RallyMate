"""Exact geometry-context reuse retains independent feature/source checks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_vision import validation
from tests.test_sam_feature_validation import sam_feature, _write_bundle


class ModelContextReuseTests(unittest.TestCase):
    def setUp(self):
        self.checks = validation._ModelContextChecks(max_bytes=16 * 1024 * 1024)
        self.original = sam_feature()

    def validate(self, record):
        return validation._validate_feature_result(record, "feature", context_checks=self.checks)

    def test_equal_contexts_reused_without_sharing_mutable_evidence(self):
        self.validate(self.original)
        copy_of_original = copy.deepcopy(self.original)
        copy_of_original["value"] = 3.0
        copy_of_original["source_frames"] = [10]
        self.validate(copy_of_original)
        self.assertEqual(self.checks.hits, 1)
        self.assertEqual(self.original, sam_feature())
        copy_of_original["source_window"]["source_bindings"][0]["pts"] = 0
        with self.assertRaisesRegex(validation.OutputValidationError, "source_window"):
            self.validate(copy_of_original)
        self.validate(self.original)

    def test_warm_context_keeps_every_feature_check(self):
        self.validate(self.original)
        mutations = {
            "name": lambda r: r.update(feature_name=""),
            "version": lambda r: r.update(feature_version=""),
            "confidence": lambda r: r.update(confidence=0.5),
            "valid": lambda r: r.update(valid=1),
            "reason": lambda r: r.update(reason=""),
            "frames_outside": lambda r: r.update(source_frames=[100]),
            "frames_empty": lambda r: r.update(source_frames=[]),
            "frame_float": lambda r: r.update(source_frames=[9.0]),
            "frame_bool": lambda r: r.update(source_frames=[True]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.original)
                mutate(record)
                with self.assertRaises(validation.OutputValidationError):
                    self.validate(record)

    def test_mutated_context_fields_are_checked_after_warmup(self):
        self.validate(self.original)
        mutations = {
            "provenance": lambda r: r["measurement_provenance"].update(scoring_allowed=True),
            "video": lambda r: r["source_window"].update(video_sha256="b" * 64),
            "person": lambda r: r["source_window"]["source_bindings"][0].update(person_track_id=99),
            "selection": lambda r: r["source_window"]["source_bindings"][0].update(selection_epoch=99),
            "camera": lambda r: r["source_window"]["source_bindings"][0].update(camera_reference_epoch=99),
            "pts": lambda r: r["source_window"]["source_bindings"][0].update(pts=1),
            "mask_length": lambda r: r["geometry_mask"].update(left_ankle=[True]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.original)
                mutate(record)
                with self.assertRaises(validation.OutputValidationError):
                    self.validate(record)
                self.validate(self.original)

    def test_json_equivalent_but_invalid_python_types_do_not_hit_cache(self):
        self.validate(self.original)
        mutations = {
            "window_tuple": lambda r: r["source_window"].update(source_frames=tuple(r["source_window"]["source_frames"])),
            "mask_tuple": lambda r: r["geometry_mask"].update(left_ankle=tuple(r["geometry_mask"]["left_ankle"])),
            "mask_integer": lambda r: r["geometry_mask"]["left_ankle"].__setitem__(0, 1),
            "start_float": lambda r: r["source_window"].update(start_ms=300.0),
            "pts_float": lambda r: r["source_window"]["source_bindings"][0].update(pts=75300.0),
            "zero_false": lambda r: r["measurement_provenance"].update(scoring_allowed=0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.original)
                mutate(record)
                with self.assertRaises(validation.OutputValidationError):
                    self.validate(record)

    def test_exact_keys_ignore_aliasing_but_preserve_complete_content(self):
        self.validate(self.original)
        roundtripped = json.loads(json.dumps(self.original))
        self.validate(roundtripped)
        self.assertEqual(self.checks.hits, 1)
        roundtripped["source_window"]["extra_metadata"] = "new"
        self.validate(roundtripped)
        self.assertEqual(self.checks.misses, 2)

    def test_lru_and_oversized_contexts_stay_within_resident_byte_budget(self):
        self.validate(self.original)
        budget = self.checks.cache_bytes * 3
        cache = validation._ModelContextChecks(max_bytes=budget)
        for i in range(30):
            record = copy.deepcopy(self.original)
            record["source_window"]["extra_metadata"] = str(i)
            validation._validate_feature_result(record, "feature", context_checks=cache)
            self.assertLessEqual(cache.cache_bytes, budget)
        self.assertGreater(cache.evictions, 0)
        before = cache.cache_bytes
        record["source_window"]["extra_metadata"] = "x" * (budget + 1)
        validation._validate_feature_result(record, "feature", context_checks=cache)
        self.assertEqual(cache.cache_bytes, before)
        self.assertLessEqual(cache.peak_bytes, budget)

    def test_zero_and_tiny_budgets_keep_original_validation(self):
        for budget in (0, 1, 100):
            with self.subTest(budget=budget):
                cache = validation._ModelContextChecks(max_bytes=budget)
                for _ in range(2):
                    validation._validate_feature_result(self.original, "feature", context_checks=cache)
                self.assertEqual(cache.hits, 0)
                self.assertEqual(cache.cache_bytes, 0)
                modified = copy.deepcopy(self.original)
                modified["geometry_mask"]["left_ankle"][0] = 1
                with self.assertRaises(validation.OutputValidationError):
                    validation._validate_feature_result(modified, "feature", context_checks=cache)


class ContextReuseIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        _write_bundle(self.directory)

    def validate(self, budget, callback=None):
        with patch.object(validation, "_MODEL_CONTEXT_CACHE_BYTES", budget):
            return validation._validate_scoring_artifacts(
                self.directory, frame_count=4, frame_timestamps={i: i * 40 for i in range(4)},
                frame_processed_indexes=range(4), frames_path=self.directory / "frames.jsonl",
                cancellation_callback=callback,
            )

    def test_complete_bundle_results_identical_with_and_without_reuse(self):
        self.assertEqual(self.validate(0), self.validate(16 * 1024 * 1024))

    def test_each_run_checks_source_hash_and_cancellation(self):
        self.validate(16 * 1024 * 1024)
        def cancel():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.validate(16 * 1024 * 1024, cancel)
        with (self.directory / "features.jsonl").open("ab") as handle:
            handle.write(b"\n")
        with self.assertRaisesRegex(validation.OutputValidationError, "SHA256 mismatch: features.jsonl"):
            self.validate(16 * 1024 * 1024)

    def test_structurally_valid_changed_mask_still_fails_actual_source_binding(self):
        feature = json.loads((self.directory / "features.jsonl").read_text(encoding="utf-8"))
        event = json.loads((self.directory / "events.jsonl").read_text(encoding="utf-8"))
        primary = {i: json.loads(row) for i, row in enumerate(
            (self.directory / "primary-player.jsonl").read_text(encoding="utf-8").splitlines())}
        cache = validation._ModelContextChecks(max_bytes=16 * 1024 * 1024)
        with validation._validation_index() as connection:
            sources = validation._model_source_index(self.directory / "frames.jsonl", primary, connection)
            validation._validate_feature_result(feature, "feature", context_checks=cache)
            validation._validate_model_feature_source(feature, event, sources, "feature")
            altered = copy.deepcopy(feature)
            altered["geometry_mask"]["left_ankle"][0] = not altered["geometry_mask"]["left_ankle"][0]
            validation._validate_feature_result(altered, "feature", context_checks=cache)
            # Its shape is valid and can be reused, but its actual proof remains
            # a separate comparison against the immutable decoder/model source.
            validation._validate_feature_result(altered, "feature", context_checks=cache)
            with self.assertRaisesRegex(validation.OutputValidationError, "differs from source geometry"):
                validation._validate_model_feature_source(altered, event, sources, "feature")


if __name__ == "__main__":
    unittest.main()
