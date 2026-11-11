"""CPU-only contract tests; no CUDA import, model, source video or process launch."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rallymate_vision.cuda_memory_budget import configure_cuda_memory_budget

SPEC = importlib.util.spec_from_file_location('managed_gpu_smoke',
    Path(__file__).resolve().parents[1] / 'scripts/smoke_managed_gpu_worker.py')
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
GPU = 'GPU-00000000-0000-0000-0000-000000000001'


def allocator(limit=1024):
    return {'logical_device': 'cuda:0', 'physical_gpu_uuid': GPU,
        'allocator_limit_mib': limit, 'total_memory_bytes': 16384 * 1024**2,
        'requested_fraction': limit / 16384, 'observed_fraction': limit / 16384,
        'scope': 'pytorch_caching_allocator_in_this_process'}


class ManagedGPUSmokeTests(unittest.TestCase):
    def test_allocator_receipt_rejects_plan_values_without_actual_readback(self):
        smoke.validate_allocator(allocator(), GPU, 1024)
        for field, value in (('observed_fraction', None), ('observed_fraction', .9),
                             ('observed_fraction', float('nan')), ('logical_device', 'cuda:1'),
                             ('physical_gpu_uuid', 'GPU-00000000-0000-0000-0000-000000000002')):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                smoke.validate_allocator({**allocator(), field: value}, GPU, 1024)

    def test_managed_cap_records_child_observed_uuid_and_fraction(self):
        cuda = Mock()
        cuda.get_device_properties.return_value = SimpleNamespace(total_memory=16384 * 1024**2, uuid=GPU[4:])
        cuda.device_count.return_value = 1
        cuda.get_per_process_memory_fraction.return_value = 11264 / 16384
        receipt = configure_cuda_memory_budget(SimpleNamespace(cuda=cuda), '0', 'CAP', env={
            'CAP': '11264', 'RALLYMATE_MANAGED_GPU_UUID': GPU, 'CUDA_VISIBLE_DEVICES': GPU})
        smoke.validate_allocator(receipt, GPU, 11264)
        self.assertEqual(receipt['process_id'], os.getpid())
        cuda.device_count.return_value = 2
        with self.assertRaisesRegex(RuntimeError, 'physical GPU UUID'):
            configure_cuda_memory_budget(SimpleNamespace(cuda=cuda), '0', 'CAP', env={
                'CAP': '11264', 'RALLYMATE_MANAGED_GPU_UUID': GPU, 'CUDA_VISIBLE_DEVICES': GPU})

    def test_smoke_environment_forces_isolated_writes_and_batch_one(self):
        plan = {'gpu_uuid': GPU, 'detector_allocator_mib': 1024, 'sam_allocator_mib': 11264,
            'physical_headroom_mib': 2048, 'cpu_threads': 2}
        output = Path(tempfile.gettempdir()) / 'unique-smoke-test'
        with patch.dict(os.environ, {'RALLYMATE_DATABASE_PATH': 'production.sqlite3',
                'RALLYMATE_API_KEY': 'do-not-copy', 'RALLYMATE_SAM3D_BATCH_FRAMES': '8'}):
            env = smoke.make_environment(plan, output, Path('sam-python'))
            self.assertEqual(os.environ['RALLYMATE_SAM3D_BATCH_FRAMES'], '8')
        self.assertEqual(env['RALLYMATE_DATABASE_PATH'], str(output / 'data/jobs.sqlite3'))
        self.assertNotIn('RALLYMATE_API_KEY', env)
        self.assertEqual(env['RALLYMATE_SAM3D_BATCH_FRAMES'], '1')
        self.assertEqual(env['RALLYMATE_SAM3D_EXPORT_MESH'], '0')
        self.assertEqual(env['RALLYMATE_SAM3D_LOG_DIR'], str(output / 'sam-logs'))
        self.assertEqual(env['RALLYMATE_ACCEPT_NEW_JOBS'], '0')

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        identity = {'frames': 2, 'job_id': 'isolated', 'job_output': str(output)}
        frames, native = [], []
        for index in range(2):
            binding = {'frame_index': index, 'processed_index': index, 'pts': index,
                'time_base_num': 1, 'time_base_den': 30, 'timestamp_ms': round(index * 1000 / 30),
                'person_track_id': 986, 'selection_epoch': 1, 'camera_reference_epoch': 0,
                'video_sha256': 'a' * 64}
            frames.append({'job_id': 'isolated', 'frame': {**binding, 'index': index},
                'poses': [{'person_track_id': 986, 'source_binding': dict(binding), 'keypoint_format': 'mhr70'}]})
            native.append({**binding, 'status': 'ok', 'keypoints_3d': {str(i): [0, 0, 1] for i in range(70)}})
        job = {'summary': {'processing': {'processed_frames': 2,
            'sam_pose_batch': {'requested_batch_frames': 1, 'peak_frames': 1},
            'detection_batch': {'requested_batch_size': 1, 'peak_batch_frames': 1}}}}
        return identity, frames, native, job

    @staticmethod
    def store_artifacts(identity, frames, native):
        for name, rows in (('frames.jsonl', frames), ('sam3d_frames.jsonl', native)):
            (Path(identity['job_output']) / name).write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf8')

    def test_artifacts_require_job_source_pts_person_epoch_and_complete_frames(self):
        identity, frames, native, job = self.fixture()
        self.store_artifacts(identity, frames, native)
        result = smoke.verify_job_artifacts(job, identity, 'a' * 64)
        self.assertEqual(result['successful_native_subjects'], 2)
        for field, value in (('pts', 99), ('person_track_id', 1), ('selection_epoch', 2),
                             ('video_sha256', 'b' * 64), ('timestamp_ms', 999)):
            rows = copy.deepcopy(native)
            rows[0][field] = value
            self.store_artifacts(identity, frames, rows)
            with self.subTest(field=field), self.assertRaises(ValueError):
                smoke.verify_job_artifacts(job, identity, 'a' * 64)
        self.store_artifacts(identity, frames[:1], native)
        with self.assertRaises(ValueError):
            smoke.verify_job_artifacts(job, identity, 'a' * 64)

    def test_empty_person_frame_preserved_without_counting_placeholder_as_success(self):
        identity, frames, native, job = self.fixture()
        frames[1]['poses'] = []
        native[1].update(status='skipped', reason='no_detected_subject', person_track_id=None, selection_epoch=None)
        self.store_artifacts(identity, frames, native)
        result = smoke.verify_job_artifacts(job, identity, 'a' * 64)
        self.assertEqual(result['successful_native_subjects'], 1)
        self.assertEqual(result['native_status_counts'], {'ok': 1, 'skipped': 1})

    def test_missing_success_or_batch_experiment_is_not_a_passed_smoke(self):
        identity, frames, native, job = self.fixture()
        for row in native:
            row['status'] = 'skipped'
        self.store_artifacts(identity, frames, native)
        with self.assertRaisesRegex(ValueError, 'successful native'):
            smoke.verify_job_artifacts(job, identity, 'a' * 64)
        job['summary']['processing']['sam_pose_batch']['requested_batch_frames'] = 8
        with self.assertRaisesRegex(ValueError, 'batch one'):
            smoke.verify_job_artifacts(job, identity, 'a' * 64)

    def test_runtime_receipt_cannot_be_reused_from_another_worker(self):
        from rallymate_service.gpu_fleet import MANAGED_INFERENCE_PROFILE
        identity = {'gpu_uuid': GPU, 'worker_id': 'fleet-new', 'nonce': 'new',
            'detector_allocator_mib': 1024, 'sam_allocator_mib': 11264, 'physical_headroom_mib': 2048}
        receipt = {**identity, 'detector_allocator': allocator(), 'sam_allocator': allocator(11264),
            'phase': 'after_queue_job', 'visible_device_count': 1, 'observed_gpu_uuid': GPU,
            'cuda_visible_devices': GPU, 'profile': MANAGED_INFERENCE_PROFILE, 'detector_model_device': 'cuda:0'}
        smoke.verify_identity(receipt, identity, phase='after_queue_job')
        for key, value in (('nonce', 'old'), ('phase', 'models_initialized'), ('visible_device_count', 2),
                           ('cuda_visible_devices', '0'), ('profile', {}), ('detector_model_device', 'cpu')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.verify_identity({**receipt, key: value}, identity, phase='after_queue_job')

    def test_explicit_ten_gib_sam_cap_requires_matching_actual_readback(self):
        from rallymate_service.gpu_fleet import MANAGED_INFERENCE_PROFILE
        identity = {'gpu_uuid': GPU, 'worker_id': 'fleet-new', 'nonce': 'new',
            'detector_allocator_mib': 1024, 'sam_allocator_mib': 10240, 'physical_headroom_mib': 2048}
        cuda = Mock()
        cuda.get_device_properties.return_value = SimpleNamespace(total_memory=16384 * 1024**2, uuid=GPU[4:])
        cuda.device_count.return_value = 1
        cuda.get_per_process_memory_fraction.return_value = 10240 / 16384
        sam = configure_cuda_memory_budget(SimpleNamespace(cuda=cuda), '0', 'CAP', env={
            'CAP': '10240', 'RALLYMATE_MANAGED_GPU_UUID': GPU, 'CUDA_VISIBLE_DEVICES': GPU})
        receipt = {**identity, 'detector_allocator': allocator(), 'sam_allocator': sam,
            'phase': 'after_queue_job', 'visible_device_count': 1, 'observed_gpu_uuid': GPU,
            'cuda_visible_devices': GPU, 'profile': MANAGED_INFERENCE_PROFILE, 'detector_model_device': 'cuda:0'}
        smoke.verify_identity(receipt, identity, phase='after_queue_job')
        self.assertEqual(sam['observed_fraction'], .625)
        for changed in ({**receipt, 'sam_allocator': allocator(11264)},
                        {**receipt, 'sam_allocator_mib': 11264},
                        {**receipt, 'sam_allocator': {**sam, 'observed_fraction': 11264 / 16384}}):
            with self.subTest(changed=changed['sam_allocator_mib']), self.assertRaises(ValueError):
                smoke.verify_identity(changed, identity, phase='after_queue_job')

    def test_cli_preserves_defaults_and_passes_explicit_caps_to_actual_plan(self):
        from rallymate_service.gpu_fleet import GPU as GPUInfo
        cases = [([], (1024, 11264, 2048)),
                 (['--sam-mib', '10240'], (1024, 10240, 2048)),
                 (['--detector-mib', '1280', '--sam-mib', '10240', '--reserve-mib', '2560'], (1280, 10240, 2560))]
        for options, expected in cases:
            with self.subTest(options=options), patch.object(sys, 'argv', ['smoke', '--launch', *options]), \
                    patch('rallymate_service.gpu_fleet.discover_gpus', return_value=[GPUInfo(GPU, 'simulated', 16384, 14025)]), \
                    patch.object(smoke, 'supervise') as supervisor, patch('builtins.print'):
                smoke.main()
                plan = supervisor.call_args.args[1]
                self.assertEqual(tuple(plan[key] for key in ('detector_allocator_mib', 'sam_allocator_mib', 'physical_headroom_mib')), expected)
                env = smoke.make_environment(plan, Path(tempfile.gettempdir()) / 'fake-smoke', Path('fake-sam'))
                self.assertEqual(env['RALLYMATE_SAM3D_CUDA_MEMORY_MIB'], str(expected[1]))


if __name__ == '__main__':
    unittest.main()
