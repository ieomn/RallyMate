import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_local_sam_native import MHR70_NAMES
from scripts.summarize_sam_profile import compare, read_records, summarize


class NativeProfileSummaryTests(unittest.TestCase):
    def fixture(self):
        return {"frame_index": 0, "processed_index": 0, "video_sha256": "a" * 64,
                "pts": 0, "time_base_num": 1, "time_base_den": 30, "timestamp_ms": 0,
                "image_size": [100, 100], "bbox_px": [0, 0, 100, 100],
                "person_track_id": 1, "selection_epoch": 0, "camera_reference_epoch": 0,
                "status": "ok", "keypoints_2d": {name: [1., 2.] for name in MHR70_NAMES},
                "keypoints_3d": {name: [1., 2., 3.] for name in MHR70_NAMES},
                "inference_ms": 10., "ipc_request_timing_ms": {"worker_round_trip_wait_ms": 12.}}

    def test_incomplete_active_tail_is_reported_but_complete_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.jsonl"
            path.write_text(json.dumps(self.fixture()) + '\n{"incomplete":', encoding="utf-8")
            self.assertEqual(len(read_records(path)[0]), 1)
            self.assertTrue(read_records(path)[1])
            path.write_text('{"invalid":\n', encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                read_records(path)

    def test_same_frame_ipc_timings_are_not_double_counted_for_two_subjects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "two-subjects.jsonl"
            path.write_text("\n".join(json.dumps(dict(self.fixture(), person_track_id=track)) for track in (1, 2)), encoding="utf-8")
            report = summarize(path)
            self.assertEqual(report["complete_records"], 2)
            self.assertEqual(report["distinct_source_frames"], 1)
            self.assertEqual(report["statistics"]["ipc_request_timing_ms.worker_round_trip_wait_ms"]["count"], 1)

    def test_exact_result_requires_matching_sources_and_all_record_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a.jsonl", Path(directory) / "b.jsonl"
            first.write_text(json.dumps(self.fixture()), encoding="utf-8")
            second.write_text(first.read_text(), encoding="utf-8")
            self.assertTrue(compare(first, second)["geometry_exact_numeric_equal"])
            second.write_text(json.dumps(dict(self.fixture(), video_sha256="b" * 64)), encoding="utf-8")
            self.assertFalse(compare(first, second)["geometry_exact_numeric_equal"])
            second.write_text(first.read_text() + "\n" + first.read_text(), encoding="utf-8")
            self.assertFalse(compare(first, second)["geometry_exact_numeric_equal"])
