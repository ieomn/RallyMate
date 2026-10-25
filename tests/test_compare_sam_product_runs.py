import json
from pathlib import Path
import tempfile
import unittest

from scripts.compare_sam_product_runs import coordinate_tolerance_check, evaluate, load_run
from tests.test_summarize_sam_profile import NativeProfileSummaryTests


class ProductComparisonTests(unittest.TestCase):
    def fixture(self, root, label, cache):
        probe, run = root / label, root / (label + '-run')
        probe.mkdir(); run.mkdir()
        upload = root / (label + '.mp4'); upload.write_bytes(b'same-source-bytes')
        import hashlib
        sha = hashlib.sha256(upload.read_bytes()).hexdigest()
        request_path = root / (label + '-request.json')
        request = {'job_id': label, 'source': {'video_path': str(upload)},
                   'processing': {'max_frames': 1}, 'models': {'pose': 'fixed.ckpt'},
                   'court': {'mode': 'disabled'}, 'scoring': {}}
        model = dict(model_sha256='a' * 64, config_sha256='b' * 64,
                     mhr_sha256='c' * 64, source_commit='d' * 40, inference_type='body',
                     load_seconds=4, cuda_cache_policy=cache, cpu_threads={'torch_intraop': 4})
        summary = {'job_id': label, 'status': 'completed', 'models': {'pose_backend': model},
                   'processing': {'processed_frames': 1, 'elapsed_seconds': 2, 'stage_seconds': {},
                                  'source_prefetch': {'queue_capacity_frames': 0}},
                   'counts': {'frames_with_pose': 1}, 'runtime': {'cpu_thread_limit': 4}}
        row = dict(NativeProfileSummaryTests().fixture(), video_sha256=sha, first_model_call=True)
        (run / 'sam3d_frames.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
        result = {'job': {'id': label, 'output_dir': str(run), 'request_path': str(request_path),
                          'status': 'succeeded'}, 'cancel_requested': False,
                  'wall_seconds': 3, 'source_sha256': sha}
        for path, value in ((probe / 'intent.json', {'sha256': sha, 'frames': 1}),
                            (probe / 'result.json', result), (request_path, request),
                            (run / 'summary.json', summary)):
            path.write_text(json.dumps(value), encoding='utf-8')
        return load_run(probe)

    def test_only_declared_configuration_may_vary(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'reuse_between_frames')
            report = evaluate(a, b)
            self.assertTrue(report['same_workload_verified'])
            self.assertTrue(report['numerical_equivalence_verified'])
            b['summary']['models']['pose_backend']['cpu_threads']['torch_intraop'] = 8
            self.assertFalse(evaluate(a, b)['same_workload_verified'])
            b['summary']['models']['pose_backend']['cpu_threads']['torch_intraop'] = 4
            b['request']['processing']['max_frames'] = 2
            self.assertFalse(evaluate(a, b)['same_workload_verified'])

    def test_extra_native_value_difference_is_not_hidden_by_joint_equality(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'reuse_between_frames')
            b['native'][0]['focal_length'] = 1100
            report = evaluate(a, b)
            self.assertTrue(report['geometry']['geometry_exact_numeric_equal'])
            self.assertFalse(report['numerical_equivalence_verified'])

    def test_prompt_allows_only_exactly_named_pose_metadata_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'official_clear_each_call')
            pose = b['summary']['models']['pose_backend']
            pose['prompt_vectorization'] = {'enabled': True, 'implementation_sha256': 'e' * 64}
            report = evaluate(a, b, treatment='prompt')
            self.assertTrue(report['same_workload_verified'])
            self.assertTrue(report['numerical_equivalence_verified'])
            self.assertEqual(report['declared_treatment_metadata']['fields']['prompt_vectorization']['candidate'], pose['prompt_vectorization'])
            self.assertFalse(evaluate(a, b, treatment='cache')['same_workload_verified'])
            for key, value in [('unknown_future_field', True), ('precision', 'float16'),
                               ('source_commit', 'f' * 40), ('cuda_cache_policy', 'reuse_between_frames')]:
                with self.subTest(key=key):
                    absent = object()
                    previous = pose.get(key, absent)
                    pose[key] = value
                    self.assertFalse(evaluate(a, b, treatment='prompt')['same_workload_verified'])
                    if previous is absent:
                        del pose[key]
                    else:
                        pose[key] = previous
            b['summary']['runtime']['unknown_future_runtime'] = True
            self.assertFalse(evaluate(a, b, treatment='prompt')['same_workload_verified'])
            with self.assertRaises(ValueError):
                evaluate(a, b, treatment='unknown')

    def test_prompt_rotation_allows_only_two_fields_and_threads_still_checks_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'official_clear_each_call')
            pose = b['summary']['models']['pose_backend']
            pose['prompt_vectorization'] = {'enabled': True, 'requested': True, 'scope': 'prompt'}
            pose['rotation_vectorization'] = {'enabled': True, 'requested': True, 'scope': 'body_head'}
            report = evaluate(a, b, treatment='prompt_rotation')
            self.assertTrue(report['same_workload_verified'])
            self.assertFalse(report['declared_treatment_metadata']['fields']['rotation_vectorization']['baseline_present'])
            self.assertFalse(evaluate(a, b, treatment='prompt')['same_workload_verified'])
            self.assertFalse(evaluate(a, b, treatment='threads')['same_workload_verified'])
            for key, value in [('unknown_future_field', True), ('precision', 'float16'),
                               ('source_commit', 'f' * 40), ('cpu_threads', {'torch_intraop': 8})]:
                with self.subTest(key=key):
                    absent = object()
                    previous = pose.get(key, absent)
                    pose[key] = value
                    self.assertFalse(evaluate(a, b, treatment='prompt_rotation')['same_workload_verified'])
                    if previous is absent:
                        del pose[key]
                    else:
                        pose[key] = previous
            for key in ('prompt_vectorization', 'rotation_vectorization'):
                a['summary']['models']['pose_backend'][key] = dict(pose[key])
            pose['cpu_threads']['torch_intraop'] = 8
            self.assertTrue(evaluate(a, b, treatment='threads')['same_workload_verified'])
            pose['rotation_vectorization']['enabled'] = False
            self.assertFalse(evaluate(a, b, treatment='threads')['same_workload_verified'])

    def test_unknown_cold_state_and_incomplete_tail_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'reuse_between_frames')
            b['native'][0]['first_model_call'] = False
            self.assertFalse(evaluate(a, b)['same_workload_verified'])
            with b['native_path'].open('a', encoding='utf-8') as f:
                f.write('{"partial":')
            self.assertFalse(evaluate(a, b)['numerical_equivalence_verified'])

    def test_declared_tolerance_is_diagnostic_and_cannot_override_exact_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.fixture(Path(tmp), 'a', 'official_clear_each_call')
            b = self.fixture(Path(tmp), 'b', 'reuse_between_frames')
            b['native'][0]['keypoints_2d']['nose'][0] += .0001
            b['native_path'].write_text(json.dumps(b['native'][0]) + '\n', encoding='utf-8')
            report = evaluate(a, b, coordinate_tolerances=(.001, .000001))
            self.assertFalse(report['numerical_equivalence_verified'])
            self.assertTrue(report['coordinate_tolerance_diagnostic']['all_bound_coordinates_within_declared_tolerances'])
            self.assertFalse(evaluate(a, b, coordinate_tolerances=(.00001, .000001))['coordinate_tolerance_diagnostic']['all_bound_coordinates_within_declared_tolerances'])
            with self.assertRaises(ValueError):
                coordinate_tolerance_check(report['geometry'], float('nan'), 0)
