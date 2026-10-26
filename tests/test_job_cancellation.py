from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase
from rallymate_service import worker


class CancellationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / 'original.mp4'
        self.video.write_bytes(b'original upload must survive cancellation')
        self.request = self.root / 'request.json'
        self.request.write_text('{}', encoding='utf-8')
        self.output = self.root / 'run'
        self.output.mkdir()
        self.database = JobDatabase(self.root / 'jobs.sqlite3')
        self.database.initialize()

    def create(self, identifier='job'):
        return self.database.create_job(identifier, 'original.mp4', self.video, self.request, self.output)

    def claim(self, identifier='job', worker_id='worker'):
        self.create(identifier)
        return self.database.claim_next(worker_id, 60, 3)

    def settings(self, api_key=None):
        return ServiceSettings(data_root=self.root / 'service', database_path=self.database.path,
                               detect_model=self.video, pose_model=self.video, api_key=api_key)

    def expire(self, identifier='job'):
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (identifier,))
            connection.commit()


class JobCancellationDatabaseTests(CancellationFixture):
    def test_queued_cancellation_is_terminal_unclaimable_and_preserves_files(self):
        original = self.create()
        cancelled = self.database.request_cancel('job')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertTrue(cancelled['cancel_requested'])
        self.assertEqual(cancelled['completed_at'], cancelled['cancelled_at'])
        self.assertEqual(cancelled['attempts'], 0)
        self.assertEqual(cancelled['created_at'], original['created_at'])
        self.assertIsNone(self.database.claim_next('worker', 60, 3))
        self.assertEqual(self.database.request_cancel('job'), cancelled)
        self.assertTrue(self.video.exists())
        self.assertTrue(self.request.exists())

    def test_running_request_waits_for_worker_acknowledgement(self):
        claim = self.claim()
        self.database.update_progress('job', {'phase': 'inference', 'percent': 27, 'processed_frames': 18}, 60)
        requested = self.database.request_cancel('job')
        self.assertEqual(requested['status'], 'running')
        self.assertIsNone(requested['completed_at'])
        self.assertIsNone(requested['cancelled_at'])
        self.assertEqual(self.database.request_cancel('job'), requested)
        self.assertTrue(self.database.mark_cancelled('job', worker_id='worker', attempts=claim['attempts']))
        cancelled = self.database.get_job('job')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['progress']['percent'], 27)
        self.assertEqual(cancelled['progress']['processed_frames'], 18)

    def test_acknowledgement_requires_cancel_request(self):
        claim = self.claim()
        self.assertFalse(self.database.mark_cancelled('job', worker_id='worker', attempts=claim['attempts']))
        self.assertEqual(self.database.get_job('job')['status'], 'running')

    def test_requested_cancel_wins_over_late_success_and_failure(self):
        for completion in ('succeeded', 'failed'):
            with self.subTest(completion=completion):
                claim = self.claim(completion)
                self.database.request_cancel(completion)
                if completion == 'succeeded':
                    self.database.mark_succeeded(completion, {'complete': True}, worker_id='worker', attempts=claim['attempts'])
                else:
                    self.database.mark_failed(completion, 'late inference failure', worker_id='worker', attempts=claim['attempts'])
                self.assertEqual(self.database.get_job(completion)['status'], 'cancelled')
                before = self.database.get_job(completion)
                self.database.mark_succeeded(completion, {'wrong': True})
                self.database.mark_failed(completion, 'wrong')
                self.assertEqual(self.database.get_job(completion), before)

    def test_terminal_success_and_failure_are_idempotent_without_erasing_history(self):
        for status in ('succeeded', 'failed'):
            self.claim(status)
            if status == 'succeeded':
                self.database.mark_succeeded(status, {'original': 'summary'})
            else:
                self.database.mark_failed(status, 'original failure')
            before = self.database.get_job(status)
            self.assertEqual(self.database.request_cancel(status), before)
            self.assertFalse(before['cancel_requested'])

    def test_unknown_job_is_absent(self):
        self.assertIsNone(self.database.request_cancel('missing'))

    def test_expired_requested_cancel_is_never_retried_or_false_acknowledged(self):
        claim = self.claim()
        self.database.request_cancel('job')
        self.expire()
        self.assertIsNone(self.database.claim_next('new-worker', 60, 3))
        current = self.database.get_job('job')
        self.assertEqual(current['status'], 'running')
        self.assertTrue(current['cancel_requested'])
        self.assertEqual(current['attempts'], claim['attempts'])
        self.assertIsNone(current['cancelled_at'])
        self.assertTrue(self.database.mark_cancelled('job', worker_id='worker', attempts=claim['attempts']))

    def test_expired_uncancelled_attempt_is_fenced_from_new_attempt(self):
        old = self.claim()
        self.expire()
        new = self.database.claim_next('new-worker', 60, 3)
        self.assertEqual(new['attempts'], old['attempts'] + 1)
        self.database.update_progress('job', {'percent': 99}, 60, worker_id='worker', attempts=old['attempts'])
        self.database.mark_succeeded('job', {'old': True}, worker_id='worker', attempts=old['attempts'])
        self.database.mark_failed('job', 'old', worker_id='worker', attempts=old['attempts'])
        self.assertEqual(self.database.get_job('job'), new)
        self.database.request_cancel('job')
        self.assertFalse(self.database.mark_cancelled('job', worker_id='worker', attempts=old['attempts']))
        self.assertTrue(self.database.mark_cancelled('job', worker_id='new-worker', attempts=new['attempts']))

    def test_completion_cancel_race_has_consistent_winner(self):
        for index in range(8):
            identifier = f'race-{index}'
            self.claim(identifier)
            barrier = threading.Barrier(2)
            errors = []
            def act(fn):
                try:
                    barrier.wait(timeout=5)
                    fn()
                except BaseException as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=act, args=(lambda: self.database.request_cancel(identifier),)),
                       threading.Thread(target=act, args=(lambda: self.database.mark_succeeded(identifier, {'done': True}),))]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
            self.assertFalse(errors)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            final = self.database.get_job(identifier)
            self.assertIn(final['status'], ('succeeded', 'cancelled'))
            self.assertEqual(final['cancel_requested'], final['status'] == 'cancelled')
            self.assertEqual(final['cancelled_at'] is not None, final['status'] == 'cancelled')

    def test_old_check_constraint_migrates_rows_indexes_and_missing_progress(self):
        old_path = self.root / 'old.sqlite3'
        with closing(sqlite3.connect(old_path)) as connection:
            connection.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
                original_filename TEXT NOT NULL, video_path TEXT NOT NULL, request_path TEXT NOT NULL,
                output_dir TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                started_at TEXT, completed_at TEXT, lease_expires_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT, summary_json TEXT, error TEXT)""")
            connection.execute('CREATE INDEX custom_job_history ON jobs(completed_at)')
            connection.execute("INSERT INTO jobs VALUES ('old','succeeded','a.mp4','video','request','output','created','updated','started','completed',NULL,2,'worker',?,NULL)", (json.dumps({'historic': True}),))
            connection.commit()
        migrated = JobDatabase(old_path)
        migrated.initialize()
        migrated.initialize()
        historic = migrated.get_job('old')
        self.assertEqual(historic['summary'], {'historic': True})
        self.assertEqual(historic['attempts'], 2)
        self.assertFalse(historic['cancel_requested'])
        self.assertEqual(migrated.request_cancel('old'), historic)
        migrated.create_job('new', 'a.mp4', self.video, self.request, self.output)
        self.assertEqual(migrated.request_cancel('new')['status'], 'cancelled')
        with migrated.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='custom_job_history'").fetchone())
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class JobCancellationWorkerTests(CancellationFixture):
    def run_one(self, runner, loader=None):
        request = SimpleNamespace(scoring=SimpleNamespace(calibration_assets=[]))
        with patch.object(worker, 'load_request', side_effect=loader or (lambda _: request)):
            return worker.process_one(self.settings(), self.database, 'worker', runner)

    def test_cooperative_callback_interrupts_and_finally_runs_before_ack(self):
        self.create()
        cleanup = []
        def runner(request, cancellation_callback=None):
            self.assertIsNotNone(cancellation_callback)
            try:
                self.database.request_cancel('job')
                cancellation_callback()
                self.fail('cancellation must interrupt runner')
            finally:
                self.assertEqual(self.database.get_job('job')['status'], 'running')
                cleanup.append(True)
        self.assertTrue(self.run_one(runner))
        self.assertEqual(cleanup, [True])
        self.assertEqual(self.database.get_job('job')['status'], 'cancelled')
        self.assertIsNone(self.database.get_job('job')['error'])

    def test_legacy_runner_remains_running_until_it_returns(self):
        self.create()
        def runner(request):
            job = self.database.request_cancel('job')
            self.assertEqual(job['status'], 'running')
            self.assertIsNone(job['cancelled_at'])
            return {'completed_after_request': True}
        self.run_one(runner)
        self.assertEqual(self.database.get_job('job')['status'], 'cancelled')

    def test_cancel_during_request_loading_skips_runner(self):
        self.create()
        def load(_):
            self.database.request_cancel('job')
            return SimpleNamespace(scoring=SimpleNamespace(calibration_assets=[]))
        self.run_one(lambda _: self.fail('cancelled job must not start inference'), loader=load)
        self.assertEqual(self.database.get_job('job')['status'], 'cancelled')

    def test_exception_after_request_cannot_turn_cancel_into_failure(self):
        self.create()
        def runner(request):
            self.database.request_cancel('job')
            raise RuntimeError('concurrent inference error')
        self.run_one(runner)
        self.assertEqual(self.database.get_job('job')['status'], 'cancelled')

    def test_stale_worker_callback_stops_without_touching_new_attempt(self):
        self.create()
        def runner(request, cancellation_callback=None):
            self.expire()
            new = self.database.claim_next('replacement', 60, 3)
            self.assertEqual(new['attempts'], 2)
            cancellation_callback()
            self.fail('stale attempt must stop')
        self.run_one(runner)
        job = self.database.get_job('job')
        self.assertEqual(job['status'], 'running')
        self.assertEqual(job['worker_id'], 'replacement')
        self.assertIsNone(job['error'])

    def test_success_without_cancellation_still_completes(self):
        self.create()
        self.run_one(lambda request, cancellation_callback=None: {'processing': {'processed_frames': 3}})
        self.assertEqual(self.database.get_job('job')['status'], 'succeeded')


class JobCancellationApiTests(CancellationFixture):
    def test_cancel_public_contract_authorization_and_idempotence(self):
        self.create()
        with TestClient(create_app(self.settings(api_key='test-key'), self.database)) as client:
            url = '/v1/jobs/job/cancel'
            self.assertEqual(client.post(url).status_code, 401)
            headers = {'Authorization': 'Bearer test-key'}
            response = client.post(url, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload['status'], 'cancelled')
            self.assertIs(payload['cancel_requested'], True)
            self.assertTrue(payload['cancel_requested_at'])
            self.assertTrue(payload['cancelled_at'])
            self.assertNotIn('video_path', payload)
            self.assertNotIn('worker_id', payload)
            self.assertEqual(payload['artifact_urls'], {})
            self.assertEqual(client.post(url, headers=headers).json(), payload)
            self.assertEqual(client.get('/v1/jobs/job', headers=headers).json(), payload)
            self.assertEqual(client.post('/v1/jobs/missing/cancel', headers=headers).status_code, 404)

    def test_running_cancel_response_does_not_claim_stopped(self):
        self.claim()
        with TestClient(create_app(self.settings(), self.database)) as client:
            response = client.post('/v1/jobs/job/cancel')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], 'running')
            self.assertTrue(response.json()['cancel_requested'])
            self.assertIsNone(response.json()['cancelled_at'])
            self.assertIsNone(response.json()['completed_at'])


if __name__ == '__main__':
    unittest.main()
