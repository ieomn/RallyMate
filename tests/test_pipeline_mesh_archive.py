"""Run the real pipeline/archiver boundary with CPU fixtures and actual NPZ files."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_service.worker import JobCancelled
from rallymate_vision import mesh_render_task, pipeline, sam3d_evaluation, sam3d_mesh_archive, sam3d_runner
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ProcessingConfig
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import LEDGER_NAME, archive_mesh_records, capture_mesh_output
from rallymate_vision.sam3d_mesh_archive import BoundedMeshArchiver


class ReachedScoring(BaseException):
    """The test stops after all real inference/archive publication boundaries."""


class TwoFrameDecoder:
    def __init__(self):
        self.index = -1
        self.released = 0
        self.binding = {}

    def read(self):
        self.index += 1
        if self.index >= 2:
            return False, None
        self.binding = {"pts": 100 + 20 * self.index, "time_base_num": 1,
                        "time_base_den": 600, "timestamp_ms": (100 + 20 * self.index) * 1000 / 600}
        return True, np.full((64, 64, 3), 60 + self.index, np.uint8)

    def get(self, _):
        return self.binding["timestamp_ms"]

    def release(self):
        self.released += 1


class PipelineMeshArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        video = self.root / "source.mp4"
        video.write_bytes(b"source identity fixture; decoder is controlled")
        self.request = PipelineRequest(
            "async-mesh", video, self.root / "output",
            ModelConfig(self.root / "detect", self.root / "pose", device="cpu", pose_backend="sam3d",
                        pose_native_keypoint_format="mhr70"),
            processing=ProcessingConfig(write_annotated_video=False, max_frames=2))
        self.capture = TwoFrameDecoder()
        self.perception = Mock()
        self.perception.detect_model_path = self.request.models.detect
        self.perception.pose_model_path = self.request.models.pose
        self.perception.pose_metadata.return_value = {"backend": "sam3d", "runtime": "pytorch",
            "profile": "realtime", "native_keypoint_format": "mhr70"}
        self.perception.detect.return_value = []
        self.perception.last_detection_timing = {}
        self.native_by_frame = []
        self.native_handles = []
        self.all_jsonl_handles = []
        self.archivers = []
        self.archive_calls = []
        self.archive_started = threading.Event()
        self.second_inference = threading.Event()
        self.closing = threading.Event()
        self.archive_completed = threading.Event()
        self.stop = False
        self.vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], np.float32)
        self.faces = np.array([[0, 1, 2]], np.int32)

    def _estimate(self, mode, frame, players, max_players, *, source_binding):
        index = source_binding["frame_index"]
        if index == 1:
            self.second_inference.set()
        rows = []
        for track in (4, 7):
            row = {**source_binding, "person_track_id": track, "selection_epoch": 0,
                   "status": "ok", "image_size": [64, 64], "bbox_px": [1., 2., 30., 50.],
                   "inference_type": "body"}
            if index == 1 and track == 7:
                row.update(status="failed", reason="controlled_subject_failure")
            else:
                row["mesh_artifact"] = capture_mesh_output(
                    {"pred_vertices": self.vertices + np.float32(index),
                     "pred_cam_t": np.array([0., 0., 3.], np.float32), "focal_length": np.float32(100)},
                    self.faces, self.runtime)
                if mode == "checksum_failure" and index == 0 and track == 4:
                    with Path(row["mesh_artifact"]["path"]).open("ab") as handle:
                        handle.write(b"tampered before archive")
            rows.append(row)
        self.native_by_frame.append(rows)
        self.perception.pose_frame_records.return_value = rows
        return []

    def _archive(self, mode, records, output, runtime):
        index = records[0]["frame_index"]
        self.archive_calls.append(index)
        self.archive_started.set()
        if mode == "success" and index == 0:
            if not self.second_inference.wait(5):
                raise AssertionError("next inference did not overlap pending mesh archive")
        if mode == "cancel":
            if not self.closing.wait(5):
                raise AssertionError("cancellation did not join the active archiver")
        self.assertEqual(len(self.native_handles), 2)
        self.assertTrue(all(not handle.closed for handle in self.native_handles))
        result = archive_mesh_records(records, output, runtime)
        self.assertTrue(all(not handle.closed for handle in self.native_handles))
        self.archive_completed.set()
        return result

    def _factory(self, mode, *args, **kwargs):
        instance = BoundedMeshArchiver(*args, **kwargs,
            _archive=lambda *items: self._archive(mode, *items))
        self.archivers.append(instance)
        original_close = instance.close
        def close():
            self.closing.set()
            original_close()
        instance.close = close
        return instance

    def _read_rows(self, name):
        return [json.loads(line) for line in (self.request.output_dir / name).read_text(encoding="utf-8").splitlines()]

    def _assert_durable_archives(self, records):
        native = self._read_rows("sam3d_frames.jsonl")
        ledger = self._read_rows(LEDGER_NAME)
        self.assertEqual(native, records)
        self.assertEqual([(row["frame_index"], row["person_track_id"]) for row in native],
                         [(0, 4), (0, 7), (1, 4), (1, 7)])
        self.assertEqual([(row["frame_index"], row["person_track_id"]) for row in ledger],
                         [(0, 4), (0, 7), (1, 4)])
        self.assertEqual(native[-1]["status"], "failed")
        by_identity = {(row["frame_index"], row["person_track_id"]): row for row in native}
        for item in ledger:
            row = by_identity[item["frame_index"], item["person_track_id"]]
            self.assertEqual(item["pts"], 100 + 20 * item["frame_index"])
            self.assertEqual(item["mesh_artifact"], row["mesh_artifact"])
            for key in ("video_sha256", "processed_index", "selection_epoch", "time_base_num", "time_base_den"):
                self.assertEqual(item[key], row[key])
            artifact = item["mesh_artifact"]
            self.assertFalse(Path(artifact["path"]).is_absolute())
            path = self.request.output_dir / artifact["path"]
            self.assertEqual(sha256_file(path), artifact["sha256"])
            with np.load(path, allow_pickle=False) as stored:
                np.testing.assert_array_equal(stored["vertices"], self.vertices + np.float32(item["frame_index"]))
                self.assertEqual(stored["vertices"].dtype, np.float32)
        self.assertTrue(self.archivers[0].receipt["joined"])
        self.assertEqual(self.archivers[0].receipt["pending_frames"], 0)

    def _run(self, mode):
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            file_mode = args[0] if args else kwargs.get("mode", "r")
            if Path(path).parent == self.request.output_dir and "w" in file_mode and Path(path).suffix == ".jsonl":
                self.all_jsonl_handles.append(handle)
                if Path(path).name in {"sam3d_frames.jsonl", LEDGER_NAME}:
                    self.native_handles.append(handle)
            return handle
        evaluated = []
        def evaluate(records, **kwargs):
            self._assert_durable_archives(records)
            evaluated.extend(records)
            return {"runtime": {}, "status": "fixture", "counts": {}}
        def score(*args, **kwargs):
            self._assert_durable_archives(evaluated)
            self.assertTrue((self.request.output_dir / mesh_render_task.TASK_NAME).is_file())
            raise ReachedScoring()
        def progress(payload):
            if mode == "cancel" and payload.get("processed_frames") == 2:
                self.assertTrue(self.archive_started.wait(5))
                self.stop = True
        def cancelled():
            if self.stop:
                raise JobCancelled("controlled cancellation while archive is active")
        self.perception.estimate_poses.side_effect = lambda *args, **kwargs: self._estimate(mode, *args, **kwargs)
        metadata = SimpleNamespace(fps=30., frame_count=2, width=64, height=64, duration_ms=67)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"RALLYMATE_SAM3D_RUNTIME": str(self.runtime),
                "RALLYMATE_SAM3D_PREFETCH_FRAMES": "0", "RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE": "1"}))
            stack.enter_context(patch.dict(sys.modules, {"torch": None, "ultralytics": None}))
            stack.enter_context(patch.object(Path, "open", new=tracked_open))
            stack.enter_context(patch.object(pipeline, "probe_video", return_value=metadata))
            stack.enter_context(patch.object(pipeline, "Yolo26Perception", side_effect=AssertionError("models must stay mocked")))
            stack.enter_context(patch.object(sam3d_runner, "SourcePTSDecoder", return_value=self.capture))
            stack.enter_context(patch.object(sam3d_mesh_archive, "BoundedMeshArchiver", side_effect=lambda *a, **kw: self._factory(mode, *a, **kw)))
            evaluator = stack.enter_context(patch.object(sam3d_evaluation, "evaluate_sam3d_records", side_effect=evaluate))
            render_task = stack.enter_context(patch.object(mesh_render_task, "create_mesh_render_task", wraps=mesh_render_task.create_mesh_render_task))
            scorer = stack.enter_context(patch.object(pipeline, "run_minimum_scoring_loop", side_effect=score))
            stack.enter_context(patch.object(pipeline, "build_primary_player_artifacts", return_value={}))
            stack.enter_context(patch.object(pipeline, "recognize_strokes_from_artifacts", return_value={}))
            expected = ReachedScoring if mode == "success" else JobCancelled if mode == "cancel" else ValueError
            with self.assertRaises(expected) as caught:
                pipeline.run_pipeline(self.request, perception=self.perception,
                    progress_callback=progress, cancellation_callback=cancelled)
            if mode == "success":
                evaluator.assert_called_once()
                render_task.assert_called_once()
                scorer.assert_called_once()
            else:
                evaluator.assert_not_called()
                render_task.assert_not_called()
                scorer.assert_not_called()
                self.assertFalse((self.request.output_dir / mesh_render_task.TASK_NAME).exists())
                self.assertFalse((self.request.output_dir / "summary.json").exists())
            if mode == "checksum_failure":
                self.assertIn("checksum mismatch", str(caught.exception))
        self.assertGreaterEqual(self.capture.released, 1)
        self.assertTrue(all(handle.closed for handle in self.all_jsonl_handles))
        self.assertEqual(len(self.all_jsonl_handles), 3)
        self.assertTrue(self.archivers[0].receipt["closed"])
        self.assertTrue(all(not thread.is_alive() for thread in self.archivers[0]._pool._threads))
        self.perception.close.assert_not_called()

    def test_two_frames_are_archived_and_durable_before_evaluation_scoring_and_render_task(self):
        self._run("success")
        self.assertEqual(self.archive_calls, [0, 1])
        # No asynchronous path rewrite leaked into adapter-owned source rows.
        for records in self.native_by_frame:
            for record in records:
                if "mesh_artifact" in record:
                    self.assertTrue(Path(record["mesh_artifact"]["path"]).is_absolute())

    def test_cancellation_joins_active_archive_and_cancels_queued_frame_before_streams_close(self):
        self._run("cancel")
        self.assertEqual(self.archive_calls, [0])
        self.assertTrue(self.archive_completed.is_set())
        self.assertEqual(self._read_rows("sam3d_frames.jsonl"), [])
        self.assertEqual(self._read_rows(LEDGER_NAME), [])

    def test_archive_checksum_failure_cannot_enter_evaluation_scoring_or_publish_preview_task(self):
        self._run("checksum_failure")

    def _assert_initialization_failure_releases_decoder(self, *, mode, constructor_failure):
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            file_mode = args[0] if args else kwargs.get("mode", "r")
            if Path(path).parent == self.request.output_dir and "w" in file_mode:
                opened.append(handle)
            return handle
        metadata = SimpleNamespace(fps=30., frame_count=2, width=64, height=64, duration_ms=67)
        failure = RuntimeError("controlled archiver constructor failure")
        with (patch.dict(os.environ, {"RALLYMATE_SAM3D_RUNTIME": str(self.runtime),
                  "RALLYMATE_SAM3D_PREFETCH_FRAMES": "2", "RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE": mode}),
              patch.dict(sys.modules, {"torch": None, "ultralytics": None}),
              patch.object(Path, "open", new=tracked_open),
              patch.object(pipeline, "probe_video", return_value=metadata),
              patch.object(sam3d_runner, "PrefetchSourcePTSDecoder", return_value=self.capture) as decoder,
              patch.object(sam3d_mesh_archive, "BoundedMeshArchiver", side_effect=failure) as constructor,
              patch.object(sam3d_evaluation, "evaluate_sam3d_records") as evaluator,
              patch.object(pipeline, "run_minimum_scoring_loop") as scorer,
              patch.object(mesh_render_task, "create_mesh_render_task") as render_task):
            expected = RuntimeError if constructor_failure else ValueError
            message = "constructor failure" if constructor_failure else "must be 0 or 1"
            with self.assertRaisesRegex(expected, message):
                pipeline.run_pipeline(self.request, perception=self.perception)
            decoder.assert_called_once_with(self.request.video_path, depth=2)
            if constructor_failure:
                constructor.assert_called_once()
            else:
                constructor.assert_not_called()
            evaluator.assert_not_called()
            scorer.assert_not_called()
            render_task.assert_not_called()
        self.assertEqual(self.capture.released, 1)
        self.assertEqual(self.capture.index, -1)
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(handle.closed for handle in opened))
        self.perception.estimate_poses.assert_not_called()
        self.perception.close.assert_not_called()

    def test_invalid_archive_configuration_releases_precreated_decoder(self):
        self._assert_initialization_failure_releases_decoder(mode="invalid", constructor_failure=False)

    def test_archive_constructor_failure_releases_precreated_decoder(self):
        self._assert_initialization_failure_releases_decoder(mode="1", constructor_failure=True)


if __name__ == "__main__":
    unittest.main()
