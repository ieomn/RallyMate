"""Optional per-process PyTorch allocator caps, configured before model load.

These do not cap driver contexts, OpenGL, NVENC, or allocations outside the
PyTorch allocator. A fleet must also reserve physical VRAM for those consumers.
"""
from __future__ import annotations

import math
import os


def configure_cuda_memory_budget(torch, device, environment_name: str, *, env=None):
    env = os.environ if env is None else env
    raw = env.get(environment_name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.isdecimal() or int(raw) < 256:
        raise ValueError(f'{environment_name} must be an integer >=256 MiB')
    requested = f'cuda:{device}' if str(device).isdigit() else str(device)
    if requested == 'cuda':
        requested = 'cuda:0'
    if not requested.startswith('cuda:') or not torch.cuda.is_available():
        raise ValueError('CUDA allocator budgets require an explicit CUDA device')
    properties = torch.cuda.get_device_properties(requested)
    total = properties.total_memory
    managed_uuid = env.get('RALLYMATE_MANAGED_GPU_UUID')
    physical_uuid = None
    if managed_uuid:
        # Record the device observed inside each process, including isolated SAM,
        # rather than presenting the launcher's desired mapping as evidence.
        import uuid
        raw_uuid = str(getattr(properties, 'uuid', ''))
        physical_uuid = 'GPU-' + str(uuid.UUID(raw_uuid.removeprefix('GPU-')))
        if (requested != 'cuda:0' or env.get('CUDA_VISIBLE_DEVICES') != managed_uuid
                or physical_uuid.lower() != managed_uuid.lower()
                or torch.cuda.device_count() != 1):
            raise RuntimeError('Managed allocator device does not match its physical GPU UUID')
    allowed = int(raw) * 1024**2
    if not 0 < allowed < total:
        raise ValueError('CUDA allocator budget must leave physical memory headroom')
    fraction = allowed / total
    torch.cuda.set_per_process_memory_fraction(fraction, device=requested)
    observed = torch.cuda.get_per_process_memory_fraction(device=requested)
    if not math.isclose(observed, fraction, rel_tol=1e-6, abs_tol=1e-9):
        raise RuntimeError('CUDA allocator budget readback did not match the request')
    return {'allocator_limit_mib': int(raw), 'total_memory_bytes': total,
            'requested_fraction': fraction, 'observed_fraction': observed,
            'logical_device': requested, 'physical_gpu_uuid': physical_uuid,
            'process_id': os.getpid(),
            'scope': 'pytorch_caching_allocator_in_this_process',
            'excludes': ['driver_context', 'opengl', 'nvenc', 'non_pytorch_allocations']}
