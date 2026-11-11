from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from rallymate_service.api import create_app
from rallymate_service.config import ServiceSettings
from rallymate_service.database import JobDatabase


class ExecutorStatusApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = ServiceSettings(data_root=root / 'data', database_path=root / 'data/jobs.sqlite3',
            detect_model=root / 'detect.pt', pose_model=root / 'pose.pt', model_license_ack='enterprise',
            api_key='executor-test-key')
        self.settings.ensure_directories()
        self.settings.detect_model.touch()
        self.settings.pose_model.touch()
        self.db = JobDatabase(self.settings.database_path)
        self.db.initialize()
        self.state = {'status': 'ready', 'available': True,
                      'heartbeat_at': '2026-11-08T11:00:00+00:00', 'work_kind': 'idle'}
        self.shutdown = Mock(return_value={'status': 'starting'})
        self.app = create_app(self.settings, self.db, executor_status=lambda: self.state, executor_shutdown=self.shutdown)
        self.client = TestClient(self.app, client=('127.0.0.1', 50000),
                                 headers={'Authorization': 'Bearer executor-test-key'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def job(self, identifier):
        return self.db.create_job(identifier, 'video.mp4', self.settings.uploads_dir / (identifier + '.mp4'),
            self.settings.requests_dir / (identifier + '.json'), self.settings.runs_dir / identifier)

    def test_dead_executor_is_not_ready_but_queued_job_remains_readable(self):
        self.job('waiting')
        self.state.update(status='unavailable', available=False, work_kind=None,
                          error=r'C:\Private\worker failed', worker_id='secret-host')
        health_response = self.client.get('/health/ready')
        self.assertEqual(health_response.status_code, 503)
        health = health_response.json()
        self.assertEqual(health['status'], 'not_ready')
        self.assertFalse(health['inference_ready'])
        self.assertFalse(health['admission']['accept_new_jobs'])
        response = self.client.get('/v1/jobs/waiting/status')
        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertEqual(status['status'], 'queued')
        self.assertIsNone(status['started_at'])
        self.assertEqual(status['queue_position'], 1)
        self.assertEqual(status['executor']['status'], 'unavailable')
        self.assertNotIn('Private', response.text)
        self.assertNotIn('secret-host', response.text)
        self.assertNotIn('summary', status)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        rejected = self.client.post('/v1/uploads', json={'upload_id': 'new-upload', 'filename': 'video.mp4', 'size': 1})
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(rejected.json()['detail']['code'], 'executor_unavailable')
        self.assertEqual(self.db.count_by_status(), {'queued': 1})

    def test_live_heartbeat_does_not_falsify_business_progress(self):
        original = self.job('running')
        claimed = self.db.claim_next('worker', 60, 2)
        first = self.client.get('/v1/jobs/running/status').json()
        self.state.update(status='busy', work_kind='analysis', heartbeat_at='2026-11-08T11:01:00+00:00')
        second = self.client.get('/v1/jobs/running/status').json()
        self.assertEqual(first['updated_at'], second['updated_at'])
        self.assertNotEqual(first['executor']['heartbeat_at'], second['executor']['heartbeat_at'])
        self.assertEqual(second['started_at'], claimed['started_at'])
        self.assertIsNone(second['queue_position'])
        self.assertEqual(second['created_at'], original['created_at'])
        self.assertTrue(self.client.get('/health/ready').json()['inference_ready'])
        self.db.mark_succeeded('running', {}, worker_id='worker', attempts=claimed['attempts'])
        finished = self.client.get('/v1/jobs/running/status').json()
        self.assertIsNotNone(finished['completed_at'])

    def test_queue_order_matches_claim_order_and_excludes_cancelled(self):
        for identifier in ('b', 'a', 'cancelled'):
            self.job(identifier)
        self.db.request_cancel('cancelled')
        with self.db.connect() as connection:
            connection.execute("UPDATE jobs SET created_at='2026-11-08T11:00:00+00:00'")
            connection.commit()
        self.assertEqual(self.client.get('/v1/jobs/a/status').json()['queue_position'], 1)
        self.assertEqual(self.client.get('/v1/jobs/b/status').json()['queue_position'], 2)
        self.assertEqual(self.db.claim_next('worker', 60, 2)['id'], 'a')
        self.assertEqual(self.client.get('/v1/jobs/b/status').json()['queue_position'], 1)
        cancelled = self.client.get('/v1/jobs/cancelled/status').json()
        self.assertIsNone(cancelled['queue_position'])
        self.assertIsNotNone(cancelled['cancelled_at'])

    def test_broken_monitor_fails_closed_and_recovery_reopens_submission(self):
        self.state.update(status='ready', available=True, heartbeat_at={'invalid': 'timestamp'})
        self.assertFalse(self.client.get('/health/ready').json()['inference_ready'])
        self.state.update(status='restarting', available=False, heartbeat_at=None)
        self.assertEqual(self.client.get('/health/ready').json()['status'], 'not_ready')
        self.state.update(status='ready', available=True, heartbeat_at=None)
        self.assertEqual(self.client.get('/health/ready').json()['status'], 'ready')

    def test_maintenance_drains_without_accepting_new_work_and_is_idempotent(self):
        response = self.client.post('/internal/maintenance/stop-executor')
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()['executor_stopped'])
        self.assertEqual(self.shutdown.call_count, 1)
        self.assertFalse(self.client.get('/health/ready').json()['admission']['accept_new_jobs'])
        self.assertFalse(self.client.get('/health/live').json()['admission']['accept_new_jobs'])
        self.assertEqual(self.client.post('/v1/uploads', json={}).status_code, 503)
        self.state.update(status='stopped', available=False)
        stopped = self.client.post('/internal/maintenance/stop-executor')
        self.assertEqual(stopped.status_code, 200)
        self.assertTrue(stopped.json()['executor_stopped'])

    def test_maintenance_does_not_interrupt_queued_analysis_or_busy_mesh(self):
        self.job('queued')
        self.assertEqual(self.client.post('/internal/maintenance/stop-executor').status_code, 409)
        self.shutdown.assert_not_called()
        self.assertTrue(self.client.get('/health/ready').json()['admission']['accept_new_jobs'])
        self.db.request_cancel('queued')
        self.shutdown.side_effect = ValueError('mesh running')
        self.assertEqual(self.client.post('/internal/maintenance/stop-executor').status_code, 409)
        self.assertTrue(self.client.get('/health/ready').json()['admission']['accept_new_jobs'])

    def test_maintenance_is_not_available_to_remote_clients(self):
        with TestClient(self.app, client=('203.0.113.1', 50000),
                        headers={'Authorization': 'Bearer executor-test-key'}) as remote:
            self.assertEqual(remote.post('/internal/maintenance/stop-executor').status_code, 403)
        self.shutdown.assert_not_called()

    def test_maintenance_requires_token_even_in_local_development(self):
        with TestClient(self.app, client=('127.0.0.1', 50001)) as client:
            self.assertEqual(client.post('/internal/maintenance/stop-executor').status_code, 401)
            self.assertEqual(client.post('/internal/maintenance/stop-executor',
                headers={'Authorization': 'Bearer wrong-key'}).status_code, 401)
        unconfigured = create_app(replace(self.settings, api_key=None), self.db,
                                   executor_status=lambda: self.state, executor_shutdown=self.shutdown)
        with TestClient(unconfigured, client=('127.0.0.1', 50002)) as client:
            response = client.post('/internal/maintenance/stop-executor')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['detail']['code'], 'maintenance_auth_not_configured')
        self.shutdown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
