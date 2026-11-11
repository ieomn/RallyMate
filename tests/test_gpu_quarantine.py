"""CPU-only ownership tests; every persistent state path is test-isolated."""
from contextlib import ExitStack
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from rallymate_service import gpu_fleet, worker
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service.gpu_fleet import (
    GPUQuarantinedError, GPUWorkerSlot, ManagedWorkerProcess, clear_gpu_quarantine,
    new_managed_worker_id, stop_managed_workers,
)
from rallymate_vision.owned_subprocess import OwnedProcess, ProcessTreeCleanupError


class GPUQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_directory = self.root / 'node-state'
        self.identifier = 'GPU-' + str(uuid.uuid4())
        self.worker_id = new_managed_worker_id()
        state_root = patch.object(gpu_fleet, 'node_gpu_state_root', return_value=self.state_directory)
        state_root.start()
        self.addCleanup(state_root.stop)

    def slot(self, *, worker_id=None, data_root='first'):
        return GPUWorkerSlot(self.root / data_root, self.identifier, worker_id=worker_id,
                             state_directory=self.state_directory)

    def state(self):
        return json.loads((self.state_directory / (self.identifier.lower() + '.json')).read_text())

    def assert_blocked(self):
        with self.assertRaises(GPUQuarantinedError):
            with self.slot(data_root='different-data-root'):
                self.fail('an unresolved GPU tree was admitted')

    def clear(self, owner, *, confirmed=True):
        return clear_gpu_quarantine(self.identifier, owner, confirmed_tree_exit=confirmed,
                                    state_directory=self.state_directory)

    def test_legacy_quarantine_survives_mutex_release_and_can_be_explicitly_repaired(self):
        with self.slot(worker_id='preview-host-123-random') as slot:
            slot.quarantine('child exit not confirmed')
        self.assertEqual(self.state()['status'], 'quarantined')
        self.assert_blocked()
        with self.assertRaises(ValueError):
            self.clear(slot.worker_id, confirmed=False)
        self.assertFalse(self.clear('different-owner'))
        self.assert_blocked()
        self.assertTrue(self.clear(slot.worker_id))
        with self.slot(data_root='repaired'):
            pass

    def test_context_manager_cleanup_error_quarantines_benchmark_owner(self):
        with self.assertRaises(ProcessTreeCleanupError):
            with self.slot() as slot:
                raise ProcessTreeCleanupError('inference child cleanup failed')
        self.assertTrue(slot.worker_id.startswith('slot-'))
        self.assert_blocked()

    def test_benchmark_explicit_close_failure_is_sticky_even_if_repeat_close_succeeds(self):
        from scripts.benchmark_sam3d_pipeline import close_benchmark_perception
        perception = Mock()
        perception.close.side_effect = [RuntimeError('cleanup failed'), None]
        with self.slot() as slot:
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                close_benchmark_perception(perception, slot)
            close_benchmark_perception(perception, slot)
        self.assert_blocked()

    def test_benchmark_successful_cleanup_after_pipeline_error_does_not_quarantine(self):
        from scripts.benchmark_sam3d_pipeline import close_benchmark_perception
        perception = Mock()
        with self.assertRaisesRegex(RuntimeError, 'pipeline failed'):
            with self.slot() as slot:
                try:
                    raise RuntimeError('pipeline failed')
                finally:
                    close_benchmark_perception(perception, slot)
        perception.close.assert_called_once_with()
        with self.slot(data_root='subsequent-benchmark'):
            pass

    def test_normal_legacy_and_managed_close_do_not_leave_blocking_state(self):
        for owner in (None, 'preview-host-123-random', self.worker_id):
            with self.subTest(owner=owner):
                with self.slot(worker_id=owner) as slot:
                    self.assertEqual(slot.state_path.exists(), owner == self.worker_id)
                self.assertFalse(slot.state_path.exists())
                with self.slot(data_root='after-clean-close'):
                    pass

    def test_another_gpu_is_available_and_an_unacquired_object_cannot_quarantine(self):
        first = self.slot()
        second = self.slot()
        with first:
            with self.assertRaises(RuntimeError):
                second.quarantine('not the owner')
            self.assertFalse(self.clear(first.worker_id))
            with GPUWorkerSlot(self.root, 'GPU-' + str(uuid.uuid4()), state_directory=self.state_directory):
                pass

    def test_managed_active_marker_survives_quarantine_write_failure(self):
        with self.slot(worker_id=self.worker_id) as slot:
            with patch.object(gpu_fleet, '_write_gpu_state', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    slot.quarantine('unconfirmed cleanup')
        self.assertEqual(self.state()['status'], 'owned')
        self.assert_blocked()
        self.assertTrue(self.clear(self.worker_id))

    def test_corrupt_state_is_fail_closed_and_is_not_deleted_by_owner_claim(self):
        path = self.state_directory / (self.identifier.lower() + '.json')
        path.parent.mkdir()
        path.write_text('{incomplete', encoding='utf8')
        self.assert_blocked()
        with self.assertRaises(GPUQuarantinedError):
            self.clear(self.worker_id)
        self.assertEqual(path.read_text(), '{incomplete')

    def test_real_cpu_child_crash_leaves_managed_marker_until_tree_confirmation(self):
        code = (
            "import sys; from pathlib import Path; "
            "from rallymate_service.gpu_fleet import GPUWorkerSlot; "
            "slot=GPUWorkerSlot(Path(sys.argv[1]),sys.argv[2],worker_id=sys.argv[3],"
            "state_directory=Path(sys.argv[4])); "
            "slot.__enter__(); print('owned',flush=True); sys.stdin.read()"
        )
        owner = OwnedProcess([sys.executable, '-u', '-c', code, str(self.root / 'child-data'),
            self.identifier, self.worker_id, str(self.state_directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process = owner.process
        try:
            self.assertEqual(process.stdout.readline().strip(), 'owned')
            self.assertFalse(self.clear(self.worker_id))  # Even a claimed receipt cannot override a live mutex.
            owner.stop()  # Simulate abrupt worker exit, without running slot.close().
            self.assert_blocked()
            database = Mock()
            database.running_attempts_for_worker.return_value = []
            database.recover_confirmed_worker_attempts.return_value = []
            receipts = stop_managed_workers([
                ManagedWorkerProcess(self.worker_id, self.identifier, owner, self.state_directory)
            ], database, max_attempts=2)
            self.assertTrue(receipts[0]['tree_exit_confirmed'])
            self.assertTrue(receipts[0]['gpu_quarantine_cleared'])
            with self.slot(data_root='recovered'):
                pass
        finally:
            owner.close()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_unconfirmed_supervisor_cleanup_never_clears_quarantine_or_reads_jobs(self):
        with self.slot(worker_id=self.worker_id) as slot:
            slot.quarantine('cleanup failed')
        owner = Mock(closed=False)
        owner.close.side_effect = ProcessTreeCleanupError('tree still unconfirmed')
        database = Mock()
        with self.assertRaises(ProcessTreeCleanupError):
            stop_managed_workers([
                ManagedWorkerProcess(self.worker_id, self.identifier, owner, self.state_directory)
            ], database, max_attempts=2)
        database.running_attempts_for_worker.assert_not_called()
        self.assert_blocked()

    def worker_context(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        fake_torch = SimpleNamespace(cuda=SimpleNamespace(get_device_properties=Mock(
            return_value=SimpleNamespace(uuid=self.identifier[4:]))))
        stack.enter_context(patch.dict(sys.modules, {'torch': fake_torch}))
        stack.enter_context(patch.object(worker, 'resolve_device', return_value='0'))
        stack.enter_context(patch('rallymate_service.mesh_render_worker.MeshRenderCoordinator'))
        return stack, Mock(data_root=self.root / 'worker-data', device='0'), Mock()

    def test_constructor_cleanup_error_quarantines_even_before_runner_assignment(self):
        stack, settings, database = self.worker_context()
        perception = Mock()
        perception.close.side_effect = ProcessTreeCleanupError('constructor child remains unconfirmed')
        stack.enter_context(patch.object(worker, 'configure_cpu_thread_pools', return_value={}))
        stack.enter_context(patch('rallymate_vision.cuda_memory_budget.configure_cuda_memory_budget', return_value=None))
        stack.enter_context(patch.object(worker, 'Yolo26Perception', return_value=perception))
        stack.enter_context(patch.object(worker.PersistentVisionRunner, '_finish_initialization',
                                        side_effect=RuntimeError('post-construction failure')))
        with self.assertRaises(ProcessTreeCleanupError):
            worker.run_worker(settings, database, once=True, worker_id=self.worker_id)
        perception.close.assert_called_once_with()
        self.assertEqual(self.state()['worker_id'], self.worker_id)
        self.assertEqual(self.state()['status'], 'quarantined')
        self.assert_blocked()

    def test_final_runner_close_failure_quarantines_before_slot_release(self):
        stack, settings, database = self.worker_context()
        runner = Mock()
        runner.close.side_effect = RuntimeError('unexpected cleanup failure')
        stack.enter_context(patch.object(worker, 'PersistentVisionRunner', return_value=runner))
        stack.enter_context(patch.object(worker, 'process_one', return_value=False))
        with self.assertRaisesRegex(RuntimeError, 'cleanup failure'):
            worker.run_worker(settings, database, once=True, worker_id=self.worker_id)
        self.assert_blocked()

    def test_no_state_write_permission_prevents_models_from_loading(self):
        stack, settings, database = self.worker_context()
        constructor = stack.enter_context(patch.object(worker, 'PersistentVisionRunner'))
        stack.enter_context(patch.object(gpu_fleet, '_write_gpu_state', side_effect=PermissionError('node state denied')))
        with self.assertRaises(PermissionError):
            worker.run_worker(settings, database, once=True, worker_id=self.worker_id)
        constructor.assert_not_called()

    def test_cleanup_error_leaves_attempt_owned_for_supervisor_recovery(self):
        model = self.root / 'model'
        model.touch()
        database = JobDatabase(self.root / 'jobs.sqlite3')
        database.initialize()
        database.create_job('job', 'source.mp4', self.root / 'source.mp4', self.root / 'request.json', self.root / 'output')
        settings = ServiceSettings(data_root=self.root, database_path=database.path, detect_model=model, pose_model=model)
        def fail_cleanup(request):
            raise ProcessTreeCleanupError('child tree not confirmed')
        with patch.object(worker, 'load_request', return_value=SimpleNamespace(upstream_metadata={})), \
                self.assertRaises(ProcessTreeCleanupError):
            worker.process_one(settings, database, self.worker_id, runner=fail_cleanup, recover_expired=False)
        current = database.get_job('job')
        self.assertEqual((current['status'], current['worker_id'], current['attempts']), ('running', self.worker_id, 1))


if __name__ == '__main__':
    unittest.main()
