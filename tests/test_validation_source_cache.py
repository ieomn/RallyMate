"""Bounded source-row reuse must never memoize a feature's validation verdict."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_vision import validation
from tests.test_sam_feature_validation import _write_bundle


class ModelSourceCacheTests(unittest.TestCase):
    def test_only_model_rows_are_cached_and_zero_budget_is_original_lookup(self):
        with validation._validation_index() as connection:
            writer = validation._DiskMap(connection, "model-sources")
            writer[7] = {"timestamp_ms": 40, "binding": {"pts": 40}, "joints": ["ankle"]}
            for budget, expected_queries in ((0, 3), (16384, 1)):
                with self.subTest(budget=budget):
                    sources = validation._ReadOnlyModelSources(connection, cache_budget_bytes=budget)
                    queries = []
                    connection.set_trace_callback(queries.append)
                    for _ in range(3):
                        self.assertEqual(sources[7]["binding"]["pts"], 40)
                    connection.set_trace_callback(None)
                    self.assertEqual(sum("SELECT value" in sql for sql in queries), expected_queries)
                    self.assertLessEqual(sources.cache_bytes, budget)
            other = validation._DiskMap(connection, "features")
            other[7] = {"value": 1}
            other[7]["value"] = 2
            self.assertEqual(other[7], {"value": 1})

    def test_nested_cached_values_and_map_are_immutable(self):
        with validation._validation_index() as connection:
            writer = validation._DiskMap(connection, "model-sources")
            writer[1] = {"timestamp_ms": 40, "binding": {"pts": 40}, "joints": ["ankle"]}
            sources = validation._ReadOnlyModelSources(connection, cache_budget_bytes=16384)
            row = sources[1]
            with self.assertRaises(TypeError):
                row["binding"]["pts"] = 99
            with self.assertRaises(TypeError):
                row["joints"][0] = "fabricated"
            with self.assertRaises(TypeError):
                sources[1] = {}
            with self.assertRaises(TypeError):
                del sources[1]
            self.assertIs(row, sources[1])
            self.assertEqual(sources[1]["binding"]["pts"], 40)

    def test_lru_eviction_missing_keys_and_oversized_rows_obey_byte_budget(self):
        with validation._validation_index() as connection:
            writer = validation._DiskMap(connection, "model-sources")
            for i in range(12):
                writer[i] = {"timestamp_ms": i, "binding": {"pts": i}, "joints": ["ankle"]}
            writer[99] = {"payload": "x" * 10000}
            one = validation._ReadOnlyModelSources(connection, cache_budget_bytes=65536)
            one[0]
            budget = one.cache_bytes * 3
            sources = validation._ReadOnlyModelSources(connection, cache_budget_bytes=budget)
            for i in range(12):
                self.assertEqual(sources[i]["timestamp_ms"], i)
                self.assertLessEqual(sources.cache_bytes, budget)
            self.assertGreater(sources._cache_evictions, 0)
            sources[11]  # Refresh newest row before attempting an oversized entry.
            hits = sources._cache_hits
            before = sources.cache_bytes
            self.assertEqual(len(sources[99]["payload"]), 10000)
            self.assertEqual(sources.cache_bytes, before)
            sources[11]
            self.assertEqual(sources._cache_hits, hits + 1)
            self.assertIsNone(sources.get(1000))
            self.assertLessEqual(sources._cache_peak_bytes, budget)

    def test_indices_do_not_reuse_rows_between_validation_calls(self):
        for pts in (40, 99):
            with validation._validation_index() as connection:
                validation._DiskMap(connection, "model-sources")[0] = {"binding": {"pts": pts}}
                sources = validation._ReadOnlyModelSources(connection, cache_budget_bytes=16384)
                self.assertEqual(sources[0]["binding"]["pts"], pts)
                self.assertEqual(sources._cache_misses, 1)


class SourceCacheValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        _write_bundle(self.path)

    def validate(self, budget):
        with patch.object(validation, "_MODEL_SOURCE_CACHE_BYTES", budget):
            return validation._validate_scoring_artifacts(
                self.path, frame_count=4, frame_timestamps={i: i * 40 for i in range(4)},
                frame_processed_indexes=range(4), frames_path=self.path / "frames.jsonl",
            )

    def test_full_scoring_validation_result_matches_without_cache(self):
        self.assertEqual(self.validate(0), self.validate(16 * 1024 * 1024))

    def test_original_artifact_sha_is_still_checked_in_each_call(self):
        self.validate(16 * 1024 * 1024)
        with (self.path / "features.jsonl").open("ab") as handle:
            handle.write(b"\n")
        for budget in (0, 16 * 1024 * 1024):
            with self.subTest(budget=budget), self.assertRaisesRegex(
                    validation.OutputValidationError, "SHA256 mismatch: features.jsonl"):
                self.validate(budget)

    def test_every_feature_is_rechecked_after_source_cache_is_warm(self):
        feature = json.loads((self.path / "features.jsonl").read_text(encoding="utf-8"))
        event = json.loads((self.path / "events.jsonl").read_text(encoding="utf-8"))
        primary = {i: json.loads(row) for i, row in enumerate(
            (self.path / "primary-player.jsonl").read_text(encoding="utf-8").splitlines())}
        with validation._validation_index() as connection:
            sources = validation._model_source_index(self.path / "frames.jsonl", primary, connection)
            check = lambda row: validation._validate_model_feature_source(row, event, sources, "feature")
            check(feature)
            mutations = {
                "sha": lambda row: row["source_window"]["source_bindings"][0].update(video_sha256="0" * 64),
                "pts": lambda row: row["source_window"]["source_bindings"][0].update(pts=-1),
                "person": lambda row: row["source_window"]["source_bindings"][0].update(person_track_id=999),
                "selection_epoch": lambda row: row["source_window"]["source_bindings"][0].update(selection_epoch=999),
                "camera_epoch": lambda row: row["source_window"]["source_bindings"][0].update(camera_reference_epoch=999),
                "mask": lambda row: row["geometry_mask"]["left_ankle"].__setitem__(0, not row["geometry_mask"]["left_ankle"][0]),
            }
            for name, mutate in mutations.items():
                with self.subTest(mutation=name):
                    modified = copy.deepcopy(feature)
                    mutate(modified)
                    with self.assertRaises(validation.OutputValidationError):
                        check(modified)
                    check(feature)
            self.assertGreater(sources._cache_hits, 0)

    def test_warm_cache_still_checks_cancellation(self):
        feature = json.loads((self.path / "features.jsonl").read_text(encoding="utf-8"))
        event = json.loads((self.path / "events.jsonl").read_text(encoding="utf-8"))
        primary = {i: json.loads(row) for i, row in enumerate(
            (self.path / "primary-player.jsonl").read_text(encoding="utf-8").splitlines())}
        with validation._validation_index() as connection:
            sources = validation._model_source_index(self.path / "frames.jsonl", primary, connection)
            validation._validate_model_feature_source(feature, event, sources, "feature")
            def cancel():
                raise RuntimeError("cancelled")
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                validation._validate_model_feature_source(feature, event, sources, "feature", cancel)


if __name__ == "__main__":
    unittest.main()
