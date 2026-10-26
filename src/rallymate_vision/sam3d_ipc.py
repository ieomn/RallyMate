"""Persistent, lossless source-pixel transport for the isolated SAM process.

There is one in-flight request per worker. The parent owns the mapping and may
reuse it only after the child has acknowledged completion. This module does not
copy, quantize, resize or reinterpret image channels in the reader.
"""
from __future__ import annotations

from multiprocessing import shared_memory
import os

import numpy as np


TRANSPORT_ENV = "RALLYMATE_SAM3D_IPC_TRANSPORT"
PROTOCOL_VERSION = "sam-source-pixels-v1"
MAX_FRAME_BYTES = 512 * 1024 * 1024


def transport_metadata(mode: str) -> dict:
    if mode not in {"npy", "shared_memory"}:
        raise ValueError(f"{TRANSPORT_ENV} must be npy or shared_memory; got {mode!r}")
    return {
        "kind": mode,
        "protocol": PROTOCOL_VERSION,
        "pixels": "exact_uint8_bgr_no_resizing",
        "one_request_in_flight": True,
        "shared_buffer_reused_after_response": mode == "shared_memory",
    }


def configured_transport(env) -> str:
    mode = env.get(TRANSPORT_ENV, "npy")
    transport_metadata(mode)
    if mode == "shared_memory" and os.name != "nt":
        # Python 3.10/3.11's independent resource trackers can unlink a mapping
        # belonging to another process on POSIX. Keep the tested Windows
        # handle-lifetime contract explicit instead of patching private APIs.
        raise RuntimeError("SAM shared-memory transport currently requires Windows")
    return mode


def _validated_shape(shape) -> tuple[tuple[int, int, int], int]:
    if not isinstance(shape, (list, tuple)) or len(shape) != 3:
        raise ValueError("SAM IPC source must have shape [height, width, 3]")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in shape):
        raise ValueError("SAM IPC dimensions must be positive integers")
    if shape[2] != 3:
        raise ValueError("SAM IPC source must have three BGR channels")
    nbytes = shape[0] * shape[1] * shape[2]
    if nbytes > MAX_FRAME_BYTES:
        raise ValueError("SAM IPC source exceeds the 512 MiB transport bound")
    return tuple(shape), nbytes


class SharedFrameWriter:
    """Parent-owned mapping; its caller serializes writes and awaits the reply."""

    def __init__(self):
        self.mapping = None
        self.capacity = 0

    def write(self, frame: np.ndarray) -> dict:
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
            raise ValueError("SAM IPC source pixels must be a uint8 array")
        shape, nbytes = _validated_shape(frame.shape)
        if self.mapping is None or self.capacity < nbytes:
            # The caller only reaches this after the previous acknowledgement.
            self.close()
            self.mapping = shared_memory.SharedMemory(create=True, size=nbytes)
            self.capacity = nbytes
        destination = np.ndarray(shape, dtype=np.uint8, buffer=self.mapping.buf)
        np.copyto(destination, frame, casting="no")
        del destination
        return {"name": self.mapping.name, "shape": list(shape),
                "dtype": "uint8", "nbytes": nbytes, "protocol": PROTOCOL_VERSION}

    def close(self):
        mapping, self.mapping = self.mapping, None
        self.capacity = 0
        if mapping is not None:
            mapping.close()
            # On Windows this is a no-op; the OS removes the mapping after
            # both handles close. No private resource-tracker operations.
            try:
                mapping.unlink()
            except FileNotFoundError:
                pass


class SharedFrameReader:
    """Child mapping cache; returned views must die before switching mappings."""

    def __init__(self):
        self.mapping = None

    def read(self, descriptor: dict) -> np.ndarray:
        if not isinstance(descriptor, dict) or descriptor.get("protocol") != PROTOCOL_VERSION:
            raise ValueError("Unsupported SAM shared-memory protocol")
        if descriptor.get("dtype") != "uint8":
            raise ValueError("Unsupported SAM shared-memory pixel type")
        shape, nbytes = _validated_shape(descriptor.get("shape"))
        if type(descriptor.get("nbytes")) is not int or descriptor["nbytes"] != nbytes:
            raise ValueError("SAM shared-memory byte count does not match shape")
        name = descriptor.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("SAM shared-memory name is missing")
        if self.mapping is None or self.mapping.name != name:
            self.close()
            self.mapping = shared_memory.SharedMemory(name=name, create=False)
        if nbytes > self.mapping.size:
            raise ValueError("SAM shared-memory mapping is smaller than its descriptor")
        frame = np.ndarray(shape, dtype=np.uint8, buffer=self.mapping.buf)
        frame.setflags(write=False)
        return frame

    def close(self):
        mapping, self.mapping = self.mapping, None
        if mapping is not None:
            mapping.close()
