import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import subprocess
import sys
import unittest
import uuid
from unittest.mock import Mock, patch

from rallymate_service.gpu_fleet import (
    GPU, GPUWorkerSlot, cuda_property_uuid, fleet_plan, parse_inventory, worker_environment, validate_worker_environment,
)
from rallymate_vision.cuda_memory_budget import configure_cuda_memory_budget

FIRST = 'GPU-00000000-0000-0000-0000-000000000001'
SECOND = 'GPU-00000000-0000-0000-0000-000000000002'


class GPUFleetTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.state_directory = Path(self.state.name)
        state_root = patch('rallymate_service.gpu_fleet.node_gpu_state_root', return_value=self.state_directory)
        state_root.start()
        self.addCleanup(state_root.stop)

    def inventory(self):
        return [GPU(FIRST, 'simulated one', 16384, 15000), GPU(SECOND, 'simulated two', 24576, 24000)]

    def test_torch_uuid_property_accepts_bare_and_prefixed_forms_only(self):
        self.assertEqual(cuda_property_uuid(FIRST[4:]), FIRST)
        self.assertEqual(cuda_property_uuid(FIRST), FIRST)
        for invalid in ('', None, '0', 'GPU-0000', FIRST + ',' + SECOND):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                cuda_property_uuid(invalid)

    def test_simulated_distinct_physical_routes_and_parent_environment_unchanged(self):
        plans = fleet_plan([FIRST, SECOND], self.inventory())
        parent = {'CUDA_VISIBLE_DEVICES': 'original', 'CUSTOM': 'kept'}
        first, second = [worker_environment(plan, parent) for plan in plans]
        self.assertEqual(first['CUDA_VISIBLE_DEVICES'], FIRST)
        self.assertEqual(second['CUDA_VISIBLE_DEVICES'], SECOND)
        self.assertEqual(first['RALLYMATE_DEVICE'], '0')
        self.assertEqual(parent['CUDA_VISIBLE_DEVICES'], 'original')
        self.assertEqual(first['CUSTOM'], 'kept')
        self.assertEqual(validate_worker_environment(first, self.inventory()), plans[0])

    def test_managed_profile_does_not_inherit_batch_experiment(self):
        plan = fleet_plan([FIRST], self.inventory())[0]
        env = worker_environment(plan, {'RALLYMATE_SAM3D_BATCH_FRAMES': '8',
            'RALLYMATE_SAM3D_BATCH_GRAPH_SIZES': '8',
            'RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB': '2560',
            'RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES': '8'})
        self.assertEqual(env['RALLYMATE_SAM3D_BATCH_FRAMES'], '1')
        self.assertEqual(env['RALLYMATE_SAM3D_BATCH_GRAPH_SIZES'], '')
        self.assertEqual(env['RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB'], '512')
        self.assertEqual(env['RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES'], '2')
        self.assertEqual(env['RALLYMATE_SAM3D_REUSE_CUDA_CACHE'], '1')
        env['RALLYMATE_SAM3D_BATCH_FRAMES'] = '8'
        with self.assertRaises(ValueError):
            validate_worker_environment(env, self.inventory())

    def test_invalid_duplicate_absent_and_oversubscribed_cards_are_rejected(self):
        for selected in ([], [FIRST, FIRST], [FIRST, 'GPU-missing']):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                fleet_plan(selected, self.inventory())
        with self.assertRaises(ValueError):
            fleet_plan([FIRST], self.inventory(), sam_mib=16000)

    def test_memory_admission_and_visibility_fail_closed(self):
        plan = fleet_plan([FIRST], self.inventory())[0]
        env = worker_environment(plan, {})
        with self.assertRaises(RuntimeError):
            validate_worker_environment(env, [GPU(FIRST, 'busy', 16384, 9000)])
        with self.assertRaises(RuntimeError):
            validate_worker_environment(env, [GPU(FIRST, 'no physical reserve', 16384, 13000)])
        env['CUDA_VISIBLE_DEVICES'] = FIRST + ',' + SECOND
        with self.assertRaises(ValueError):
            validate_worker_environment(env, self.inventory())

    def test_one_worker_per_card_and_lock_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = 'GPU-' + str(uuid.uuid4()), 'GPU-' + str(uuid.uuid4())
            with GPUWorkerSlot(root, first):
                with self.assertRaises(RuntimeError):
                    with GPUWorkerSlot(root / 'different-data-root', first):
                        self.fail('same GPU was admitted twice')
                with GPUWorkerSlot(root, second):
                    pass
            with GPUWorkerSlot(root, first):
                pass

    def test_physical_slot_blocks_other_process_roots_and_recovers_after_crash(self):
        from rallymate_vision.owned_subprocess import OwnedProcess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identifier = 'GPU-' + str(uuid.uuid4())
            code = ("import sys; from pathlib import Path; "
                    "from rallymate_service.gpu_fleet import GPUWorkerSlot; "
                    "slot=GPUWorkerSlot(Path(sys.argv[1]),sys.argv[2],state_directory=Path(sys.argv[3])); "
                    "slot.__enter__(); print('locked',flush=True); sys.stdin.read()")
            owner = OwnedProcess([sys.executable, '-u', '-c', code, str(root / 'child'), identifier, str(self.state_directory)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child = owner.process
            try:
                self.assertEqual(child.stdout.readline().strip(), 'locked')
                with self.assertRaises(RuntimeError):
                    with GPUWorkerSlot(root / 'parent', identifier):
                        self.fail('physical GPU was locked through two data roots')
                # Windows venv executables may be launchers. Killing only that
                # PID does not stop the interpreter that owns the mutex.
                owner.stop()
                with GPUWorkerSlot(root / 'recovery', identifier):
                    pass
            finally:
                owner.close()
                child.stdin.close(); child.stdout.close(); child.stderr.close()

    def test_sam_child_rejects_mutated_visibility_before_process_creation(self):
        from rallymate_vision.sam3d_runner import Sam3DProcess
        with patch.dict(os.environ, {'RALLYMATE_MANAGED_GPU_UUID': FIRST, 'CUDA_VISIBLE_DEVICES': '0'}), \
                patch('rallymate_vision.sam3d_runner.OwnedProcess') as process:
            with self.assertRaisesRegex(ValueError, 'physical UUID'):
                Sam3DProcess(Path('python'), Path('checkpoint'), runtime_dir=Path('runtime'),
                             device='0', inference_type='body')
            process.assert_not_called()

    def test_inventory_rejects_missing_memory_and_duplicate_devices(self):
        good = f'{FIRST}, NVIDIA device, 16384, 15000\n'
        self.assertEqual(parse_inventory(good)[0].free_mib, 15000)
        for value in (good + good, good.replace('15000', 'N/A'), good.replace('15000', '17000')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_inventory(value)

    def test_allocator_cap_is_optional_scoped_and_verified(self):
        torch = SimpleNamespace(cuda=Mock())
        self.assertIsNone(configure_cuda_memory_budget(torch, '0', 'CAP', env={}))
        torch.cuda.get_device_properties.assert_not_called()
        torch.cuda.get_device_properties.return_value.total_memory = 16384 * 1024**2
        torch.cuda.get_per_process_memory_fraction.return_value = 1024 / 16384
        receipt = configure_cuda_memory_budget(torch, '0', 'CAP', env={'CAP': '1024'})
        self.assertEqual(receipt['allocator_limit_mib'], 1024)
        torch.cuda.set_per_process_memory_fraction.assert_called_once_with(1024 / 16384, device='cuda:0')
        torch.cuda.get_per_process_memory_fraction.return_value = .99
        with self.assertRaises(RuntimeError):
            configure_cuda_memory_budget(torch, '0', 'CAP', env={'CAP': '1024'})


if __name__ == '__main__':
    unittest.main()
