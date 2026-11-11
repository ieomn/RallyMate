from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.benchmark_upload_delivery import existing_monitor, fetch_action_detail, fetch_http, main, record_checkpoint, validate_delivery, validate_full_video
from tests.test_model_measurement_contract import model_window_fixture


class UploadDeliveryAcceptanceTests(unittest.TestCase):
    def test_existing_monitor_requires_canonical_uuid_and_paired_utc_timestamps(self):
        job = '9e2ea1ae-b4d2-450c-8afa-d52c34507b8e'
        self.assertIsNone(existing_monitor(None, None, None))
        result = existing_monitor(job, '2026-11-08T12:00:00Z', '2026-11-08T12:01:00+00:00')
        self.assertEqual(result['upload_complete_seconds_from_upload'], 60)
        for arguments in [(job, None, None), (None, '2026-11-08T12:00:00Z', None),
                          ('short', '2026-11-08T12:00:00Z', '2026-11-08T12:01:00Z'),
                          (job, '2026-11-08T12:00:00', '2026-11-08T12:01:00Z'),
                          (job, '2026-11-08T20:00:00+08:00', '2026-11-08T12:01:00Z'),
                          (job, '2026-11-08T12:02:00Z', '2026-11-08T12:01:00Z')]:
            with self.assertRaises(ValueError):
                existing_monitor(*arguments)

    def test_existing_monitor_and_resume_never_upload_or_submit(self):
        job = '9e2ea1ae-b4d2-450c-8afa-d52c34507b8e'
        start, end = '2026-11-08T12:00:00Z', '2026-11-08T12:01:00Z'
        digest = hashlib.sha256(b'fixture').hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'video.mp4'
            source.write_bytes(b'fixture')
            output = root / 'receipt'
            calls = []

            def fetch(_opener, _base, path, **options):
                calls.append((path, options['method']))
                if path.endswith('/status'):
                    value = {'id': job, 'status': 'succeeded'}
                elif path.endswith('/report-summary'):
                    value = {'video': {'frame_count': 4}, 'processing': {'processed_frames': 4}}
                elif options.get('raw'):
                    return b'x' * 1024, {'status': 206}
                elif path.endswith('/mesh-preview'):
                    value = {'status': 'ready'}
                else:
                    value = {}
                return value, {'status': 200}

            def validate(stage, directory, actual_job, actual_digest):
                self.assertEqual((actual_job, actual_digest), (job, digest))
                return {'status': 'validated', 'selected_action': {'action_id': 'event-1'}}

            arguments = ['benchmark', '--base-url', 'https://example.invalid', '--source', str(source), '--output-dir', str(output),
                         '--existing-job-id', job, '--upload-started-at', start, '--upload-completed-at', end]
            with patch('sys.argv', arguments), patch('scripts.benchmark_upload_delivery.source_video_metadata', return_value={'frame_count': 4}), \
                 patch('scripts.benchmark_upload_delivery.fetch_http', side_effect=fetch), \
                 patch('scripts.benchmark_upload_delivery.validate_delivery', side_effect=validate), patch('builtins.print'):
                main()
                saved = json.loads((output / 'receipt.json').read_text())
                first_summary = saved['summary_ready_at']
                saved.update(status='interrupted', failure_type='HTTPError', failure='HTTP Error 429', interrupted_at=end)
                (output / 'receipt.json').write_text(json.dumps(saved))
                main()
            self.assertTrue(calls)
            self.assertTrue(all(method == 'GET' and '/uploads' not in path and '/health' not in path for path, method in calls))
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['job_id'], job)
            self.assertEqual(receipt['summary_ready_at'], first_summary)
            self.assertEqual(receipt['transport_scope'], 'browser_through_public_cloudflare')
            self.assertEqual(receipt['status'], 'passed')
            self.assertNotIn('failure', receipt)
            self.assertEqual(receipt['failure_history'][0]['failure'], 'HTTP Error 429')

    def test_full_video_rejects_a_clip_or_skipped_frames(self):
        for count, processed in [(3, 3), (4, 2)]:
            with self.assertRaises(ValueError):
                validate_full_video({'video': {'frame_count': count}, 'processing': {'processed_frames': processed}}, {'frame_count': 4})
        self.assertEqual(validate_full_video({'video': {'frame_count': 4}, 'processing': {'processed_frames': 4}}, {'frame_count': 4})['processed_frames'], 4)

    class Reply(io.BytesIO):
        def __init__(self, value, status=200, headers=None):
            super().__init__(json.dumps(value).encode())
            self.status, self.headers = status, headers or {}

    def transport(self, replies, *, budget=180):
        class Opener:
            def __init__(self):
                self.calls = []

            def open(self, request, **options):
                self.calls.append(request)
                value = replies.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
        opener, elapsed, waits, observations = Opener(), [0], [], []
        def wait(seconds):
            waits.append(seconds)
            elapsed[0] += seconds
        def read():
            return fetch_http(opener, 'https://example.invalid', '/v1/jobs/test-job/report-overview',
                              clock=lambda: elapsed[0], wait=wait, observe=observations.append, preparation_seconds=budget)
        return read, opener.calls, waits, observations

    @staticmethod
    def preparing(job='test-job'):
        return {'schema_version': 'segmented-report-v1', 'job_id': job, 'status': 'preparing', 'retry_after_ms': 1000, 'delivery': {'state': 'preparing'}}

    def test_transport_polls_explicit_preparation_and_busy_retaining_attempt_evidence(self):
        busy = self.error(429, 'report_segments_busy')
        busy.headers['Retry-After'] = '2'
        read, calls, waits, observed = self.transport([self.Reply(self.preparing(), 202), busy, self.Reply({'ready': True})])
        value, timing = read()
        self.assertTrue(value['ready'])
        self.assertEqual(waits, [1, 2])
        self.assertEqual([row['status'] for row in observed], [202, 429, 200])
        self.assertEqual(timing['total_read_seconds'], 3)
        self.assertEqual(len(timing['attempts']), 3)
        self.assertTrue(all(request.method == 'GET' for request in calls))
        self.assertTrue(all(request.full_url.endswith('?report_delivery=deferred-v1') for request in calls))

    def test_transport_fails_closed_for_wrong_preparation_and_resource_error(self):
        for response, error in [(self.Reply(self.preparing('foreign'), 202), ValueError),
                                (self.error(503, 'report_segment_resource_limit'), HTTPError),
                                (self.error(429, 'other_busy'), HTTPError)]:
            read, calls, waits, observed = self.transport([response])
            with self.assertRaises(error):
                read()
            self.assertEqual(len(calls), 1)
            self.assertEqual(waits, [])
            self.assertEqual(len(observed), 1)

    def test_transport_wait_budget_stops_without_extra_request(self):
        read, calls, waits, _ = self.transport([self.Reply(self.preparing(), 202) for _ in range(3)], budget=3)
        with self.assertRaises(TimeoutError):
            read()
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(waits), 3)

    def test_resume_preserves_first_milestone(self):
        receipt = {'upload_started_at': '2026-11-08T10:00:00+00:00'}
        self.assertTrue(record_checkpoint(receipt, 'summary_ready', observed_at='2026-11-08T10:20:00+00:00'))
        first = dict(receipt)
        self.assertFalse(record_checkpoint(receipt, 'summary_ready', observed_at='2026-11-08T11:20:00+00:00'))
        self.assertEqual(receipt, first)
        self.assertEqual(receipt['summary_ready_seconds_from_upload'], 1200)

    @staticmethod
    def error(code=413, detail='report_segment_limit'):
        return HTTPError('https://example.invalid/detail', code, 'rejected', {},
                         io.BytesIO(json.dumps({'detail': {'code': detail}}).encode()))

    def test_detail_shrinks_only_for_explicit_segment_limit(self):
        calls = []

        def fetch(path, **options):
            calls.append((path, options))
            if len(calls) == 1:
                raise self.error()
            return {'complete_record': True}, {'status': 200}

        payload, timing = fetch_action_detail(fetch, '/v1/jobs/test', 'event-1')
        self.assertTrue(payload['complete_record'])
        self.assertEqual([path.rsplit('=', 1)[-1] for path, _ in calls], ['4', '1'])
        self.assertTrue(all(options['limit'] == 16*1024*1024 for _, options in calls))
        self.assertEqual(timing['page_size_fallback']['code'], 'report_segment_limit')

    def test_other_failures_and_second_rejection_never_repeat(self):
        for code, detail, expected_calls in ((413, 'other_limit', 1), (422, 'report_segment_limit', 1), (413, 'report_segment_limit', 2)):
            calls = []

            def fetch(path, **options):
                calls.append(path)
                raise self.error(code, detail)

            with self.assertRaises(HTTPError):
                fetch_action_detail(fetch, '/v1/jobs/test', 'event-1')
            self.assertEqual(len(calls), expected_calls)

    def fixture(self, root):
        job = 'test-job-12345678'
        digest = 'a' * 64
        source = {'source_job_id': job, 'video_id': job, 'video_sha256': digest,
                  'frames_sha256': 'b' * 64, 'evidence_revision': 'c' * 64}
        envelope = {'schema_version': 'segmented-report-v1', 'job_id': job, 'source': source}
        summary = {**envelope, 'status': 'ready',
                   'video': {'width': 960, 'height': 448, 'fps': 30, 'duration_ms': 400, 'frame_count': 4},
                   'processing': {'processed_frames': 4},
                   'scoring': {'message': None, 'event_counts': {'FS01': 1}, 'score_status_counts': {'calibration_required': 1}},
                   'delivery': {'summary': 'complete', 'evidence': 'on_demand'}}
        overview = {**envelope, 'status': 'complete', 'assessment': None,
                    'result': {'job_id': job, 'input': {'video': {'sha256': digest}}}}
        action = {'action_id': 'event-1', 'event_id': 'event-1', 'event_code': 'FS01', 'label': 'Action',
                  'start_ms': 300, 'end_ms': 400, 'person_track_id': 99, 'source_track_id': 1,
                  'indicator_count': 1, 'evidence_status': 'available'}
        actions = {**envelope, 'status': 'complete', 'actions': [action],
                   'pagination': {'offset': 0, 'limit': 20, 'total': 1, 'next_offset': None}}
        event = {**model_window_fixture(), 'event_id': action['event_id'], 'event_code': 'FS01',
                 'person_track_id': 99, 'source_track_id': 1, 'video_id': job}
        detail = {**envelope, 'status': 'complete', 'action': action, 'event': event,
                  'records': [copy.deepcopy(event)], 'evidence_validation': 'validated', 'scope': 'event_indicator_records',
                  'pagination': {'offset': 0, 'limit': 4, 'total': 1, 'next_offset': None}}
        values = {'summary': summary, 'overview': overview, 'actions': actions, 'action-detail': detail}
        for name, value in values.items():
            self.save(root, name, value)
        return job, digest, values

    @staticmethod
    def save(root, name, value):
        (root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def test_saved_http200_responses_must_pass_real_frontend_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, digest, values = self.fixture(root)
            for stage in ('summary', 'report', 'detail'):
                result = validate_delivery(stage, root, job, digest)
                self.assertEqual(result['status'], 'validated')
            self.assertEqual(result['pagination']['total'], 1)
            # A successful HTTP response alone cannot establish a usable report.
            values['overview']['status'] = 'unavailable'
            self.save(root, 'overview', values['overview'])
            with self.assertRaises(ValueError):
                validate_delivery('report', root, job, digest)

    def test_wrong_source_revision_and_inconsistent_pagination_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, digest, values = self.fixture(root)
            bad = copy.deepcopy(values['actions'])
            bad['source']['evidence_revision'] = 'd' * 64
            self.save(root, 'actions', bad)
            with self.assertRaises(ValueError):
                validate_delivery('report', root, job, digest)
            bad = copy.deepcopy(values['actions'])
            bad['pagination']['total'] = 2
            self.save(root, 'actions', bad)
            with self.assertRaises(ValueError):
                validate_delivery('report', root, job, digest)

    def test_detail_binding_and_unknown_nested_proofs_are_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, digest, values = self.fixture(root)
            for defect in ('revision', 'nested_proof'):
                bad = copy.deepcopy(values['action-detail'])
                if defect == 'revision':
                    bad['source']['evidence_revision'] = 'd' * 64
                else:
                    bad['records'][0]['unknown'] = model_window_fixture()
                    bad['records'][0]['unknown']['source_window']['source_bindings'][0]['pts'] += 1000
                self.save(root, 'action-detail', bad)
                with self.assertRaises(ValueError):
                    validate_delivery('detail', root, job, digest)

    def test_summary_rejects_another_uploaded_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, _, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                validate_delivery('summary', root, job, 'f' * 64)


if __name__ == '__main__':
    unittest.main()
