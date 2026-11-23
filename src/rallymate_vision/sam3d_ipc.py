"""Persistent, lossless source-pixel transport for the isolated SAM process.

There is one in-flight request per worker. The parent owns the mapping and may
reuse it only after the child has acknowledged completion. This module does not
copy, quantize, resize or reinterpret image channels in the reader.

On Windows this uses ``multiprocessing.shared_memory`` (handle lifetime is
kernel-managed). On POSIX this uses private ``0o600`` file-backed ``mmap``
segments inside ``RALLYMATE_SAM3D_SHM_DIR`` (defaulting to ``/dev/shm`` when
writable, else ``tempfile.gettempdir()``), avoiding Python 3.10/3.11's
``multiprocessing.resource_tracker`` unlinking another process's mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
import mmap
from multiprocessing import shared_memory
import os
from pathlib import Path
import stat
import tempfile
import uuid

import numpy as np


TRANSPORT_ENV = "RALLYMATE_SAM3D_IPC_TRANSPORT"
SHM_DIR_ENV = "RALLYMATE_SAM3D_SHM_DIR"
PROTOCOL_VERSION = "sam-source-pixels-v1"
MAX_FRAME_BYTES = 512 * 1024 * 1024


def _use_posix_mmap() -> bool:
    return os.name != "nt"


def resolve_posix_shm_dir(env=None) -> Path:
    """Return the directory used for POSIX file-backed mmap IPC segments."""
    mapping = os.environ if env is None else env
    configured = mapping.get(SHM_DIR_ENV)
    if configured is not None:
        if not isinstance(configured, str):
            raise ValueError(f"{SHM_DIR_ENV} must be a filesystem path string")
        stripped = configured.strip()
        if stripped:
            path = Path(stripped).resolve()
            if path.exists() and not path.is_dir():
                raise ValueError(f"{SHM_DIR_ENV} is not a directory: {path}")
            return path
    if _use_posix_mmap():
        dev_shm = Path("/dev/shm")
        if dev_shm.is_dir() and os.access(dev_shm, os.W_OK | os.X_OK):
            return dev_shm.resolve()
    return Path(tempfile.gettempdir()).resolve()


def transport_metadata(mode: str) -> dict:
    if mode not in {"npy", "shared_memory"}:
        raise ValueError(f"{TRANSPORT_ENV} must be npy or shared_memory; got {mode!r}")
    metadata = {
        "kind": mode,
        "protocol": PROTOCOL_VERSION,
        "pixels": "exact_uint8_bgr_no_resizing",
        "one_request_in_flight": True,
        "shared_buffer_reused_after_response": mode == "shared_memory",
    }
    if mode == "shared_memory":
        metadata["backend"] = "posix_mmap" if _use_posix_mmap() else "windows_shared_memory"
    return metadata


def configured_transport(env) -> str:
    mode = env.get(TRANSPORT_ENV, "npy")
    transport_metadata(mode)
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


def _validate_descriptor_common(descriptor: dict) -> tuple[tuple[int, int, int], int, str]:
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
    return shape, nbytes, name


def _resolve_contained_shm_path(shm_dir: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\x00" in name
            or "/" in name or "\\" in name or name in {".", ".."}
            or Path(name).is_absolute() or Path(name).name != name):
        raise ValueError("SAM shared-memory segment name escapes its configured directory")
    root = shm_dir.resolve()
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError("SAM shared-memory segment must not be a symlink")
    resolved = candidate.resolve()
    if resolved == root or resolved.parent != root:
        raise ValueError("SAM shared-memory segment escapes its configured directory")
    return resolved


@dataclass
class _PosixMmapHandle:
    name: str
    path: Path
    buf: mmap.mmap
    size: int

    def close(self) -> None:
        self.buf.close()


class PosixSharedFrameWriter:
    """Parent-owned POSIX file-backed mmap writer without resource_tracker."""

    def __init__(self, shm_dir: Path | str | None = None):
        self._shm_dir = Path(shm_dir).resolve() if shm_dir is not None else None
        self.mapping: _PosixMmapHandle | None = None
        self.capacity = 0
        self._deferred_unlinks: list[Path] = []

    def _target_dir(self) -> Path:
        return self._shm_dir if self._shm_dir is not None else resolve_posix_shm_dir()

    def _retry_deferred_unlinks(self) -> None:
        if not self._deferred_unlinks:
            return
        remaining = []
        for path in self._deferred_unlinks:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except PermissionError:
                if os.name == "nt":
                    remaining.append(path)
                else:
                    raise
        self._deferred_unlinks = remaining

    def _unlink_segment(self, path: Path) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except PermissionError:
            if os.name == "nt":
                # Windows forbids unlinking an open mapped file until all reader
                # handles close; retry once the caller switches/closes mappings.
                self._deferred_unlinks.append(path)
            else:
                raise

    def write(self, frame: np.ndarray) -> dict:
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
            raise ValueError("SAM IPC source pixels must be a uint8 array")
        shape, nbytes = _validated_shape(frame.shape)
        self._retry_deferred_unlinks()
        if self.mapping is None or self.capacity < nbytes:
            # The caller only reaches this after the previous acknowledgement.
            self.close()
            shm_dir = self._target_dir()
            shm_dir.mkdir(parents=True, exist_ok=True)
            filename = f"rallymate-sam3d-{os.getpid()}-{uuid.uuid4().hex}.shm"
            path = shm_dir / filename
            flags = (os.O_RDWR | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            fd = os.open(str(path), flags, 0o600)
            try:
                os.ftruncate(fd, nbytes)
                mm = mmap.mmap(fd, nbytes, access=mmap.ACCESS_WRITE)
            except BaseException:
                os.close(fd)
                self._unlink_segment(path)
                raise
            os.close(fd)
            self.mapping = _PosixMmapHandle(name=filename, path=path, buf=mm, size=nbytes)
            self.capacity = nbytes
        destination = np.ndarray(shape, dtype=np.uint8, buffer=self.mapping.buf)
        np.copyto(destination, frame, casting="no")
        del destination
        return {
            "name": self.mapping.name,
            "shape": list(shape),
            "dtype": "uint8",
            "nbytes": nbytes,
            "protocol": PROTOCOL_VERSION,
            "backend": "posix_mmap",
        }

    def close(self) -> None:
        mapping, self.mapping = self.mapping, None
        self.capacity = 0
        if mapping is not None:
            try:
                mapping.close()
            finally:
                self._unlink_segment(mapping.path)
        self._retry_deferred_unlinks()


class PosixSharedFrameReader:
    """Child read-only mmap reader; never unlinks parent-owned segments."""

    def __init__(self, shm_dir: Path | str | None = None):
        self._shm_dir = Path(shm_dir).resolve() if shm_dir is not None else None
        self.mapping: _PosixMmapHandle | None = None

    def _target_dir(self) -> Path:
        return self._shm_dir if self._shm_dir is not None else resolve_posix_shm_dir()

    def read(self, descriptor: dict) -> np.ndarray:
        shape, nbytes, name = _validate_descriptor_common(descriptor)
        backend = descriptor.get("backend")
        if backend is not None and backend != "posix_mmap":
            raise ValueError(f"Unsupported POSIX shared-memory backend: {backend!r}")
        shm_dir = self._target_dir()
        resolved = _resolve_contained_shm_path(shm_dir, name)
        if self.mapping is None or self.mapping.name != name or self.mapping.path != resolved:
            self.close()
            flags = (os.O_RDONLY
                     | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            fd = os.open(str(resolved), flags)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError("SAM shared-memory segment must be a regular file")
                size = st.st_size
                if size <= 0 or size > MAX_FRAME_BYTES:
                    raise ValueError("SAM shared-memory segment size is out of bounds")
                if nbytes > size:
                    raise ValueError("SAM shared-memory mapping is smaller than its descriptor")
                mm = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
            finally:
                os.close(fd)
            self.mapping = _PosixMmapHandle(name=name, path=resolved, buf=mm, size=size)
        if nbytes > self.mapping.size:
            raise ValueError("SAM shared-memory mapping is smaller than its descriptor")
        frame = np.ndarray(shape, dtype=np.uint8, buffer=self.mapping.buf)
        frame.setflags(write=False)
        return frame

    def close(self) -> None:
        mapping, self.mapping = self.mapping, None
        if mapping is not None:
            mapping.close()


class SharedFrameWriter:
    """Parent-owned mapping; its caller serializes writes and awaits the reply."""

    def __init__(self, *, shm_dir: Path | str | None = None):
        self._posix = PosixSharedFrameWriter(shm_dir=shm_dir) if _use_posix_mmap() else None
        self.mapping = None
        self.capacity = 0

    def write(self, frame: np.ndarray) -> dict:
        if self._posix is not None:
            descriptor = self._posix.write(frame)
            self.mapping = self._posix.mapping
            self.capacity = self._posix.capacity
            return descriptor
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
        if self._posix is not None:
            self._posix.close()
            self.mapping = None
            self.capacity = 0
            return
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

    def __init__(self, *, shm_dir: Path | str | None = None):
        self._shm_dir = shm_dir
        self._posix = PosixSharedFrameReader(shm_dir=shm_dir) if _use_posix_mmap() else None
        self.mapping = None

    def read(self, descriptor: dict) -> np.ndarray:
        if isinstance(descriptor, dict) and descriptor.get("backend") == "posix_mmap":
            if self._posix is None:
                self._posix = PosixSharedFrameReader(shm_dir=self._shm_dir)
            frame = self._posix.read(descriptor)
            self.mapping = self._posix.mapping
            return frame
        if self._posix is not None:
            frame = self._posix.read(descriptor)
            self.mapping = self._posix.mapping
            return frame
        shape, nbytes, name = _validate_descriptor_common(descriptor)
        backend = descriptor.get("backend")
        if backend is not None and backend != "windows_shared_memory":
            raise ValueError(f"Unsupported Windows shared-memory backend: {backend!r}")
        if self.mapping is None or self.mapping.name != name:
            self.close()
            self.mapping = shared_memory.SharedMemory(name=name, create=False)
        if nbytes > self.mapping.size:
            raise ValueError("SAM shared-memory mapping is smaller than its descriptor")
        frame = np.ndarray(shape, dtype=np.uint8, buffer=self.mapping.buf)
        frame.setflags(write=False)
        return frame

    def close(self):
        if self._posix is not None:
            self._posix.close()
            self.mapping = None
            return
        mapping, self.mapping = self.mapping, None
        if mapping is not None:
            mapping.close()
