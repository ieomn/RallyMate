"""Public, path-free identity of source files verified when the API starts.

This is a startup file snapshot, not a signature or continuous file-integrity
monitor. Later discovery requests compare the manifest bytes with that snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest member")
        result[key] = value
    return result


def _within_repository(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Manifest member is not a repository file")
    return resolved


def _manifest_bytes(root: Path) -> bytes:
    return _within_repository(root / "source-manifest.json", root).read_bytes()


def _file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


@dataclass(frozen=True)
class DeploymentIdentity:
    _repository_root: Path
    _startup_sha256: str | None = None

    def public_status(self) -> dict:
        unavailable = {"source_manifest_sha256": None, "source_files_verified": False,
                       "manifest_unchanged_since_startup": False}
        if self._startup_sha256 is None:
            return unavailable
        try:
            current = hashlib.sha256(_manifest_bytes(self._repository_root)).hexdigest()
        except (OSError, ValueError, RuntimeError):
            return unavailable
        return {"source_manifest_sha256": self._startup_sha256, "source_files_verified": True,
                "manifest_unchanged_since_startup": current == self._startup_sha256}


def load_deployment_identity(repository_root: Path) -> DeploymentIdentity:
    """Fail closed for identity only; a missing/invalid manifest never stops API use."""
    root = Path(repository_root)
    try:
        root = root.resolve(strict=True)
        raw = _manifest_bytes(root)
        manifest = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("Manifest requires files")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid manifest file")
            name, expected = entry.get("path"), entry.get("sha256")
            if (not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name
                    or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
                    or any(part in {"", ".", ".."} for part in name.split("/"))):
                raise ValueError("Manifest path must be relative")
            if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                raise ValueError("Invalid file digest")
            path = _within_repository(root / name, root)
            if path in seen:
                raise ValueError("Duplicate manifest file")
            seen.add(path)
            if "bytes" in entry:
                size = entry["bytes"]
                if not isinstance(size, int) or isinstance(size, bool) or size < 0 or path.stat().st_size != size:
                    raise ValueError("File size does not match manifest")
            if _file_digest(path) != expected.lower():
                raise ValueError("File digest does not match manifest")
        # Do not bind a manifest that changed while its file hashes were checked.
        if _manifest_bytes(root) != raw:
            raise ValueError("Manifest changed during startup verification")
        return DeploymentIdentity(root, hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError):
        return DeploymentIdentity(root)
