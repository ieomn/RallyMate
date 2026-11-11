"""Measure a full-video upload through the browser gateway to usable results.

Does not cancel work or restart services. Reusing an output directory resumes the
same upload/job; retries never create another inference. Responses and timestamps
are retained so upload, queue, analysis, report and independent mesh remain distinct.
With --existing-job-id and both UTC upload timestamps, only monitor the browser's
existing task. This mode cannot upload, complete an upload or submit inference.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def existing_monitor(job_id, started_at, completed_at):
    """An explicit existing-job run cannot enter the upload/submission branch."""
    if not any((job_id, started_at, completed_at)):
        return None
    if not all((job_id, started_at, completed_at)):
        raise ValueError('Existing job monitoring requires job UUID and both UTC upload timestamps')
    if str(uuid.UUID(job_id)) != job_id.lower():
        raise ValueError('Existing job ID must be a canonical UUID')
    stamps = []
    for value in (started_at, completed_at):
        if not value.endswith(('Z', '+00:00')):
            raise ValueError('Upload timestamps must explicitly use UTC')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError('Upload timestamps must explicitly use UTC')
        stamps.append(stamp)
    if stamps[1] < stamps[0]:
        raise ValueError('Upload completion precedes upload start')
    return {'job_id': job_id.lower(), 'upload_started_at': stamps[0].isoformat(),
            'upload_complete_at': stamps[1].isoformat(),
            'upload_complete_seconds_from_upload': (stamps[1] - stamps[0]).total_seconds()}


def source_video_metadata(source):
    # Metadata inspection only: no frame decoding, model load or GPU work.
    import cv2
    video = cv2.VideoCapture(str(source))
    try:
        if not video.isOpened():
            raise ValueError('Source video metadata is unavailable')
        count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        if count <= 0:
            raise ValueError('Source video frame count is unavailable')
        return {'frame_count': count, 'width': int(video.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'height': int(video.get(cv2.CAP_PROP_FRAME_HEIGHT)), 'fps': video.get(cv2.CAP_PROP_FPS),
                'method': 'opencv_container_metadata_no_decode'}
    finally:
        video.release()


def validate_full_video(summary, metadata):
    count = metadata['frame_count']
    if (summary.get('video', {}).get('frame_count') != count
            or summary.get('processing', {}).get('processed_frames') != count):
        raise ValueError('Report does not cover every source video frame')
    return {'source_frame_count': count, 'reported_frame_count': count, 'processed_frames': count}


def fetch_http(opener, base, path, *, method='GET', data=None, headers=None,
               limit=8*1024*1024, raw=False, clock=time.monotonic, wait=time.sleep,
               observe=None, preparation_seconds=210):
    """Poll explicit preparation/busy responses, preserving every attempt.

    A real evidence/resource error is terminal. Only the same GET is repeated;
    this helper never creates another job or rewrites a request method.
    """
    header = {'Origin': base, **(headers or {})}
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        header['Content-Type'] = 'application/json'
    match = re.fullmatch(r'/v1/jobs/([^/]+)/report-(?:summary|overview|actions(?:/[^/]+)?|motion/[^/]+)', urlsplit(path).path)
    segmented = method == 'GET' and match is not None
    if segmented and not urlsplit(path).path.endswith('/report-summary'):
        parts = urlsplit(path)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'report_delivery']
        path = urlunsplit(parts._replace(query=urlencode([*query, ('report_delivery', 'deferred-v1')])))
    started = clock()
    attempts, transient_errors = [], 0
    while True:
        if segmented and clock() - started >= preparation_seconds:
            raise TimeoutError('Report preparation wait expired; existing job was left untouched')
        begin = clock()
        try:
            with opener.open(Request(base + path, method=method, data=data, headers=header),
                             timeout=min(90, max(1, preparation_seconds - (clock() - started))) if segmented else 90) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('Response exceeds acceptance budget: ' + path)
                value = body if raw else json.loads(body)
                if segmented and clock() - started >= preparation_seconds:
                    raise TimeoutError('Report response exceeded its total read budget')
                timing = {'path': path, 'method': method, 'status': response.status,
                          'bytes': len(body), 'seconds': clock()-begin,
                          'observed_at': now(), 'content_range': response.headers.get('Content-Range')}
                attempts.append(timing.copy())
                if observe:
                    observe(timing)
                if segmented and response.status == 202:
                    if (not isinstance(value, dict) or value.get('schema_version') != 'segmented-report-v1'
                            or value.get('job_id') != match.group(1) or value.get('status') != 'preparing'
                            or not isinstance(value.get('delivery'), dict) or value['delivery'].get('state') != 'preparing'
                            or type(value.get('retry_after_ms')) is not int or value['retry_after_ms'] < 0):
                        raise ValueError('Preparation response does not match the requested job')
                    delay = value['retry_after_ms'] / 1000
                    retry_header = response.headers.get('Retry-After')
                    if retry_header and re.fullmatch(r'\d+(?:\.\d+)?', retry_header):
                        delay = float(retry_header)
                else:
                    if segmented and response.status != 200:
                        raise ValueError('Segmented report did not return a complete HTTP 200 response')
                    timing['attempts'] = attempts
                    timing['total_read_seconds'] = clock() - started
                    return value, timing
        except (HTTPError, URLError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, HTTPError) else None
            detail, retry_header = None, None
            if isinstance(exc, HTTPError):
                retry_header = exc.headers.get('Retry-After')
                body = exc.read(65537)
                try:
                    payload = json.loads(body) if len(body) <= 65536 else None
                    detail = payload.get('detail') if isinstance(payload, dict) else None
                except ValueError:
                    pass
                # Keep the body readable for the precise 413 detail-page fallback.
                replacement = HTTPError(exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(body))
                exc.close()
                exc = replacement
            timing = {'path': path, 'method': method, 'status': code,
                      'seconds': clock()-begin, 'observed_at': now(), 'error': str(exc), 'detail': detail}
            attempts.append(timing)
            if observe:
                observe(timing)
            busy = segmented and code in (429, 503) and isinstance(detail, dict) and detail.get('code') == 'report_segments_busy'
            if not busy:
                transient_errors += 1
                retryable = code is None or code in ((502, 504) if segmented else (429, 502, 503, 504))
                if not retryable or transient_errors >= 3:
                    raise exc
            delay = float(retry_header) if retry_header and re.fullmatch(r'\d+(?:\.\d+)?', retry_header) else 2
            if isinstance(exc, HTTPError):
                exc.close()
        delay = max(1, min(5, delay))
        if segmented:
            delay = min(delay, max(0, preparation_seconds - (clock() - started)))
        wait(delay)


def record_checkpoint(receipt, name, *, observed_at=None):
    """A resumed read cannot replace the first successfully observed milestone."""
    if name + '_at' in receipt:
        return False
    stamp = observed_at or now()
    seconds = (datetime.fromisoformat(stamp) - datetime.fromisoformat(receipt['upload_started_at'])).total_seconds()
    receipt[name + '_at'] = stamp
    receipt[name + '_seconds_from_upload'] = seconds
    return True


def validate_delivery(stage, directory, job_id, digest):
    """Reuse the actual frontend contracts rather than equating HTTP200 to ready."""
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('Node is required to verify the real results-screen contracts')
    script = Path(__file__).with_name('validate_benchmark_delivery.mjs')
    result = subprocess.run([node, str(script), stage, str(directory), job_id, digest],
                            capture_output=True, text=True, encoding='utf-8', timeout=90)
    if result.returncode:
        raise ValueError((result.stderr or 'Results-screen validation failed')[:1000])
    value = json.loads(result.stdout)
    if value.get('status') != 'validated' or value.get('job_id') != job_id or value.get('stage') != stage:
        raise ValueError('Unexpected results-screen validation receipt')
    return value


def fetch_action_detail(fetch, route, action_id):
    """Match getReportActionDetail: four records, or one for this precise 413."""
    path = route + '/report-actions/' + action_id + '?offset=0&limit='
    try:
        return fetch(path + '4', limit=16*1024*1024)
    except HTTPError as error:
        if error.code != 413:
            raise
        try:
            body = error.read(65537)
            detail = json.loads(body).get('detail') if len(body) <= 65536 else None
        except (ValueError, AttributeError):
            detail = None
        finally:
            error.close()
        if not isinstance(detail, dict) or detail.get('code') != 'report_segment_limit':
            raise
        value, timing = fetch(path + '1', limit=16*1024*1024)
        timing['page_size_fallback'] = {'from': 4, 'to': 1, 'status': 413, 'code': 'report_segment_limit'}
        return value, timing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--poll-seconds', type=float, default=3)
    parser.add_argument('--timeout-seconds', type=float, default=7200)
    parser.add_argument('--existing-job-id')
    parser.add_argument('--upload-started-at')
    parser.add_argument('--upload-completed-at')
    args = parser.parse_args()
    monitor = existing_monitor(args.existing_job_id, args.upload_started_at, args.upload_completed_at)
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))):
        raise ValueError('Use an explicit HTTPS origin or loopback browser gateway')
    if args.poll_seconds < 1 or args.timeout_seconds < 60:
        raise ValueError('Invalid polling/deadline')
    source = args.source.resolve(strict=True)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    record_path = out / 'receipt.json'
    opener = build_opener(ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    attempt_lock = Lock()

    def write(name, value):
        target = out / name
        temp = target.with_suffix(target.suffix + '.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(target)

    def fetch(path, *, method='GET', data=None, headers=None, limit=8*1024*1024, raw=False):
        if monitor and (method != 'GET' or '/uploads' in path):
            raise ValueError('Existing-job monitoring permits read-only requests only')
        def observe(attempt):
            with attempt_lock, (out / 'request-attempts.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(attempt, ensure_ascii=False) + '\n')
        return fetch_http(opener, base, path, method=method, data=data, headers=headers,
                          limit=limit, raw=raw, observe=observe)

    hasher = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(block)
    digest = hasher.hexdigest()
    metadata = source_video_metadata(source)
    if record_path.exists():
        receipt = json.loads(record_path.read_text(encoding='utf-8'))
        if receipt['source_sha256'] != digest or receipt['base_url'] != base:
            raise ValueError('Output directory belongs to another source/origin')
        if monitor and any(receipt.get(key) != value for key, value in monitor.items()):
            raise ValueError('Existing-job monitor identity/timestamps differ from saved receipt')
    else:
        if not monitor:
            health, _ = fetch('/health/ready', limit=65536)
            if (health['status'] != 'ready' or not health.get('inference_ready')
                    or any(health['queue'].get(key, 0) for key in ('running', 'queued'))
                    or health.get('executor', {}).get('work_kind') != 'idle'):
                raise RuntimeError('Acceptance requires a healthy idle executor; existing work was not touched')
        receipt = {'schema_version': 'upload-result-acceptance-v1', 'base_url': base,
                   'transport_scope': 'same_host_through_public_cloudflare' if parsed.scheme == 'https' else 'local_gateway',
                   'source': str(source), 'source_sha256': digest, 'source_bytes': source.stat().st_size,
                   'job_id': str(uuid.uuid4()), 'upload_started_at': now(), 'requests': [],
                   'full_video': True, 'poll_interval_seconds': args.poll_seconds,
                   'qualification': 'HTTP result readiness; browser display is separately verified'}
        if monitor:
            receipt.update(monitor, transport_scope='browser_through_public_cloudflare' if parsed.scheme == 'https' else 'browser_through_local_gateway',
                           upload_timing_source='operator_observed_browser_upload', monitor_mode='existing_job_read_only')
        write('receipt.json', receipt)
    receipt['source_metadata'] = metadata
    job_id = receipt['job_id']
    route = '/v1/jobs/' + job_id

    def retain(name, value, timing):
        write(name + '.json', value)
        receipt['requests'].append(timing)
        write('receipt.json', receipt)

    def checkpoint(name):
        record_checkpoint(receipt, name)
        write('receipt.json', receipt)
        print(json.dumps({'milestone': name, 'job_id': job_id,
            'seconds_from_upload': receipt[name + '_seconds_from_upload']}, ensure_ascii=False), flush=True)

    try:
        if 'upload_complete_at' not in receipt:
            session, timing = fetch('/v1/uploads', method='POST', data={'upload_id': job_id,
                'filename': source.name, 'size': source.stat().st_size,
                'options': {'court_mode': 'disabled', 'max_players': 2, 'write_annotated_video': True, 'start_ms': 0}})
            retain('upload-session', session, timing)
            chunk_size = session['chunk_size']
            receipt['upload_chunk_bytes'] = chunk_size

            def send(index):
                with source.open('rb') as stream:
                    stream.seek(index * chunk_size)
                    body = stream.read(chunk_size)
                _, timing = fetch(f'/v1/uploads/{job_id}/chunks/{index}', method='PUT', data=body,
                    headers={'Content-Type': 'application/octet-stream', 'X-Chunk-SHA256': hashlib.sha256(body).hexdigest(),
                             'X-Upload-Acknowledgement': 'chunk-v1'}, limit=65536)
                return timing

            with ThreadPoolExecutor(max_workers=2) as pool:
                receipt['requests'].extend(pool.map(send, range(session['chunk_count'])))
            submitted, timing = fetch(f'/v1/uploads/{job_id}/complete', method='POST')
            if submitted['id'] != job_id:
                raise ValueError('Upload/job identity differs')
            retain('submitted', submitted, timing)
            checkpoint('upload_complete')
        with (out / 'progress.jsonl').open('a', encoding='utf-8') as history:
            while True:
                status, timing = fetch(route + '/status', limit=65536)
                if status.get('id') != job_id or status.get('status') not in ('queued', 'running', 'processing', 'succeeded', 'failed', 'cancelled'):
                    raise ValueError('Status response does not describe the submitted job')
                history.write(json.dumps({'observed_at': now(), **status}, ensure_ascii=False) + '\n')
                history.flush()
                if status['status'] in ('succeeded', 'failed', 'cancelled'):
                    retain('terminal-status', status, timing)
                    if status['status'] != 'succeeded':
                        raise RuntimeError('Analysis ended: ' + status['status'])
                    checkpoint('analysis_complete_observed')
                    break
                if time.monotonic() - started > args.timeout_seconds:
                    raise TimeoutError('Acceptance wait expired; server task was left running')
                time.sleep(args.poll_seconds)
        summary, timing = fetch(route + '/report-summary', limit=256*1024)
        retain('summary', summary, timing)
        receipt['summary_validation'] = validate_delivery('summary', out, job_id, digest)
        receipt['full_video_validation'] = validate_full_video(summary, metadata)
        checkpoint('summary_ready')

        def overview():
            return fetch(route + '/report-overview', limit=2*1024*1024)

        def actions():
            return fetch(route + '/report-actions?offset=0&limit=20', limit=512*1024)

        with ThreadPoolExecutor(max_workers=2) as pool:
            overview_future, action_future = pool.submit(overview), pool.submit(actions)
            value, timing = overview_future.result()
            retain('overview', value, timing)
            action_page, timing = action_future.result()
            retain('actions', action_page, timing)
        receipt['report_validation'] = validate_delivery('report', out, job_id, digest)
        body, timing = fetch(route + '/original-video', headers={'Range': 'bytes=0-1023'}, limit=1024, raw=True)
        if timing['status'] != 206 or len(body) != 1024:
            raise ValueError('Original video cannot be played via byte ranges')
        receipt['requests'].append(timing)
        checkpoint('report_and_original_ready')
        action = receipt['report_validation']['selected_action']
        value, timing = fetch_action_detail(fetch, route, action['action_id'])
        retain('action-detail', value, timing)
        receipt['detail_validation'] = validate_delivery('detail', out, job_id, digest)
        checkpoint('selected_detail_ready')
        while True:
            mesh, timing = fetch(route + '/mesh-preview', limit=256*1024)
            if mesh['status'] == 'ready':
                retain('mesh', mesh, timing)
                for name in ('sam3d-mesh', 'sam3d-mesh-side'):
                    body, timing = fetch(route + '/artifacts/' + name + '.mp4',
                                         headers={'Range': 'bytes=0-1023'}, limit=1024, raw=True)
                    if timing['status'] != 206 or len(body) != 1024:
                        raise ValueError('Mesh replay is not readable')
                    receipt['requests'].append(timing)
                checkpoint('mesh_playable')
                break
            if mesh['status'] in ('failed', 'unavailable'):
                retain('mesh-failed', mesh, timing)
                raise RuntimeError('Mesh generation ended: ' + mesh['status'])
            if time.monotonic() - started > args.timeout_seconds:
                raise TimeoutError('Mesh wait expired; task was left untouched')
            time.sleep(args.poll_seconds)
        if 'failure' in receipt:
            receipt.setdefault('failure_history', []).append({key: receipt.pop(key) for key in ('failure_type', 'failure', 'interrupted_at') if key in receipt})
        receipt['status'] = 'passed'
        receipt['completed_at'] = now()
        write('receipt.json', receipt)
        print(json.dumps({'status': 'passed', 'job_id': job_id, 'receipt': str(record_path)}, ensure_ascii=False), flush=True)
    except Exception as exc:
        receipt.update(status='interrupted', failure_type=type(exc).__name__, failure=str(exc), interrupted_at=now())
        write('receipt.json', receipt)
        raise


if __name__ == '__main__':
    main()
