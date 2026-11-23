"""Bounded, source-verified 3D SAM mesh geometry payload builder for interactive WebGL replay.

Reads archived estimator geometry from ``sam3d-mesh-frames.jsonl`` and
``sam3d-mesh-data/*.mesh.npz`` + ``faces-*.npy``, validates strict containment
and SHA-256 digests, decimates and compacts topology deterministically when
needed, centers the athlete at the origin with unit height in Y-up right-handed
coordinates, and Int16-quantizes per-frame vertices into a compact payload
strictly capped below 4 MiB.
"""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

import numpy as np

from rallymate_vision.sam3d_mesh import (
    LEDGER_NAME,
    LIGHT_BLUE,
    MAX_GEOMETRY_FILE_BYTES,
    VERSION as ARCHIVE_VERSION,
)


GEOMETRY_PAYLOAD_VERSION = "sam3d-mesh-geometry-v1"
DATA_DIR_NAME = "sam3d-mesh-data"
DEFAULT_MAX_FRAMES = 60
MAX_ALLOWED_FRAMES = 90
DEFAULT_MAX_FACES = 12000
MAX_ALLOWED_FACES = 50000
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_LEDGER_LINE_CHARS = 16384
MAX_LEDGER_ROWS = 100000
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_VERTICES_PER_FRAME = 100000
MAX_FACES_PER_FRAME = 200000
QUANTIZATION_MIN = -32767
QUANTIZATION_MAX = 32767
QUANTIZATION_SPAN = float(QUANTIZATION_MAX - QUANTIZATION_MIN)  # 65534.0

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MATERIAL_RGB = [int(round(channel * 255)) for channel in LIGHT_BLUE]


class MeshGeometryUnavailable(ValueError):
    """Public-safe exception mapping cleanly to HTTP 404 / 409 / 400 responses."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 404,
        code: str = "mesh_geometry_unavailable",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _normalize_relative_subpath(raw_path: Any) -> Path:
    """Parse an archive-relative path safely across POSIX and Windows separators."""
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise MeshGeometryUnavailable("Mesh geometry path is invalid.", status_code=404, code="invalid_artifact_path")
    # Reject drive prefixes (e.g. C:\...) or absolute paths on any platform.
    if raw_path.startswith(("/", "\\")) or (len(raw_path) >= 2 and raw_path[1] == ":"):
        raise MeshGeometryUnavailable("Mesh geometry path must be relative.", status_code=404, code="path_escape")
    posix = PurePosixPath(raw_path.replace("\\", "/"))
    parts = posix.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise MeshGeometryUnavailable("Mesh geometry path escapes its directory.", status_code=404, code="path_escape")
    return Path(*parts)


def _check_no_symlink_components(base_dir: Path, relative_path: Path) -> None:
    """Reject any symlink along the path from base_dir to base_dir / relative_path."""
    current = base_dir
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise MeshGeometryUnavailable("Symlinked mesh artifact is not allowed.", status_code=404, code="path_escape")


def _resolve_contained_file(output_dir: Path, data_root: Path, raw_path: Any) -> Path:
    """Resolve an archive file strictly inside output_dir / sam3d-mesh-data with no symlink escape."""
    rel = _normalize_relative_subpath(raw_path)
    _check_no_symlink_components(output_dir, rel)
    candidate = output_dir / rel
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MeshGeometryUnavailable("Mesh geometry archive file is missing.", status_code=404, code="artifact_missing") from exc
    if resolved == output_dir or output_dir not in resolved.parents:
        raise MeshGeometryUnavailable("Mesh geometry file escapes job output directory.", status_code=404, code="path_escape")
    if resolved == data_root or data_root not in resolved.parents:
        raise MeshGeometryUnavailable("Mesh geometry file escapes mesh data directory.", status_code=404, code="path_escape")
    if not resolved.is_file():
        raise MeshGeometryUnavailable("Mesh geometry path is not a regular file.", status_code=404, code="artifact_missing")
    return resolved


def _resolve_job_roots(job: dict) -> tuple[Path, Path, Path]:
    """Validate job lifecycle state and return (output_dir, data_root, ledger_path)."""
    if not isinstance(job, dict):
        raise MeshGeometryUnavailable("Job record is unavailable.", status_code=404, code="job_not_found")
    if job.get("deleted_at") is not None:
        raise MeshGeometryUnavailable("Job has been deleted.", status_code=404, code="job_deleted")
    if job.get("cancel_requested"):
        raise MeshGeometryUnavailable("Job was cancelled.", status_code=409, code="job_cancelled")
    if job.get("status") != "succeeded":
        raise MeshGeometryUnavailable("Job has not succeeded yet.", status_code=409, code="job_not_succeeded")

    raw_output_dir = job.get("output_dir")
    if not isinstance(raw_output_dir, (str, Path)) or not str(raw_output_dir):
        raise MeshGeometryUnavailable("Job output directory is missing.", status_code=404, code="output_dir_missing")

    try:
        output_dir = Path(raw_output_dir).resolve(strict=True)
    except OSError as exc:
        raise MeshGeometryUnavailable("Job output directory does not exist.", status_code=404, code="output_dir_missing") from exc
    if not output_dir.is_dir():
        raise MeshGeometryUnavailable("Job output directory is invalid.", status_code=404, code="output_dir_missing")

    ledger_candidate = output_dir / LEDGER_NAME
    if ledger_candidate.is_symlink():
        raise MeshGeometryUnavailable("Mesh ledger symlink is not permitted.", status_code=404, code="path_escape")
    try:
        ledger_path = ledger_candidate.resolve(strict=True)
    except OSError as exc:
        raise MeshGeometryUnavailable("3D mesh geometry archive is not available for this job.", status_code=404, code="ledger_missing") from exc
    if ledger_path.parent != output_dir or not ledger_path.is_file():
        raise MeshGeometryUnavailable("Mesh ledger is invalid.", status_code=404, code="ledger_missing")
    try:
        ledger_size = ledger_path.stat().st_size
    except OSError as exc:
        raise MeshGeometryUnavailable("Mesh ledger cannot be read.", status_code=404, code="ledger_unreadable") from exc
    if ledger_size <= 0 or ledger_size > MAX_LEDGER_BYTES:
        raise MeshGeometryUnavailable("Mesh ledger size is out of bounds.", status_code=404, code="ledger_invalid")

    data_candidate = output_dir / DATA_DIR_NAME
    if data_candidate.is_symlink():
        raise MeshGeometryUnavailable("Mesh data directory symlink is not permitted.", status_code=404, code="path_escape")
    try:
        data_root = data_candidate.resolve(strict=True)
    except OSError as exc:
        raise MeshGeometryUnavailable("Mesh geometry data directory is missing.", status_code=404, code="data_dir_missing") from exc
    if data_root.parent != output_dir or not data_root.is_dir():
        raise MeshGeometryUnavailable("Mesh geometry data directory is invalid.", status_code=404, code="data_dir_missing")

    return output_dir, data_root, ledger_path


def _expected_video_sha256(job: dict) -> str | None:
    summary = job.get("summary")
    if not isinstance(summary, dict):
        return None
    loop = summary.get("minimum_scoring_loop")
    if not isinstance(loop, dict):
        return None
    provenance = loop.get("provenance")
    if not isinstance(provenance, dict):
        return None
    value = provenance.get("video_sha256")
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value.lower()
    return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _extract_row_timestamps(row: dict) -> tuple[float, float | None]:
    """Return (timeline_timestamp_ms, source_timestamp_ms) from a ledger row."""
    rel = _finite_number(row.get("relative_timestamp_ms"))
    src = _finite_number(row.get("timestamp_ms"))
    if src is None:
        pts = row.get("pts")
        tb_num = row.get("time_base_num")
        tb_den = row.get("time_base_den")
        if (
            type(pts) is int
            and type(tb_num) is int
            and type(tb_den) is int
            and tb_num > 0
            and tb_den > 0
        ):
            computed = (pts * tb_num / tb_den) * 1000.0
            if math.isfinite(computed):
                src = computed
    if rel is not None:
        return rel, src
    if src is not None:
        return src, src
    frame_idx = row.get("frame_index")
    if type(frame_idx) is int and frame_idx >= 0:
        return float(frame_idx) * (1000.0 / 30.0), None
    raise MeshGeometryUnavailable("Mesh ledger row lacks valid timing metadata.", status_code=404, code="ledger_invalid")


def _validate_artifact_meta(artifact: Any) -> dict:
    if not isinstance(artifact, dict):
        raise MeshGeometryUnavailable("Mesh ledger row lacks artifact metadata.", status_code=404, code="ledger_invalid")
    if artifact.get("version") != ARCHIVE_VERSION:
        raise MeshGeometryUnavailable("Unsupported SAM mesh artifact version.", status_code=404, code="unsupported_artifact")
    for digest_key in ("sha256", "faces_sha256"):
        digest = artifact.get(digest_key)
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise MeshGeometryUnavailable("Mesh artifact checksum metadata is invalid.", status_code=404, code="ledger_invalid")
    return artifact


def _read_ledger_entries(
    ledger_path: Path,
    output_dir: Path,
    data_root: Path,
    expected_video_sha: str | None,
) -> list[dict[str, Any]]:
    """Read, validate, and group ledger rows into one primary subject per frame."""
    by_frame: dict[int, list[dict[str, Any]]] = {}
    row_count = 0
    try:
        with ledger_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                if len(raw_line) > MAX_LEDGER_LINE_CHARS or row_count >= MAX_LEDGER_ROWS:
                    raise MeshGeometryUnavailable("Mesh ledger exceeds safe bounds.", status_code=404, code="ledger_invalid")
                stripped = raw_line.strip()
                if not stripped:
                    continue
                row_count += 1
                try:
                    row = json.loads(stripped)
                except ValueError as exc:
                    raise MeshGeometryUnavailable("Mesh ledger contains invalid JSON.", status_code=404, code="ledger_invalid") from exc
                if not isinstance(row, dict):
                    raise MeshGeometryUnavailable("Mesh ledger row is malformed.", status_code=404, code="ledger_invalid")

                row_sha = row.get("video_sha256")
                if expected_video_sha is not None:
                    if not isinstance(row_sha, str) or row_sha.lower() != expected_video_sha:
                        raise MeshGeometryUnavailable("Mesh ledger video digest does not match job provenance.", status_code=404, code="source_mismatch")

                frame_index = row.get("frame_index")
                if type(frame_index) is not int or frame_index < 0:
                    raise MeshGeometryUnavailable("Mesh ledger row has invalid frame_index.", status_code=404, code="ledger_invalid")

                artifact = _validate_artifact_meta(row.get("mesh_artifact"))
                # Validate containment for every row in the ledger upfront.
                mesh_file = _resolve_contained_file(output_dir, data_root, artifact.get("path"))
                faces_file = _resolve_contained_file(output_dir, data_root, artifact.get("faces_path"))
                timeline_ms, source_ms = _extract_row_timestamps(row)

                person_track_id = row.get("person_track_id")
                if person_track_id is not None and type(person_track_id) is not int:
                    person_track_id = None
                selection = row.get("subject_selection")
                primary_track_id = (
                    selection.get("primary_track_id")
                    if isinstance(selection, dict) and type(selection.get("primary_track_id")) is int
                    else None
                )
                is_primary = primary_track_id is not None and person_track_id == primary_track_id

                entry = {
                    "frame_index": frame_index,
                    "timestamp_ms": timeline_ms,
                    "source_timestamp_ms": source_ms,
                    "person_track_id": person_track_id,
                    "is_primary": is_primary,
                    "video_sha256": row_sha.lower() if isinstance(row_sha, str) and _SHA256_RE.fullmatch(row_sha) else None,
                    "mesh_file": mesh_file,
                    "mesh_sha256": artifact["sha256"].upper(),
                    "faces_file": faces_file,
                    "faces_sha256": artifact["faces_sha256"].upper(),
                }
                by_frame.setdefault(frame_index, []).append(entry)
    except MeshGeometryUnavailable:
        raise
    except OSError as exc:
        raise MeshGeometryUnavailable("Failed to read mesh ledger.", status_code=404, code="ledger_unreadable") from exc

    if not by_frame:
        raise MeshGeometryUnavailable("No 3D mesh geometry frames were archived.", status_code=404, code="no_mesh_frames")

    selected_per_frame: list[dict[str, Any]] = []
    for frame_index in sorted(by_frame):
        candidates = by_frame[frame_index]
        primary = next((item for item in candidates if item["is_primary"]), candidates[0])
        selected_per_frame.append(primary)

    # Ensure a single consistent shared topology across all returned frames.
    topology_counts = Counter(item["faces_sha256"] for item in selected_per_frame)
    dominant_topology = topology_counts.most_common(1)[0][0]
    consistent = [item for item in selected_per_frame if item["faces_sha256"] == dominant_topology]
    consistent.sort(key=lambda item: (item["timestamp_ms"], item["frame_index"]))
    return consistent


def _read_verified_bytes(path: Path, expected_sha256: str, label: str) -> bytes:
    """Single-read atomic snapshot with bounded size and SHA-256 verification."""
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size <= 0 or size > MAX_GEOMETRY_FILE_BYTES:
                raise MeshGeometryUnavailable(f"Mesh {label} file exceeds size limit.", status_code=404, code="artifact_oversized")
            payload = stream.read(size + 1)
            if len(payload) != size:
                raise MeshGeometryUnavailable(f"Mesh {label} file changed during read.", status_code=404, code="artifact_changed")
    except MeshGeometryUnavailable:
        raise
    except OSError as exc:
        raise MeshGeometryUnavailable(f"Mesh {label} file could not be read.", status_code=404, code="artifact_unreadable") from exc

    actual = hashlib.sha256(payload).hexdigest().upper()
    if actual != expected_sha256.upper():
        raise MeshGeometryUnavailable(f"Mesh {label} checksum verification failed.", status_code=404, code="checksum_mismatch")
    return payload


def _load_verified_faces(path: Path, expected_sha256: str) -> np.ndarray:
    raw = _read_verified_bytes(path, expected_sha256, "topology")
    try:
        faces = np.asarray(np.load(io.BytesIO(raw), allow_pickle=False))
    except Exception as exc:
        raise MeshGeometryUnavailable("Mesh topology array could not be decoded.", status_code=404, code="topology_invalid") from exc
    if (
        faces.ndim != 2
        or faces.shape[1] != 3
        or not 1 <= len(faces) <= MAX_FACES_PER_FRAME
        or faces.dtype.kind not in "iu"
    ):
        raise MeshGeometryUnavailable("Mesh topology has invalid shape or dtype.", status_code=404, code="topology_invalid")
    faces_i64 = faces.astype(np.int64, copy=False)
    if int(faces_i64.min()) < 0:
        raise MeshGeometryUnavailable("Mesh topology contains negative vertex index.", status_code=404, code="topology_invalid")
    return faces_i64


def _load_verified_frame_mesh(path: Path, expected_sha256: str, faces_max_index: int) -> tuple[np.ndarray, np.ndarray, float]:
    raw = _read_verified_bytes(path, expected_sha256, "geometry")
    try:
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            vertices = np.asarray(data["vertices"])
            camera = np.asarray(data["camera"])
            focal = np.asarray(data["focal"])
    except Exception as exc:
        raise MeshGeometryUnavailable("Mesh frame NPZ could not be decoded.", status_code=404, code="geometry_invalid") from exc

    if (
        vertices.ndim != 2
        or vertices.shape[1] != 3
        or not 3 <= len(vertices) <= MAX_VERTICES_PER_FRAME
        or vertices.dtype.kind != "f"
        or not np.isfinite(vertices).all()
    ):
        raise MeshGeometryUnavailable("Mesh frame vertices are invalid.", status_code=404, code="geometry_invalid")
    if faces_max_index >= len(vertices):
        raise MeshGeometryUnavailable("Mesh topology references out-of-range vertex.", status_code=404, code="topology_invalid")
    if camera.shape != (3,) or camera.dtype.kind not in "fiu" or not np.isfinite(camera).all() or float(camera[2]) <= 0.0:
        raise MeshGeometryUnavailable("Mesh frame camera translation is invalid.", status_code=404, code="geometry_invalid")
    if focal.size != 1 or not np.isfinite(focal).all() or float(focal.reshape(-1)[0]) <= 0.0:
        raise MeshGeometryUnavailable("Mesh frame focal length is invalid.", status_code=404, code="geometry_invalid")

    return vertices.astype(np.float64, copy=False), camera.astype(np.float64, copy=False), float(focal.reshape(-1)[0])


def _compact_topology(faces: np.ndarray, total_vertices: int) -> tuple[np.ndarray, np.ndarray]:
    """Strip unreferenced vertices and remap triangle indices to dense 0..V'-1."""
    used = np.unique(faces)
    remap = np.full(total_vertices, -1, dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    compacted = remap[faces]
    return used, compacted


def _spatial_cluster_faces(reference_vertices: np.ndarray, faces: np.ndarray, bins: int) -> np.ndarray:
    """Deterministic Rossignac-Borrel vertex-grid clustering preserving real estimator vertices."""
    vmin = reference_vertices.min(axis=0)
    vmax = reference_vertices.max(axis=0)
    max_span = float(np.max(vmax - vmin))
    if max_span <= 1e-12:
        return np.empty((0, 3), dtype=np.int64)
    step = max_span / float(bins)
    coords = np.floor((reference_vertices - vmin) / step).astype(np.int64)
    coords = np.clip(coords, 0, bins)
    stride = bins + 1
    keys = (coords[:, 0] * stride + coords[:, 1]) * stride + coords[:, 2]
    _, inverse = np.unique(keys, return_inverse=True)

    # Pick the exact original vertex closest to each grid cell's centroid so
    # output vertices remain unmodified estimator predictions without drift.
    counts = np.bincount(inverse).astype(np.float64)
    cx = np.bincount(inverse, weights=reference_vertices[:, 0]) / counts
    cy = np.bincount(inverse, weights=reference_vertices[:, 1]) / counts
    cz = np.bincount(inverse, weights=reference_vertices[:, 2]) / counts
    centroids = np.column_stack((cx, cy, cz))
    dist_sq = np.sum((reference_vertices - centroids[inverse]) ** 2, axis=1)
    vertex_ids = np.arange(len(reference_vertices), dtype=np.int64)
    order = np.lexsort((vertex_ids, dist_sq, inverse))
    sorted_inv = inverse[order]
    first_positions = np.concatenate(([0], np.flatnonzero(sorted_inv[1:] != sorted_inv[:-1]) + 1))
    rep_vertex = order[first_positions]

    clustered = inverse[faces]
    non_degenerate = (
        (clustered[:, 0] != clustered[:, 1])
        & (clustered[:, 1] != clustered[:, 2])
        & (clustered[:, 2] != clustered[:, 0])
    )
    valid = clustered[non_degenerate]
    if len(valid) == 0:
        return np.empty((0, 3), dtype=np.int64)

    # Deduplicate triangles sharing the same 3 cluster vertices while keeping original winding order.
    sorted_triples = np.sort(valid, axis=1)
    _, unique_idx = np.unique(sorted_triples, axis=0, return_index=True)
    deduped = valid[np.sort(unique_idx)]
    return rep_vertex[deduped]


def _decimate_and_compact(
    reference_vertices: np.ndarray,
    faces: np.ndarray,
    max_faces: int,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Deterministically decimate faces if > max_faces, then compact vertex table."""
    total_vertices = len(reference_vertices)
    if len(faces) <= max_faces:
        used, compacted = _compact_topology(faces, total_vertices)
        return used, compacted, False

    best_faces: np.ndarray | None = None
    vmin = reference_vertices.min(axis=0)
    vmax = reference_vertices.max(axis=0)
    if float(np.max(vmax - vmin)) > 1e-12:
        lo, hi = 4, 256
        for _ in range(9):
            if lo > hi:
                break
            mid = (lo + hi) // 2
            candidate = _spatial_cluster_faces(reference_vertices, faces, mid)
            count = len(candidate)
            if 0 < count <= max_faces:
                if best_faces is None or count > len(best_faces):
                    best_faces = candidate
                lo = mid + 1
            elif count == 0:
                lo = mid + 1
            else:
                hi = mid - 1

    min_acceptable = min(max_faces, max(1, int(max_faces * 0.2)))
    if best_faces is None or len(best_faces) < min_acceptable:
        # Deterministic evenly-spaced fallback for degenerate or ultra-low-budget meshes.
        pick = np.linspace(0, len(faces) - 1, max_faces, dtype=np.int64)
        best_faces = faces[pick]

    used, compacted = _compact_topology(best_faces, total_vertices)
    return used, compacted, True


def _stride_select(items: list[Any], limit: int) -> list[Any]:
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return [items[0]]
    indices = np.linspace(0, len(items) - 1, limit, dtype=np.int64)
    return [items[int(i)] for i in indices]


def _to_y_up_world(vertices: np.ndarray, camera: np.ndarray) -> np.ndarray:
    """Convert SAM 3D camera-space vertices to Y-up right-handed display coordinates.

    Matches ``OfficialMeshRenderer`` (180-degree rotation around X axis, det = +1),
    preserving outward triangle winding while placing +Y up and athlete facing +Z.
    """
    pts = vertices + camera
    pts[:, 1] = -pts[:, 1]
    pts[:, 2] = -pts[:, 2]
    return pts


def _quantize_vertices_int16(
    norm_vertices: np.ndarray,
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
) -> np.ndarray:
    """Quantize (V, 3) float vertices into symmetric little-endian Int16 [-32767, 32767]."""
    spans = bounds_max - bounds_min
    safe_spans = np.where(spans > 1e-12, spans, 1.0)
    normalized_01 = np.where(spans > 1e-12, (norm_vertices - bounds_min) / safe_spans, 0.5)
    quantized = np.rint(normalized_01 * QUANTIZATION_SPAN + QUANTIZATION_MIN)
    return np.clip(quantized, QUANTIZATION_MIN, QUANTIZATION_MAX).astype("<i2")


def _estimate_payload_bytes(vertex_count: int, face_count: int, frame_count: int) -> int:
    index_elem_bytes = 2 if vertex_count <= 65535 else 4
    indices_b64_len = ((face_count * 3 * index_elem_bytes + 2) // 3) * 4
    frame_v_b64_len = ((vertex_count * 3 * 2 + 2) // 3) * 4
    return 1536 + indices_b64_len + frame_count * (frame_v_b64_len + 180)


def mesh_geometry_availability(job: dict) -> bool:
    """Lightweight check indicating whether bounded 3D mesh geometry can be served."""
    try:
        output_dir, data_root, ledger_path = _resolve_job_roots(job)
        expected_sha = _expected_video_sha256(job)
        with ledger_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                if len(raw_line) > MAX_LEDGER_LINE_CHARS:
                    return False
                stripped = raw_line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if not isinstance(row, dict):
                    return False
                if expected_sha is not None:
                    row_sha = row.get("video_sha256")
                    if not isinstance(row_sha, str) or row_sha.lower() != expected_sha:
                        return False
                artifact = _validate_artifact_meta(row.get("mesh_artifact"))
                mesh_file = _resolve_contained_file(output_dir, data_root, artifact.get("path"))
                faces_file = _resolve_contained_file(output_dir, data_root, artifact.get("faces_path"))
                if mesh_file.stat().st_size <= 0 or faces_file.stat().st_size <= 0:
                    return False
                return True
        return False
    except (MeshGeometryUnavailable, OSError, ValueError, TypeError):
        return False


def build_mesh_geometry_payload(
    job: dict,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_faces: int = DEFAULT_MAX_FACES,
) -> dict[str, Any]:
    """Build a bounded, Int16-quantized 3D mesh clip payload from archived SAM 3D geometry."""
    if start_ms is not None and (isinstance(start_ms, bool) or not isinstance(start_ms, (int, float)) or not math.isfinite(start_ms) or start_ms < 0):
        raise MeshGeometryUnavailable("Invalid start_ms parameter.", status_code=400, code="invalid_time_range")
    if end_ms is not None and (isinstance(end_ms, bool) or not isinstance(end_ms, (int, float)) or not math.isfinite(end_ms) or end_ms < 0):
        raise MeshGeometryUnavailable("Invalid end_ms parameter.", status_code=400, code="invalid_time_range")
    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        raise MeshGeometryUnavailable("start_ms must not exceed end_ms.", status_code=400, code="invalid_time_range")
    if isinstance(max_frames, bool) or not isinstance(max_frames, int):
        raise MeshGeometryUnavailable("max_frames must be an integer.", status_code=400, code="invalid_parameter")
    if isinstance(max_faces, bool) or not isinstance(max_faces, int):
        raise MeshGeometryUnavailable("max_faces must be an integer.", status_code=400, code="invalid_parameter")

    clamped_max_frames = max(1, min(max_frames, MAX_ALLOWED_FRAMES))
    clamped_max_faces = max(1, min(max_faces, MAX_ALLOWED_FACES))

    output_dir, data_root, ledger_path = _resolve_job_roots(job)
    expected_sha = _expected_video_sha256(job)
    entries = _read_ledger_entries(ledger_path, output_dir, data_root, expected_sha)
    source_frame_count = len(entries)

    filtered = [
        item
        for item in entries
        if (start_ms is None or item["timestamp_ms"] >= start_ms)
        and (end_ms is None or item["timestamp_ms"] <= end_ms)
    ]
    if not filtered:
        raise MeshGeometryUnavailable(
            "No 3D mesh frames exist within the requested time window.",
            status_code=404,
            code="no_mesh_frames_in_range",
        )

    selected_entries = _stride_select(filtered, clamped_max_frames)

    # Load verified shared topology once.
    first_entry = selected_entries[0]
    raw_faces = _load_verified_faces(first_entry["faces_file"], first_entry["faces_sha256"])
    faces_max_index = int(raw_faces.max())
    original_face_count = int(len(raw_faces))

    # Load verified per-frame meshes.
    loaded_frames: list[tuple[dict[str, Any], np.ndarray, np.ndarray]] = []
    original_vertex_count: int | None = None
    for entry in selected_entries:
        vertices, camera, _ = _load_verified_frame_mesh(
            entry["mesh_file"], entry["mesh_sha256"], faces_max_index
        )
        if original_vertex_count is None:
            original_vertex_count = int(len(vertices))
        elif len(vertices) != original_vertex_count:
            raise MeshGeometryUnavailable(
                "Mesh vertex count changed across frames for shared topology.",
                status_code=404,
                code="topology_mismatch",
            )
        loaded_frames.append((entry, vertices, camera))

    assert original_vertex_count is not None

    # Use midpoint frame in Y-up coordinates as spatial clustering reference.
    mid_entry_vertices, mid_entry_camera = loaded_frames[len(loaded_frames) // 2][1:3]
    reference_world = _to_y_up_world(mid_entry_vertices, mid_entry_camera)
    used_vertex_indices, compacted_faces, decimated = _decimate_and_compact(
        reference_world, raw_faces, clamped_max_faces
    )
    vertex_count = int(len(used_vertex_indices))
    face_count = int(len(compacted_faces))

    # Enforce strict < 4 MiB serialized payload budget by reducing frame count if needed.
    index_elem_bytes = 2 if vertex_count <= 65535 else 4
    indices_b64_len = ((face_count * 3 * index_elem_bytes + 2) // 3) * 4
    frame_bytes_est = ((vertex_count * 3 * 2 + 2) // 3) * 4 + 180
    affordable_frames = (MAX_PAYLOAD_BYTES - 2048 - indices_b64_len) // max(1, frame_bytes_est)
    if affordable_frames < 1:
        raise MeshGeometryUnavailable(
            "Mesh geometry exceeds maximum payload budget.",
            status_code=404,
            code="payload_budget_exceeded",
        )
    if len(loaded_frames) > affordable_frames:
        loaded_frames = _stride_select(loaded_frames, int(affordable_frames))

    # Convert compacted frames into centered, unit-height Y-up coordinates.
    world_frames = np.stack(
        [
            _to_y_up_world(vertices[used_vertex_indices], camera)
            for _, vertices, camera in loaded_frames
        ],
        axis=0,
    )  # shape (T, V, 3)

    raw_min = world_frames.min(axis=(0, 1))
    raw_max = world_frames.max(axis=(0, 1))
    center = (raw_min + raw_max) * 0.5
    per_frame_heights = np.ptp(world_frames[:, :, 1], axis=1)
    median_height = float(np.median(per_frame_heights))
    max_extent = float(np.max(raw_max - raw_min))
    scale = median_height if median_height > 1e-6 else (max_extent if max_extent > 1e-6 else 1.0)

    normalized_frames = (world_frames - center) / scale
    bounds_min = normalized_frames.min(axis=(0, 1))
    bounds_max = normalized_frames.max(axis=(0, 1))

    if vertex_count <= 65535:
        index_type = "uint16"
        index_bytes = np.ascontiguousarray(compacted_faces, dtype="<u2").tobytes()
    else:
        index_type = "uint32"
        index_bytes = np.ascontiguousarray(compacted_faces, dtype="<u4").tobytes()
    indices_b64 = base64.b64encode(index_bytes).decode("ascii")

    encoded_frames: list[dict[str, Any]] = []
    for frame_idx, (entry, _, _) in enumerate(loaded_frames):
        quantized = _quantize_vertices_int16(normalized_frames[frame_idx], bounds_min, bounds_max)
        encoded_frames.append(
            {
                "frame_index": entry["frame_index"],
                "timestamp_ms": round(float(entry["timestamp_ms"]), 3),
                "source_timestamp_ms": (
                    round(float(entry["source_timestamp_ms"]), 3)
                    if entry["source_timestamp_ms"] is not None
                    else None
                ),
                "person_track_id": entry["person_track_id"],
                "vertices_b64": base64.b64encode(quantized.tobytes()).decode("ascii"),
            }
        )

    source_video_sha = expected_sha or first_entry["video_sha256"]
    payload: dict[str, Any] = {
        "version": GEOMETRY_PAYLOAD_VERSION,
        "job_id": str(job.get("id") or ""),
        "coordinate_system": "y_up_right_handed_centered",
        "source_video_sha256": source_video_sha,
        "source_frame_count": source_frame_count,
        "selected_frame_count": len(encoded_frames),
        "vertex_count": vertex_count,
        "face_count": face_count,
        "original_vertex_count": original_vertex_count,
        "original_face_count": original_face_count,
        "decimated": decimated,
        "bounds_min": [float(v) for v in bounds_min],
        "bounds_max": [float(v) for v in bounds_max],
        "normalization": {
            "center": [float(v) for v in center],
            "scale": float(scale),
        },
        "material": {
            "base_color_rgb": list(_MATERIAL_RGB),
        },
        "topology": {
            "index_type": index_type,
            "index_count": face_count * 3,
            "indices_b64": indices_b64,
        },
        "quantization": {
            "encoding": "int16_le_b64",
            "range_min": QUANTIZATION_MIN,
            "range_max": QUANTIZATION_MAX,
        },
        "frames": encoded_frames,
    }

    # Final defensive payload-size assertion (guarantees strictly < 4 MiB on wire).
    if _estimate_payload_bytes(vertex_count, face_count, len(encoded_frames)) > MAX_PAYLOAD_BYTES:
        serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(serialized) > MAX_PAYLOAD_BYTES:
            raise MeshGeometryUnavailable(
                "Mesh geometry payload exceeded 4 MiB limit.",
                status_code=404,
                code="payload_budget_exceeded",
            )

    return payload
