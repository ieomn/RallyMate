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

    def summarize_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            return summarize(path)

    def identified(self, request, session="a" * 32, **changes):
        return {**self.fixture(), "ipc_request_identity": {"version": "sam-ipc-request-v1",
            "session_id": session, "request_id": request}, **changes}

    def test_one_request_across_eight_frames_is_counted_once(self):
        report = self.summarize_rows([self.identified(7, frame_index=frame, processed_index=frame,
            person_track_id=track) for frame in range(8) for track in (1, 2)])
        result = report["statistics"]["ipc_request_timing_ms.worker_round_trip_wait_ms"]
        self.assertEqual((result["count"], result["sum"]), (1, 12.))
        self.assertEqual(report["ipc_request_accounting"]["status"], "exact")
        self.assertEqual(report["ipc_request_accounting"]["exact_requests"], 1)
        self.assertEqual(report["ipc_request_accounting"]["duplicate_exact_records"], 15)
        self.assertTrue(report["ipc_request_accounting"]["exact_for_saved_timing_records"])

    def test_different_requests_with_identical_timing_are_not_merged(self):
        report = self.summarize_rows([self.identified(1), self.identified(2)])
        result = report["ipc_request_statistics"]["exact"]["worker_round_trip_wait_ms"]
        self.assertEqual((result["count"], result["sum"]), (2, 24.))

    def test_restarted_worker_request_numbers_do_not_collide(self):
        report = self.summarize_rows([self.identified(1, session="a" * 32),
                                      self.identified(1, session="b" * 32)])
        self.assertEqual(report["ipc_request_accounting"]["exact_requests"], 2)
        self.assertEqual(report["statistics"]["ipc_request_timing_ms.worker_round_trip_wait_ms"]["sum"], 24.)

    def test_legacy_multi_frame_fingerprint_is_explicitly_estimated(self):
        report = self.summarize_rows([{**self.fixture(), "frame_index": frame,
            "processed_index": frame} for frame in range(8)])
        self.assertEqual(report["ipc_request_accounting"]["status"], "legacy_estimate")
        self.assertFalse(report["ipc_request_accounting"]["exact_for_saved_timing_records"])
        self.assertEqual(report["ipc_request_accounting"]["legacy_estimated_requests"], 1)
        self.assertEqual(report["ipc_request_statistics"]["exact"], {})
        self.assertEqual(report["ipc_request_statistics"]["legacy_estimated"]["worker_round_trip_wait_ms"]["count"], 1)
        self.assertIn("cannot prove exact", report["ipc_request_accounting"]["legacy_limitations"])

    def test_complete_legacy_fingerprint_and_contiguity_are_required(self):
        first = {**self.fixture(), "ipc_request_timing_ms": {"worker_round_trip_wait_ms": 12., "send_request_ms": .1}}
        second = {**first, "ipc_request_timing_ms": {"worker_round_trip_wait_ms": 12., "send_request_ms": .2}}
        report = self.summarize_rows([first, second, first])
        self.assertEqual(report["ipc_request_accounting"]["legacy_estimated_requests"], 3)
        self.assertEqual(report["statistics"]["ipc_request_timing_ms.worker_round_trip_wait_ms"]["sum"], 36.)

    def test_mixed_records_keep_exact_and_legacy_totals_separate(self):
        report = self.summarize_rows([self.fixture(), self.identified(1)])
        self.assertEqual(report["ipc_request_accounting"]["status"], "mixed_estimate")
        self.assertFalse(report["ipc_request_accounting"]["exact_for_saved_timing_records"])
        for family in ("exact", "legacy_estimated"):
            self.assertEqual(report["ipc_request_statistics"][family]["worker_round_trip_wait_ms"]["sum"], 12.)

    def test_conflicting_or_malformed_identity_cannot_claim_exact_count(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.summarize_rows([self.identified(1), self.identified(1,
                ipc_request_timing_ms={"worker_round_trip_wait_ms": 13.})])
        for changed in ({"request_id": True}, {"session_id": "unknown"}, {"version": "future"}):
            row = self.identified(1)
            row["ipc_request_identity"].update(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "request identity"):
                self.summarize_rows([row])

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
