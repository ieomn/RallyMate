import copy
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

import numpy as np

from scripts import sam3d_pipeline_comparison as comparison
from rallymate_vision.pose.metadata import MHR70_NAMES, sha256_file


class StreamingComparisonTests(unittest.TestCase):
    def test_jsonl_is_lazy_and_a_late_invalid_or_oversized_row_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            path.write_text('{"frame":1}\nnot json\n', encoding="utf-8")
            stream = comparison.rows(path)
            self.assertEqual(next(stream), {"frame": 1})
            with self.assertRaises(json.JSONDecodeError):
                next(stream)
            path.write_text('{"payload":"' + 'x' * 100 + '"}\n', encoding="utf-8")
            with patch.object(comparison, "MAX_JSONL_ROW_BYTES", 64), self.assertRaisesRegex(ValueError, "64 MiB"):
                list(comparison.rows(path))

    def test_difference_counts_and_largest_changes_include_values_beyond_the_example_cap(self):
        count = 1000
        changes = comparison.recursive_changes(
            [{"n": 0, "status": "old"} for _ in range(count)],
            [{"n": index + 1, "status": "new"} for index in range(count)])
        self.assertEqual(changes["numeric_changes"], count)
        self.assertEqual(changes["nonnumeric_changes"], count)
        self.assertEqual(changes["largest_numeric_changes"][0]["abs_delta"], count)
        self.assertEqual(changes["largest_numeric_changes"][0]["path"], "/999/n")
        self.assertEqual(len(changes["nonnumeric_differences"]), comparison.MAX_DIFFERENCE_EXAMPLES)
        self.assertTrue(changes["examples_truncated"])
        self.assertTrue(changes["nonnumeric_examples_truncated"])

    def test_diagnostic_value_preview_never_keeps_a_whole_changed_container(self):
        old = {"value": ["x" * 10000] * 1000}
        changes = comparison.recursive_changes(old, {"value": "changed"})
        self.assertEqual(changes["nonnumeric_changes"], 1)
        self.assertTrue(changes["nonnumeric_differences"][0]["old"]["value_truncated"])
        self.assertTrue(changes["examples_truncated"])
        self.assertLess(len(json.dumps(changes)), 2000)

    def test_stream_comparison_exhausts_extra_rows_and_does_not_short_circuit_late_failures(self):
        consumed = []
        def actual():
            for index in range(7):
                consumed.append(index)
                yield {"value": index + 10}
        result = comparison.compare_rows(({"value": i} for i in range(2)), actual(),
            projections={"value": lambda row: row["value"]}, differences={"all": lambda row: row})
        self.assertEqual(consumed, list(range(7)))
        self.assertEqual(result["actual_count"], 7)
        self.assertFalse(result["exact"]["value"])
        self.assertEqual(result["differences"]["all"]["numeric_changes"], 2)
        self.assertEqual(result["differences"]["all"]["nonnumeric_changes"], 1)
        def corrupt_tail():
            yield {"value": 99}
            raise ValueError("late corruption")
        with self.assertRaisesRegex(ValueError, "late corruption"):
            comparison.compare_rows(iter([{"value": 0}]), corrupt_tail(), projections={"value": lambda row: row["value"]})

    def test_large_difference_stream_has_bounded_retained_memory_and_exact_totals(self):
        count = 20000
        def values(offset):
            for index in range(count):
                yield {"status": str(offset), "numbers": [index + offset] * 16}
        tracemalloc.start()
        try:
            result = comparison.compare_rows(values(0), values(1), projections={"all": lambda row: row},
                differences={"all": lambda row: row})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertEqual(result["actual_count"], count)
        self.assertEqual(result["differences"]["all"]["numeric_changes"], count * 16)
        self.assertEqual(result["differences"]["all"]["nonnumeric_changes"], count)

    @staticmethod
    def snapshot(root, row, cache):
        value = row.get("value", 0)
        return {"binding": {"frame_index": row["frame_index"]},
                "keypoints_2d": np.full((70, 2), value), "keypoints_3d": np.full((70, 3), value),
                "vertices": np.full((3, 3), value), "camera": np.full(3, value), "focal": np.full(1, value),
                "faces": np.array([[0, 1, 2]], dtype=np.int32)}

    def test_full_geometry_maxima_and_counts_remain_exact_after_subject_examples_fill(self):
        count = comparison.MAX_SUBJECT_EXAMPLES + 3
        def subjects(candidate=False):
            return ({"status": "ok", "frame_index": i, "value": 7 if candidate and i == count - 1 else 0} for i in range(count))
        with patch.object(comparison, "native_snapshot", side_effect=self.snapshot) as reader:
            result = comparison.compare_native(Path("a"), Path("b"), subjects(), subjects(True))
        self.assertEqual(reader.call_count, count * 2)
        self.assertEqual(result["compared_subjects"], count)
        self.assertTrue(result["all_subjects_compared"])
        self.assertFalse(result["all_exact"])
        self.assertEqual(result["max_abs_by_field"]["vertices"], 7)
        self.assertEqual(len(result["per_subject"]), comparison.MAX_SUBJECT_EXAMPLES)
        self.assertTrue(result["per_subject_examples_truncated"])

    def test_unpaired_geometry_is_still_loaded_and_skipped_counts_are_not_truncated(self):
        count = comparison.MAX_SUBJECT_EXAMPLES + 3
        subjects = ({"status": "ok", "frame_index": i} for i in range(count))
        with patch.object(comparison, "native_snapshot", side_effect=self.snapshot) as reader:
            result = comparison.compare_native(Path("a"), Path("b"), iter([]), subjects)
        self.assertEqual(reader.call_count, count)
        self.assertFalse(result["all_subjects_compared"])
        self.assertFalse(result["all_exact"])
        self.assertEqual(result["skipped_subject_count"], count)
        self.assertEqual(len(result["skipped_subjects"]), comparison.MAX_SUBJECT_EXAMPLES)
        self.assertTrue(result["skipped_examples_truncated"])

    def test_nonfinite_geometry_after_example_limit_still_fails(self):
        count = comparison.MAX_SUBJECT_EXAMPLES + 1
        subjects = lambda bad: ({"status": "ok", "frame_index": i, "value": float("nan") if bad and i == count - 1 else 0} for i in range(count))
        with patch.object(comparison, "native_snapshot", side_effect=self.snapshot), self.assertRaisesRegex(ValueError, "non-finite"):
            comparison.compare_native(Path("a"), Path("b"), subjects(False), subjects(True))

    def test_faces_cache_is_bounded_without_omitting_topology_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, cache = Path(directory), {}
            np.savez(root / "mesh.npz", vertices=np.zeros((3, 3)), camera=np.zeros(3), focal=np.ones(1))
            mesh = {"path": "mesh.npz", "sha256": sha256_file(root / "mesh.npz"), "vertex_count": 3, "face_count": 1}
            for i in range(comparison.MAX_FACES_CACHE_ENTRIES + 3):
                np.save(root / f"faces-{i}.npy", np.array([[0, 1, 2]], dtype=np.int32))
                artifact = {**mesh, "faces_path": f"faces-{i}.npy", "faces_sha256": sha256_file(root / f"faces-{i}.npy")}
                row = {key: 0 for key in comparison.BINDING_FIELDS}
                row.update(status="ok", mesh_artifact=artifact,
                    keypoints_2d={name: [0., 0.] for name in MHR70_NAMES}, keypoints_3d={name: [0., 0., 0.] for name in MHR70_NAMES})
                result = comparison.native_snapshot(root, row, cache)
                self.assertEqual(result["faces"].shape, (1, 3))
                self.assertLessEqual(len(cache), comparison.MAX_FACES_CACHE_ENTRIES)
            np.save(root / "bad-faces.npy", np.array([[0, 1, 3]], dtype=np.int32))
            bad = copy.deepcopy(row)
            bad["mesh_artifact"].update(faces_path="bad-faces.npy", faces_sha256=sha256_file(root / "bad-faces.npy"))
            with self.assertRaisesRegex(ValueError, "topology"):
                comparison.native_snapshot(root, bad, cache)

    def test_stream_source_binding_validation_rejects_missing_and_reordered_rows(self):
        bindings = [{"frame_index": i, "pts": i, "time_base_num": 1, "time_base_den": 30, "image_size": [10, 10]} for i in range(3)]
        source = {"frames": [[binding] for binding in bindings], "video_sha256": "a" * 64}
        frames = [{"frame": {"index": i, "processed_index": i, "pts": i, "time_base_num": 1, "time_base_den": 30, "video_sha256": "a" * 64}} for i in range(3)]
        native = [{**binding, "video_sha256": "a" * 64, "processed_index": i, "person_track_id": 1} for i, binding in enumerate(bindings)]
        comparison.validate_source_bindings(iter(native), iter(frames), source)
        for bad in ([native[0], native[2]], [native[1], native[0], native[2]], native + [native[-1]]):
            with self.assertRaises(ValueError):
                comparison.validate_source_bindings(iter(bad), iter(frames), source)
        with self.assertRaises(ValueError):
            comparison.validate_source_bindings(iter(native), iter(frames[:-1]), source)


if __name__ == "__main__":
    unittest.main()
