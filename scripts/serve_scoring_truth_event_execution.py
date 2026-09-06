#!/usr/bin/env python3
"""Validate and serve a portable M93 event/phase execution bundle.

This copied launcher is Python-stdlib-only.  It freezes every declared file in
memory before opening a loopback listener.  Domain validation (operator-release
replay, role separation, media probing, and submission revisions) remains in
``rallymate_annotation.scoring_truth_event_execution``; this standalone layer
provides portable byte/topology validation and an exact HTTP allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import stat
import sys
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import unquote_to_bytes, urlsplit


SCHEMA_VERSION = "1.0.0"
EXECUTION_VERSION = "scoring-truth-event-execution-bundle-v1.0.0"
EXECUTION_STATUS = "annotation_execution_ready_operator_release_verified"
ADJUDICATION_VERSION = "scoring-truth-event-adjudication-bundle-v1.0.0"
ADJUDICATION_STATUS = "adjudication_ready_both_submissions_verified"
EXECUTION_MANIFEST = "execution-manifest.json"
ADJUDICATION_MANIFEST = "adjudication-manifest.json"
ENTRYPOINT = "review.html"
LAUNCHER_NAME = "serve_scoring_truth_event_execution.py"
CORE_JS = "scoring-truth-event-collection-core.js"
WORKBENCH_JS = "scoring-truth-event-collection-workbench.js"
WORKBENCH_CSS = "scoring-truth-event-collection-workbench.css"
WORKBENCH_HTML_TEMPLATE_SHA256 = "6E005BAD0199FD1D5DDBDD014580131046CA5B81F8E2F55D23D957383D4AC1A0"
BOOTSTRAP_OPEN = (
    '<script id="scoring-truth-event-collection-bootstrap" '
    'type="application/json">'
)
BOOTSTRAP_CLOSE = "</script>"
EXPECTED_PUBLIC_ASSET_SHA256 = {
    CORE_JS: "B18F34C5BC3481F1D4B8BA16439077369011ACE318757BC240762A8B332B979D",
    WORKBENCH_JS: "3783DE18D7F3CEE4E8A5CE8D6A6AACBF63A33F267893D5EA59A5B71670110AAD",
    WORKBENCH_CSS: "C918BAA178E15DCC2E68E73242E806B8842C99BC27BBD85673922995CF30C7AF",
}
AUTHORITY_ARTIFACT_PATHS = {
    "authority/annotation-plan.json",
    "authority/operator-release.json",
    "authority/m89-handoff/event-handoff-manifest.json",
    "authority/m89-handoff/OPERATOR_README.md",
    "authority/m89-handoff/media/3ae77ee3271d67de171585a5c39ddd69.mp4",
    "authority/m89-handoff/media/850cb0006b406c7176eeda8d711cd065.mp4",
    "authority/m89-handoff/media/8d7754d0de6d315674013d5b69a0b6ba.mp4",
    "authority/m89-handoff/review.html",
    "authority/m89-handoff/scoring-truth-event-workbench.css",
    "authority/m89-handoff/scoring-truth-event-workbench.js",
    "authority/m89-handoff/serve_scoring_truth_event_handoff.py",
}
COMMON_ARTIFACT_PATHS = AUTHORITY_ARTIFACT_PATHS | {
    ENTRYPOINT,
    CORE_JS,
    WORKBENCH_JS,
    WORKBENCH_CSS,
    LAUNCHER_NAME,
}
ADJUDICATION_SOURCE_PATHS = {
    "sources/execution-A-manifest.json",
    "sources/execution-B-manifest.json",
    "submissions/annotator-A.json",
    "submissions/annotator-B.json",
}

EXECUTION_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "execution_id",
    "generated_at",
    "status",
    "role",
    "event_authorization",
    "source_files",
    "scope",
    "revision_contract",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "manifest_binding_sha256",
    "safety",
}
ADJUDICATION_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "execution_id",
    "generated_at",
    "status",
    "role",
    "event_authorization",
    "execution_sources",
    "annotation_submissions",
    "scope",
    "revision_contract",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "manifest_binding_sha256",
    "safety",
}
ARTIFACT_KEYS = {"path", "bytes", "sha256"}
SHA_RE = re.compile(r"^[0-9A-F]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
JS_SAFE_INTEGER_MAX = 9_007_199_254_740_991
ROLE_BY_VERSION = {EXECUTION_VERSION: {"A", "B"}, ADJUDICATION_VERSION: {"C"}}
STATUS_BY_VERSION = {
    EXECUTION_VERSION: EXECUTION_STATUS,
    ADJUDICATION_VERSION: ADJUDICATION_STATUS,
}


class BundleValidationError(ValueError):
    """The directory is not the exact portable bundle declared by its manifest."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit_json({"ok": False, "error": message}, stream=sys.stderr)
        raise SystemExit(2)


def _emit_json(value: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
        file=stream,
        flush=True,
    )


def _reject_json_constant(value: str) -> None:
    raise BundleValidationError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_unicode(value: Any, *, name: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BundleValidationError(f"{name} contains an isolated surrogate") from exc
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_unicode(item, name=f"{name}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key, name=f"{name} key")
            _validate_unicode(item, name=f"{name}.{key}")
    elif (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > JS_SAFE_INTEGER_MAX
    ):
        raise BundleValidationError(
            f"{name} exceeds the JavaScript safe integer range"
        )


def _canonical_bytes(value: Any) -> bytes:
    _validate_unicode(value, name="value")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise BundleValidationError("value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _load_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except BundleValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BundleValidationError(f"{name} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"{name} must be a JSON object")
    _validate_unicode(value, name=name)
    return value


def _bundle_ref(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        "bundle_id": manifest["bundle_id"],
        "manifest_binding_sha256": manifest["manifest_binding_sha256"],
    }


def _expected_bootstrap(manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        safety = manifest["safety"]
        tasks = [
            {
                "task_id": item["task_id"],
                "video_id": item["video_id"],
                "media_path": item["media_path"],
                "media_sha256": item["video_sha256"],
                "duration_ms": item["duration_ms"],
                "frame_rate": dict(item["frame_rate"]),
                "full_video_review_required": item[
                    "full_video_review_required"
                ],
            }
            for item in manifest["scope"]["tasks"]
        ]
        common = {
            "schema_version": SCHEMA_VERSION,
            "workbench_version": "scoring-truth-event-collection-workbench-v1.0.0",
            "bundle_version": manifest["bundle_version"],
            "execution_id": manifest["execution_id"],
            "bundle_status": manifest["status"],
            "authorization_status": manifest["event_authorization"]["status"],
            "authorization_binding_sha256": manifest["event_authorization"][
                "binding_sha256"
            ],
            "role_slot": manifest["role"]["slot"],
            "annotation_execution_authorized": safety[
                "annotation_execution_authorized"
            ],
            "operator_release_record_verified": safety[
                "operator_release_record_verified"
            ],
            "mutation_enabled": safety["mutation_enabled"],
            "import_enabled": safety["import_enabled"],
            "export_enabled": safety["export_enabled"],
            "full_video_only": safety["full_video_only"],
            "machine_event_boundaries_embedded": safety[
                "machine_event_boundaries_embedded"
            ],
            "phase_values_embedded": safety["phase_values_embedded"],
            "machine_keypoints_embedded": safety[
                "machine_keypoints_embedded"
            ],
            "grades_or_thresholds_supported": safety[
                "grades_or_thresholds_supported"
            ],
            "calibration_authorized": safety["calibration_authorized"],
            "promotion_authorized": safety["promotion_authorized"],
            "production_enabled": safety["production_enabled"],
            "maturity_promoted": safety["maturity_promoted"],
            "event_codes": manifest["scope"]["event_codes"],
            "phase_keys_by_event": manifest["scope"]["phase_keys_by_event"],
            "required_annotator_slots": ["A", "B"],
            "reviewer_slot": "C",
            "tasks": tasks,
        }
        if manifest["bundle_version"] == EXECUTION_VERSION:
            return {**common, "execution_bundle": _bundle_ref(manifest)}
        execution_sources = {
            item["role_slot"]: {
                "bundle_id": item["bundle_id"],
                "manifest_binding_sha256": item["manifest_binding_sha256"],
            }
            for item in manifest["execution_sources"]
        }
        expected_submissions = {
            item["role_slot"]: {
                "submission_id": item["submission_id"],
                "raw_sha256": item["raw_sha256"],
                "submission_revision_sha256": item[
                    "submission_revision_sha256"
                ],
                "annotator_id": item["annotator_id"],
            }
            for item in manifest["annotation_submissions"]
        }
        return {
            **common,
            "adjudication_bundle": _bundle_ref(manifest),
            "source_execution_bundles": execution_sources,
            "expected_source_submissions": expected_submissions,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleValidationError(
            "manifest cannot produce the exact workbench bootstrap"
        ) from exc


def _validate_review_html(raw: bytes, manifest: Mapping[str, Any]) -> None:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleValidationError("review.html must be UTF-8") from exc
    if html.count(BOOTSTRAP_OPEN) != 1:
        raise BundleValidationError("review.html bootstrap element is invalid")
    start = html.index(BOOTSTRAP_OPEN) + len(BOOTSTRAP_OPEN)
    end = html.find(BOOTSTRAP_CLOSE, start)
    if end < 0:
        raise BundleValidationError("review.html bootstrap is not closed")
    encoded = html[start:end]
    bootstrap = _load_json_object(
        encoded.replace("<\\/", "</").encode("utf-8"),
        name="review.html bootstrap",
    )
    expected_encoded = _canonical_bytes(bootstrap).decode("utf-8").replace(
        "</", "<\\/"
    )
    expected_encoded = expected_encoded.replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029"
    )
    if encoded != expected_encoded:
        raise BundleValidationError("review.html bootstrap JSON is not canonical")
    restored = (html[:start] + "{}" + html[end:]).encode("utf-8")
    if _sha256(restored) != WORKBENCH_HTML_TEMPLATE_SHA256:
        raise BundleValidationError("approved workbench HTML template drifted")
    if bootstrap != _expected_bootstrap(manifest):
        raise BundleValidationError("review.html bootstrap does not match manifest")


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise BundleValidationError(
            f"{name} keys are not exact "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )
    return value


def _require_sha(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise BundleValidationError(f"{name} must be uppercase SHA-256")
    return value


def _timestamp(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise BundleValidationError(f"{name} must be canonical UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BundleValidationError(f"{name} is not a real timestamp") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise BundleValidationError(f"{name} is not canonical UTC")
    return parsed


def _safe_path(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "%" in value:
        raise BundleValidationError(f"{name} must be a portable relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or posix.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise BundleValidationError(f"{name} must be a portable relative path")
    return value


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    is_junction = getattr(path, "is_junction", None)
    return bool(
        stat.S_ISLNK(metadata.st_mode)
        or attributes & reparse_flag
        or (is_junction is not None and is_junction())
    )


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _snapshot_file(path: Path, *, name: str) -> bytes:
    requested = path.absolute()
    descriptor: int | None = None
    try:
        before_path = requested.lstat()
        before_resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise BundleValidationError(f"missing file: {name}") from exc
    if _is_link_like(requested) or not stat.S_ISREG(before_path.st_mode):
        raise BundleValidationError(f"{name} must be a regular non-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(requested, flags)
        before_open = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
        after_path = requested.lstat()
        after_resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise BundleValidationError(f"could not snapshot file: {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len({_path_identity(before_path), _path_identity(before_open), _path_identity(after_open), _path_identity(after_path)}) != 1
        or before_resolved != after_resolved
        or len(raw) != after_open.st_size
    ):
        raise BundleValidationError(f"file changed while read: {name}")
    return raw


def _tree_files(root: Path) -> tuple[str, ...]:
    if _is_link_like(root) or not root.is_dir():
        raise BundleValidationError("bundle root must be a regular directory")
    files: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise BundleValidationError("could not enumerate bundle") from exc
        for child in children:
            if _is_link_like(child):
                raise BundleValidationError(
                    f"bundle contains a link or reparse point: {child.relative_to(root).as_posix()}"
                )
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise BundleValidationError("bundle changed while enumerated") from exc
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child)
            elif stat.S_ISREG(metadata.st_mode):
                files.append(child.relative_to(root).as_posix())
            else:
                raise BundleValidationError(
                    f"bundle contains a non-regular entry: {child.relative_to(root).as_posix()}"
                )
    return tuple(sorted(files))


def _manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"artifacts", "content_root_sha256", "manifest_binding_sha256"}
    }


def manifest_binding_sha256(manifest: Mapping[str, Any]) -> str:
    return _sha256(_canonical_bytes(_manifest_projection(manifest)))


def _content_root(artifacts: list[dict[str, Any]]) -> str:
    return _sha256(_canonical_bytes({"artifacts": artifacts}))


def _manifest_name(root: Path) -> str:
    names = [
        name
        for name in (EXECUTION_MANIFEST, ADJUDICATION_MANIFEST)
        if (root / name).is_file() and not _is_link_like(root / name)
    ]
    if len(names) != 1:
        raise BundleValidationError("bundle must contain exactly one M93 manifest")
    return names[0]


def _validate_manifest(manifest: dict[str, Any], *, manifest_name: str) -> None:
    version = manifest.get("bundle_version")
    if version == EXECUTION_VERSION:
        expected_keys = EXECUTION_KEYS
        expected_manifest = EXECUTION_MANIFEST
    elif version == ADJUDICATION_VERSION:
        expected_keys = ADJUDICATION_KEYS
        expected_manifest = ADJUDICATION_MANIFEST
    else:
        raise BundleValidationError("bundle_version is unsupported")
    _require_exact_keys(manifest, expected_keys, name="manifest")
    if manifest_name != expected_manifest:
        raise BundleValidationError("manifest filename does not match bundle_version")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError("schema_version is unsupported")
    if manifest["status"] != STATUS_BY_VERSION[version]:
        raise BundleValidationError("bundle status is invalid")
    if not isinstance(manifest["bundle_id"], str) or ID_RE.fullmatch(manifest["bundle_id"]) is None:
        raise BundleValidationError("bundle_id is invalid")
    if not isinstance(manifest["execution_id"], str) or ID_RE.fullmatch(manifest["execution_id"]) is None:
        raise BundleValidationError("execution_id is invalid")
    _timestamp(manifest["generated_at"], name="generated_at")
    role = _require_exact_keys(manifest["role"], {"slot", "kind"}, name="role")
    if role["slot"] not in ROLE_BY_VERSION[version] or not isinstance(role["kind"], str) or not role["kind"]:
        raise BundleValidationError("bundle role is invalid")
    if manifest["entrypoint"] != ENTRYPOINT or manifest["server_launcher"] != LAUNCHER_NAME:
        raise BundleValidationError("bundle entrypoint or launcher drifted")
    binding = _require_sha(manifest["manifest_binding_sha256"], name="manifest binding")
    if manifest_binding_sha256(manifest) != binding:
        raise BundleValidationError("manifest binding checksum mismatch")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise BundleValidationError("manifest artifacts must be a non-empty array")
    paths: list[str] = []
    for index, item in enumerate(artifacts):
        record = _require_exact_keys(item, ARTIFACT_KEYS, name=f"artifacts[{index}]")
        path = _safe_path(record["path"], name=f"artifacts[{index}].path")
        if not isinstance(record["bytes"], int) or isinstance(record["bytes"], bool) or record["bytes"] < 1:
            raise BundleValidationError(f"artifacts[{index}].bytes is invalid")
        _require_sha(record["sha256"], name=f"artifacts[{index}].sha256")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BundleValidationError("artifact paths must be unique and sorted")
    if any(name in paths for name in (EXECUTION_MANIFEST, ADJUDICATION_MANIFEST)):
        raise BundleValidationError("manifest cannot list itself as an artifact")
    _require_sha(manifest["content_root_sha256"], name="content root")
    if _content_root(artifacts) != manifest["content_root_sha256"]:
        raise BundleValidationError("content root checksum mismatch")
    records = {item["path"]: item for item in artifacts}
    for path, expected_sha in EXPECTED_PUBLIC_ASSET_SHA256.items():
        if path not in records or records[path]["sha256"] != expected_sha:
            raise BundleValidationError(f"approved workbench asset drifted: {path}")
    expected_paths = set(COMMON_ARTIFACT_PATHS)
    if version == ADJUDICATION_VERSION:
        expected_paths.update(ADJUDICATION_SOURCE_PATHS)
    if set(paths) != expected_paths:
        raise BundleValidationError("bundle artifact topology is not exact")


def validate_and_snapshot(directory: str | Path) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    root = Path(directory).expanduser().absolute()
    manifest_name = _manifest_name(root)
    before_tree = _tree_files(root)
    manifest_raw = _snapshot_file(root / manifest_name, name=manifest_name)
    manifest = _load_json_object(manifest_raw, name=manifest_name)
    _validate_manifest(manifest, manifest_name=manifest_name)
    declared = [item["path"] for item in manifest["artifacts"]]
    expected_tree = tuple(sorted([manifest_name, *declared]))
    if before_tree != expected_tree:
        raise BundleValidationError("bundle topology does not exactly match manifest artifacts")
    snapshots: dict[str, bytes] = {manifest_name: manifest_raw}
    for record in manifest["artifacts"]:
        raw = _snapshot_file(root / Path(*record["path"].split("/")), name=record["path"])
        if len(raw) != record["bytes"] or _sha256(raw) != record["sha256"]:
            raise BundleValidationError(f"artifact bytes do not match manifest: {record['path']}")
        snapshots[record["path"]] = raw
    _validate_review_html(snapshots[ENTRYPOINT], manifest)
    if _tree_files(root) != expected_tree:
        raise BundleValidationError("bundle changed while validated")
    return manifest, MappingProxyType(snapshots)


def _served_paths(manifest: Mapping[str, Any]) -> set[str]:
    served = {ENTRYPOINT, CORE_JS, WORKBENCH_JS, WORKBENCH_CSS}
    try:
        for task in manifest["scope"]["tasks"]:
            served.add(task["media_path"])
    except (KeyError, TypeError) as exc:
        raise BundleValidationError("scope media paths are incomplete") from exc
    if manifest["bundle_version"] == ADJUDICATION_VERSION:
        try:
            for item in manifest["annotation_submissions"]:
                served.add(item["path"])
        except (KeyError, TypeError) as exc:
            raise BundleValidationError("adjudication submission sources are incomplete") from exc
    declared = {item["path"] for item in manifest["artifacts"]}
    if not served <= declared:
        raise BundleValidationError("HTTP allowlist references undeclared artifacts")
    return served


def _decode_target(target: str) -> str | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    try:
        raw = unquote_to_bytes(parsed.path)
        decoded = raw.decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if decoded == "/":
        return ENTRYPOINT
    value = decoded[1:] if decoded.startswith("/") else decoded
    try:
        return _safe_path(value, name="request path")
    except BundleValidationError:
        return None


def _range(value: str | None, size: int) -> tuple[int, int] | None | bool:
    if value is None:
        return None
    match = RANGE_RE.fullmatch(value.strip())
    if match is None or "," in value:
        return False
    first, last = match.groups()
    if not first and not last:
        return False
    if first:
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or end < start:
            return False
        return start, min(end, size - 1)
    suffix = int(last)
    if suffix <= 0:
        return False
    return max(0, size - suffix), size - 1


def _handler(
    snapshots: Mapping[str, bytes], served: set[str], _requested_port: int
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "RallyMateLocalBundle/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

        def _send(self, *, head: bool) -> None:
            host = self.headers.get("Host", "")
            port = int(self.server.server_address[1])
            allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}
            if host not in allowed_hosts:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            path = _decode_target(self.path)
            if path is None or path not in served:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            raw = snapshots[path]
            selected = _range(self.headers.get("Range"), len(raw))
            if selected is False:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._headers()
                self.send_header("Content-Range", f"bytes */{len(raw)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if selected is None:
                start, end, status = 0, len(raw) - 1, HTTPStatus.OK
            else:
                start, end, status = selected[0], selected[1], HTTPStatus.PARTIAL_CONTENT
            body = raw[start : end + 1]
            self.send_response(status)
            self._headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            if selected is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(raw)}")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send(head=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._send(head=True)

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self._headers()
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_CONNECT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_TRACE(self) -> None:  # noqa: N802
            self._method_not_allowed()

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Validate and serve one local M93 event/phase execution bundle.")
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    if args.bind not in {"127.0.0.1", "localhost"}:
        parser.error("--bind must be loopback")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be in 0..65535")
    try:
        manifest, snapshots = validate_and_snapshot(args.directory)
        served = _served_paths(manifest)
    except BundleValidationError as exc:
        parser.error(str(exc))
    if args.validate_only:
        _emit_json(
            {
                "ok": True,
                "operation": "portable_validation",
                "bundle_id": manifest["bundle_id"],
                "bundle_version": manifest["bundle_version"],
                "status": manifest["status"],
                "content_root_sha256": manifest["content_root_sha256"],
                "manifest_binding_sha256": manifest["manifest_binding_sha256"],
            }
        )
        return 0
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(snapshots, served, args.port))
    port = int(httpd.server_address[1])
    url = f"http://127.0.0.1:{port}/"
    _emit_json({"ok": True, "operation": "serve", "url": url, "bundle_id": manifest["bundle_id"]})
    if not args.no_open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ADJUDICATION_MANIFEST",
    "ADJUDICATION_STATUS",
    "ADJUDICATION_VERSION",
    "BundleValidationError",
    "EXECUTION_MANIFEST",
    "EXECUTION_STATUS",
    "EXECUTION_VERSION",
    "manifest_binding_sha256",
    "validate_and_snapshot",
]
