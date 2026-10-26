"""Durable, source-bound visualization requests; inference never renders them."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from rallymate_vision.pose.metadata import sha256_file

TASK_VERSION = "sam3d-mesh-background-task-v1"
TASK_NAME = "sam3d-mesh-task.json"
STATE_NAME = "sam3d-mesh-state.json"
CANCEL_NAME = "sam3d-mesh-cancel.json"


def atomic_json(path: Path, value: dict) -> None:
    """Replace only this small sidecar, never a summary or inference artifact."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                     suffix=".tmp", mode="w", encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_mesh_render_task(*, job_id: str, source: Path, output_dir: Path, runtime_dir: Path,
                            first_frame: int, last_frame: int, stride: int,
                            source_sha256: str, model: dict) -> dict:
    from rallymate_vision.sam3d_mesh import LEDGER_NAME, MANIFEST_NAME, VERSION

    ledger_sha = sha256_file(output_dir / LEDGER_NAME)
    task = {"version": TASK_VERSION, "job_id": job_id, "ledger_sha256": ledger_sha,
            "render": {"source": str(source.resolve()), "output_dir": str(output_dir.resolve()),
                       "runtime_dir": str(runtime_dir.resolve()), "first_frame": first_frame,
                       "last_frame": last_frame, "stride": stride, "source_sha256": source_sha256,
                       "model": model}}
    path = output_dir / TASK_NAME
    atomic_json(path, task)
    return {"version": VERSION, "status": "pending", "geometry": "official_estimator_pred_vertices",
            "source_job_id": job_id, "source_video_sha256": source_sha256,
            "source_frame_start": first_frame, "source_frame_end": last_frame + 1,
            "source_frame_end_semantics": "exclusive", "frame_stride": stride,
            "ledger": LEDGER_NAME, "ledger_sha256": ledger_sha,
            "task": TASK_NAME, "task_sha256": sha256_file(path), "state": STATE_NAME,
            "manifest": MANIFEST_NAME, "model": model,
            "message_zh": "评分已先行完成；官方人体表面预览将在显卡空闲时生成。"}
