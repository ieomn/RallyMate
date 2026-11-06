"""Re-render selected existing SAM surfaces without altering inference evidence.

Prepare and render write only a new staging directory. Publish is an explicit
separate operation, verifies unchanged inputs/media, backs up all replaced files,
and installs the manifest last. Native frames, geometry, scores and the original
mesh ledger are never replaced. Run with the installed SAM Python for rendering.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from rallymate_service.mesh_render_worker import RenderLock
from rallymate_vision.mesh_render_task import TASK_NAME, atomic_json
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import (LEDGER_NAME, MANIFEST_NAME, PREVIEW_NAME,
    SIDE_VIDEO_NAME, VIDEO_NAME, _read_ledger, render_mesh_subprocess)

VERSION = "sam3d-replay-repair-v1"
PLAN_NAME = "repair-plan.json"
DECISIONS_NAME = "repair-decisions.json"
RENDER_RECEIPT = "repair-render-receipt.json"
INPUTS = ("frames.jsonl", "sam3d_frames.jsonl", LEDGER_NAME)
MEDIA = (VIDEO_NAME, SIDE_VIDEO_NAME, PREVIEW_NAME)
PUBLISHED = (*MEDIA, MANIFEST_NAME)


def _read(path: Path, limit=32 * 1024 * 1024) -> dict:
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("invalid repair sidecar")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("repair sidecar must be an object")
    return value


def _digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _verify(path: Path, expected: str) -> None:
    if not _digest(expected) or path.is_symlink() or sha256_file(path).lower() != expected.lower():
        raise ValueError(f"repair input changed: {path.name}")


def _key(row):
    values = tuple(row.get(k) for k in ("frame_index", "person_track_id", "selection_epoch"))
    if any(type(v) is not int or v < 0 for v in values):
        raise ValueError("selection requires exact frame/track/epoch integer keys")
    return values


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents or path.is_symlink():
        raise ValueError("repair path escapes its owned directory")
    return resolved


def _idle(run: Path) -> None:
    database = run.parent.parent / "jobs.sqlite3"
    if not database.is_file():
        raise ValueError("cannot verify service queue before mesh repair")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        busy = connection.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        job = connection.execute("SELECT status,cancel_requested,output_dir FROM jobs WHERE id=?", (run.name,)).fetchone()
    if busy or not job or job[0] != "succeeded" or job[1] or Path(job[2]).resolve() != run:
        raise RuntimeError("mesh repair requires an idle service and a completed source job")


def prepare(run: Path, decisions_path: Path, staging: Path) -> dict:
    run, staging = run.resolve(), staging.absolute()
    if staging.exists() or run == staging or run in staging.parents or staging.resolve() != staging:
        raise ValueError("use a new staging directory outside the source job")
    decisions = _read(decisions_path)
    if decisions.get("version") != "sam3d-replay-selection-v1" or not isinstance(decisions.get("inputs"), dict):
        raise ValueError("unsupported replay decisions")
    for name in INPUTS:
        _verify(run / name, decisions["inputs"].get(name))
    task = _read(run / TASK_NAME)
    arguments = task["render"]
    if task.get("job_id") != run.name or Path(arguments["output_dir"]).resolve() != run:
        raise ValueError("source task identity mismatch")
    source = Path(arguments["source"]).resolve()
    _verify(source, decisions.get("source_video_sha256"))
    if arguments.get("source_sha256", "").lower() != decisions["source_video_sha256"].lower():
        raise ValueError("source task video mismatch")
    _verify(run / LEDGER_NAME, task.get("ledger_sha256"))
    requested = decisions.get("keep")
    if not isinstance(requested, list):
        raise ValueError("selection keep must be a list")
    keys = [_key(row) for row in requested]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate replay selection")
    keep = set(keys)
    rows = _read_ledger(run / LEDGER_NAME, run)
    counts = Counter(_key(row) for row in rows)
    if any(counts[key] != 1 for key in keep) or any(n != 1 for n in counts.values()):
        raise ValueError("selection does not map uniquely to the source ledger")
    selected = [row for row in rows if _key(row) in keep]
    if not selected:
        raise ValueError("repair selected no surfaces")
    snapshot = {name: sha256_file(run / name) for name in (*PUBLISHED, TASK_NAME)}
    staging.mkdir(parents=True)
    # Each original hash is verified before sharing an inode. No writes to
    # shared geometry occur anywhere in this tool or the renderer.
    linked = set()
    for row in selected:
        if row["video_sha256"].lower() != decisions["source_video_sha256"].lower():
            raise ValueError("selected row belongs to another video")
        artifact = row["mesh_artifact"]
        for path_field, hash_field in (("path", "sha256"), ("faces_path", "faces_sha256")):
            relative = Path(artifact[path_field])
            if relative.is_absolute():
                raise ValueError("archived mesh path must be relative")
            original = _inside(run / relative, run / "sam3d-mesh-data")
            destination = _inside(staging / relative, staging)
            _verify(original, artifact[hash_field])
            if destination in linked:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(original, destination)
            except OSError:
                shutil.copy2(original, destination)
            linked.add(destination)
    with (staging / LEDGER_NAME).open("w", encoding="utf-8", newline="\n") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    shutil.copy2(decisions_path, staging / DECISIONS_NAME)
    plan = {"version": VERSION, "source_run": str(run), "source": str(source),
        "source_video_sha256": decisions["source_video_sha256"], "source_inputs": decisions["inputs"],
        "source_snapshot": snapshot, "decisions_sha256": sha256_file(staging / DECISIONS_NAME),
        "filtered_ledger_sha256": sha256_file(staging / LEDGER_NAME),
        "original_subject_records": len(rows), "selected_subject_records": len(selected),
        "render": {**arguments, "output_dir": str(staging)}}
    atomic_json(staging / PLAN_NAME, plan)
    return plan


def _verify_plan(staging: Path, *, current_media=True) -> dict:
    plan = _read(staging / PLAN_NAME)
    if plan.get("version") != VERSION or Path(plan["render"]["output_dir"]).resolve() != staging:
        raise ValueError("invalid repair plan")
    run = Path(plan["source_run"]).resolve()
    if run == staging or run in staging.parents:
        raise ValueError("repair staging overlaps the source job")
    _verify(Path(plan["source"]), plan["source_video_sha256"])
    for name in INPUTS:
        _verify(run / name, plan["source_inputs"][name])
    for name, digest in plan["source_snapshot"].items():
        if current_media or name not in PUBLISHED:
            _verify(run / name, digest)
    _verify(staging / DECISIONS_NAME, plan["decisions_sha256"])
    _verify(staging / LEDGER_NAME, plan["filtered_ledger_sha256"])
    return plan


def _verify_pts(staging: Path, arguments: dict) -> None:
    import av
    expected, origin = [], None
    with av.open(arguments["source"]) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index > arguments["last_frame"]:
                break
            if index < arguments["first_frame"] or (index - arguments["first_frame"]) % arguments["stride"]:
                continue
            if frame.pts is None:
                raise ValueError("source frame lacks PTS")
            value = Fraction(frame.pts) * frame.time_base
            if origin is None:
                origin = value
            expected.append(value - origin)
    if not expected:
        raise ValueError("source selection is empty")
    for name in (VIDEO_NAME, SIDE_VIDEO_NAME):
        with av.open(str(staging / name)) as container:
            actual = [Fraction(frame.pts) * frame.time_base for frame in container.decode(video=0)]
        if actual != expected:
            raise ValueError("repaired replay PTS differ from the original source")


def render(staging: Path, *, renderer=None, check_idle=None, verify_pts=None) -> dict:
    staging = staging.resolve()
    plan = _verify_plan(staging)
    run = Path(plan["source_run"])
    idle = check_idle or (lambda: _idle(run))
    idle()
    lock = RenderLock(run.parent / ".sam3d-mesh-render.lock")
    if not lock.acquire():
        raise RuntimeError("another mesh renderer is active")
    try:
        idle()
        arguments = {**plan["render"]}
        for name in ("source", "output_dir", "runtime_dir"):
            arguments[name] = Path(arguments[name])
        manifest = (renderer or render_mesh_subprocess)(**arguments, check_cancelled=idle)
        _verify_plan(staging)
        if (manifest.get("status") != "ready" or manifest.get("timing_preserved") is not True
                or manifest.get("ledger_sha256", "").lower() != plan["filtered_ledger_sha256"].lower()
                or manifest.get("source_video_sha256", "").lower() != plan["source_video_sha256"].lower()):
            raise ValueError("rendered manifest does not match repair inputs")
        for name in MEDIA:
            _verify(staging / name, manifest["files"][name]["sha256"])
            if (staging / name).stat().st_size != manifest["files"][name]["bytes"]:
                raise ValueError("rendered file size mismatch")
        (verify_pts or _verify_pts)(staging, plan["render"])
        receipt = {"version": VERSION, "plan_sha256": sha256_file(staging / PLAN_NAME),
            "manifest_sha256": sha256_file(staging / MANIFEST_NAME),
            "files": {name: sha256_file(staging / name) for name in MEDIA},
            "exact_source_pts_verified": True, "inference_rerun": False}
        atomic_json(staging / RENDER_RECEIPT, receipt)
        return receipt
    finally:
        lock.close()


def _replace_copy(source: Path, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".mesh-repair-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def publish(staging: Path, *, check_idle=None) -> dict:
    staging = staging.resolve()
    plan = _verify_plan(staging)
    receipt = _read(staging / RENDER_RECEIPT)
    _verify(staging / PLAN_NAME, receipt.get("plan_sha256"))
    _verify(staging / MANIFEST_NAME, receipt.get("manifest_sha256"))
    if receipt.get("exact_source_pts_verified") is not True:
        raise ValueError("repair lacks full PTS verification")
    for name in MEDIA:
        _verify(staging / name, receipt["files"][name])
    run = Path(plan["source_run"])
    idle = check_idle or (lambda: _idle(run))
    idle()
    lock = RenderLock(run.parent / ".sam3d-mesh-render.lock")
    if not lock.acquire():
        raise RuntimeError("another mesh renderer is active")
    try:
        idle()
        _verify_plan(staging)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = run / "mesh-replay-backups" / identifier
        backup.mkdir(parents=True)
        for name in PUBLISHED:
            shutil.copy2(run / name, backup / name)
            _verify(backup / name, plan["source_snapshot"][name])
        filtered_name = f"sam3d-mesh-selected-{identifier}.jsonl"
        decisions_name = f"sam3d-mesh-selection-{identifier}.json"
        shutil.copy2(staging / LEDGER_NAME, run / filtered_name)
        shutil.copy2(staging / DECISIONS_NAME, run / decisions_name)
        manifest = _read(staging / MANIFEST_NAME)
        manifest.update(source_job_id=run.name, task_sha256=plan["source_snapshot"][TASK_NAME],
                        ledger=filtered_name)
        # ledger_sha256 remains the actual rendered ledger hash. The original
        # task ledger is separately named and hashed, never masquerading as it.
        manifest["replay_repair"] = {"version": VERSION, "inference_rerun": False,
            "source_ledger": LEDGER_NAME, "source_ledger_sha256": plan["source_inputs"][LEDGER_NAME],
            "source_inputs": plan["source_inputs"], "decisions": decisions_name,
            "decisions_sha256": plan["decisions_sha256"], "backup": str(backup.relative_to(run)),
            "selected_subject_records": plan["selected_subject_records"],
            "original_subject_records": plan["original_subject_records"],
            "exact_source_pts_verified": True,
            "scope": "visual_replay_only_original_native_and_scoring_evidence_unchanged"}
        try:
            for name in MEDIA:
                _replace_copy(staging / name, run / name)
            atomic_json(run / MANIFEST_NAME, manifest)
            _verify_plan(staging, current_media=False)
            for name in MEDIA:
                _verify(run / name, receipt["files"][name])
            if _read(run / MANIFEST_NAME) != manifest:
                raise ValueError("published repair manifest changed")
        except BaseException:
            for name in PUBLISHED:
                _replace_copy(backup / name, run / name)
            raise
        result = {"version": VERSION, "published": True, "backup": str(backup),
            "manifest_sha256": sha256_file(run / MANIFEST_NAME), "source_run": str(run)}
        atomic_json(staging / "repair-publish-receipt.json", result)
        return result
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--run", type=Path, required=True)
    create.add_argument("--decisions", type=Path, required=True)
    create.add_argument("--staging", type=Path, required=True)
    for command in ("render", "publish"):
        commands.add_parser(command).add_argument("--staging", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.run, args.decisions, args.staging)
        print(json.dumps({key: result[key] for key in ("version", "original_subject_records", "selected_subject_records")}))
    else:
        result = (render if args.command == "render" else publish)(args.staging)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
