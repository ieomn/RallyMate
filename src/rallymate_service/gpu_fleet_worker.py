"""Child entry point; parent sets UUID visibility before Python imports Torch."""
from __future__ import annotations

import json
import logging
import os
import signal
from threading import Event

from rallymate_service.gpu_fleet import (
    cuda_property_uuid, discover_gpus, require_managed_worker_id, validate_worker_environment,
)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    identifier = os.environ['RALLYMATE_MANAGED_GPU_UUID']
    worker_id = require_managed_worker_id(os.environ.get('RALLYMATE_FLEET_WORKER_ID'))
    plan = validate_worker_environment(dict(os.environ), discover_gpus())
    # Import after the visibility/environment checks and before model load.
    import torch
    if torch.cuda.device_count() != 1:
        raise RuntimeError('Managed worker must see exactly one CUDA device')
    properties = torch.cuda.get_device_properties(0)
    observed = cuda_property_uuid(getattr(properties, 'uuid', ''))
    if observed.lower() != identifier.lower():
        raise RuntimeError('CUDA logical device does not match the requested physical GPU UUID')
    from rallymate_service.config import load_settings
    from rallymate_service.database import JobDatabase
    from rallymate_service.worker import run_worker
    settings = load_settings()
    if settings.pose_backend != 'sam3d':
        raise ValueError('This fleet profile is validated for the SAM3D worker only')
    print(json.dumps({'event': 'managed_worker_start', 'worker_id': worker_id, **plan,
                      'mesh_scheduling': 'disabled_pending_independent_device_routing_qualification'}), flush=True)
    stop = Event()
    for name in ('SIGINT', 'SIGTERM'):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    # The shared run_worker entry point owns the physical slot for both managed
    # and development workers, including descendants and cleanup.
    run_worker(settings, JobDatabase(settings.database_path), poll_seconds=.5, stop_event=stop, worker_id=worker_id,
               on_state=lambda state, error: print(json.dumps({
                   'event': 'worker_state', 'gpu_uuid': identifier, 'worker_id': worker_id,
                   'state': state, 'error': error}), flush=True))


if __name__ == '__main__':
    main()
