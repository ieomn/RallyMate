from __future__ import annotations

import ctypes
import os
from pathlib import Path
from ctypes import wintypes


class LocalSecretError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_crypt(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise LocalSecretError("Encrypted local credentials require Windows; use MINIMAX_API_KEY on this host")
    buffer = ctypes.create_string_buffer(value, len(value))
    source = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN. DPAPI binds this credential to the current user.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise LocalSecretError("Unable to access the encrypted local MiniMax credential")
    try:
        return ctypes.string_at(target.data, target.length)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(target.data)


def save_minimax_key(data_root: Path, value: str) -> Path:
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        raise LocalSecretError("A non-empty API credential without whitespace is required")
    encrypted = _windows_crypt(value.encode("utf-8"), decrypt=False)
    destination = Path(data_root) / "secrets" / "minimax.dpapi"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(destination)
    return destination


def load_minimax_key(data_root: Path) -> str | None:
    environment_value = (os.environ.get("MINIMAX_API_KEY") or "").strip()
    if environment_value:
        return environment_value
    destination = Path(data_root) / "secrets" / "minimax.dpapi"
    if not destination.is_file():
        return None
    return _windows_crypt(destination.read_bytes(), decrypt=True).decode("utf-8").strip() or None
