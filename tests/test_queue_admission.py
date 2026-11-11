from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from rallymate_service.database import JobDatabase, QueueCapacityError


class QueueAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = JobDatabase(self.root / 'jobs.sqlite3')
        self.database.initialize()

    def create(self, identifier, limit=8):
        return self.database.create_job(identifier, 'video.mp4', self.root / 'video.mp4',
            self.root / f'{identifier}.json', self.root / identifier, max_active_jobs=limit)

    def test_simultaneous_completions_cannot_exceed_capacity(self):
        def submit(number):
            try:
                self.create(str(number))
                return 'accepted'
            except QueueCapacityError as exc:
                self.assertEqual(exc.limit, 8)
                return 'full'
        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(submit, range(24)))
        self.assertEqual(outcomes.count('accepted'), 8)
        self.assertEqual(outcomes.count('full'), 16)
        self.assertEqual(self.database.count_by_status(), {'queued': 8})

    def test_running_cancellation_holds_capacity_until_acknowledged(self):
        self.create('first', 1)
        owned = self.database.claim_next('worker', 60, 2)
        self.database.request_cancel('first')
        with self.assertRaises(QueueCapacityError):
            self.create('second', 1)
        self.assertIsNone(self.database.get_job('second'))
        self.database.mark_cancelled('first', worker_id='worker', attempts=owned['attempts'])
        self.assertEqual(self.create('second', 1)['status'], 'queued')

    def test_queued_cancellation_releases_capacity_and_rejection_is_retriable(self):
        self.create('first', 1)
        with self.assertRaises(QueueCapacityError):
            self.create('second', 1)
        self.database.request_cancel('first')
        self.assertEqual(self.create('second', 1)['status'], 'queued')

    def test_invalid_limits_do_not_insert_jobs(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.create('invalid', limit)
        self.assertEqual(self.database.count_by_status(), {})

    def test_managed_claim_does_not_recover_a_tree_merely_because_lease_expired(self):
        self.create('expired')
        first = self.database.claim_next('old-worker', 60, 2)
        with self.database.connect() as connection:
            connection.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id='expired'")
            connection.commit()
        self.create('queued')
        next_job = self.database.claim_next('new-worker', 60, 2, recover_expired=False)
        self.assertEqual(next_job['id'], 'queued')
        old = self.database.get_job('expired')
        self.assertEqual(old['worker_id'], 'old-worker')
        self.assertEqual(old['attempts'], first['attempts'])
        self.assertEqual(old['status'], 'running')


if __name__ == '__main__':
    unittest.main()
