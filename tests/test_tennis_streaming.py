from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service import tennis_assessment as tennis
from rallymate_service import swing_kinematics as swing
from rallymate_service.tennis_source_motion import source_motion_by_frame
from tests.test_tennis_assessment import inputs, episodes
from tests.test_tennis_subject_scoring import source_frames


class TennisStreamingTests(unittest.TestCase):
    def test_full_projection_preserves_scores_camera_binding_and_duplicate_rejection(self):
        timeline, records = inputs()
        frames = source_frames(records)
        for row in frames:
            row['unused_pose_payload'] = ['padding'] * 50
        for source in (frames, frames + deepcopy(frames[:1])):
            projected = [tennis._source_frame_projection(row) for row in source]
            self.assertEqual(source_motion_by_frame(records, projected, 'a'*64),
                             source_motion_by_frame(records, source, 'a'*64))
            kwargs = dict(video_sha256='a'*64, motion_episodes=episodes())
            self.assertEqual(tennis.analyze_tennis_assessment(timeline, records, source_frames=source, **kwargs),
                             tennis.analyze_tennis_assessment(timeline, records, source_frames=projected, **kwargs))

    def test_stream_reads_tail_and_rejects_oversize_rows_count_bytes_and_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'frames.jsonl'
            path.write_text('{"frame":{"index":0}}\n{"frame":{"index":99}}\n', encoding='utf8')
            self.assertEqual([r['frame']['index'] for r in swing._read_rows(path, max_file_bytes=200)], [0,99])
            with self.assertRaises(ValueError):
                list(swing._read_rows(path, max_file_bytes=10))
            with patch.object(swing, 'MAX_RECORD_BYTES', 5), self.assertRaises(ValueError):
                list(swing._read_rows(path))
            with patch.object(swing, 'MAX_RECORDS', 1), self.assertRaises(ValueError):
                list(swing._read_rows(path))
            reader = swing._read_rows(path)
            next(reader)
            with path.open('a',encoding='utf8') as stream:
                stream.write('{"late":true}\n')
            with self.assertRaises(ValueError):
                list(reader)

    def test_retention_budget_checks_nested_objects_and_combined_rows(self):
        with patch.object(tennis, 'MAX_ASSESSMENT_RETAINED_BYTES', 800):
            retained = tennis._AssessmentRows()
            retained.retain({'small':'one'})
            with self.assertRaises(ValueError):
                retained.retain({'nested':['x'*500]})

    def test_builder_streams_source_frames_and_keeps_all_original_native_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'test-run'; root.mkdir()
            timeline, native = inputs()
            frames = source_frames(native)
            for row in frames:
                row['unused_pose_payload'] = 'padding' * 200
            for name, data in [('primary-player.jsonl',timeline),('sam3d_frames.jsonl',native),('frames.jsonl',frames)]:
                (root/name).write_text(''.join(json.dumps(row)+'\n' for row in data),encoding='utf8')
            candidates = episodes()
            for row in candidates:
                row['evidence'] = {'process_continuity':row.pop('continuity')}
            summary = {'job_id':'test-run','minimum_scoring_loop':{'provenance':{'video_sha256':'a'*64}},
                       'action_recognition':{'motion_analysis':{'families':{'baseline':{'episodes':candidates}}}}}
            original = tennis.analyze_tennis_assessment
            def check(timeline_arg, native_arg, **kwargs):
                self.assertEqual(native_arg, native)
                self.assertEqual(len(kwargs['source_frames']), len(frames))
                self.assertNotIn('unused_pose_payload', kwargs['source_frames'][-1])
                return original(timeline_arg,native_arg,**kwargs)
            with patch.object(tennis,'analyze_tennis_assessment',side_effect=check):
                result = tennis.build_tennis_assessment(summary, root)
            expected = original(timeline,native,video_sha256='a'*64,source_run_id='test-run',
                                motion_episodes=tennis._summary_episodes(summary),source_frames=frames)
            self.assertEqual(result, expected)
            self.assertEqual(result['status'],'scored')


if __name__ == '__main__':
    unittest.main()
