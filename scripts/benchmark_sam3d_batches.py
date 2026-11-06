"""Bounded, source-bound SAM body batch benchmark; never a scoring accuracy test.

Run when the service worker is idle and GPU headroom is available. Model load,
source decode, warmup and comparison are separate from measured inference wall
time. Both paths include IPC, complete native keypoints and mesh capture.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import statistics
import time

import numpy as np

from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import archive_mesh_records
from rallymate_vision.sam3d_runner import Sam3DProcess, SourcePTSDecoder
try:
    from scripts.benchmark_idle import IdleJobsGuard
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    from benchmark_idle import IdleJobsGuard


BINDING_FIELDS = ("video_sha256", "frame_index", "processed_index", "pts", "time_base_num",
                  "time_base_den", "timestamp_ms", "person_track_id", "selection_epoch",
                  "camera_reference_epoch", "image_size", "bbox_px")
PROFILE = {
    "RALLYMATE_SAM3D_IPC_TRANSPORT": "shared_memory" if os.name == "nt" else "npy",
    "RALLYMATE_SAM3D_GPU_PREPROCESS": "1",
    "RALLYMATE_SAM3D_FORWARD_GRAPH": "backbone_decoder_layers",
    "RALLYMATE_SAM3D_CUDA_GRAPH": "1",
    "RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH": "1",
    "RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER": "1",
    "RALLYMATE_SAM3D_PROFILE_STAGES": "0",
    "RALLYMATE_SAM3D_REUSE_CUDA_CACHE": "0",
    "RALLYMATE_SAM3D_EXPORT_MESH": "1",
    "RALLYMATE_SAM3D_CPU_THREADS": "1",
    "RALLYMATE_SAM3D_INTEROP_THREADS": "1",
    "RALLYMATE_SAM3D_WAIT_MODE": "passive",
}


def build_worker_profile(*, profile, batch_sizes, max_players, batch_preprocess=None, environment=None):
    """Explicit CLI selection wins; otherwise preserve the environment or off."""
    environment = os.environ if environment is None else environment
    selected = environment.get("RALLYMATE_SAM3D_BATCH_PREPROCESS", "0") if batch_preprocess is None else batch_preprocess
    if type(selected) not in (int, str) or selected not in (0, 1, "0", "1"):
        raise ValueError("SAM batch preprocessing must be 0 or 1")
    cuda_wait = environment.get("RALLYMATE_SAM3D_CUDA_WAIT_MODE", "runtime_default")
    if cuda_wait not in ("runtime_default", "blocking_sync"):
        raise ValueError("SAM CUDA wait mode must be runtime_default or blocking_sync")
    settings = {**PROFILE,
                "RALLYMATE_SAM3D_BATCH_PERSONS": str(max(batch_sizes) * max_players),
                "RALLYMATE_SAM3D_BATCH_PREPROCESS": str(selected),
                "RALLYMATE_SAM3D_CUDA_WAIT_MODE": cuda_wait}
    if "RALLYMATE_SAM3D_BATCH_GRAPH_SIZES" in environment:
        settings["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"] = environment["RALLYMATE_SAM3D_BATCH_GRAPH_SIZES"]
    if profile == "eager":
        settings.update(RALLYMATE_SAM3D_CUDA_GRAPH="0", RALLYMATE_SAM3D_FORWARD_GRAPH="off",
                        RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH="0", RALLYMATE_SAM3D_BATCH_GRAPH_SIZES="")
    elif profile != "production":
        raise ValueError("Unknown SAM benchmark profile")
    return settings


def verify_batch_execution(outputs, requested_subjects, *, batch_preprocess):
    """Reject silent microbatch splitting or an ignored preprocessing switch."""
    subjects = [row for rows in outputs for row in rows]
    if len(subjects) != requested_subjects:
        raise ValueError("benchmark output subject count differs from the requested batch")
    for row in subjects:
        execution = row.get("batch_execution", {})
        if execution.get("subjects") != requested_subjects or execution.get("forward_calls") != 1:
            raise ValueError("benchmark requested a true batch but worker did not execute exactly one forward")
        receipt = execution.get("batch_preprocess", {})
        if not batch_preprocess:
            if receipt:
                raise ValueError("benchmark disabled grouped preprocessing but worker used it")
            continue
        if (not isinstance(receipt, dict)
                or receipt.get("candidate") != "official_crop_batched_uint8_staging_v1"
                or receipt.get("last_batch_subjects") != requested_subjects
                or receipt.get("last_image_uploads") != 1
                or type(receipt.get("batch_calls")) is not int or receipt["batch_calls"] < 1
                or receipt.get("image_uploads") != receipt["batch_calls"]):
            raise ValueError("benchmark requested grouped preprocessing but its single-upload receipt is absent or invalid")


def _existing_filesystem_device(path):
    """Find the target filesystem even before cache/artifact folders exist."""
    ancestor = Path(path).resolve()
    while not ancestor.exists():
        ancestor = ancestor.parent
    return ancestor.stat().st_dev


def validate_archive_filesystem(runtime_dir, artifact_root):
    """Native mesh archival uses atomic rename and requires one filesystem."""
    mesh_cache = Path(runtime_dir) / "cache" / "mesh"
    if _existing_filesystem_device(mesh_cache) != _existing_filesystem_device(artifact_root):
        raise ValueError("SAM benchmark mesh cache and artifact output must share a filesystem; "
                         "choose an output beside the runtime on Linux, then copy the report JSON separately")


@contextmanager
def worker_profile(overrides):
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def select_records(path: Path, *, count: int, start_frame: int, max_players: int) -> list[list[dict]]:
    selected: dict[int, list[dict]] = {}
    previous_index = -1
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            index = row.get("frame_index")
            if type(index) is not int or index < 0:
                continue
            if index < previous_index:
                raise ValueError("native archive is not in source-frame order")
            previous_index = index
            if len(selected) >= count and index not in selected:
                break
            if index < start_frame or row.get("status") != "ok":
                continue
            if any(field not in row for field in BINDING_FIELDS):
                raise ValueError("successful native row lacks source binding")
            box = np.asarray(row["bbox_px"], dtype=float)
            width, height = row["image_size"]
            if (box.shape != (4,) or not np.isfinite(box).all()
                    or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
                raise ValueError("successful native row has invalid source bbox")
            rows = selected.setdefault(index, [])
            if len(rows) < max_players:
                if any(item["person_track_id"] == row["person_track_id"] for item in rows):
                    raise ValueError("duplicate source frame/person binding")
                rows.append({field: row[field] for field in BINDING_FIELDS})
    if len(selected) != count:
        raise ValueError(f"Only {len(selected)} eligible frames; requested {count}")
    return list(selected.values())


def decode_bound_frames(video: Path, records: list[list[dict]], *, max_decode_frames: int,
                        max_source_bytes: int = 64 * 1024 * 1024) -> tuple[list[dict], float]:
    source_sha = sha256_file(video).lower()
    if any(row["video_sha256"].lower() != source_sha for rows in records for row in rows):
        raise ValueError("video bytes differ from the native archive source")
    by_index = {rows[0]["frame_index"]: rows for rows in records}
    if max(by_index) >= max_decode_frames:
        raise ValueError("selected source frame exceeds bounded decode limit")
    decoder = SourcePTSDecoder(video)
    frames, total_bytes = [], 0
    started = time.perf_counter()
    try:
        for index in range(max(by_index) + 1):
            ok, frame = decoder.read()
            if not ok:
                raise ValueError("source video ended before selected archive frames")
            if index not in by_index:
                continue
            rows = by_index[index]
            for row in rows:
                if any(row[key] != decoder.binding[key] for key in ("pts", "time_base_num", "time_base_den")):
                    raise ValueError("decoded PTS differs from the native archive")
                if row["image_size"] != [frame.shape[1], frame.shape[0]]:
                    raise ValueError("decoded source dimensions differ from archive")
            total_bytes += frame.nbytes
            if total_bytes > max_source_bytes:
                raise ValueError("selected source images exceed 64 MiB; use fewer frames")
            frames.append({"frame": frame, "boxes": [row["bbox_px"] for row in rows], "bindings": rows})
    finally:
        decoder.release()
    return frames, time.perf_counter() - started


def array_difference(reference, candidate) -> dict:
    reference, candidate = np.asarray(reference), np.asarray(candidate)
    if reference.shape != candidate.shape:
        return {"shape_equal": False, "reference_shape": list(reference.shape),
                "candidate_shape": list(candidate.shape)}
    if not np.isfinite(reference).all() or not np.isfinite(candidate).all():
        raise ValueError("non-finite geometry must not be accepted as benchmark output")
    delta = np.abs(reference.astype(np.float64) - candidate.astype(np.float64)).reshape(-1)
    return {"shape_equal": True, "exact": bool(np.array_equal(reference, candidate)),
            "max_abs": float(delta.max(initial=0)), "mean_abs": float(delta.mean()) if delta.size else 0.,
            "rms": float(np.sqrt(np.mean(delta * delta))) if delta.size else 0.,
            "p95_abs": float(np.quantile(delta, .95)) if delta.size else 0.}


def compare_snapshots(reference: list[dict], actual: list[dict]) -> dict:
    if len(reference) != len(actual):
        raise ValueError("batch changed the number of requested subjects")
    fields = ("keypoints_2d", "keypoints_3d", "vertices", "camera", "focal", "faces")
    per_subject = []
    for old, new in zip(reference, actual):
        if old["binding"] != new["binding"]:
            raise ValueError("batch changed source/person output ordering")
        values = {name: array_difference(old[name], new[name]) for name in fields}
        per_subject.append({"binding": old["binding"], "differences": values})
    return {"subjects": len(actual), "all_exact": all(
                item["differences"][name].get("exact", False) for item in per_subject for name in fields),
            "max_abs_by_field": {name: (max((item["differences"][name]["max_abs"]
                                              for item in per_subject), default=0.)
                                        if all(item["differences"][name]["shape_equal"]
                                               for item in per_subject) else None) for name in fields},
            "per_subject": per_subject,
            "semantics": "numerical_equivalence_only_not_ground_truth_or_scoring_acceptance"}


def snapshots(outputs: list[list[dict]], frames: list[dict], *, runtime: Path, archive: Path) -> list[dict]:
    if len(outputs) != len(frames):
        raise ValueError("batch output frame count mismatch")
    records = []
    for result_rows, frame in zip(outputs, frames):
        if len(result_rows) != len(frame["bindings"]):
            raise ValueError("batch output subject count mismatch")
        for result, binding in zip(result_rows, frame["bindings"]):
            if result.get("status") != "ok" or not result.get("mesh_artifact"):
                raise RuntimeError(f"Inference did not produce complete native geometry: {result.get('reason')}")
            records.append({**result, **binding})
    archive.mkdir(parents=True, exist_ok=True)
    archive_mesh_records(records, archive, runtime)
    summary, result = [], []
    for record in records:
        artifact = record["mesh_artifact"]
        with np.load(archive / artifact["path"], allow_pickle=False) as mesh:
            vertices, camera, focal = (np.array(mesh[key], copy=True) for key in ("vertices", "camera", "focal"))
        faces = np.load(archive / artifact["faces_path"], allow_pickle=False)
        binding = {key: record[key] for key in BINDING_FIELDS}
        result.append({"binding": binding, "keypoints_2d": np.asarray(record["keypoints_2d"]),
                       "keypoints_3d": np.asarray(record["keypoints_3d"]), "vertices": vertices,
                       "camera": camera, "focal": focal, "faces": faces})
        summary.append({**binding, **{key: value for key, value in record.items()
                                     if key not in BINDING_FIELDS}})
    (archive / "native-results.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    return result


def load_archived_snapshots(archive: Path) -> list[dict]:
    """Read complete benchmark outputs for cross-worker off/on comparisons.

    Geometry is read from the retained full mesh, never from a thumbnail or a
    selected subset. Verify archive ownership and hashes before comparison.
    """
    archive = archive.resolve()
    records = json.loads((archive / "native-results.json").read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("native benchmark archive must contain a record list")

    def verified_path(artifact, key):
        relative = Path(artifact[key])
        path = (archive / relative).resolve()
        if relative.is_absolute() or archive not in path.parents:
            raise ValueError("benchmark mesh path escaped its archive")
        digest_key = "sha256" if key == "path" else "faces_sha256"
        if sha256_file(path).upper() != artifact[digest_key].upper():
            raise ValueError("benchmark mesh checksum changed")
        return path

    result = []
    for record in records:
        if record.get("status") != "ok":
            raise ValueError("benchmark archive contains failed subjects")
        artifact = record["mesh_artifact"]
        with np.load(verified_path(artifact, "path"), allow_pickle=False) as mesh:
            vertices, camera, focal = (np.array(mesh[key], copy=True) for key in ("vertices", "camera", "focal"))
        faces = np.load(verified_path(artifact, "faces_path"), allow_pickle=False)
        result.append({"binding": {key: record[key] for key in BINDING_FIELDS},
                       "keypoints_2d": np.asarray(record["keypoints_2d"]),
                       "keypoints_3d": np.asarray(record["keypoints_3d"]),
                       "vertices": vertices, "camera": camera, "focal": focal, "faces": faces})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--native-jsonl", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True, help="SAM runtime Python")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--idle-jobs-db", type=Path, required=True)
    parser.add_argument("--idle-jobs-python", type=Path,
                        help="Optional native-OS Python for a persistent read-only jobs bridge")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-decode-frames", type=int, default=2000)
    parser.add_argument("--max-players", type=int, default=1)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--profile", choices=("production", "eager"), default="production")
    parser.add_argument("--batch-preprocess", type=int, choices=(0, 1), default=None,
                        help="Grouped uint8 crop staging: explicit 0/1 overrides environment; default env or 0")
    args = parser.parse_args()
    if (not 1 <= args.frames <= 16 or not 1 <= args.max_players <= 2 or args.start_frame < 0
            or not 1 <= args.repeats <= 5 or not 1 <= args.max_decode_frames <= 10000
            or any(not 1 <= size <= 8 or size * args.max_players > 8 for size in args.batch_sizes)):
        parser.error("frames 1..16; max-players 1..2; repeats 1..5; batches at most 8 subjects; bounded positive decode required")
    try:
        settings = build_worker_profile(profile=args.profile, batch_sizes=args.batch_sizes,
                                        max_players=args.max_players, batch_preprocess=args.batch_preprocess)
    except ValueError as error:
        parser.error(str(error))
    for path in (args.video, args.native_jsonl, args.python, args.checkpoint, args.idle_jobs_db):
        if not path.is_file():
            parser.error(f"Missing input file: {path}")
    if args.output.exists():
        parser.error("Use a new output report path to preserve previous benchmarks")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifact_root = args.output.parent / (args.output.stem + "-artifacts")
    if artifact_root.exists():
        parser.error("Benchmark artifact directory already exists; use a new output name")
    try:
        validate_archive_filesystem(args.runtime_dir, artifact_root)
    except ValueError as error:
        parser.error(str(error))
    artifact_root.mkdir()

    guard = IdleJobsGuard(args.idle_jobs_db, python=args.idle_jobs_python)
    check_idle = guard.check

    selected = select_records(args.native_jsonl, count=args.frames, start_frame=args.start_frame, max_players=args.max_players)
    frames, decode_seconds = decode_bound_frames(args.video, selected, max_decode_frames=args.max_decode_frames)
    sizes = list(dict.fromkeys([1, *args.batch_sizes]))
    report = {"schema_version": "sam3d-batch-benchmark-v1", "status": "running",
              "scope": "SAM_body_source_bound_native70_and_complete_mesh_including_IPC_not_end_to_end_scoring",
              "video": str(args.video.resolve()), "native_jsonl": str(args.native_jsonl.resolve()),
              "source_video_sha256": selected[0][0]["video_sha256"],
              "source_frames": selected, "source_image_bytes": sum(item["frame"].nbytes for item in frames),
              "source_decode_seconds_excluded": decode_seconds, "worker_profile": settings,
              "measured_call_scope": "CPU_RGB_and_official_crop_preprocessing_H2D_model_D2H_native_validation_mesh_capture_and_IPC; excludes_decode_load_warmup_comparison_archive_move",
              "batch_preprocess_selection": {"enabled": settings["RALLYMATE_SAM3D_BATCH_PREPROCESS"] == "1",
                                              "source": "explicit_cli" if args.batch_preprocess is not None else "environment_or_default_off"},
              "idle_guard": guard.receipt,
              "warmup": [], "trials": []}

    def save():
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")

    worker = None
    try:
        check_idle()
        with worker_profile(settings):
            started = time.perf_counter()
            worker = Sam3DProcess(args.python, args.checkpoint, runtime_dir=args.runtime_dir,
                                 device=args.device, inference_type="body")
            report["process_load_seconds_excluded"] = time.perf_counter() - started
            report["worker_metadata"] = worker.metadata

        def run(items, size):
            outputs, call_seconds = [], []
            for begin in range(0, len(items), size):
                check_idle()
                chunk = items[begin:begin + size]
                started = time.perf_counter()
                if size == 1:
                    chunk_outputs = [worker.infer(chunk[0]["frame"], chunk[0]["boxes"])]
                else:
                    chunk_outputs = worker.infer_batch([
                        {"frame": item["frame"], "boxes": item["boxes"]} for item in chunk])
                call_seconds.append(time.perf_counter() - started)
                requested_subjects = sum(len(item["boxes"]) for item in chunk)
                if size > 1 and requested_subjects > 1:
                    verify_batch_execution(chunk_outputs, requested_subjects,
                        batch_preprocess=settings["RALLYMATE_SAM3D_BATCH_PREPROCESS"] == "1")
                outputs.extend(chunk_outputs)
            return outputs, call_seconds

        for size in sizes:
            warm_frames = frames[:min(len(frames), size)]
            outputs, calls = run(warm_frames, size)
            snapshots(outputs, warm_frames, runtime=args.runtime_dir, archive=artifact_root / f"warmup-{size}")
            report["warmup"].append({"batch_size": size, "frames": len(warm_frames), "wall_seconds": sum(calls)})
            save()
        reference = None
        for repeat in range(args.repeats):
            for size in sizes if repeat % 2 == 0 else list(reversed(sizes)):
                outputs, calls = run(frames, size)
                current = snapshots(outputs, frames, runtime=args.runtime_dir,
                                    archive=artifact_root / f"trial-{repeat + 1}-batch-{size}")
                if reference is None:
                    reference = current
                elapsed = sum(calls)
                flat = [row for rows in outputs for row in rows]
                trial = {"repeat": repeat, "batch_size": size, "source_frames": len(frames),
                         "subjects": len(flat), "wall_seconds": elapsed, "call_seconds": calls,
                         "frames_per_second": len(frames) / elapsed, "subjects_per_second": len(flat) / elapsed,
                         "peak_cuda_allocated_bytes": max(row.get("peak_memory_bytes", 0) for row in flat),
                         "peak_cuda_reserved_bytes": max(row.get("peak_reserved_memory_bytes", 0) for row in flat),
                         "difference_from_first_single_frame_trial": compare_snapshots(reference, current)}
                report["trials"].append(trial)
                save()
                print(f"batch={size}, repeat={repeat + 1}, SAM frames/s={trial['frames_per_second']:.3f}", flush=True)
        report["summary"] = [{"batch_size": size, "median_frames_per_second": statistics.median(
            row["frames_per_second"] for row in report["trials"] if row["batch_size"] == size),
            "all_exact": all(row["difference_from_first_single_frame_trial"]["all_exact"]
                             for row in report["trials"] if row["batch_size"] == size)} for size in sizes]
        report["status"] = "completed_numerical_report_not_scoring_acceptance"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            if worker is not None:
                worker.close()
        finally:
            guard.close()
            save()
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
