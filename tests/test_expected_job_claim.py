from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service import worker


class ExpectedJobClaimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.video = self.root / 'cpu-fixture.mp4'
        self.video.write_bytes(b'no inference or model loading')
        self.database = JobDatabase(self.root / 'jobs.sqlite3')
        self.database.initialize()

    def create(self, identifier):
        return self.database.create_job(identifier, self.video.name, self.video,
            self.root / (identifier + '.json'), self.root / identifier)

    def claim(self, identifier=None, worker_id='worker', max_attempts=3):
        return self.database.claim_next(worker_id, 60, max_attempts,
                                       expected_job_id=identifier)

    def expire(self, identifier):
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (identifier,))
            connection.commit()

    def create_recovery(self, identifier, marker):
        self.create(identifier)
        payload = {
            'schema_version': '1.0.0', 'job_id': identifier,
            'source': {'video_path': str(self.video)},
            'output': {'directory': str(self.root / identifier)},
            'models': {'detect': str(self.video), 'pose': str(self.video)},
            'upstream_metadata': {'finalization_recovery': marker},
        }
        (self.root / (identifier + '.json')).write_text(json.dumps(payload), encoding='utf-8')

    def recovery_marker(self):
        return {'reused_inference': True, 'source_failed_job_id': 'source-failed',
                'frames_sha256': 'a' * 64, 'video_sha256': 'b' * 64}

    def settings(self):
        return ServiceSettings(data_root=self.root, database_path=self.database.path,
                               detect_model=self.video, pose_model=self.video)

    def assert_orphaned_recovery_cannot_reach_inference(self, expired):
        self.create_recovery('recovery', self.recovery_marker())
        source_raw = self.root / 'source-frames.jsonl'
        source_raw.write_bytes(b'original raw fixture must remain unchanged\n')
        original = source_raw.read_bytes()
        if expired:
            self.claim('recovery', worker_id='terminated-cpu-process')
            self.expire('recovery')
        calls = []
        def forbidden_runner(request):
            calls.append(request.job_id)
            raise AssertionError('ordinary inference must never run')
        with self.assertLogs(worker.LOGGER, level='ERROR'):
            self.assertTrue(worker.process_one(self.settings(), self.database,
                                               'ordinary-worker', forbidden_runner))
        state = self.database.get_job('recovery')
        self.assertEqual(calls, [])
        self.assertEqual(state['status'], 'failed')
        self.assertIn('CpuFinalizationSafetyError:', state['error'])
        self.assertEqual(state['attempts'], 2 if expired else 1)
        self.assertEqual(source_raw.read_bytes(), original)

    def test_orphaned_queued_recovery_is_failed_before_inference(self):
        self.assert_orphaned_recovery_cannot_reach_inference(expired=False)

    def test_orphaned_expired_recovery_is_failed_before_inference(self):
        self.assert_orphaned_recovery_cannot_reach_inference(expired=True)

    def test_recovery_marker_runner_capability_and_exact_target_fail_closed(self):
        for number, (marker, capability, targeted) in enumerate((
            (None, True, True), (False, True, True), ({}, True, True),
            ({**self.recovery_marker(), 'reused_inference': False}, True, True),
            ({**self.recovery_marker(), 'frames_sha256': 'invalid'}, True, True),
            (self.recovery_marker(), False, True),
            (self.recovery_marker(), 1, True),
            (self.recovery_marker(), True, False),
        )):
            with self.subTest(number=number):
                identifier = f'recovery-{number}'
                self.create_recovery(identifier, marker)
                calls = []
                def runner(request):
                    calls.append(request.job_id)
                    return {}
                runner.cpu_finalization_only = capability
                with self.assertLogs(worker.LOGGER, level='ERROR'):
                    worker.process_one(self.settings(), self.database, 'cpu', runner,
                                       expected_job_id=identifier if targeted else None)
                self.assertEqual(calls, [])
                self.assertEqual(self.database.get_job(identifier)['status'], 'failed')

    def test_explicit_cpu_runner_requires_marker_and_accepts_exact_target(self):
        self.create_recovery('valid', self.recovery_marker())
        calls = []
        def cpu_runner(request, cancellation_callback=None):
            cancellation_callback()
            calls.append(request.job_id)
            return {'processing': {'processed_frames': 1}}
        cpu_runner.cpu_finalization_only = True
        self.assertTrue(worker.process_one(self.settings(), self.database, 'cpu', cpu_runner,
                                           expected_job_id='valid'))
        self.assertEqual(calls, ['valid'])
        self.assertEqual(self.database.get_job('valid')['status'], 'succeeded')
        self.create_recovery('missing-marker', self.recovery_marker())
        path = self.root / 'missing-marker.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['upstream_metadata'] = {}
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertLogs(worker.LOGGER, level='ERROR'):
            worker.process_one(self.settings(), self.database, 'cpu', cpu_runner,
                               expected_job_id='missing-marker')
        self.assertEqual(calls, ['valid'])
        self.assertEqual(self.database.get_job('missing-marker')['status'], 'failed')

    def test_target_claim_leaves_other_queue_and_expired_leases_unchanged(self):
        for name in ('older-queued', 'other-retry', 'other-exhausted', 'target'):
            self.create(name)
        self.claim('other-retry')
        self.claim('other-exhausted')
        self.expire('other-retry')
        self.expire('other-exhausted')
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET attempts = 3 WHERE id = 'other-exhausted'")
            connection.commit()
        originals = {name: self.database.get_job(name) for name in ('older-queued', 'other-retry', 'other-exhausted')}
        selected = self.claim('target')
        self.assertEqual(selected['id'], 'target')
        self.assertEqual(selected['attempts'], 1)
        for name, original in originals.items():
            self.assertEqual(self.database.get_job(name), original)

    def test_missing_or_active_target_never_falls_back_to_other_job(self):
        self.create('other')
        self.create('active')
        active = self.claim('active')
        other = self.database.get_job('other')
        self.assertIsNone(self.claim('missing'))
        self.assertIsNone(self.claim('active', worker_id='second'))
        self.assertEqual(self.database.get_job('other'), other)
        self.assertEqual(self.database.get_job('active'), active)

    def test_target_retry_retains_lease_and_owner_fencing(self):
        self.create('target')
        original = self.claim('target')
        self.expire('target')
        replacement = self.claim('target', worker_id='replacement')
        self.assertEqual(replacement['attempts'], 2)
        self.assertIsNone(self.database.mark_succeeded('target', {'stale': True},
            worker_id='worker', attempts=original['attempts']))
        self.assertEqual(self.database.get_job('target'), replacement)

    def test_target_cancel_and_exhausted_attempt_stay_unclaimable(self):
        self.create('cancelled')
        self.database.request_cancel('cancelled')
        self.assertIsNone(self.claim('cancelled'))
        self.create('cancel-running')
        self.claim('cancel-running')
        self.database.request_cancel('cancel-running')
        self.expire('cancel-running')
        original = self.database.get_job('cancel-running')
        self.assertIsNone(self.claim('cancel-running'))
        self.assertEqual(self.database.get_job('cancel-running'), original)
        self.create('exhausted')
        self.claim('exhausted')
        self.expire('exhausted')
        self.assertIsNone(self.claim('exhausted', max_attempts=1))
        self.assertEqual(self.database.get_job('exhausted')['status'], 'failed')

    def test_concurrent_target_claims_have_only_one_owner(self):
        self.create('other')
        self.create('target')
        barrier = threading.Barrier(2)
        def attempt(name):
            barrier.wait(timeout=5)
            return self.claim('target', worker_id=name)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ('one', 'two')))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.database.get_job('target')['attempts'], 1)
        self.assertEqual(self.database.get_job('other')['status'], 'queued')

    def test_default_claim_keeps_queue_order_and_expired_retry(self):
        self.create('first')
        self.create('second')
        self.assertEqual(self.claim()['id'], 'first')
        self.expire('first')
        self.assertEqual(self.claim(worker_id='retry')['id'], 'first')
        self.assertEqual(self.claim()['id'], 'second')

    def test_process_one_passes_target_and_preserves_runner_callbacks(self):
        self.create('older')
        self.create('target')
        settings = ServiceSettings(data_root=self.root, database_path=self.database.path,
                                   detect_model=self.video, pose_model=self.video)
        request = SimpleNamespace(scoring=SimpleNamespace(calibration_assets=[]))
        def runner(loaded, progress_callback=None, cancellation_callback=None):
            self.assertIs(loaded, request)
            cancellation_callback()
            progress_callback({'processed_frames': 2911, 'percent': 90})
            return {'processing': {'processed_frames': 2911}}
        with patch.object(worker, 'load_request', return_value=request) as loader:
            self.assertTrue(worker.process_one(settings, self.database, 'cpu', runner,
                                               expected_job_id='target'))
            loader.assert_called_once_with(str(self.root / 'target.json'))
        self.assertEqual(self.database.get_job('target')['status'], 'succeeded')
        self.assertEqual(self.database.get_job('older')['status'], 'queued')
        with patch.object(worker, 'load_request') as loader:
            self.assertFalse(worker.process_one(settings, self.database, 'cpu', runner,
                                                expected_job_id='missing'))
            loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
