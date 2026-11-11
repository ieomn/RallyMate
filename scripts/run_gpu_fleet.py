"""Review or launch a local fleet. This never launches an API or exposes a port."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from rallymate_service.database import JobDatabase
from rallymate_service.gpu_fleet import (
    MANAGED_INFERENCE_PROFILE, ManagedWorkerProcess, discover_gpus, fleet_plan,
    new_managed_worker_id, stop_managed_workers, worker_environment,
)
from rallymate_vision.owned_subprocess import OwnedProcess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpus', required=True, help='Comma-separated full physical GPU UUIDs')
    parser.add_argument('--data-root', type=Path, default=ROOT / 'service_data')
    parser.add_argument('--worker-python', type=Path, default=ROOT / 'runtime/rtmpose/.venv/Scripts/python.exe')
    parser.add_argument('--sam-python', type=Path, default=ROOT / 'runtime/sam3d/.venv311/Scripts/python.exe')
    parser.add_argument('--detector-mib', type=int, default=1024)
    parser.add_argument('--sam-mib', type=int, default=11264)
    parser.add_argument('--reserve-mib', type=int, default=2048)
    parser.add_argument('--cpu-threads', type=int, default=2)
    parser.add_argument('--launch', action='store_true', help='Actually launch; otherwise print a read-only plan')
    args = parser.parse_args()
    plans = fleet_plan([value.strip() for value in args.gpus.split(',')], discover_gpus(),
        detector_mib=args.detector_mib, sam_mib=args.sam_mib, reserve_mib=args.reserve_mib,
        cpu_threads=args.cpu_threads)
    print(json.dumps({'mode': 'launch' if args.launch else 'plan_only', 'workers': plans,
        'data_root': str(args.data_root.resolve()), 'physical_gpus_detected': len(discover_gpus()),
        'memory_scope': 'separate PyTorch allocator caps; contexts/GL/NVENC require headroom',
        'inference_profile': MANAGED_INFERENCE_PROFILE,
        'mesh_scheduling': 'disabled_pending_independent_device_routing_qualification',
        'shutdown_recovery': 'exact owner/attempt only after every owned tree is confirmed stopped; parent crash requires explicit repair',
        'qualification': 'single-node scheduling; multi-card throughput requires real hardware validation'}, indent=2), flush=True)
    if not args.launch:
        return
    checkpoint = ROOT / 'runtime/sam3d/checkpoints/sam-3d-body-dinov3'
    for path in (args.worker_python, args.sam_python, checkpoint / 'model.ckpt',
                 checkpoint / 'model_config.yaml', ROOT / 'models/yolo26n.pt'):
        if not path.is_file():
            raise FileNotFoundError(path)
    base = dict(os.environ)
    base.pop('RALLYMATE_POSE_PRESET', None)
    base.update(PYTHONPATH=os.pathsep.join(filter(None, (str(ROOT / 'src'), base.get('PYTHONPATH')))),
        RALLYMATE_DATA_ROOT=str(args.data_root.resolve()),
        RALLYMATE_DATABASE_PATH=str(args.data_root.resolve() / 'jobs.sqlite3'),
        RALLYMATE_DETECT_MODEL=str(ROOT / 'models/yolo26n.pt'),
        RALLYMATE_POSE_MODEL=str(checkpoint / 'model.ckpt'),
        RALLYMATE_POSE_CONFIG=str(checkpoint / 'model_config.yaml'),
        RALLYMATE_POSE_BACKEND='sam3d', RALLYMATE_POSE_RUNTIME='pytorch',
        RALLYMATE_POSE_PROFILE='analysis', RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT='mhr70',
        RALLYMATE_SAM3D_MODE='body', RALLYMATE_SAM3D_RUNTIME=str(ROOT / 'runtime/sam3d'),
        RALLYMATE_SAM3D_PYTHON=str(args.sam_python.resolve()))
    # worker_environment forcibly resets batch/graph/archive experiment values
    # to the default profile; this does not inherit shell batch qualifications.
    defaults = {'RALLYMATE_SAM3D_IPC_TRANSPORT': 'shared_memory',
        'RALLYMATE_SAM3D_GPU_PREPROCESS': '1', 'RALLYMATE_SAM3D_FORWARD_GRAPH': 'backbone_decoder_layers',
        'RALLYMATE_SAM3D_PREFETCH_FRAMES': '2', 'RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH': '1',
        'RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER': '1', 'RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE': '1',
        'RALLYMATE_SAM3D_CPU_THREADS': '1', 'RALLYMATE_SAM3D_WAIT_MODE': 'passive'}
    for key, value in defaults.items():
        base.setdefault(key, value)
    max_attempts = int(base.get('RALLYMATE_MAX_ATTEMPTS', '2'))
    if max_attempts < 1:
        raise ValueError('RALLYMATE_MAX_ATTEMPTS must be positive')
    database = JobDatabase(args.data_root.resolve() / 'jobs.sqlite3')
    database.initialize()
    children, logs = [], []
    log_root = args.data_root.resolve() / 'gpu-worker-logs'
    log_root.mkdir(parents=True, exist_ok=True)
    try:
        for plan in plans:
            worker_id = new_managed_worker_id()
            log_path = log_root / f"{plan['gpu_uuid']}-{worker_id}.log"
            log = log_path.open('x', encoding='utf-8')
            logs.append(log)
            environment = worker_environment(plan, base)
            environment['RALLYMATE_FLEET_WORKER_ID'] = worker_id
            child = OwnedProcess([str(args.worker_python.resolve()), '-u', '-m',
                                  'rallymate_service.gpu_fleet_worker'],
                cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
            children.append(ManagedWorkerProcess(worker_id, plan['gpu_uuid'], child))
            print(json.dumps({'event': 'worker_launched', 'worker_id': worker_id,
                'gpu_uuid': plan['gpu_uuid'], 'log_path': str(log_path)}), flush=True)
        while True:
            for child in children:
                if child.owner.poll() is not None:
                    raise RuntimeError(f'A managed worker exited ({child.owner.returncode}); inspect its log before restarting')
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            receipts = stop_managed_workers(list(reversed(children)), database, max_attempts=max_attempts)
            print(json.dumps({'event': 'fleet_stopped', 'workers': receipts}), flush=True)
        finally:
            for log in logs:
                log.close()


if __name__ == '__main__':
    main()
