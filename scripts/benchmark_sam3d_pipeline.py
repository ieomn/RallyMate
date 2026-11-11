"""Isolated source-bound SAM pipeline paired/ABBA benchmark; never submits service jobs.

Run with ``python -m scripts.benchmark_sam3d_pipeline`` when the service is idle.
Model loading, source validation, warmup and artifact comparison are excluded
from measured pipeline wall time. Reports are evidence, not scoring approval.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_sam3d_batches import BINDING_FIELDS, add_runtime_profile_arguments, build_worker_profile, snapshots, validate_archive_filesystem, verify_batch_execution, worker_profile
from scripts.benchmark_idle import IdleJobsGuard
from scripts.sam3d_pipeline_comparison import compare_pipeline
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_runner import SourcePTSDecoder
from rallymate_vision.sam3d_limits import (
    MAX_BATCH_SOURCE_BYTES, MIN_AUTO_SOURCE_BYTES, automatic_source_buffer_bytes,
    configured_source_buffer_bytes,
)


ROOT = Path(__file__).resolve().parents[1]


class IdleGuard:
    """Read-only queue guard; bounded polling also runs inside the pipeline."""
    def __init__(self, database: Path, python: Path | None = None):
        self.database = database.resolve()
        self.reader = IdleJobsGuard(self.database, python=python)
        self.last_check = float("-inf")
        self.polls = 0
        self.database_seconds = 0.

    def __call__(self, *, force=False):
        now = time.perf_counter()
        if not force and now - self.last_check < .25:
            return
        started = time.perf_counter()
        self.reader.check()
        self.database_seconds += time.perf_counter() - started
        self.polls += 1
        self.last_check = now

    def close(self):
        self.reader.close()


MAX_WARMUP_SOURCE_BYTES = MAX_BATCH_SOURCE_BYTES
MAX_BENCHMARK_FRAMES = 72_000
MAX_NATIVE_LINE_BYTES = 8 * 1024 * 1024


def close_benchmark_perception(perception, gpu_slot):
    """Quarantine unconfirmed cleanup even when the close error is unexpected."""
    if perception is None:
        return
    try:
        perception.close()
    except BaseException as error:
        if gpu_slot is not None:
            gpu_slot.quarantine(f'{type(error).__name__}: {error}')
        raise


def select_pipeline_records(path: Path, *, count: int, start_frame: int = 0,
                            max_players: int = 2, guard=lambda: None) -> list[list[dict]]:
    """Retain compact bindings for a continuous interval, including empty frames.

    Unlike the pose-only microbenchmark, a pipeline benchmark may not skip
    native ``skipped`` records. Missing records are errors, not empty frames.
    """
    if (type(count) is not int or count < 1 or type(start_frame) is not int
            or start_frame < 0 or start_frame + count > MAX_BENCHMARK_FRAMES
            or type(max_players) is not int or not 1 <= max_players <= 4):
        raise ValueError("Invalid bounded pipeline source interval or player limit")
    selected, previous, identities = {}, -1, set()
    before = path.stat()
    with path.open("rb") as stream:
        while line := stream.readline(MAX_NATIVE_LINE_BYTES + 1):
            guard()
            if len(line) > MAX_NATIVE_LINE_BYTES:
                raise ValueError("Native archive row exceeds bounded line size")
            if not line.strip():
                continue
            row = json.loads(line)
            index = row.get("frame_index")
            if type(index) is not int or index < 0 or index < previous:
                raise ValueError("Native archive is not in source-frame order")
            previous = index
            if index >= start_frame + count:
                break
            if index < start_frame:
                continue
            common = ("video_sha256", "frame_index", "processed_index", "pts", "time_base_num",
                      "time_base_den", "timestamp_ms", "image_size", "status")
            if any(key not in row for key in common):
                raise ValueError("Native row lacks source binding")
            if (not isinstance(row["video_sha256"], str) or len(row["video_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["video_sha256"].lower())
                    or any(type(row[key]) is not int for key in ("pts", "time_base_num", "time_base_den"))
                    or row["time_base_num"] <= 0 or row["time_base_den"] <= 0
                    or type(row["processed_index"]) is not int or row["processed_index"] < 0
                    or type(row["timestamp_ms"]) not in (int, float) or not math.isfinite(row["timestamp_ms"])
                    or abs(row["timestamp_ms"] - row["pts"] * row["time_base_num"] / row["time_base_den"] * 1000) > .51
                    or not isinstance(row["image_size"], list) or len(row["image_size"]) != 2
                    or any(type(value) is not int or value <= 0 for value in row["image_size"])):
                raise ValueError("Native source binding has invalid SHA, PTS or dimensions")
            status = row["status"]
            if status not in ("ok", "skipped", "error", "failed"):
                raise ValueError("Native archive has an unknown inference status")
            if status == "ok":
                if any(key not in row for key in BINDING_FIELDS):
                    raise ValueError("Successful native row lacks source binding")
                box = row["bbox_px"]
                width, height = row["image_size"]
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in box)
                        or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height
                        or type(row["person_track_id"]) is not int or row["person_track_id"] < 0):
                    raise ValueError("Successful native row has invalid source bbox or person")
            identity = (index, row.get("person_track_id"))
            if identity in identities:
                raise ValueError("Duplicate source frame/person binding")
            identities.add(identity)
            group = selected.setdefault(index, [])
            if group and any(row[key] != group[0][key] for key in
                             ("video_sha256", "pts", "time_base_num", "time_base_den", "image_size")):
                raise ValueError("Subjects disagree about the source frame binding")
            group.append({key: row[key] for key in set(BINDING_FIELDS) | {"status", "reason"} if key in row})
            if len(group) > 4:
                raise ValueError("Native archive exceeds supported four-person envelope")
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Native archive changed during source preparation")
    if list(selected) != list(range(start_frame, start_frame + count)):
        raise ValueError("Native archive must bind every requested source frame without gaps")
    # Keep every source row for validation. max_players controls the experiment,
    # not selection of convenient successful frames from the reference run.
    return list(selected.values())


def _warmup_subjects(group, max_players=2):
    return [row for row in group if row.get("status", "ok") == "ok"][:max_players]


def parse_graph_sizes(raw: str) -> tuple[int, ...]:
    values = raw.split(",")
    if (not 1 <= len(values) <= 2 or any(not value.isascii() or not value.isdecimal()
                                       or not 2 <= int(value) <= 8 for value in values)):
        raise ValueError("graph sizes must contain one or two distinct integers in [2,8]")
    result = tuple(int(value) for value in values)
    if len(set(result)) != len(result):
        raise ValueError("graph sizes must be distinct")
    return result


def parse_trial_order(raw: str | None, candidate: int) -> tuple[int, ...]:
    if raw is None:
        return (1, candidate, candidate, 1)
    values = raw.split(",")
    if any(not value.isascii() or not value.isdecimal() for value in values):
        raise ValueError("trial order must be comma-separated batch frame sizes")
    order = tuple(map(int, values))
    if order not in ((1, candidate), (candidate, 1),
                     (1, candidate, candidate, 1), (candidate, 1, 1, candidate)):
        raise ValueError("trial order must be AB, BA, ABBA or BAAB using 1 and the candidate")
    return order


def _budget_mib(value, name):
    raw = os.environ.get(name, "512") if value is None else str(value)
    raw = raw.strip()
    if (not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 4
            or not 128 <= int(raw) <= 4096):
        raise ValueError(f"{name} must be an integer in [128,4096] MiB")
    return str(int(raw))


def scheduling_trials(experiment, candidate, pending):
    """Compare one scheduling change or an explicitly declared combined candidate."""
    if experiment not in ("pipeline-overlap", "archive-queue", "combined-scheduling"):
        raise ValueError("Unknown scheduling experiment")
    return [{"batch_frames": candidate,
             "pipeline_overlap": int(changed) if experiment in ("pipeline-overlap", "combined-scheduling") else 0,
             "archive_pending_frames": pending if experiment == "pipeline-overlap" else (8 if changed else 2),
             "configuration_label": "candidate" if changed else "baseline"}
            for changed in (False, True, True, False)]


def pipeline_settings(runtime: Path, sam_python: Path, batch_preprocess: int, *,
                      graph_sizes=(8,), forward_graph_budget_mib=None,
                      head_graph_budget_mib=None, archive_pending_frames=None,
                      reuse_cuda_cache=None, profile_stages=None, async_mesh_capture=None) -> dict:
    if type(batch_preprocess) is not int or batch_preprocess not in (0, 1):
        raise ValueError("batch preprocessing must be explicitly 0 or 1")
    profile = build_worker_profile(profile="production", batch_sizes=[1, 4], max_players=2,
        batch_preprocess=batch_preprocess, reuse_cuda_cache=reuse_cuda_cache,
        profile_stages=profile_stages, async_mesh_capture=async_mesh_capture)
    source_budget = configured_source_buffer_bytes(os.environ)
    if source_budget is not None:
        profile["RALLYMATE_SAM3D_SOURCE_BUFFER_MIB"] = str(source_budget // (1024 * 1024))
    graphs = parse_graph_sizes(",".join(str(value) for value in graph_sizes))
    decoder_raw = os.environ.get("RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES", "").strip()
    decoder_graphs = parse_graph_sizes(decoder_raw) if decoder_raw else ()
    pending = (os.environ.get("RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES", "2")
               if archive_pending_frames is None else str(archive_pending_frames))
    if pending not in {"2", "4", "8"}:
        raise ValueError("RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES must be 2, 4 or 8")
    return {**profile, "RALLYMATE_SAM3D_PYTHON": str(sam_python),
            "RALLYMATE_SAM3D_RUNTIME": str(runtime), "RALLYMATE_SAM3D_BATCH_PERSONS": "8",
            "RALLYMATE_SAM3D_BATCH_GRAPH_SIZES": ",".join(map(str, graphs)), "RALLYMATE_SAM3D_BATCH_FRAMES": "1",
            "RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES": ",".join(map(str, decoder_graphs)),
            "RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB": _budget_mib(forward_graph_budget_mib, "RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB"),
            "RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB": _budget_mib(head_graph_budget_mib, "RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB"),
            "RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES": pending,
            "RALLYMATE_SAM3D_PIPELINE_OVERLAP": "0",
            "RALLYMATE_SAM3D_BATCH_PREPROCESS": str(batch_preprocess),
            "RALLYMATE_SAM3D_PREFETCH_FRAMES": "2", "RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE": "1",
            "RALLYMATE_DETECT_BATCH_SIZE": "1", "RALLYMATE_SAM3D_MODE": "body"}


def validate_output_root(output: Path, jobs_db: Path) -> Path:
    output, service_root = output.resolve(), jobs_db.resolve().parent
    if output == service_root or service_root in output.parents:
        raise ValueError("Benchmark output must be outside the production service data directory")
    if output.exists():
        raise ValueError("Use a new output root; previous artifacts must not be overwritten")
    return output


def candidate_subject_shapes(selected, batch_frames, max_source_bytes=None, *, max_players=2):
    """Expected BODY microbatch shapes from source-bound people and image limits."""
    if type(batch_frames) is not int or not 1 <= batch_frames <= 8:
        raise ValueError("Candidate batch frames must be in [1,8]")
    shapes, pending_people, pending_frames, pending_bytes = set(), 0, 0, 0
    budget = MIN_AUTO_SOURCE_BYTES if max_source_bytes is None else max_source_bytes
    def flush():
        if pending_people:
            if pending_frames == 1:
                # Pipeline's one-frame path uses scalar inference per person.
                shapes.add(1)
            else:
                shapes.update(min(8, pending_people - start) for start in range(0, pending_people, 8))
    for group in selected:
        width, height = group[0]["image_size"]
        size = width * height * 3
        if max_source_bytes is None:
            budget = max(budget, automatic_source_buffer_bytes(size, batch_frames))
        if pending_frames and pending_bytes + size > budget:
            flush()
            pending_people, pending_frames, pending_bytes = 0, 0, 0
        pending_people += len(_warmup_subjects(group, max_players))
        pending_frames += 1
        pending_bytes += size
        if pending_frames >= batch_frames or pending_bytes >= budget:
            flush()
            pending_people, pending_frames, pending_bytes = 0, 0, 0
    flush()
    return sorted(shapes)


def prepare_source(video: Path, archive: Path, count: int, guard, *, batch_frames=4,
                   start_frame=0, max_players=2) -> tuple[dict, list]:
    """Check all selected PTS; retain at most eight images and 256 MiB for warmup."""
    guard(force=True)
    selected = select_pipeline_records(archive, count=count, start_frame=start_frame,
                                       max_players=max_players, guard=guard)
    if [group[0]["frame_index"] for group in selected] != list(range(start_frame, start_frame + count)):
        raise ValueError("Native archive must bind every requested source frame without gaps")
    source_sha = sha256_file(video).lower()
    if any(row["video_sha256"].lower() != source_sha for group in selected for row in group):
        raise ValueError("Video bytes differ from the existing native source archive")
    source_budget = configured_source_buffer_bytes(os.environ)
    shapes = candidate_subject_shapes(selected, batch_frames,
        max_source_bytes=source_budget, max_players=max_players)
    warmup_budget = source_budget or MIN_AUTO_SOURCE_BYTES
    if not shapes:
        raise ValueError("Source interval has no successful real subjects for model warmup")
    decoder = SourcePTSDecoder(video)
    warmup, warmup_bytes, origin_ms, previous_ms, start_ms = [], 0, None, None, None
    try:
        for index in range(start_frame + count):
            guard()
            ok, frame = decoder.read()
            if not ok:
                raise ValueError("Video ended before the requested bound frame prefix")
            decoded = decoder.binding
            pts_ms = decoded.get("timestamp_ms")
            if pts_ms is None:
                pts_ms = round(decoded["pts"] * decoded["time_base_num"] / decoded["time_base_den"] * 1000)
            if origin_ms is None:
                origin_ms = pts_ms
            relative_ms = round(pts_ms - origin_ms)
            if relative_ms < 0 or previous_ms is not None and relative_ms <= previous_ms:
                raise ValueError("Source interval requires strictly increasing decoder timestamps")
            previous_ms = relative_ms
            if index < start_frame:
                continue
            if start_ms is None:
                start_ms = relative_ms
            group = selected[index - start_frame]
            for row in group:
                if any(row[key] != decoder.binding[key] for key in ("pts", "time_base_num", "time_base_den")):
                    raise ValueError("Decoded PTS differs from the existing native archive")
                if row["image_size"] != [frame.shape[1], frame.shape[0]]:
                    raise ValueError("Decoded dimensions differ from the existing native archive")
            subjects = _warmup_subjects(group, max_players)
            if source_budget is None:
                warmup_budget = max(warmup_budget, automatic_source_buffer_bytes(frame.nbytes, batch_frames))
            needs_images = (len(warmup) < min(batch_frames, count)
                            or sum(len(item["boxes"]) for item in warmup) < max(shapes))
            if subjects and needs_images and len(warmup) < 8 and warmup_bytes + frame.nbytes <= warmup_budget:
                warmup_bytes += frame.nbytes
                warmup.append({"frame": frame, "boxes": [row["bbox_px"] for row in subjects], "bindings": subjects})
    finally:
        decoder.release()
    guard(force=True)
    if not warmup or sum(len(item["boxes"]) for item in warmup) < max(shapes):
        raise ValueError("Cannot warm every expected candidate shape within eight source frames / 256 MiB")
    return {"video_sha256": source_sha, "frames": selected, "start_frame": start_frame,
            "start_ms": start_ms, "end_frame_exclusive": start_frame + count,
            "max_players": max_players, "decoded_frames_excluded": start_frame + count,
            "empty_source_frames": sum(not _warmup_subjects(group, max_players) for group in selected),
            "warmup_image_bytes": warmup_bytes,
            "warmup_source_byte_budget": warmup_budget,
            "warmup_source_frame_count": len(warmup), "warmup_expected_subject_shapes": shapes,
            "warmup_shapes_basis": "native archive source-bound subjects; production source-byte budget and <=8-person forwards"}, warmup


def warmup_group(frames, subjects):
    """Select real bound crops, without duplicating frames or inventing people."""
    if type(subjects) is not int or not 1 <= subjects <= 8:
        raise ValueError("Warmup forward must contain 1..8 subjects")
    result, remaining = [], subjects
    for frame in frames:
        take = min(remaining, len(frame["boxes"]))
        if take:
            result.append({**frame, "boxes": frame["boxes"][:take], "bindings": frame["bindings"][:take]})
            remaining -= take
        if not remaining:
            return result
    raise ValueError("Warmup source pool cannot supply the requested real subjects")


def run_pose_warmup(engine, frames, shapes, *, guard, runtime, output, batch_preprocess):
    """One verified call per distinct expected shape, plus scalar shape 1.

    A candidate with 16 people uses two <=8-person forwards in production. Its
    representative warmup is one shape-8 call, never a claimed 16-person forward.
    """
    receipts = []
    for shape in sorted({1, *shapes}):
        group = warmup_group(frames, shape)
        guard(force=True)
        started = time.perf_counter()
        if shape == 1:
            outputs = [engine.infer(group[0]["frame"], group[0]["boxes"])]
        else:
            outputs = engine.infer_batch([{"frame": item["frame"], "boxes": item["boxes"]} for item in group])
            verify_batch_execution(outputs, shape, batch_preprocess=batch_preprocess)
        guard(force=True)
        snapshots(outputs, group, runtime=runtime, archive=output / f"warmup-subjects-{shape}")
        receipts.append({"subjects": shape, "source_frames": len(group), "forward_calls": 1,
                         "source_frame_indices": [item["bindings"][0]["frame_index"] for item in group],
                         "wall_seconds_excluded": time.perf_counter() - started,
                         "true_batch_receipt_verified": shape > 1})
    return {"calls": receipts, "scope": "one representative verified forward per unique expected shape; no timed inference",
            "source_frame_count_retained": len(frames), "source_bytes_retained": sum(item["frame"].nbytes for item in frames)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--frames", type=int, default=128, help="Continuous source length, 4..72000 (default 128); no frame skipping")
    parser.add_argument("--start-frame", type=int, default=0, help="First source frame; start+frames must be <=72000; prefix decode is timed separately during preparation")
    parser.add_argument("--max-players", type=int, choices=(1, 2, 3, 4), default=2,
                        help="Same player limit for both ABBA configurations (default 2)")
    parser.add_argument("--batch-frames", type=int, choices=(4, 8), default=4,
                        help="Candidate frame envelope for ABBA 1,N,N,1; person microbatch limit remains 8")
    parser.add_argument("--graph-sizes", default="8", help="One or two distinct person graph shapes, 2..8 (default 8)")
    parser.add_argument("--trial-order", help="Explicit AB/BA pair or ABBA/BAAB sequence, e.g. 1,8; default 1,N,N,1")
    parser.add_argument("--schedule-abba", choices=("pipeline-overlap", "archive-queue", "combined-scheduling"),
                        help="Compare named scheduling changes at fixed --batch-frames, baseline/candidate/candidate/baseline")
    parser.add_argument("--forward-graph-budget-mib", type=int, help="Extra forward graph retained pool: 128..4096 MiB; default environment or 512")
    parser.add_argument("--head-graph-budget-mib", type=int, help="Extra head graph retained pool: 128..4096 MiB; default environment or 512")
    parser.add_argument("--archive-pending-frames", type=int, choices=(2, 4, 8), help="Archive queue bound; default environment or 2; still one worker")
    parser.add_argument("--batch-preprocess", type=int, choices=(0, 1), default=0,
                        help="Explicit grouped preprocessing selection; default 0 even if the environment enables it")
    parser.add_argument("--detect-model", type=Path, default=ROOT / "models/yolo26n.pt")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--pose-config", type=Path)
    parser.add_argument("--device", default="0")
    parser.add_argument("--idle-jobs-python", type=Path,
                        help="Optional host Python for a persistent read-only queue bridge (for WSL reading a Windows WAL database)")
    parser.add_argument("--prepare-only", action="store_true", help="Validate source/configuration on CPU; never load models")
    parser.add_argument("--write-annotated-video", action="store_true", help="Include the normal annotated-video artifact and its cost in each measured trial")
    add_runtime_profile_arguments(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.schedule_abba and args.trial_order:
        parser.error("--schedule-abba supplies its own balanced trial order")
    try:
        trial_order = parse_trial_order(args.trial_order, args.batch_frames)
    except ValueError as error:
        parser.error(str(error))
    if not 4 <= args.frames <= MAX_BENCHMARK_FRAMES or not 0 <= args.start_frame <= MAX_BENCHMARK_FRAMES - args.frames:
        parser.error("frames must be 4..72000 and start-frame+frames must be <=72000")
    for key in ("video", "native_jsonl", "runtime_dir", "sam_python", "idle_jobs_db", "detect_model"):
        setattr(args, key, getattr(args, key).resolve())
    args.checkpoint = (args.checkpoint or args.runtime_dir / "checkpoints/sam-3d-body-dinov3/model.ckpt").resolve()
    args.pose_config = (args.pose_config or args.runtime_dir / "checkpoints/sam-3d-body-dinov3/model_config.yaml").resolve()
    for path in (args.video, args.native_jsonl, args.sam_python, args.idle_jobs_db, args.detect_model, args.checkpoint, args.pose_config):
        if not path.is_file():
            parser.error(f"Missing input file: {path}")
    if not args.runtime_dir.is_dir():
        parser.error("SAM runtime directory does not exist")
    if args.idle_jobs_python is not None and not args.idle_jobs_python.is_file():
        parser.error("Queue-bridge Python does not exist")
    try:
        output = validate_output_root(args.output_root, args.idle_jobs_db)
        validate_archive_filesystem(args.runtime_dir, output)
        graph_sizes = parse_graph_sizes(args.graph_sizes)
        settings = pipeline_settings(args.runtime_dir, args.sam_python, args.batch_preprocess,
            graph_sizes=graph_sizes, forward_graph_budget_mib=args.forward_graph_budget_mib,
            head_graph_budget_mib=args.head_graph_budget_mib, archive_pending_frames=args.archive_pending_frames,
            reuse_cuda_cache=args.reuse_cuda_cache, profile_stages=args.profile_stages,
            async_mesh_capture=args.async_mesh_capture)
    except ValueError as error:
        parser.error(str(error))
    trial_configurations = (scheduling_trials(args.schedule_abba, args.batch_frames,
        int(settings["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"])) if args.schedule_abba else
        [{"batch_frames": batch, "pipeline_overlap": 0,
          "archive_pending_frames": int(settings["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"]),
          "configuration_label": f"frames-{batch}"} for batch in trial_order])
    trial_order = tuple(item["batch_frames"] for item in trial_configurations)
    guard = IdleGuard(args.idle_jobs_db, args.idle_jobs_python)
    try:
        guard(force=True)
        output.mkdir(parents=True)
    except Exception:
        guard.close()
        raise
    report = {"schema_version": "sam3d-isolated-pipeline-probe-v2", "status": "preparing", "trials": [],
              "implementation_sha256": {relative: sha256_file(ROOT / relative) for relative in (
                  "src/rallymate_vision/pipeline.py", "src/rallymate_vision/pipeline_overlap.py",
                  "src/rallymate_vision/sam3d_runner.py", "src/rallymate_vision/sam3d_decoder_graph.py",
                  "src/rallymate_vision/sam3d_mesh_archive.py", "src/rallymate_vision/sam3d_mesh.py")},
              "frames_requested": args.frames, "start_frame": args.start_frame,
              "max_players": args.max_players, "candidate_batch_frames": args.batch_frames,
              "trial_order": list(trial_order),
              "schedule_abba": args.schedule_abba,
              "trial_configurations": trial_configurations,
              "sampling_design": "paired_single_trials_not_repeatability" if len(trial_order) == 2 else "balanced_repeated_trials",
              "interval_semantics": "independent tracking from start frame; sequential prefix decoding remains included in each pipeline wall time",
              "environment": settings,
              "experiment_configuration": {
                  "graph_sizes": list(graph_sizes), "graph_sizes_source": "explicit_cli_or_default_8",
                  "decoder_batch_graph_sizes": settings["RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES"],
                  "forward_graph_budget_mib": int(settings["RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB"]),
                  "head_graph_budget_mib": int(settings["RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB"]),
                  "archive_pending_frames": int(settings["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"]),
                  "selection_sources": {key: "cli" if value is not None else "environment" if env in os.environ else "default"
                      for key, value, env in (
                          ("forward_graph_budget_mib", args.forward_graph_budget_mib, "RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB"),
                          ("head_graph_budget_mib", args.head_graph_budget_mib, "RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB"),
                          ("archive_pending_frames", args.archive_pending_frames, "RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"))},
                  "budget_scope": "retained extra graph pools; not capture peaks or total VRAM",
                  "archive_worker_threads": 1},
              "video": str(args.video), "native_jsonl": str(args.native_jsonl), "idle_jobs_db": str(args.idle_jobs_db),
              "batch_preprocess_selection": {"enabled": bool(args.batch_preprocess), "source": "explicit_cli_or_default_off"},
              "scope": "real_pipeline_detector_batch_1_explicit_player_limit_no_video_render",
              "timing_scope": "decode_detect_pose_identity_events_scoring_and_artifact_IO; excludes_load_warmup_and_comparison",
              "idle_guard": {"poll_interval_seconds": .25, "queue_access": "read_only", "poll_time_included_in_wall": True,
                             "access": guard.reader.receipt},
              "scoring_accuracy_validated": False}
    def save():
        temporary = output / "probe.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(output / "probe.json")
    perception = None
    gpu_slot = None
    try:
        save()
        started = time.perf_counter()
        binding, warmup = prepare_source(args.video, args.native_jsonl, args.frames, guard,
            batch_frames=args.batch_frames, start_frame=args.start_frame, max_players=args.max_players)
        report["source_binding"] = binding
        report["source_validation_seconds_excluded"] = time.perf_counter() - started
        request = {"schema_version": "1.0.0", "job_id": "isolated-sam-abba-" + str(uuid.uuid4()),
            "source": {"video_path": str(args.video)}, "output": {"directory": str(output / "placeholder")},
            "models": {"detect": str(args.detect_model), "pose": str(args.checkpoint), "pose_config": str(args.pose_config),
                       "pose_backend": "sam3d", "pose_runtime": "pytorch", "pose_profile": "analysis",
                       "pose_native_keypoint_format": "mhr70", "device": args.device, "detect_imgsz": 960,
                       "detect_confidence": .15, "pose_imgsz": 640, "pose_confidence": .25},
            "processing": {"start_ms": binding.get("start_ms", 0), "max_frames": args.frames, "max_players": args.max_players,
                           "frame_stride": 1, "write_annotated_video": args.write_annotated_video}, "court": {"mode": "disabled"}}
        base_request = output / "base-request.json"
        base_request.write_text(json.dumps(request, indent=2), encoding="utf-8")
        from rallymate_vision.contracts import load_request
        config = load_request(base_request)
        report['annotated_video_included'] = args.write_annotated_video
        if args.write_annotated_video:
            report['scope'] = 'real_pipeline_detector_batch_1_explicit_player_limit_with_annotated_video'
            report['timing_scope'] = 'decode_detect_pose_identity_events_scoring_annotated_video_and_artifact_IO; excludes_model_load_warmup_mesh_preview_and_comparison'
        if args.prepare_only:
            report["status"] = "cpu_prepared_no_inference"
            return
        with worker_profile(settings):
            guard(force=True)
            import cv2
            import torch
            from rallymate_service.gpu_fleet import GPUWorkerSlot, cuda_property_uuid
            from rallymate_vision.inference import Yolo26Perception
            from rallymate_vision.pipeline import run_pipeline
            cv2.setNumThreads(2)
            torch.set_num_threads(2)
            selected_device = f'cuda:{args.device}' if str(args.device).isdigit() else args.device
            gpu_uuid = cuda_property_uuid(torch.cuda.get_device_properties(selected_device).uuid)
            gpu_slot = GPUWorkerSlot(output, gpu_uuid)
            if not gpu_slot.acquire():
                raise RuntimeError('A worker already owns this physical GPU; benchmark refused')
            report['physical_gpu_uuid'] = gpu_uuid
            started = time.perf_counter()
            perception = Yolo26Perception(config.models.detect, config.models.pose, device=args.device,
                detect_imgsz=960, pose_imgsz=640, detect_confidence=.15, pose_confidence=.25,
                pose_backend_name="sam3d", pose_runtime="pytorch", pose_profile="analysis",
                pose_config=config.models.pose_config, pose_native_keypoint_format="mhr70")
            perception.device = torch.device(selected_device)
            report["load_seconds_excluded"] = time.perf_counter() - started
            guard(force=True)
            started = time.perf_counter()
            perception.detect(warmup[0]["frame"])
            engine = perception.pose_backend.engine
            report["warmup"] = run_pose_warmup(engine, warmup, binding["warmup_expected_subject_shapes"],
                guard=guard, runtime=args.runtime_dir, output=output, batch_preprocess=bool(args.batch_preprocess))
            report["warmup"]["configured_graph_shapes_not_exercised"] = sorted(set(graph_sizes) - set(binding["warmup_expected_subject_shapes"]))
            report["warmup_seconds_excluded"] = time.perf_counter() - started
            del warmup
            report["status"] = "running"
            save()
            for trial, configuration in enumerate(trial_configurations, start=1):
                batch = configuration["batch_frames"]
                guard(force=True)
                os.environ["RALLYMATE_SAM3D_BATCH_FRAMES"] = str(batch)
                os.environ["RALLYMATE_SAM3D_PIPELINE_OVERLAP"] = str(configuration["pipeline_overlap"])
                os.environ["RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES"] = str(configuration["archive_pending_frames"])
                name = f"trial-{trial}-frames-{batch}"
                request["output"]["directory"] = str(output / name)
                request_path = output / f"{name}-request.json"
                request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
                current = load_request(request_path)
                guard_seconds = guard.database_seconds
                progress_write_seconds = 0.0
                sampled_main_memory_peak_bytes = 0
                last_progress_write = float('-inf')
                def progress_receipt(progress):
                    nonlocal progress_write_seconds, last_progress_write, sampled_main_memory_peak_bytes
                    from rallymate_vision.postprocess_resources import process_memory_bytes
                    guard()
                    now = time.perf_counter()
                    if now - last_progress_write < 5:
                        return
                    last_progress_write = now
                    memory = process_memory_bytes()
                    sampled_main_memory_peak_bytes = max(sampled_main_memory_peak_bytes, memory)
                    receipt = {'trial': trial, **configuration,
                               'observed_epoch': time.time(),
                               'elapsed_seconds': now - started, 'progress': progress,
                               'main_process_memory_bytes': memory,
                               'memory_scope': 'main pipeline process only; excludes isolated SAM process'}
                    temporary = output / 'progress.tmp'
                    temporary.write_text(json.dumps(receipt, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                    temporary.replace(output / 'progress.json')
                    with (output / 'progress-history.jsonl').open('a', encoding='utf-8') as history:
                        history.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False) + '\n')
                    progress_write_seconds += time.perf_counter() - now
                started = time.perf_counter()
                started_epoch = time.time()
                summary = run_pipeline(current, perception=perception, cancellation_callback=guard,
                                       progress_callback=progress_receipt)
                elapsed = time.perf_counter() - started
                guard(force=True)
                report["trials"].append({"trial": trial, **configuration, "output_dir": str(current.output_dir),
                    "started_epoch": started_epoch,
                    "caller_wall_seconds": elapsed, "idle_guard_database_seconds": guard.database_seconds - guard_seconds,
                    "progress_receipt_write_seconds_included": progress_write_seconds,
                    "sampled_main_memory_peak_bytes": sampled_main_memory_peak_bytes,
                    "processing": summary["processing"], "counts": summary["counts"],
                    "sam3d_measurement": summary["sam3d_measurement"], "scoring_state": summary["scoring_state"]})
                save()
                print(json.dumps({"trial": trial, **configuration, "pipeline_seconds": elapsed,
                                  "pose_seconds": summary["processing"]["stage_seconds"]["pose_seconds"],
                                  "counts": summary["counts"]}), flush=True)
            close_benchmark_perception(perception, gpu_slot)
            perception = None
        guard(force=True)
        if sha256_file(args.video).lower() != binding["video_sha256"]:
            raise ValueError("Source video changed during the ABBA benchmark")
        report["status"] = "completed_pending_artifact_comparison"
        save()
        comparison = compare_pipeline(output, guard=guard)
        (output / "comparison.json").write_text(json.dumps(comparison, indent=2, allow_nan=False), encoding="utf-8")
        report["status"] = "completed_comparison_not_scoring_acceptance"
        report["comparison"] = "comparison.json"
        print(json.dumps(comparison["timing"], indent=2), flush=True)
    except Exception as error:
        from rallymate_vision.owned_subprocess import ProcessTreeCleanupError
        if gpu_slot is not None and isinstance(error, ProcessTreeCleanupError):
            gpu_slot.quarantine(f'{type(error).__name__}: {error}')
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            try:
                close_benchmark_perception(perception, gpu_slot)
            except BaseException as error:
                report.update(status="failed", error_type=type(error).__name__, error=str(error))
                raise
            finally:
                if gpu_slot is not None:
                    gpu_slot.close()
        finally:
            guard.close()
            report["idle_guard"].update(polls=guard.polls, database_seconds=guard.database_seconds)
            save()


if __name__ == "__main__":
    main()
