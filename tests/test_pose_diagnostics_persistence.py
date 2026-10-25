from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from rallymate_vision.contracts import ModelConfig, PipelineRequest, ProcessingConfig
from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.pipeline import run_pipeline
from rallymate_vision.pose.adapters import PoseEstimator
from rallymate_vision.pose.diagnostics import bind_pose_association_diagnostics
from tests.test_pose_backend import StubBackend
from tests.test_pose_person_association import backend, result


FIXTURE = Path(__file__).parent / 'fixtures/pose-association-usta-11986-v1.json'


def frame_info(index=0):
    return {'index': index, 'processed_index': index, 'timestamp_ms': index * 40,
            'timestamp_source': 'decoder_pts', 'width': 1280, 'height': 720}


def facade(estimator):
    obj = object.__new__(Yolo26Perception)
    obj.pose_estimator = estimator
    obj.pose_backend = StubBackend([])
    return obj


def recorded_results(row):
    return [result([c['bbox_roi_px'] for c in roi['candidates']],
                   [c['box_confidence'] for c in roi['candidates']],
                   [c['keypoints_roi_px'] for c in roi['candidates']],
                   [c['keypoint_confidences'] for c in roi['candidates']])
            for roi in row['roi_results']]


class PoseDiagnosticsPersistenceTests(unittest.TestCase):
    def test_absent_backend_evidence_is_not_an_association_success(self):
        for absent in (None, {}):
            self.assertIsNone(bind_pose_association_diagnostics(absent, frame_info()))
        obj = facade(SimpleNamespace(estimate=lambda *args: []))
        obj.last_pose_association_diagnostics = {'old': True}
        self.assertEqual(obj.estimate_poses(np.zeros((720, 1280, 3)), [], 2), [])
        self.assertIsNone(obj.last_pose_association_diagnostics)

    def test_failed_call_clears_previous_snapshot_before_propagating(self):
        def fail(*args):
            raise ValueError('model failed')
        obj = facade(SimpleNamespace(estimate=fail))
        obj.last_pose_association_diagnostics = {'old': True}
        with self.assertRaisesRegex(ValueError, 'model failed'):
            obj.estimate_poses(np.zeros((720, 1280, 3)), [], 2)
        self.assertIsNone(obj.last_pose_association_diagnostics)

    def test_snapshot_and_bound_record_do_not_alias_mutable_estimator_evidence(self):
        original = {'policy_version': 'test', 'rois': [{'reason_code': 'rejected'}]}
        estimator = SimpleNamespace(estimate=lambda *args: [], last_association_diagnostics=original)
        obj = facade(estimator)
        obj.estimate_poses(np.zeros((720, 1280, 3)), [], 2)
        bound = bind_pose_association_diagnostics(obj.last_pose_association_diagnostics, frame_info(2))
        original['rois'][0]['reason_code'] = 'changed'
        obj.last_pose_association_diagnostics['rois'].clear()
        self.assertEqual(bound['diagnostics']['rois'][0]['reason_code'], 'rejected')
        self.assertEqual(bound['source_frame'], frame_info(2))

    def test_rejected_nonfinite_candidates_are_strict_json_with_explicit_paths(self):
        values = {'a/b~c': [float('nan'), float('inf'), float('-inf'), np.float32(.5), np.int64(2)]}
        bound = bind_pose_association_diagnostics(values, frame_info())
        self.assertEqual(bound['diagnostics']['a/b~c'], [None, None, None, .5, 2])
        self.assertEqual(bound['invalid_numeric_values'], [
            {'json_pointer': '/diagnostics/a~1b~0c/0', 'representation': 'nan'},
            {'json_pointer': '/diagnostics/a~1b~0c/1', 'representation': 'inf'},
            {'json_pointer': '/diagnostics/a~1b~0c/2', 'representation': '-inf'}])
        self.assertEqual(json.loads(json.dumps(bound, allow_nan=False)), bound)
        self.assertTrue(np.isnan(values['a/b~c'][0]))

    def test_source_pts_remains_distinct_from_playback_clock(self):
        info = {**frame_info(2), 'source_timestamp_ms': 98765}
        bound = bind_pose_association_diagnostics({'rois': []}, info)
        self.assertEqual(bound['source_frame']['timestamp_ms'], 80)
        self.assertEqual(bound['source_frame']['source_timestamp_ms'], 98765)
        for key, bad in [('index', True), ('width', 0), ('timestamp_ms', -1), ('source_timestamp_ms', 2.5), ('timestamp_source', '')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                bind_pose_association_diagnostics({'rois': []}, {**info, key: bad})

    def test_saved_real_rejection_survives_roundtrip_and_next_empty_frame(self):
        rows = json.loads(FIXTURE.read_text(encoding='utf-8'))['frames']
        obj = facade(PoseEstimator(backend(recorded_results(rows[1]))))
        poses = obj.estimate_poses(np.zeros((720, 1280, 3)), rows[1]['players'], 2)
        self.assertEqual([pose['person_track_id'] for pose in poses], [1])
        record = bind_pose_association_diagnostics(obj.last_pose_association_diagnostics, frame_info(11986))
        rejected = record['diagnostics']['rois'][0]
        self.assertEqual(rejected['person_track_id'], 2)
        self.assertEqual(rejected['reason_code'], 'pose_target_candidate_ambiguous')
        self.assertEqual(len(rejected['backend']['candidate_boxes_xyxy']), 3)
        stored = json.loads(json.dumps(record, allow_nan=False))
        self.assertEqual(obj.estimate_poses(np.zeros((720, 1280, 3)), [], 2), [])
        empty = bind_pose_association_diagnostics(obj.last_pose_association_diagnostics, frame_info(11987))
        self.assertEqual(empty['diagnostics']['rois'], [])
        self.assertEqual(stored, record)

    def _run_writer(self, use_recorded_candidates):
        # Exercise the real pipeline writer/validator with a three-frame fake
        # decoder and saved candidate tensors. No new model or video evidence.
        rows = json.loads(FIXTURE.read_text(encoding='utf-8'))['frames']
        state = {'index': -1}
        class Capture:
            def read(self):
                state['index'] += 1
                return state['index'] < 3, np.zeros((720, 1280, 3), dtype=np.uint8)
            def get(self, key): return state['index'] * 40
            def release(self): pass
        class StopAfterFrames(Exception): pass
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / 'three-frame-contract-test.mp4'
            video.write_bytes(b'fake decoder input; not real media')
            obj = facade(PoseEstimator(backend([])) if use_recorded_candidates else SimpleNamespace(estimate=lambda *a: []))
            obj.pose_backend = StubBackend([])
            obj.detect_model_path = root / 'detect.pt'
            obj.pose_model_path = root / 'stub.pt'
            def detect(frame):
                if not use_recorded_candidates:
                    return []
                row = rows[state['index']]
                obj.pose_estimator.backend.model = SimpleNamespace(predict=lambda *a, **kw: recorded_results(row))
                return copy.deepcopy(row['raw_record']['detections'])
            obj.detect = detect
            request = PipelineRequest(job_id='pose-diagnostic-contract', video_path=video, output_dir=root / 'out',
                models=ModelConfig(detect=obj.detect_model_path, pose=obj.pose_model_path, device='cpu', pose_backend='stub', pose_runtime='test'),
                processing=ProcessingConfig(max_frames=3, max_players=2, write_annotated_video=False))
            with patch('rallymate_vision.pipeline.probe_video', return_value=SimpleNamespace(width=1280, height=720, fps=25, frame_count=3)), \
                 patch('rallymate_vision.pipeline.cv2.VideoCapture', return_value=Capture()), \
                 patch('rallymate_vision.pipeline.SimpleMultiClassTracker.update', side_effect=lambda detections, *a, **kw: detections), \
                 patch('rallymate_vision.pipeline.build_primary_player_artifacts', side_effect=StopAfterFrames):
                with self.assertRaises(StopAfterFrames):
                    run_pipeline(request, perception=obj)
            return [json.loads(line) for line in (root / 'out/frames.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_pipeline_jsonl_keeps_rejection_without_creating_a_missing_pose(self):
        records = self._run_writer(True)
        self.assertEqual(len(records), 3)
        self.assertEqual([[p['person_track_id'] for p in record['poses']] for record in records], [[2, 1], [1], [2, 1]])
        for index, record in enumerate(records):
            proof = record['pose_association']
            self.assertEqual(proof['source_frame']['index'], index)
            self.assertEqual(proof['source_frame']['timestamp_ms'], record['frame']['timestamp_ms'])
            self.assertEqual(proof['source_frame']['timestamp_source'], record['frame']['timestamp_source'])
        self.assertEqual(records[1]['pose_association']['diagnostics']['rois'][0]['reason_code'], 'pose_target_candidate_ambiguous')

    def test_pipeline_legacy_backend_does_not_get_fabricated_diagnostics(self):
        for record in self._run_writer(False):
            self.assertNotIn('pose_association', record)


if __name__ == '__main__':
    unittest.main()
