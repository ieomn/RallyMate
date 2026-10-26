"""Publish completed, source-bound official SAM mesh replay media."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any


MESH_ARTIFACTS = frozenset({
    "sam3d-mesh.mp4", "sam3d-mesh-side.mp4",
    "sam3d-mesh-preview.jpg", "sam3d-mesh-manifest.json",
})
MESH_VIDEOS = frozenset({"sam3d-mesh.mp4", "sam3d-mesh-side.mp4"})


def mesh_preview_descriptor(job: dict, public_base_url: str | None = None) -> dict[str, Any] | None:
    """Incomplete exports never become selectable, including after cancellation."""
    if job.get("status") != "succeeded" or job.get("cancel_requested"):
        return None
    summary = job.get("summary")
    if not isinstance(summary, dict):
        return None
    artifacts = summary.get("artifacts")
    mesh = artifacts.get("sam3d_mesh") if isinstance(artifacts, dict) else None
    if not isinstance(mesh, dict) or mesh.get("status") not in {"ready", "pending"}:
        return None
    output = Path(job["output_dir"]).resolve()
    artifact_sizes = {}
    try:
        for name in MESH_ARTIFACTS:
            path = output / name
            if path.resolve().parent != output or not path.is_file() or path.stat().st_size == 0:
                return None
            artifact_sizes[name] = path.stat().st_size
        manifest_path = output / "sam3d-mesh-manifest.json"
        if manifest_path.stat().st_size > 1024 * 1024:
            return None
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    if mesh.get("status") == "pending":
        task_sha = mesh.get("task_sha256")
        if (not isinstance(task_sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", task_sha)
                or manifest.get("source_job_id") != job.get("id")
                or manifest.get("task_sha256") != task_sha):
            return None
    files = manifest.get("files")
    if not isinstance(files, dict):
        return None
    for name in MESH_ARTIFACTS - {"sam3d-mesh-manifest.json"}:
        entry = files.get(name)
        if not isinstance(entry, dict) or type(entry.get("bytes")) is not int:
            return None
        if entry["bytes"] != artifact_sizes[name]:
            return None
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", entry["sha256"]):
            return None
    source_sha = manifest.get("source_video_sha256")
    loop = summary.get("minimum_scoring_loop")
    provenance = loop.get("provenance") if isinstance(loop, dict) else None
    expected_sha = provenance.get("video_sha256") if isinstance(provenance, dict) else None
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", source_sha):
        return None
    if not isinstance(expected_sha, str) or source_sha.lower() != expected_sha.lower():
        return None
    start, end, count = (manifest.get(key) for key in (
        "source_frame_start", "source_frame_end", "frame_count"))
    if any(type(value) is not int for value in (start, end, count)):
        return None
    if not (0 <= start < end and 0 < count <= end - start):
        return None
    origin = manifest.get("source_timestamp_origin_ms")
    relative_origin = manifest.get("source_relative_origin_ms")
    for timestamp in (origin, relative_origin):
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            return None
    if manifest.get("timing_preserved") is not True:
        return None
    renderer = manifest.get("renderer")
    if renderer != "official-sam3d-pyrender-persistent-context":
        return None
    base = (public_base_url or "").rstrip("/")
    route = f"{base}/v1/jobs/{job['id']}/artifacts/"
    return {
        "status": "ready",
        "video_url": route + "sam3d-mesh.mp4",
        "side_video_url": route + "sam3d-mesh-side.mp4",
        "preview_image_url": route + "sam3d-mesh-preview.jpg",
        "manifest_url": route + "sam3d-mesh-manifest.json",
        "source_video_sha256": source_sha.lower(),
        "source_frame_start": start, "source_frame_end": end, "frame_count": count,
        "source_timestamp_origin_ms": origin, "source_relative_origin_ms": relative_origin,
        "timing_preserved": True,
        "renderer": renderer,
    }


def mesh_preview_status(job: dict, descriptor: dict | None = None) -> str:
    if descriptor is not None:
        return "ready"
    summary = job.get("summary")
    if not isinstance(summary, dict):
        return "unavailable"
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        return "unavailable"
    mesh = artifacts.get("sam3d_mesh")
    if not isinstance(mesh, dict) or job.get("status") != "succeeded":
        return "unavailable"
    if mesh.get("status") == "failed":
        return "failed"
    if mesh.get("status") == "pending":
        state_path = Path(job["output_dir"]) / "sam3d-mesh-state.json"
        try:
            if state_path.is_file() and state_path.stat().st_size <= 64 * 1024:
                state = json.loads(state_path.read_text("utf-8"))
                if (isinstance(state, dict) and state.get("source_job_id") == job.get("id")
                        and state.get("task_sha256") == mesh.get("task_sha256")
                        and state.get("status") in {"failed", "cancelled"}):
                    return "failed"
        except (OSError, ValueError):
            pass
        return "pending"
    return "unavailable"
