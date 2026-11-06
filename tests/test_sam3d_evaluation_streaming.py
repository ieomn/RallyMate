from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

from rallymate_vision.sam3d_evaluation import (
    CORE_JOINTS,
    RTMPOSE_RAW_SCORE_SEMANTICS,
    compact_evaluation_record,
    evaluate_sam3d_jsonl,
    evaluate_sam3d_records,
)
from tests.test_sam3d_evaluation import _frame


class Sam3dEvaluationStreamingTests(unittest.TestCase):
    def assert_projection_equivalent(self, records, **kwargs):
        before = deepcopy(records)
        projected = [compact_evaluation_record(record) for record in records]
        self.assertEqual(evaluate_sam3d_records(records, **kwargs),
                         evaluate_sam3d_records(projected, **kwargs))
        self.assertEqual(records, before)

    def test_projection_drops_native_payload_but_keeps_core_diagnostics(self):
        records = [_frame(i, offset=i * 3) for i in range(4)]
        for record in records:
            record.update(camera_intrinsics=[800, 800, 400, 300],
                          mesh_vertices=[[1, 2, 3]] * 2000, stage_ms={"backbone": 10},
                          measurements={"precomputed": "not consumed by evaluator"})
            for field in ("keypoints_2d", "keypoints_3d"):
                record[field]["unused_native_joint"] = [1, 2, 3]
            record["baseline"]["keypoints_2d"]["unused_native_joint"] = [1, 2]
            record["baseline"]["keypoint_scores"]["unused_native_joint"] = 0.99
            record["baseline"]["debug_payload"] = [1] * 2000
        self.assert_projection_equivalent(records)
        compact = compact_evaluation_record(records[0])
        self.assertNotIn("mesh_vertices", compact)
        self.assertEqual(set(compact["keypoints_3d"]), set(CORE_JOINTS))
        self.assertNotIn("debug_payload", compact["baseline"])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(records[0])) / 4)

    def test_failures_duplicates_malformed_binding_and_continuity_are_preserved(self):
        records = [_frame(i, offset=i * 3) for i in range(12)]
        records[1]["status"] = "failed"
        records[2]["status"] = "skipped"
        records[3]["time_base_den"] = 0
        records[4]["timestamp_ms"] += 5
        records[5]["selection_epoch"] = 2
        records[6]["coordinate_system_2d"] = "roi_pixels"
        records[7]["units_3d"] = "pixels"
        records[8]["keypoints_3d"]["left_wrist"] = [None, 1, 2]
        records[9]["baseline"]["keypoint_scores"]["left_wrist"] = 0.1
        records.extend([deepcopy(records[10]), None, {}, {"schema_version": "wrong"}])
        self.assert_projection_equivalent(records, max_frame_gap=2, max_gap_ms=90)

    def test_baseline_binding_and_raw_score_contract_are_not_normalized_or_repaired(self):
        binding_fields = ("video_sha256", "frame_index", "pts", "time_base_num", "time_base_den",
                          "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch")
        for binding_change in ("matched", "missing_field", "mismatch", "wrong_size", "list", "none"):
            for semantics in ("absent", RTMPOSE_RAW_SCORE_SEMANTICS, "probability"):
                with self.subTest(binding_change=binding_change, semantics=semantics):
                    record = _frame()
                    binding = {field: record[field] for field in binding_fields}
                    binding.update(image_size=record["image_size"], ignored_payload=[7] * 1000)
                    if binding_change == "missing_field":
                        del binding["pts"]
                    elif binding_change == "mismatch":
                        binding["person_track_id"] = 99
                    elif binding_change == "wrong_size":
                        binding["image_size"] = [1, 1]
                    elif binding_change == "list":
                        binding = [1, 2]
                    elif binding_change == "none":
                        binding = None
                    record["baseline"].update(model="rtmpose_rerun_on_same_manifest", source_binding=binding)
                    record["baseline"]["keypoint_scores"]["left_wrist"] = 1.23
                    if semantics != "absent":
                        record["baseline"]["confidence_semantics"] = semantics
                    self.assert_projection_equivalent([record])

    def test_missing_or_malformed_geometry_and_baseline_fields_stay_explicit(self):
        for field in ("keypoints_2d", "keypoints_3d", "baseline"):
            for value in (None, [], "invalid", {}):
                with self.subTest(field=field, value=value):
                    record = _frame()
                    record[field] = value
                    self.assert_projection_equivalent([record])
        for field in ("provenance", "keypoint_scores", "keypoints_2d"):
            record = _frame()
            del record["baseline"][field]
            self.assert_projection_equivalent([record])

    def test_jsonl_preserves_report_parameters_and_original_archive(self):
        records = [_frame(i, time_ms=t, offset=t / 5) for i, t in enumerate((0, 25, 85, 130))]
        kwargs = dict(max_gap_ms=75, baseline_min_confidence=.5, reversal_cosine=-.7)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sam3d-frames.jsonl"
            original = "\n" + "\n\n".join(json.dumps(record) for record in records) + "\n"
            path.write_text(original, encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(evaluate_sam3d_jsonl(path, **kwargs), evaluate_sam3d_records(records, **kwargs))
            self.assertEqual(path.read_bytes(), before)

    def test_jsonl_releases_full_rows_before_loading_the_next(self):
        class NativeRow(dict):
            pass

        references = []
        decode = json.loads

        def tracked_decode(line):
            # Compact rows may retain core point values, but never the original
            # envelope containing camera/mesh/debug payloads.
            self.assertFalse(any(reference() is not None for reference in references))
            record = NativeRow(decode(line))
            references.append(weakref.ref(record))
            return record

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sam3d-frames.jsonl"
            path.write_text("\n".join(json.dumps(_frame(i)) for i in range(6)), encoding="utf-8")
            with patch("rallymate_vision.sam3d_evaluation.json.loads", side_effect=tracked_decode):
                result = evaluate_sam3d_jsonl(path)
            self.assertEqual(result["counts"]["input_records"], 6)
            self.assertFalse(any(reference() is not None for reference in references))

    def test_invalid_json_is_not_silently_removed_from_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sam3d-frames.jsonl"
            path.write_text(json.dumps(_frame()) + "\n{broken\n", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                evaluate_sam3d_jsonl(path)


if __name__ == "__main__":
    unittest.main()
