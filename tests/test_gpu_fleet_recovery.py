from __future__ import annotations

import subprocess
import sys
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rallymate_service.database import JobDatabase
from rallymate_service.gpu_fleet import (
    GPU as GPUInventory, ManagedWorkerProcess, new_managed_worker_id, require_managed_worker_id, stop_managed_workers,
)
from rallymate_vision.owned_subprocess import OwnedProcess, ProcessTreeCleanupError


GPU = 'GPU-00000000-0000-0000-0000-000000000001'


class FakeOwnedProcess:
    def __init__(self, on_close=None, failure=False):
        self.closed = False
        self.on_close = on_close
        self.failure = failure
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        if self.on_close:
            self.on_close()
        self.closed = True
        if self.failure:
            raise ProcessTreeCleanupError('descendant exit was not confirmed')

    def poll(self):
        return 0 if self.closed else None


class FleetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        state_root = patch('rallymate_service.gpu_fleet.node_gpu_state_root', return_value=self.root / 'node-state')
        state_root.start()
        self.addCleanup(state_root.stop)
        self.database = JobDatabase(self.root / 'jobs.sqlite3')
        self.database.initialize()
        self.worker_id = new_managed_worker_id()

    def create(self, identifier):
        return self.database.create_job(identifier, 'source.mp4', self.root / 'source.mp4',
            self.root / 'request.json', self.root / identifier)

    def claim(self, identifier, worker_id=None, max_attempts=3):
        self.create(identifier)
        return self.database.claim_next(worker_id or self.worker_id, 3600, max_attempts,
            expected_job_id=identifier, recover_expired=False)

    def recover(self, snapshots=None, **kwargs):
        return self.database.recover_confirmed_worker_attempts(self.worker_id,
            snapshots if snapshots is not None else self.database.running_attempts_for_worker(self.worker_id),
            max_attempts=kwargs.pop('max_attempts', 3), confirmed_tree_exit=kwargs.pop('confirmed_tree_exit', True), **kwargs)

    def test_confirmed_stop_requeues_now_and_fences_late_callbacks(self):
        original = self.claim('job')
        self.assertIsNotNone(original['lease_expires_at'])
        recovered = self.recover()
        self.assertEqual(recovered, [{'job_id': 'job', 'attempts': 1, 'worker_id': self.worker_id, 'status': 'queued'}])
        queued = self.database.get_job('job')
        self.assertIsNone(queued['worker_id'])
        self.assertIsNone(queued['lease_expires_at'])
        self.assertIsNone(queued['completed_at'])
        self.assertEqual(queued['attempts'], 1)
        self.assertEqual(queued['video_path'], original['video_path'])
        replacement = self.database.claim_next('replacement', 3600, 3, recover_expired=False)
        self.assertEqual(replacement['attempts'], 2)
        self.assertIsNone(self.database.mark_succeeded('job', {'wrong': True}, worker_id=self.worker_id, attempts=1))
        self.database.update_progress('job', {'wrong': True}, 3600, worker_id=self.worker_id, attempts=1)
        self.assertEqual(self.database.get_job('job'), replacement)

    def test_legacy_lease_recovery_cannot_steal_or_fail_any_reserved_fleet_owner(self):
        self.claim('managed-retry')
        self.claim('managed-exhausted')
        self.claim('reserved-prefix', worker_id='fleet-reserved-invalid-id')
        self.claim('legacy-retry', worker_id='legacy-worker')
        self.claim('legacy-exhausted', worker_id='legacy-worker')
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET attempts = 2 WHERE id IN ('managed-exhausted', 'legacy-exhausted')")
            connection.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00'")
            connection.commit()
        unchanged = {identifier: self.database.get_job(identifier) for identifier in (
            'managed-retry', 'managed-exhausted', 'reserved-prefix')}
        claimed = self.database.claim_next('legacy-new-worker', 3600, 2)  # recover_expired=True default.
        self.assertEqual(claimed['id'], 'legacy-retry')
        self.assertEqual(claimed['attempts'], 2)
        self.assertEqual(self.database.get_job('legacy-exhausted')['status'], 'failed')
        for identifier, expected in unchanged.items():
            self.assertEqual(self.database.get_job(identifier), expected)
            self.assertIsNone(self.database.claim_next('legacy-targeted', 3600, 2, expected_job_id=identifier))
            self.assertEqual(self.database.get_job(identifier), expected)
        # A confirmed exact-owner snapshot can still recover managed work.
        recovered = self.recover(max_attempts=2)
        self.assertEqual({row['job_id']: row['status'] for row in recovered}, {
            'managed-retry': 'queued', 'managed-exhausted': 'failed'})
        self.assertEqual(self.database.get_job('reserved-prefix'), unchanged['reserved-prefix'])

    def test_cancel_request_wins_even_when_retry_budget_exhausted(self):
        self.claim('cancelled')
        snapshot = self.database.running_attempts_for_worker(self.worker_id)
        self.database.request_cancel('cancelled')  # Arrived after the snapshot.
        self.assertEqual(self.recover(snapshot, max_attempts=1)[0]['status'], 'cancelled')
        result = self.database.get_job('cancelled')
        self.assertTrue(result['cancel_requested'])
        self.assertIsNotNone(result['cancelled_at'])
        self.assertEqual(result['cancelled_at'], result['completed_at'])
        self.assertIsNone(result['error'])

    def test_exhaustion_is_terminal_and_other_owners_or_attempts_untouched(self):
        self.claim('exhausted')
        self.claim('other', worker_id='other-worker')
        self.claim('new-attempt')
        self.claim('finished')
        snapshot = self.database.running_attempts_for_worker(self.worker_id)
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET attempts = 2 WHERE id = 'new-attempt'")
            connection.commit()
        self.database.mark_succeeded('finished', {'done': True}, worker_id=self.worker_id, attempts=1)
        unchanged = {identifier: self.database.get_job(identifier) for identifier in ('other', 'new-attempt', 'finished')}
        # Even an explicit snapshot pointing at another owner's row cannot affect it.
        snapshot.append({'job_id': 'other', 'attempts': 1})
        self.assertEqual(self.recover(snapshot, max_attempts=1), [
            {'job_id': 'exhausted', 'attempts': 1, 'worker_id': self.worker_id, 'status': 'failed'}])
        for identifier, expected in unchanged.items():
            self.assertEqual(self.database.get_job(identifier), expected)
        failed = self.database.get_job('exhausted')
        self.assertIsNotNone(failed['completed_at'])
        self.assertIsNone(failed['lease_expires_at'])

    def test_unconfirmed_or_invalid_recovery_does_not_mutate_any_job(self):
        before = self.claim('job')
        with self.assertRaisesRegex(ValueError, 'confirmed'):
            self.recover(confirmed_tree_exit=False)
        with self.assertRaises(ValueError):
            self.recover([{'job_id': 'job', 'attempts': 1}, {'job_id': 'bad', 'attempts': True}])
        with self.assertRaises(ValueError):
            self.recover([{'job_id': 'job', 'attempts': 1}] * 2)
        self.assertEqual(self.database.get_job('job'), before)

    def test_failed_tree_cleanup_never_reads_or_recovers_and_cannot_be_laundered_by_retry(self):
        owner = FakeOwnedProcess(failure=True)
        worker = ManagedWorkerProcess(self.worker_id, GPU, owner)
        database = Mock()
        for _ in range(2):
            with self.assertRaises(ProcessTreeCleanupError):
                stop_managed_workers([worker], database, max_attempts=2)
        self.assertEqual(owner.close_calls, 1)
        database.running_attempts_for_worker.assert_not_called()
        database.recover_confirmed_worker_attempts.assert_not_called()

    def test_all_workers_stop_before_recovery_and_last_moment_claim_is_included(self):
        self.create('late-claim')
        other_id = new_managed_worker_id()
        first = FakeOwnedProcess(on_close=lambda: self.database.claim_next(
            self.worker_id, 3600, 3, recover_expired=False))
        second = FakeOwnedProcess()
        children = [ManagedWorkerProcess(self.worker_id, GPU, first), ManagedWorkerProcess(other_id, GPU, second)]
        original = self.database.running_attempts_for_worker
        def checked_snapshot(worker_id):
            self.assertTrue(first.closed and second.closed)
            return original(worker_id)
        with patch.object(self.database, 'running_attempts_for_worker', side_effect=checked_snapshot):
            receipts = stop_managed_workers(children, self.database, max_attempts=3)
        self.assertEqual(receipts[0]['recovered_attempts'][0]['job_id'], 'late-claim')
        self.assertEqual(self.database.get_job('late-claim')['status'], 'queued')

    def test_one_failed_tree_prevents_recovery_of_all_workers(self):
        self.claim('job')
        workers = [ManagedWorkerProcess(self.worker_id, GPU, FakeOwnedProcess()),
                   ManagedWorkerProcess(new_managed_worker_id(), GPU, FakeOwnedProcess(failure=True))]
        with self.assertRaises(ProcessTreeCleanupError):
            stop_managed_workers(workers, self.database, max_attempts=3)
        self.assertEqual(self.database.get_job('job')['status'], 'running')
        self.assertTrue(workers[0].tree_exit_confirmed)
        self.assertFalse(workers[1].tree_exit_confirmed)

    def test_real_cpu_process_tree_stop_recovers_only_its_owner(self):
        self.claim('job')
        other = self.claim('other', worker_id='other-worker')
        owner = OwnedProcess([sys.executable, '-u', '-c', "import sys; print('ready',flush=True); sys.stdin.read()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process = owner.process
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            receipts = stop_managed_workers([ManagedWorkerProcess(self.worker_id, GPU, owner)], self.database, max_attempts=3)
            self.assertTrue(receipts[0]['tree_exit_confirmed'])
            self.assertEqual(self.database.get_job('job')['status'], 'queued')
            self.assertEqual(self.database.get_job('other'), other)
        finally:
            owner.close()
            process.stdin.close(); process.stdout.close(); process.stderr.close()

    def test_launcher_ids_are_fresh_and_old_generic_owner_names_are_rejected(self):
        generated = [new_managed_worker_id() for _ in range(20)]
        self.assertEqual(len(set(generated)), 20)
        for value in generated:
            self.assertEqual(require_managed_worker_id(value), value)
        for value in ('worker', '', None, 'fleet-123'):
            with self.assertRaises(ValueError):
                require_managed_worker_id(value)

    def test_launcher_binds_owner_environment_and_recovers_a_failed_child(self):
        from scripts import run_gpu_fleet
        self.create('job')
        paths = [self.root / 'worker-python', self.root / 'sam-python',
                 self.root / 'runtime/sam3d/checkpoints/sam-3d-body-dinov3/model.ckpt',
                 self.root / 'runtime/sam3d/checkpoints/sam-3d-body-dinov3/model_config.yaml',
                 self.root / 'models/yolo26n.pt']
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        observed = []
        class ExitedLauncher(FakeOwnedProcess):
            returncode = 7
            def poll(self):
                # Direct launcher has exited while tree ownership still needs close().
                return 7
        def launch(arguments, **kwargs):
            environment = kwargs['env']
            owner_id = require_managed_worker_id(environment['RALLYMATE_FLEET_WORKER_ID'])
            observed.append(owner_id)
            self.assertEqual(environment['RALLYMATE_SAM3D_BATCH_FRAMES'], '1')
            self.database.claim_next(owner_id, 3600, 2, recover_expired=False)
            return ExitedLauncher(on_close=lambda: self.assertEqual(self.database.get_job('job')['status'], 'running'))
        argv = ['fleet', '--gpus', GPU, '--launch', '--data-root', str(self.root),
                '--worker-python', str(paths[0]), '--sam-python', str(paths[1])]
        with patch.object(run_gpu_fleet, 'ROOT', self.root), patch.object(sys, 'argv', argv), \
                patch.object(run_gpu_fleet, 'discover_gpus', return_value=[GPUInventory(GPU, 'simulated', 16384, 16000)]), \
                patch.object(run_gpu_fleet, 'OwnedProcess', side_effect=launch), \
                patch.dict('os.environ', {'RALLYMATE_MAX_ATTEMPTS': '2'}), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'managed worker exited'):
                run_gpu_fleet.main()
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.database.get_job('job')['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
