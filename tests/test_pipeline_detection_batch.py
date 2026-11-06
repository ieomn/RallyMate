"""Exercise decoder -> detector -> ordered tracker/pose binding through the pipeline."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from rallymate_service.worker import JobCancelled
from rallymate_vision import pipeline, sam3d_evaluation, sam3d_runner
from rallymate_vision.contracts import ModelConfig, PipelineRequest, ProcessingConfig


class ReachedScoring(Exception):
    pass


class MutablePTSDecoder:
    timestamps = [0, 37, 91, 128, 181, 209, 260]

    def __init__(self):
        self.index = -1
        self.binding = {}
        self.released = False

    def read(self):
        self.index += 1
        if self.index >= len(self.timestamps):
            return False, None
        raw = 1000 + self.timestamps[self.index]
        # Reuse the same object as a decoder may do; queued frames must copy it.
        self.binding.update(pts=raw, time_base_num=1, time_base_den=1000, timestamp_ms=raw)
        return True, np.full((64, 64, 3), self.index, np.uint8)

    def get(self, _property):
        return self.binding["timestamp_ms"]

    def release(self):
        self.released = True


class PipelineDetectionBatchTests(unittest.TestCase):
    def _run(self, batch_size, *, max_frames=None, cancel_after_detection=False, sam_batch_frames=1):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"constant input identity; controlled VFR decoder")
            request = PipelineRequest(
                "batch-integration", source, root / "out",
                ModelConfig(root / "detect", root / "pose", device="cpu", pose_backend="sam3d",
                            pose_native_keypoint_format="mhr70"),
                processing=ProcessingConfig(start_ms=30, end_ms=230, frame_stride=2,
                                            max_frames=max_frames, write_annotated_video=False),
            )
            capture = MutablePTSDecoder()
            perception = Mock()
            perception.detect_model_path = request.models.detect
            perception.pose_model_path = request.models.pose
            perception.pose_metadata.return_value = {"backend": "sam3d", "runtime": "pytorch",
                "profile": "realtime", "native_keypoint_format": "mhr70"}
            perception.last_pose_association_diagnostics = None
            perception.last_detection_timing = {}
            perception.last_detection_timings = []
            perception.pose_frame_records.return_value = []
            batches, poses, tracker_times = [], [], []
            stop = False

            def detect(images):
                nonlocal stop
                batches.append([int(image[0, 0, 0]) for image in images])
                if cancel_after_detection:
                    stop = True
                return [[] for _ in images]

            perception.detect.side_effect = lambda image: detect([image])[0]
            perception.detect_batch.side_effect = detect

            def pose(image, _players, _max_players, *, source_binding):
                poses.append((int(image[0, 0, 0]), dict(source_binding)))
                return []

            perception.estimate_poses.side_effect = pose
            pose_batches = []
            def batch_pose(requests):
                pose_batches.append([request["source_binding"]["frame_index"] for request in requests])
                return [{"poses": pose(item["frame"], item["player_detections"], item["max_players"],
                                       source_binding=item["source_binding"]), "records": []}
                        for item in requests]
            perception.estimate_pose_batch.side_effect = batch_pose
            tracker = Mock()

            def track(detections, _width, _height, *, timestamp_ms):
                tracker_times.append(timestamp_ms)
                return detections

            tracker.update.side_effect = track

            def cancelled():
                if stop:
                    raise JobCancelled("stop immediately after batched detection")

            metadata = SimpleNamespace(fps=30., frame_count=7, width=64, height=64, duration_ms=294)
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {
                    "RALLYMATE_DETECT_BATCH_SIZE": str(batch_size),
                    "RALLYMATE_SAM3D_BATCH_FRAMES": str(sam_batch_frames),
                    "RALLYMATE_SAM3D_PREFETCH_FRAMES": "0", "RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE": "0",
                    "RALLYMATE_SAM3D_RUNTIME": str(root / "runtime"),
                }))
                stack.enter_context(patch.object(pipeline, "probe_video", return_value=metadata))
                stack.enter_context(patch.object(pipeline, "SimpleMultiClassTracker", return_value=tracker))
                stack.enter_context(patch.object(sam3d_runner, "SourcePTSDecoder", return_value=capture))
                evaluation = stack.enter_context(patch.object(sam3d_evaluation, "evaluate_sam3d_jsonl",
                    return_value={"runtime": {}, "status": "fixture", "counts": {}}))
                stack.enter_context(patch.object(pipeline, "build_primary_player_artifacts", return_value={}))
                stack.enter_context(patch.object(pipeline, "recognize_strokes_from_artifacts", return_value={}))
                scoring = stack.enter_context(patch.object(pipeline, "run_minimum_scoring_loop", side_effect=ReachedScoring))
                with self.assertRaises(JobCancelled if cancel_after_detection else ReachedScoring):
                    pipeline.run_pipeline(request, perception=perception, cancellation_callback=cancelled)
                if cancel_after_detection:
                    evaluation.assert_not_called()
                    scoring.assert_not_called()
                else:
                    scoring.assert_called_once()
            rows = [json.loads(line) for line in (request.output_dir / "frames.jsonl").read_text("utf-8").splitlines()]
            self.assertTrue(capture.released)
            perception.close.assert_not_called()
            self.assertFalse((request.output_dir / "summary.json").exists())
            self.pose_batches = pose_batches
            return rows, batches, poses, tracker_times, capture.index

    def test_sam_batches_preserve_complete_vfr_frame_records_and_tail(self):
        single = self._run(1)
        batched = self._run(2, sam_batch_frames=2)
        self.assertEqual(batched[0], single[0])
        self.assertEqual(batched[2], single[2])
        self.assertEqual(batched[3], single[3])
        self.assertEqual(self.pose_batches, [[1, 3]])

    def test_sam_batch_max_frames_stops_at_requested_source_boundary(self):
        rows, _, poses, times, last_read = self._run(2, sam_batch_frames=4, max_frames=2)
        self.assertEqual(self.pose_batches, [[1, 3]])
        self.assertEqual(last_read, 3)
        self.assertEqual([row["frame"]["processed_index"] for row in rows], [0, 1])
        self.assertEqual(len(poses), 2)

    def test_batches_preserve_vfr_clip_stride_tail_and_pixel_pts_bindings(self):
        single = self._run(1)
        rows, batches, poses, tracker_times, _ = self._run(2)
        self.assertEqual(rows, single[0])
        self.assertEqual(batches, [[1, 3], [5]])
        self.assertEqual(tracker_times, [37, 128, 209])
        self.assertEqual([row["frame"]["index"] for row in rows], [1, 3, 5])
        self.assertEqual([row["frame"]["processed_index"] for row in rows], [0, 1, 2])
        for processed, (marker, binding) in enumerate(poses):
            self.assertEqual(marker, [1, 3, 5][processed])
            self.assertEqual(binding["frame_index"], marker)
            self.assertEqual(binding["processed_index"], processed)
            self.assertEqual(binding["pts"], 1000 + tracker_times[processed])
            self.assertEqual(binding["timestamp_ms"], binding["pts"])
            self.assertEqual(binding["relative_timestamp_ms"], tracker_times[processed])
            self.assertEqual(binding["timestamp_source"], "decoder_pts")

    def test_max_frames_stops_decode_without_filling_an_unneeded_batch(self):
        rows, batches, poses, tracker_times, last_read = self._run(4, max_frames=2)
        self.assertEqual(batches, [[1, 3]])
        self.assertEqual(last_read, 3)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(poses), 2)
        self.assertEqual(tracker_times, [37, 128])

    def test_cancellation_after_detection_prevents_tracking_pose_and_publishing(self):
        rows, batches, poses, tracker_times, last_read = self._run(2, cancel_after_detection=True)
        self.assertEqual(batches, [[1, 3]])
        self.assertEqual(last_read, 3)
        self.assertEqual(rows, [])
        self.assertEqual(poses, [])
        self.assertEqual(tracker_times, [])


if __name__ == "__main__":
    unittest.main()
