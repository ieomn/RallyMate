from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from rallymate_scoring.scoring_loop_report import (
    ReportRecordLimit,
    _json_text,
    build_scoring_loop_report_html,
    write_scoring_loop_report,
)


def fixture(count: int = 3) -> dict:
    events, records, features, scores = [], [], [], []
    for i in reversed(range(count)):
        event_id = f"event-{i:05d}"
        events.append({"event_id": event_id, "event_code": "FS01", "start_ms": i * 10,
                       "end_ms": i * 10 + 9, "person_track_id": 1, "confidence": .9,
                       "key_phases_ms": {"landing": i * 10 + 3}, "quality_flags": [],
                       "track_diagnostics": {"coverage": .75}})
        feature = {"event_id": event_id, "feature_name": "height", "feature_version": "1",
                   "value": 1.25, "unit": "body", "valid": True, "confidence": .8,
                   "reason": "valid", "source_frames": [i, i + 1],
                   "raw_value": [{"value": 1., "timestamp_ms": i * 10},
                                 {"value": 2., "timestamp_ms": i * 10 + 9}],
                   "smoothed_value": [1., 2.]}
        features.append(feature)
        compact = {key: value for key, value in feature.items()
                   if key not in {"raw_value", "smoothed_value", "event_id"}}
        records.append({"event_id": event_id, "event_code": "FS01", "indicator_id": "FS01-M02",
                        "person_track_id": 1, "feature_status": "measured",
                        "scoring_feature_status": "measured", "scoring_status": "scored",
                        "grade": "B", "features": [compact], "supplemental_features": [],
                        "reason_codes": ["z-last", "a-first"], "quality_gate": {"status": "pass"}})
        scores.append({"event_id": event_id, "indicator_id": "FS01-M02", "status": "scored",
                       "grade": "B", "confidence": .9, "threshold_version": "threshold-1",
                       "feedback": "原始反馈 <script>", "reason_codes": ["valid"],
                       "evidence": [{"source_frames": [i + 2, i], "source_frame": i + 3,
                                     "detail": "原始证据"}], "model_versions": {"model": "original"}})
    return {"summary": {"video_id": "video-test", "loop_version": "test", "status": "ready",
                        "score_status_counts": {"scored": count}, "model_versions": {}},
            "events": events, "indicator_records": records, "feature_records": features,
            "scores": scores, "feasibility": {"indicators": [
                {"indicator_id": "FS01-M02", "name_zh": "测试指标", "feasibility_level": "F4"},
                {"indicator_id": "FS01-M03", "name_zh": "没有事件", "feasibility_level": "F2"},
            ]}}


class StreamingScoringLoopReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "scoring-loop-report.html"

    def assert_no_scratch(self):
        self.assertFalse(list(self.root.glob(".scoring-report-*")))

    def test_streaming_html_matches_string_api_and_keeps_every_event(self):
        data = fixture()
        # Preserve the existing last-value join behavior and stable sample order.
        data["feature_records"].append({**data["feature_records"][0], "raw_value": [9, 10]})
        data["scores"].append({**data["scores"][0], "grade": "C"})
        # Explicit indicator evidence must still override canonical raw evidence.
        data["indicator_records"][1]["features"][0]["raw_value"] = [42]
        before = copy.deepcopy(data)
        expected = build_scoring_loop_report_html(data)
        progress = []
        write_scoring_loop_report(data, self.output, progress_callback=progress.append)
        actual = self.output.read_text("utf-8")
        self.assertEqual(actual, expected)
        self.assertEqual(actual.count('class="instance-card"'), 3)
        self.assertEqual(data, before)
        self.assertEqual(progress[-1]["report_stage"], "writing_instances")
        self.assertEqual(progress[-1]["completed_items"], 3)
        self.assert_no_scratch()

    def test_streams_disk_artifacts_without_whole_file_or_whole_html_builder(self):
        data = fixture()
        expected = build_scoring_loop_report_html(data)
        for key, name in (("events", "events.jsonl"), ("feature_records", "features.jsonl"),
                          ("indicator_records", "indicator-features.jsonl"), ("scores", "scores.jsonl")):
            with (self.root / name).open("w", encoding="utf-8") as handle:
                for row in data.pop(key):
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        with patch("rallymate_scoring.scoring_loop_report._load_jsonl", side_effect=AssertionError("list loader")), \
             patch("rallymate_scoring.scoring_loop_report.build_scoring_loop_report_html", side_effect=AssertionError("whole HTML")):
            write_scoring_loop_report(data, self.output)
        self.assertEqual(self.output.read_text("utf-8"), expected)
        self.assert_no_scratch()

    def test_cancellation_during_render_preserves_published_report_and_cleans_index(self):
        self.output.write_text("previous complete report", encoding="utf-8")

        class Cancelled(Exception):
            pass

        cancelled = False

        def progress(value):
            nonlocal cancelled
            if value["report_stage"] == "writing_instances":
                cancelled = True

        def check():
            if cancelled:
                raise Cancelled("cancelled by caller")

        with self.assertRaisesRegex(Cancelled, "cancelled by caller"):
            write_scoring_loop_report(fixture(), self.output, progress_callback=progress,
                                      cancellation_callback=check)
        self.assertEqual(self.output.read_text("utf-8"), "previous complete report")
        self.assert_no_scratch()

    def test_referenced_evidence_renders_equivalently_and_missing_pool_is_fatal(self):
        from rallymate_scoring.artifact_evidence import EvidencePoolWriter, POOL_FILENAME
        data = fixture(3)
        ledger = {"frames": {str(i): {"identity": 1, "point": [1.5, 2.5]} for i in range(200)}}
        for feature in data["feature_records"]:
            feature["provenance"] = {"source_pose_person_association": ledger}
        expected = build_scoring_loop_report_html(data)
        with EvidencePoolWriter(self.root, video_id="video-test", source_id="source-test") as writer:
            writer.write_jsonl(self.root / "features.jsonl", data.pop("feature_records"))
        data["summary"]["provenance"] = {"pipeline_job_id": "source-test"}
        write_scoring_loop_report(data, self.output)
        self.assertEqual(self.output.read_text("utf-8"), expected)
        (self.root / POOL_FILENAME).unlink()
        with self.assertRaises(ValueError):
            write_scoring_loop_report(data, self.output)
        self.assertEqual(self.output.read_text("utf-8"), expected)
        self.assert_no_scratch()

    def test_record_limit_and_late_invalid_record_never_publish_partial_output(self):
        data = fixture(1)
        data["events"][0]["extra"] = "x" * 4096
        self.output.write_text("previous", encoding="utf-8")
        with self.assertRaises(ReportRecordLimit):
            write_scoring_loop_report(data, self.output, max_record_bytes=1024)
        self.assertEqual(self.output.read_text("utf-8"), "previous")
        data = fixture(1)
        data.pop("feature_records")
        (self.root / "features.jsonl").write_text('{"event_id":"x","feature_name":"a"}\ninvalid\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            write_scoring_loop_report(data, self.output)
        self.assertEqual(self.output.read_text("utf-8"), "previous")
        self.assert_no_scratch()

    def test_diagnostic_projection_has_bounded_peak_before_serialization(self):
        payload = {"proof": ["x" * 10000] * 100000}
        tracemalloc.start()
        try:
            rendered = _json_text(payload, max_chars=8000)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertIn("摘要截断", rendered)
        self.assertLess(len(rendered), 8100)
        self.assertLess(peak, 512 * 1024)

    def test_peak_memory_does_not_grow_with_disk_event_count(self):
        # Each source record has 32 KiB of repeated raw evidence; a list loader
        # exceeds 32 MiB on the larger case, while report projection/indexing
        # retains only a record and the fixed SQLite cache.
        def measure(count):
            header = fixture(0)
            for key in ("events", "feature_records", "indicator_records", "scores"):
                header.pop(key)
            filenames = ("events.jsonl", "features.jsonl", "indicator-features.jsonl", "scores.jsonl")
            handles = [(self.root / name).open("w", encoding="utf-8") for name in filenames]
            try:
                row = fixture(1)
                row["feature_records"][0]["raw_value"] = "x" * (32 * 1024)
                for i in range(count):
                    event = f"event-{i:05d}"
                    for key, handle in zip(("events", "feature_records", "indicator_records", "scores"), handles):
                        item = row[key][0]
                        item["event_id"] = event
                        if key == "events":
                            item["start_ms"], item["end_ms"] = i * 10, i * 10 + 9
                        handle.write(json.dumps(item) + "\n")
            finally:
                for handle in handles:
                    handle.close()
            tracemalloc.start()
            try:
                write_scoring_loop_report(header, self.output)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            with self.output.open(encoding="utf-8") as report:
                # The file is intentionally not loaded while measuring memory.
                self.assertEqual(report.read().count('class="instance-card"'), count)
            self.assert_no_scratch()
            return peak

        small = measure(40)
        large = measure(1000)
        self.assertLess(large, 8 * 1024 * 1024)
        self.assertLess(large, small + 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
