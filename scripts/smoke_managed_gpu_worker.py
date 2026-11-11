"""Prepare, or explicitly run, one bounded managed GPU worker queue smoke.

No API/listener is started. Only a newly created diagnostic data root is used.
The real local source is read in place; there is no upload or production DB write.
Run only after other GPU work has stopped. A successful launcher is not readiness.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import functools
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON_BYTES = 32 * 1024**2
MAX_LOG_BYTES = 16 * 1024**2
SOURCE_JOB = 'afd950a6-0967-49dc-8e74-fd1b4c76a9e6'


def read_json(path, *, maximum=MAX_JSON_BYTES):
    path = Path(path)
    if path.stat().st_size > maximum:
        raise ValueError(f'Diagnostic JSON exceeded its read bound: {path.name}')
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    if len(text.encode('utf-8')) > MAX_JSON_BYTES:
        raise ValueError('Diagnostic receipt exceeded its write bound')
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(text)
    temporary.replace(path)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()


def validate_allocator(receipt, identifier, limit_mib):
    if not isinstance(receipt, dict):
        raise ValueError('Missing observed allocator receipt')
    if (receipt.get('logical_device') != 'cuda:0'
            or str(receipt.get('physical_gpu_uuid')).lower() != identifier.lower()
            or receipt.get('allocator_limit_mib') != limit_mib
            or receipt.get('scope') != 'pytorch_caching_allocator_in_this_process'):
        raise ValueError('Observed allocator identity or limit did not match the smoke plan')
    total = receipt.get('total_memory_bytes')
    if type(total) is not int or total <= limit_mib * 1024**2:
        raise ValueError('Invalid observed physical memory total')
    expected = limit_mib * 1024**2 / total
    for key in ('requested_fraction', 'observed_fraction'):
        value = receipt.get(key)
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or not math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-9)):
            raise ValueError('Observed allocator fraction did not match the cap')


def runtime_receipt(runner, identity, phase):
    """Called in the real worker, after model initialization / first prediction."""
    import torch
    from rallymate_service.gpu_fleet import MANAGED_INFERENCE_PROFILE, cuda_property_uuid
    identifier = identity['gpu_uuid']
    observed_uuid = cuda_property_uuid(torch.cuda.get_device_properties(0).uuid)
    if (torch.cuda.device_count() != 1 or observed_uuid.lower() != identifier.lower()
            or os.environ.get('CUDA_VISIBLE_DEVICES') != identifier
            or str(runner.perception.device) != 'cuda:0'):
        raise RuntimeError('Worker CUDA visibility changed during model startup or inference')
    detector = dict(runner.cuda_memory_budget)
    detector['observed_fraction'] = torch.cuda.get_per_process_memory_fraction(device='cuda:0')
    sam_metadata = runner.perception.pose_backend.engine.metadata
    sam = dict(sam_metadata.get('cuda_memory_budget', {}))
    validate_allocator(detector, identifier, identity['detector_allocator_mib'])
    validate_allocator(sam, identifier, identity['sam_allocator_mib'])
    profile = {key: os.environ.get(key) for key in MANAGED_INFERENCE_PROFILE}
    if profile != MANAGED_INFERENCE_PROFILE:
        raise RuntimeError('Managed inference profile changed during smoke')
    detector_model_device = str(next(runner.perception.detect_model.model.parameters()).device)
    if phase == 'after_queue_job' and detector_model_device != 'cuda:0':
        raise RuntimeError('Detector did not materialize on logical CUDA 0 at first prediction')
    return {**identity, 'phase': phase, 'observed_gpu_uuid': observed_uuid,
        'visible_device_count': torch.cuda.device_count(), 'cuda_visible_devices': os.environ['CUDA_VISIBLE_DEVICES'],
        'detector_allocator': detector, 'sam_allocator': sam,
        'sam_readback_phase': 'isolated_process_before_model_load_verified_before_inference',
        'detector_model_device': detector_model_device,
        'sam_model_sha256': sam_metadata['model_sha256'], 'sam_load_seconds': sam_metadata['load_seconds'],
        'profile': profile, 'cpu_threads_after_model_load': runner.cpu_thread_configuration_after_model_load,
        'detector_memory_allocated_bytes': torch.cuda.memory_allocated(0),
        'detector_memory_reserved_bytes': torch.cuda.memory_reserved(0),
        'detector_peak_memory_allocated_bytes': torch.cuda.max_memory_allocated(0)}


def child_main(identity_path):
    """Instrument the production wrapper; its normal claim/lease/runner path stays intact."""
    identity = read_json(identity_path, maximum=65536)
    if (os.environ.get('RALLYMATE_FLEET_WORKER_ID') != identity['worker_id']
            or Path(os.environ['RALLYMATE_DATA_ROOT']).resolve() != Path(identity['data_root']).resolve()):
        raise RuntimeError('Smoke child identity does not match the supervisor')
    from rallymate_service import worker
    from rallymate_service.gpu_fleet_worker import main as managed_main
    original_runner, original_run_worker = worker.PersistentVisionRunner, worker.run_worker
    holder = {}

    class ObservedRunner(original_runner):
        def __init__(self, settings):
            super().__init__(settings)
            holder['runner'] = self

        @functools.wraps(original_runner.__call__)
        def __call__(self, request, *args, **kwargs):
            if (request.job_id != identity['job_id'] or request.processing.max_frames != identity['frames']
                    or request.processing.start_ms != 0 or request.processing.frame_stride != 1
                    or request.output_dir.resolve() != Path(identity['job_output']).resolve()
                    or request.video_path.resolve() != Path(identity['source_video']).resolve()):
                raise RuntimeError('Smoke refuses a different job, source, output or processing bound')
            result = super().__call__(request, *args, **kwargs)
            write_json(Path(identity_path).parent / 'runtime-after-job.json',
                       runtime_receipt(self, identity, 'after_queue_job'))
            return result

    @functools.wraps(original_run_worker)
    def observed_worker(*args, **kwargs):
        original_callback = kwargs.get('on_state')

        def observe(state, error):
            if state == 'ready':
                write_json(Path(identity_path).parent / 'runtime-ready.json',
                           runtime_receipt(holder['runner'], identity, 'models_initialized'))
            if original_callback:
                original_callback(state, error)

        kwargs['on_state'] = observe
        return original_run_worker(*args, **kwargs)

    worker.PersistentVisionRunner, worker.run_worker = ObservedRunner, observed_worker
    managed_main()


def make_environment(plan, output, sam_python):
    from rallymate_service.gpu_fleet import worker_environment
    # No inherited batch experiment, alternate DB, model preset or upload path.
    base = {key: value for key, value in os.environ.items()
            if not key.startswith(('RALLYMATE_', 'SAM3D_'))}
    checkpoint = ROOT / 'runtime/sam3d/checkpoints/sam-3d-body-dinov3'
    base.update(PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1',
        RALLYMATE_DATA_ROOT=str(output / 'data'), RALLYMATE_DATABASE_PATH=str(output / 'data/jobs.sqlite3'),
        RALLYMATE_DETECT_MODEL=str(ROOT / 'models/yolo26n.pt'),
        RALLYMATE_POSE_MODEL=str(checkpoint / 'model.ckpt'), RALLYMATE_POSE_CONFIG=str(checkpoint / 'model_config.yaml'),
        RALLYMATE_POSE_BACKEND='sam3d', RALLYMATE_POSE_RUNTIME='pytorch', RALLYMATE_POSE_PROFILE='analysis',
        RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT='mhr70', RALLYMATE_SAM3D_MODE='body',
        RALLYMATE_SAM3D_RUNTIME=str(ROOT / 'runtime/sam3d'), RALLYMATE_SAM3D_PYTHON=str(sam_python),
        RALLYMATE_SAM3D_LOG_DIR=str(output / 'sam-logs'), RALLYMATE_SAM3D_IPC_DIR=str(output / 'ipc'),
        RALLYMATE_SAM3D_IPC_TRANSPORT='shared_memory', RALLYMATE_SAM3D_GPU_PREPROCESS='1',
        RALLYMATE_SAM3D_FORWARD_GRAPH='backbone_decoder_layers', RALLYMATE_SAM3D_PREFETCH_FRAMES='2',
        RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH='1', RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER='1',
        RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE='0', RALLYMATE_SAM3D_EXPORT_MESH='0',
        RALLYMATE_SAM3D_WAIT_MODE='passive', RALLYMATE_ACCEPT_NEW_JOBS='0',
        RALLYMATE_MAX_ACTIVE_JOBS='1', RALLYMATE_MAX_ATTEMPTS='1',
        RALLYMATE_MODEL_LICENSE_ACK=os.environ.get('RALLYMATE_MODEL_LICENSE_ACK', 'development'),
        SAM3D_DINOV3_REPO=str(ROOT / 'runtime/sam3d/dinov3'),
        YOLO_CONFIG_DIR=str(output / 'yolo-config'), MPLCONFIGDIR=str(output / 'mpl-config'),
        TEMP=str(output / 'temp'), TMP=str(output / 'temp'))
    return worker_environment(plan, base)


def build_request(source_request, identity, environment):
    """Use only source identity; never replay the original output/model paths."""
    source = read_json(source_request, maximum=1024**2)
    video = Path(source['source']['video_path'])
    if not video.is_absolute():
        video = source_request.parent / video
    video = video.resolve(strict=True)
    if not video.is_file():
        raise ValueError('Source must be an existing local video file')
    identity['source_video'] = str(video)
    return {'schema_version': '1.0.0', 'job_id': identity['job_id'],
        'source': {'video_path': str(video)}, 'output': {'directory': identity['job_output']},
        'models': {'detect': environment['RALLYMATE_DETECT_MODEL'], 'pose': environment['RALLYMATE_POSE_MODEL'],
            'pose_config': environment['RALLYMATE_POSE_CONFIG'], 'pose_backend': 'sam3d',
            'pose_runtime': 'pytorch', 'pose_profile': 'analysis', 'pose_native_keypoint_format': 'mhr70',
            'device': '0', 'detect_imgsz': 960, 'pose_imgsz': 640,
            'detect_confidence': .15, 'pose_confidence': .25},
        'processing': {'start_ms': 0, 'frame_stride': 1, 'max_frames': identity['frames'],
            'max_players': 2, 'write_annotated_video': False}, 'court': {'mode': 'disabled'},
        'upstream_metadata': {'diagnostic': 'managed_single_gpu_queue_smoke',
            'source_request_sha256': sha256_file(source_request), 'source_job_id': source.get('job_id')}}


def verify_identity(receipt, identity, *, phase):
    from rallymate_service.gpu_fleet import MANAGED_INFERENCE_PROFILE
    for field, expected in identity.items():
        if receipt.get(field) != expected:
            raise ValueError(f'Smoke runtime receipt identity mismatch: {field}')
    if (receipt.get('phase') != phase or type(receipt.get('visible_device_count')) is not int
            or receipt['visible_device_count'] != 1
            or str(receipt.get('observed_gpu_uuid')).lower() != identity['gpu_uuid'].lower()
            or receipt.get('cuda_visible_devices') != identity['gpu_uuid']
            or receipt.get('profile') != MANAGED_INFERENCE_PROFILE
            or phase == 'after_queue_job' and receipt.get('detector_model_device') != 'cuda:0'):
        raise ValueError('Smoke runtime phase, observed mapping or profile mismatch')
    validate_allocator(receipt.get('detector_allocator'), identity['gpu_uuid'], identity['detector_allocator_mib'])
    validate_allocator(receipt.get('sam_allocator'), identity['gpu_uuid'], identity['sam_allocator_mib'])


def read_jsonl(path, *, max_rows):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('Smoke artifact exceeded the bounded diagnostic reader')
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                if len(rows) >= max_rows:
                    raise ValueError('Smoke artifact exceeded its frame/subject count')
                rows.append(json.loads(line))
    return rows


def verify_job_artifacts(job, identity, source_sha):
    summary = job['summary']
    if summary['processing']['processed_frames'] != identity['frames']:
        raise ValueError('Queue smoke did not complete the exact continuous frame bound')
    if (summary['processing']['sam_pose_batch']['requested_batch_frames'] != 1
            or summary['processing']['sam_pose_batch']['peak_frames'] != 1
            or summary['processing']['detection_batch']['requested_batch_size'] != 1
            or summary['processing']['detection_batch']['peak_batch_frames'] != 1):
        raise ValueError('Queue smoke did not use SAM frame batch one')
    output = Path(identity['job_output'])
    frames = read_jsonl(output / 'frames.jsonl', max_rows=identity['frames'])
    native = read_jsonl(output / 'sam3d_frames.jsonl', max_rows=identity['frames'] * 2)
    if ([row['frame']['index'] for row in frames] != list(range(identity['frames']))
            or [row['frame']['processed_index'] for row in frames] != list(range(identity['frames']))
            or any(row['job_id'] != identity['job_id'] for row in frames)):
        raise ValueError('Queue smoke changed the continuous source frame sequence')
    frame_map = {row['frame']['processed_index']: row for row in frames}
    successful, source_frames, statuses = 0, set(), {}
    keys = ('video_sha256', 'frame_index', 'processed_index', 'pts', 'time_base_num', 'time_base_den',
            'timestamp_ms', 'person_track_id', 'selection_epoch', 'camera_reference_epoch')
    for row in native:
        source_frames.add(row['frame_index'])
        statuses[row['status']] = statuses.get(row['status'], 0) + 1
        if row['video_sha256'].lower() != source_sha or row['processed_index'] not in frame_map:
            raise ValueError('SAM native source identity does not match this queue job')
        frame = frame_map[row['processed_index']]
        timing = frame['frame']
        if row['frame_index'] != timing['index'] or timing['video_sha256'].lower() != source_sha:
            raise ValueError('Original-frame source identity changed')
        for key in ('processed_index', 'pts', 'time_base_num', 'time_base_den'):
            if row[key] != timing[key]:
                raise ValueError('SAM native frame binding changed')
        if row['timestamp_ms'] != round(Fraction(row['pts'] * row['time_base_num'] * 1000, row['time_base_den'])):
            raise ValueError('SAM native timestamp does not match exact source PTS')
        if row['status'] != 'ok':
            continue
        poses = [pose for pose in frame['poses'] if pose['person_track_id'] == row['person_track_id']]
        if len(poses) != 1 or any(poses[0]['source_binding'].get(key) != row.get(key) for key in keys):
            raise ValueError('SAM native identity/PTS/epoch does not match the original-frame pose')
        if len(row['keypoints_3d']) != 70 or poses[0]['keypoint_format'] != 'mhr70':
            raise ValueError('Native SAM result was not retained')
        successful += 1
    if source_frames != set(range(identity['frames'])) or successful == 0:
        raise ValueError('Smoke requires every source frame and at least one successful native SAM subject')
    return {'processed_frames': len(frames), 'successful_native_subjects': successful,
        'native_status_counts': statuses, 'source_video_sha256': source_sha,
        'frames_sha256': sha256_file(output / 'frames.jsonl'),
        'native_sha256': sha256_file(output / 'sam3d_frames.jsonl'),
        'sam_peak_memory_allocated_bytes': max((row.get('peak_memory_bytes', 0) for row in native), default=0)}


def supervise(args, plan, output):
    from rallymate_service.database import JobDatabase
    from rallymate_service.gpu_fleet import ManagedWorkerProcess, new_managed_worker_id, stop_managed_workers
    from rallymate_vision.owned_subprocess import OwnedProcess
    if os.name != 'nt':
        raise ValueError('This smoke currently requires Windows Job Object whole-tree confirmation')
    if output.exists() or not output.is_relative_to((ROOT / '.codex_tmp').resolve()):
        raise ValueError('Choose a new, absent output below this workspace .codex_tmp')
    environment = make_environment(plan, output, args.sam_python.resolve())
    identity = {'gpu_uuid': plan['gpu_uuid'], 'worker_id': new_managed_worker_id(),
        'job_id': str(uuid.uuid4()), 'frames': args.frames, 'nonce': uuid.uuid4().hex,
        'data_root': str(output / 'data'),
        'detector_allocator_mib': plan['detector_allocator_mib'],
        'sam_allocator_mib': plan['sam_allocator_mib'],
        'physical_headroom_mib': plan['physical_headroom_mib']}
    identity['job_output'] = str(output / 'data/runs' / identity['job_id'])
    request = build_request(args.source_request.resolve(), identity, environment)
    for path in (args.worker_python, args.sam_python, Path(environment['RALLYMATE_DETECT_MODEL']),
                 Path(environment['RALLYMATE_POSE_MODEL']), Path(environment['RALLYMATE_POSE_CONFIG'])):
        if not path.is_file():
            raise FileNotFoundError(path)
    source_before = Path(identity['source_video']).stat()
    source_sha = sha256_file(identity['source_video'])
    output.mkdir(parents=True, exist_ok=False)
    for name in ('temp', 'data/requests', 'data/uploads', 'data/runs'):
        (output / name).mkdir(parents=True, exist_ok=False)
    write_json(output / 'identity.json', identity)
    write_json(output / 'plan.json', {'plan': plan, 'identity': identity, 'request': request,
        'no_ports': True, 'mesh_export_and_rendering': 'excluded', 'source_video_sha256': source_sha})
    database = JobDatabase(output / 'data/jobs.sqlite3')
    database.initialize()
    if database.count_by_status():
        raise ValueError('Smoke database must be empty before worker startup')
    children, failure, recovery = [], None, None
    result = {'status': 'started', 'identity': identity, 'source_video_sha256': source_sha,
        'scope': 'single_physical_gpu_managed_queue_smoke_not_throughput_or_accuracy_qualification'}
    started = time.monotonic()
    log_path = output / 'worker.log'
    with log_path.open('x', encoding='utf-8') as log:
        try:
            environment['RALLYMATE_FLEET_WORKER_ID'] = identity['worker_id']
            owner = OwnedProcess([str(args.worker_python.resolve()), '-u', str(Path(__file__).resolve()),
                '--child', str(output / 'identity.json')], cwd=ROOT, env=environment,
                stdout=log, stderr=subprocess.STDOUT)
            children.append(ManagedWorkerProcess(identity['worker_id'], plan['gpu_uuid'], owner))
            submitted = False
            while True:
                if time.monotonic() - started > args.timeout_seconds:
                    raise TimeoutError('Bounded managed queue smoke timed out')
                if owner.poll() is not None:
                    raise RuntimeError('Managed worker exited; inspect isolated worker.log')
                if log_path.stat().st_size > MAX_LOG_BYTES:
                    raise ValueError('Managed worker log exceeded diagnostic bound')
                ready_path = output / 'runtime-ready.json'
                if not submitted and ready_path.is_file():
                    ready = read_json(ready_path)
                    verify_identity(ready, identity, phase='models_initialized')
                    if database.count_by_status() or any((output / 'data/uploads').iterdir()):
                        raise RuntimeError('Isolated smoke root was changed before admission')
                    result['model_ready_seconds'] = time.monotonic() - started
                    request_path = output / 'data/requests' / (identity['job_id'] + '.json')
                    write_json(request_path, request)
                    database.create_job(identity['job_id'], Path(identity['source_video']).name,
                        Path(identity['source_video']), request_path, Path(identity['job_output']), max_active_jobs=1)
                    submitted = True
                    print(json.dumps({'event': 'smoke_ready_and_job_submitted', **identity}), flush=True)
                if submitted:
                    job = database.get_job(identity['job_id'])
                    if job['status'] in ('failed', 'cancelled'):
                        raise RuntimeError(f"Isolated queue job {job['status']}: {job.get('error')}")
                    if job['status'] == 'succeeded':
                        if job['worker_id'] != identity['worker_id'] or job['attempts'] != 1:
                            raise ValueError('Queue job was not owned by this exact managed worker attempt')
                        after = read_json(output / 'runtime-after-job.json')
                        verify_identity(after, identity, phase='after_queue_job')
                        result['artifacts'] = verify_job_artifacts(job, identity, source_sha)
                        result['job_succeeded_seconds'] = time.monotonic() - started
                        break
                time.sleep(.2)
        except BaseException as exc:
            failure = exc
            result['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            try:
                # This helper owns close+confirmation. Never close separately or
                # clear a marker based on a stale PID / launcher return code.
                recovery = stop_managed_workers(children, database, max_attempts=1)
                result['shutdown'] = recovery
            except BaseException as exc:
                result['cleanup_error'] = f'{type(exc).__name__}: {exc}'
                failure = exc
    result['elapsed_seconds'] = time.monotonic() - started
    if recovery is not None:
        try:
            result['queue_after_shutdown'] = database.count_by_status()
            result['uploads_empty'] = not any((output / 'data/uploads').iterdir())
            source_after = Path(identity['source_video']).stat()
            result['source_file_stat_unchanged'] = (source_before.st_size, source_before.st_mtime_ns) == (
                source_after.st_size, source_after.st_mtime_ns)
            if not result['uploads_empty'] or not result['source_file_stat_unchanged']:
                raise RuntimeError('Unexpected upload or source-file change during isolated smoke')
            if failure is None and (result['queue_after_shutdown'] != {'succeeded': 1}
                    or len(recovery) != 1 or recovery[0]['tree_exit_confirmed'] is not True
                    or recovery[0]['recovered_attempts']):
                raise RuntimeError('Passed smoke requires exactly one completed job and its confirmed stopped tree')
        except BaseException as exc:
            failure = exc
            result['post_shutdown_error'] = f'{type(exc).__name__}: {exc}'
    result['status'] = 'passed' if failure is None else 'failed'
    write_json(output / 'receipt.json', result)
    print(json.dumps({'event': 'managed_gpu_smoke_finished', 'status': result['status'],
        'receipt': str(output / 'receipt.json')}), flush=True)
    if failure:
        raise failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu', help='Full physical UUID from current inventory; omit only when exactly one GPU is present')
    parser.add_argument('--source-request', type=Path, default=ROOT / f'service_data/requests/{SOURCE_JOB}.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.codex_tmp' /
        ('managed-gpu-smoke-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]))
    parser.add_argument('--worker-python', type=Path, default=ROOT / 'runtime/rtmpose/.venv/Scripts/python.exe')
    parser.add_argument('--sam-python', type=Path, default=ROOT / 'runtime/sam3d/.venv311/Scripts/python.exe')
    parser.add_argument('--detector-mib', type=int, default=1024, help='Detector PyTorch allocator cap in MiB')
    parser.add_argument('--sam-mib', type=int, default=11264, help='SAM PyTorch allocator cap in MiB')
    parser.add_argument('--reserve-mib', type=int, default=2048, help='Physical free-memory reserve required at startup in MiB')
    parser.add_argument('--frames', type=int, choices=(8, 16), default=16)
    parser.add_argument('--timeout-seconds', type=int, default=900)
    parser.add_argument('--launch', action='store_true', help='Actually load models and run one isolated bounded queue job')
    parser.add_argument('--child', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child_main(args.child)
        return
    if not 60 <= args.timeout_seconds <= 1800:
        parser.error('timeout-seconds must be between 60 and 1800')
    from rallymate_service.gpu_fleet import discover_gpus, fleet_plan
    inventory = discover_gpus()
    if args.gpu is None and len(inventory) != 1:
        parser.error('More than one physical GPU is present; specify exactly one full UUID')
    identifier = args.gpu or inventory[0].uuid
    plan = fleet_plan([identifier], inventory, detector_mib=args.detector_mib, sam_mib=args.sam_mib,
                      reserve_mib=args.reserve_mib, cpu_threads=2)[0]
    output = args.output.resolve()
    print(json.dumps({'mode': 'launch' if args.launch else 'plan_only_no_models_loaded',
        'inventory': [asdict(gpu) for gpu in inventory], 'plan': plan, 'output': str(output),
        'frames': args.frames, 'maximum_wall_seconds': args.timeout_seconds,
        'mesh': 'excluded', 'ports_opened': [], 'source_request': str(args.source_request.resolve())}, indent=2), flush=True)
    if args.launch:
        supervise(args, plan, output)


if __name__ == '__main__':
    main()
