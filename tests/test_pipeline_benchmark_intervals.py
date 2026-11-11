from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts.benchmark_sam3d_pipeline import (
    build_parser, candidate_subject_shapes, parse_trial_order, prepare_source, select_pipeline_records, scheduling_trials,
)
from scripts.sam3d_pipeline_comparison import compare_pipeline, validate_source_bindings, validate_trial_order, timing_summary


def binding(index, *, person=1, status="ok"):
    value = {"video_sha256": "a" * 64, "frame_index": index, "processed_index": index,
             "pts": index * 3000 + 90000, "time_base_num": 1, "time_base_den": 90000,
             "timestamp_ms": 1000 + round(index * 1000 / 30), "image_size": [10, 10],
             "status": status, "person_track_id": person,
             "selection_epoch": 0, "camera_reference_epoch": 0}
    if status == "ok":
        value["bbox_px"] = [0, 0, 10, 10]
    else:
        value["reason"] = "no_detected_subject"
    return value


class Decoder:
    def __init__(self, broken_pts=None):
        self.index = 0
        self.released = False
        self.broken_pts = broken_pts

    def read(self):
        self.binding = binding(self.index)
        if self.index == self.broken_pts:
            self.binding["pts"] += 1
        self.index += 1
        return True, np.zeros((10, 10, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class PipelineBenchmarkIntervalTests(unittest.TestCase):
    def test_fixed_batch_scheduling_abba_preserves_one_variable_and_separate_timing(self):
        for experiment in ("pipeline-overlap", "archive-queue", "combined-scheduling"):
            configurations = scheduling_trials(experiment, 8, 2)
            probe = {"schedule_abba": experiment, "candidate_batch_frames": 8,
                     "trial_order": [8] * 4, "trial_configurations": configurations,
                     "trials": [dict(item, trial=i, caller_wall_seconds=10 if i in (0, 3) else 8,
                                     processing={"stage_seconds": {"pose_seconds": 5}})
                                for i, item in enumerate(configurations)]}
            self.assertEqual(validate_trial_order(probe), 8)
            summary = timing_summary(probe["trials"], [{"trial": i, "source_frames": 8,
                "successful_subjects": 16} for i in range(4)])
            self.assertEqual([item["trials"] for item in summary], [2, 2])
            self.assertEqual([item["median_pipeline_wall_seconds"] for item in summary], [10, 8])
            broken = copy.deepcopy(probe)
            broken["trials"][2]["archive_pending_frames"] = 4
            with self.assertRaises(ValueError):
                validate_trial_order(broken)
            broken = copy.deepcopy(probe)
            broken["trials"].pop()
            with self.assertRaises(ValueError):
                validate_trial_order(broken)

    def test_explicit_single_pair_has_own_receipt_and_default_remains_abba(self):
        self.assertEqual(parse_trial_order(None, 8), (1, 8, 8, 1))
        for order in ("1,8", "8,1", "1,8,8,1", "8,1,1,8"):
            parsed = parse_trial_order(order, 8)
            self.assertEqual(validate_trial_order({"candidate_batch_frames": 8, "trial_order": list(parsed),
                "trials": [{"batch_frames": value} for value in parsed]}), 8)
        for order in ("1,4", "8,8", "1,8,1", "１,８", "1,,8"):
            with self.subTest(order=order), self.assertRaises(ValueError):
                parse_trial_order(order, 8)
        with self.assertRaises(ValueError):
            validate_trial_order({"candidate_batch_frames": 8, "trials": [{"batch_frames": value} for value in (1, 8)]})

    def test_contiguous_interval_retains_empty_frames_and_does_not_fill_from_later(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.jsonl"
            values = [binding(0), binding(1, person=None, status="skipped"), binding(2), binding(2, person=2)]
            path.write_text("".join(json.dumps(row) + "\n" for row in values), encoding="utf8")
            selected = select_pipeline_records(path, start_frame=0, count=3)
            self.assertEqual([group[0]["frame_index"] for group in selected], [0, 1, 2])
            self.assertEqual(selected[1][0]["status"], "skipped")
            self.assertEqual(candidate_subject_shapes(selected, 4), [3])
            self.assertEqual(candidate_subject_shapes(selected, 4, max_players=1), [2])
            path.write_text("".join(json.dumps(row) + "\n" for row in values if row["frame_index"] != 1), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "without gaps"):
                select_pipeline_records(path, count=2)

    def test_skipped_source_pts_are_verified_and_nonzero_start_preserves_source_indices(self):
        selected = [[binding(3)], [binding(4, person=None, status="skipped")], [binding(5)], [binding(6)]]
        for broken in (None, 4):
            decoder = Decoder(broken)
            with patch("scripts.benchmark_sam3d_pipeline.select_pipeline_records", return_value=selected), \
                    patch("scripts.benchmark_sam3d_pipeline.sha256_file", return_value="a" * 64), \
                    patch("scripts.benchmark_sam3d_pipeline.SourcePTSDecoder", return_value=decoder):
                if broken is not None:
                    with self.assertRaisesRegex(ValueError, "PTS"):
                        prepare_source(Path("video"), Path("native"), 4, lambda **kw: None, start_frame=3)
                else:
                    source, images = prepare_source(Path("video"), Path("native"), 4, lambda **kw: None, start_frame=3)
                    self.assertEqual(source["start_ms"], 100)
                    self.assertEqual(source["start_frame"], 3)
                    self.assertEqual(source["decoded_frames_excluded"], 7)
                    self.assertEqual(source["empty_source_frames"], 1)
                    self.assertEqual([row["bindings"][0]["frame_index"] for row in images], [3, 5, 6])
                    self.assertEqual(source["warmup_expected_subject_shapes"], [3])
            self.assertTrue(decoder.released)

    def test_duplicate_and_inconsistent_source_rows_are_rejected(self):
        for change in ("duplicate", "pts", "bbox", "unknown_status", "timestamp", "processed"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "native.jsonl"
                first, second = binding(0), binding(0, person=2)
                if change == "duplicate": second["person_track_id"] = 1
                if change == "pts": second["pts"] += 1
                if change == "bbox": second["bbox_px"][2] = 20
                if change == "unknown_status": second["status"] = "pretend_ok"
                if change == "timestamp": second["timestamp_ms"] += 1
                if change == "processed": second["processed_index"] = True
                path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf8")
                with self.assertRaises(ValueError):
                    select_pipeline_records(path, count=1)

    def test_comparison_keeps_skipped_status_failures_and_interval_processed_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = binding(10, person=None, status="skipped")
            native = {**original, "processed_index": 0}
            frame = {"frame": {"index": 10, "processed_index": 0, "pts": original["pts"],
                               "time_base_num": 1, "time_base_den": 90000, "video_sha256": "a" * 64}}
            source = {"video_sha256": "a" * 64, "start_frame": 10, "frames": [[original]]}
            validate_source_bindings([native], [frame], source)
            with self.assertRaisesRegex(ValueError, "omitted"):
                validate_source_bindings([], [frame], source)
            trials = []
            for trial, batch in enumerate((1, 8, 8, 1), 1):
                output = root / str(trial)
                output.mkdir()
                actual = copy.deepcopy(native)
                if trial == 3:
                    actual.update(status="failed", reason="runtime_error")
                for name, rows in (("sam3d_frames.jsonl", [actual]), ("frames.jsonl", [frame]),
                                   ("events.jsonl", []), ("scores.jsonl", [])):
                    (output / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf8")
                trials.append({"trial": trial, "batch_frames": batch, "output_dir": str(output),
                               "caller_wall_seconds": 1, "processing": {"stage_seconds": {"pose_seconds": .1}}})
            (root / "probe.json").write_text(json.dumps({"trials": trials, "candidate_batch_frames": 8,
                                                         "source_binding": source}), encoding="utf8")
            report = compare_pipeline(root)
            self.assertFalse(report["routing_and_decisions_equal"])
            self.assertTrue(report["trials"][1]["native_status_exact"])
            self.assertFalse(report["trials"][2]["native_status_exact"])
            self.assertEqual(report["trials"][2]["successful_subjects"], 0)

    def test_parser_supports_whole_clip_and_player_scenario_without_changing_defaults(self):
        required = ["--" + name + "=unused" for name in
                    ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root")]
        defaults = build_parser().parse_args(required)
        self.assertEqual((defaults.frames, defaults.start_frame, defaults.max_players), (128, 0, 2))
        configured = build_parser().parse_args(required + ["--frames=13116", "--start-frame=0", "--max-players=4"])
        self.assertEqual((configured.frames, configured.max_players), (13116, 4))


if __name__ == "__main__":
    unittest.main()
