import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from rallymate_service.worker import JobCancelled
from rallymate_vision import pipeline
from rallymate_vision.contracts import ModelConfig, PipelineRequest

from rallymate_vision.progress import POSTPROCESS_STAGES, postprocess_progress


class PipelineProgressTests(unittest.TestCase):
    def test_all_postprocessing_stages_remain_visible_and_below_complete(self):
        previous = 85
        for phase in POSTPROCESS_STAGES:
            start = postprocess_progress(phase, processed_frames=11516, total_frames=11516)
            end = postprocess_progress(phase, processed_frames=11516, total_frames=11516, completed=212, total=212)
            self.assertGreaterEqual(start["percent"], previous)
            self.assertLessEqual(start["percent"], end["percent"])
            self.assertLess(end["percent"], 100)
            self.assertEqual(end["completed_items"], 212)
            self.assertEqual(end["processed_frames"], 11516)
            previous = end["percent"]

    def test_partial_measurement_progress_reports_actual_work(self):
        state = postprocess_progress("extracting_features", processed_frames=60, total_frames=60, completed=5, total=10)
        self.assertEqual(state["percent"], 94)
        self.assertEqual(state["message"], "正在计算动作测量（5/10）")

    def test_empty_work_never_implies_complete(self):
        state = postprocess_progress("detecting_events", processed_frames=3, total_frames=3, completed=0, total=0)
        self.assertEqual(state["percent"], 90)
        self.assertNotIn("total_items", state)


class PipelineCancellationTests(unittest.TestCase):
    def test_cancel_before_preflight_has_no_output_or_model_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = PipelineRequest('cancelled', root / 'missing.mp4', root / 'out',
                                      ModelConfig(root / 'detect', root / 'pose', device='cpu'))
            with patch.object(pipeline, 'Yolo26Perception') as loader:
                with self.assertRaises(JobCancelled):
                    pipeline.run_pipeline(request, cancellation_callback=Mock(side_effect=JobCancelled('stop')))
            loader.assert_not_called()
            self.assertFalse(request.output_dir.exists())

    def _cancel_at(self, boundary):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'input.mp4'
            video.write_bytes(b'content identity only; decoding is controlled by the fixture')
            request = PipelineRequest('cancel-test', video, root / 'out',
                                      ModelConfig(root / 'detect', root / 'pose', device='cpu'))
            capture, writer, perception = Mock(), Mock(), Mock()
            perception.last_pose_association_diagnostics = None
            capture.get.return_value = 0.0
            writer.isOpened.return_value = True
            perception.detect_model_path = request.models.detect
            perception.pose_model_path = request.models.pose
            perception.pose_metadata.return_value = {'backend': 'yolo', 'runtime': 'pytorch',
                'profile': 'realtime', 'native_keypoint_format': 'coco17'}
            state = {'stop': False, 'reads': 0}
            def read():
                state['reads'] += 1
                if boundary == 'after_read': state['stop'] = True
                return True, np.zeros((120, 160, 3), dtype=np.uint8)
            def pose(*args):
                if boundary == 'after_pose': state['stop'] = True
                return []
            def report(progress):
                if boundary == 'before_postprocess' and progress.get('processed_frames') == 1:
                    state['stop'] = True
                # Existing UI progress faults remain non-fatal. Cancellation
                # uses the separate callback and must not share this catch.
                raise RuntimeError('progress transport unavailable')
            def check():
                if state['stop']: raise JobCancelled(boundary)
            capture.read.side_effect = read
            perception.detect.return_value = []
            perception.estimate_poses.side_effect = pose
            metadata = SimpleNamespace(fps=30.0, frame_count=1, width=160, height=120, duration_ms=34)
            with (patch.object(pipeline, 'probe_video', return_value=metadata),
                  patch.object(pipeline.cv2, 'VideoCapture', return_value=capture),
                  patch.object(pipeline.cv2, 'VideoWriter', return_value=writer),
                  patch.object(pipeline, 'run_minimum_scoring_loop') as scoring):
                with self.assertRaisesRegex(JobCancelled, boundary):
                    pipeline.run_pipeline(request, perception=perception,
                        progress_callback=report, cancellation_callback=check)
            self.assertGreaterEqual(capture.release.call_count, 1)
            self.assertGreaterEqual(writer.release.call_count, 1)
            self.assertEqual(state['reads'], 1)
            perception.close.assert_not_called()  # external persistent model survives
            scoring.assert_not_called()
            self.assertFalse((request.output_dir / 'summary.json').exists())
            # ExitStack/with released the open output on Windows too.
            frames = request.output_dir / 'frames.jsonl'
            if frames.exists(): frames.rename(request.output_dir / 'cancelled-frames.jsonl')

    def test_midframe_cancellation_releases_capture_writer_and_frame_file(self):
        self._cancel_at('after_read')

    def test_cancellation_after_pose_propagates_and_cleans_up(self):
        self._cancel_at('after_pose')

    def test_cancel_before_postprocessing_is_not_swallowed_as_progress_error(self):
        self._cancel_at('before_postprocess')
