"""Shared bounds for source pixels retained by SAM staging and IPC."""
from __future__ import annotations


MAX_BATCH_FRAMES = 8
MAX_BATCH_SOURCE_BYTES = 256 * 1024 * 1024
MIN_AUTO_SOURCE_BYTES = 64 * 1024 * 1024
SOURCE_BUFFER_ENV = "RALLYMATE_SAM3D_SOURCE_BUFFER_MIB"


def configured_source_buffer_bytes(environment):
    """Unset uses a frame-size-aware budget; explicit values remain hard bounds."""
    value = environment.get(SOURCE_BUFFER_ENV)
    if value is None:
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{SOURCE_BUFFER_ENV} must be an integer between 1 and 256")
    mib = int(value)
    if not 1 <= mib <= MAX_BATCH_SOURCE_BYTES // (1024 * 1024):
        raise ValueError(f"{SOURCE_BUFFER_ENV} must be an integer between 1 and 256")
    return mib * 1024 * 1024


def automatic_source_buffer_bytes(frame_bytes, batch_frames):
    return min(MAX_BATCH_SOURCE_BYTES, max(MIN_AUTO_SOURCE_BYTES, frame_bytes * batch_frames))
