"""Isolated source-bound SAM pipeline ABBA benchmark; never submits service jobs.

Run with ``python -m scripts.benchmark_sam3d_pipeline`` when the service is idle.
Model loading, source validation, warmup and artifact comparison are excluded
from measured pipeline wall time. Reports are evidence, not scoring approval.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_sam3d_batches import build_worker_profile, select_records, snapshots, validate_archive_filesystem, verify_batch_execution, worker_profile
from scripts.benchmark_idle import IdleJobsGuard
from scripts.sam3d_pipeline_comparison import compare_pipeline
from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_runner import SourcePTSDecoder


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


def pipeline_settings(runtime: Path, sam_python: Path, batch_preprocess: int) -> dict:
    if type(batch_preprocess) is not int or batch_preprocess not in (0, 1):
        raise ValueError("batch preprocessing must be explicitly 0 or 1")
    profile = build_worker_profile(profile="production", batch_sizes=[1, 4], max_players=2,
                                   batch_preprocess=batch_preprocess)
    return {**profile, "RALLYMATE_SAM3D_PYTHON": str(sam_python),
            "RALLYMATE_SAM3D_RUNTIME": str(runtime), "RALLYMATE_SAM3D_BATCH_PERSONS": "8",
            "RALLYMATE_SAM3D_BATCH_GRAPH_SIZES": "8", "RALLYMATE_SAM3D_BATCH_FRAMES": "1",
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


def prepare_source(video: Path, archive: Path, count: int, guard) -> tuple[dict, list]:
    """Check every selected PTS while retaining only four warmup images."""
    guard(force=True)
    selected = select_records(archive, count=count, start_frame=0, max_players=2)
    if [group[0]["frame_index"] for group in selected] != list(range(count)):
        raise ValueError("Native archive must bind every requested source frame, starting at frame zero")
    source_sha = sha256_file(video).lower()
    if any(row["video_sha256"].lower() != source_sha for group in selected for row in group):
        raise ValueError("Video bytes differ from the existing native source archive")
    decoder = SourcePTSDecoder(video)
    warmup, warmup_bytes = [], 0
    try:
        for index, group in enumerate(selected):
            guard()
            ok, frame = decoder.read()
            if not ok:
                raise ValueError("Video ended before the requested bound frame prefix")
            for row in group:
                if any(row[key] != decoder.binding[key] for key in ("pts", "time_base_num", "time_base_den")):
                    raise ValueError("Decoded PTS differs from the existing native archive")
                if row["image_size"] != [frame.shape[1], frame.shape[0]]:
                    raise ValueError("Decoded dimensions differ from the existing native archive")
            if index < 4:
                warmup_bytes += frame.nbytes
                if warmup_bytes > 64 * 1024 * 1024:
                    raise ValueError("Four-frame warmup exceeds the pipeline's 64 MiB frame buffer")
                warmup.append({"frame": frame, "boxes": [row["bbox_px"] for row in group], "bindings": group})
    finally:
        decoder.release()
    guard(force=True)
    return {"video_sha256": source_sha, "frames": selected, "warmup_image_bytes": warmup_bytes}, warmup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("video", "native-jsonl", "runtime-dir", "sam-python", "idle-jobs-db", "output-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--frames", type=int, default=128, help="Source prefix length, 4..512 (default 128)")
    parser.add_argument("--batch-frames", type=int, choices=(4, 8), default=4,
                        help="Candidate frame envelope for ABBA 1,N,N,1; person and graph limits remain 8")
    parser.add_argument("--batch-preprocess", type=int, choices=(0, 1), default=0,
                        help="Explicit grouped preprocessing selection; default 0 even if the environment enables it")
    parser.add_argument("--detect-model", type=Path, default=ROOT / "models/yolo26n.pt")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--pose-config", type=Path)
    parser.add_argument("--device", default="0")
    parser.add_argument("--idle-jobs-python", type=Path,
                        help="Optional host Python for a persistent read-only queue bridge (for WSL reading a Windows WAL database)")
    parser.add_argument("--prepare-only", action="store_true", help="Validate source/configuration on CPU; never load models")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    trial_order = (1, args.batch_frames, args.batch_frames, 1)
    if not 4 <= args.frames <= 512:
        parser.error("frames must be between 4 and 512")
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
        settings = pipeline_settings(args.runtime_dir, args.sam_python, args.batch_preprocess)
    except ValueError as error:
        parser.error(str(error))
    guard = IdleGuard(args.idle_jobs_db, args.idle_jobs_python)
    try:
        guard(force=True)
        output.mkdir(parents=True)
    except Exception:
        guard.close()
        raise
    report = {"schema_version": "sam3d-isolated-pipeline-probe-v2", "status": "preparing", "trials": [],
              "frames_requested": args.frames, "candidate_batch_frames": args.batch_frames,
              "trial_order": list(trial_order), "environment": settings,
              "video": str(args.video), "native_jsonl": str(args.native_jsonl), "idle_jobs_db": str(args.idle_jobs_db),
              "batch_preprocess_selection": {"enabled": bool(args.batch_preprocess), "source": "explicit_cli_or_default_off"},
              "scope": "real_pipeline_detector_batch_1_two_subject_limit_no_video_render",
              "timing_scope": "decode_detect_pose_identity_events_scoring_and_artifact_IO; excludes_load_warmup_and_comparison",
              "idle_guard": {"poll_interval_seconds": .25, "queue_access": "read_only", "poll_time_included_in_wall": True,
                             "access": guard.reader.receipt},
              "scoring_accuracy_validated": False}
    def save():
        temporary = output / "probe.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(output / "probe.json")
    perception = None
    try:
        save()
        started = time.perf_counter()
        binding, warmup = prepare_source(args.video, args.native_jsonl, args.frames, guard)
        report["source_binding"] = binding
        report["source_validation_seconds_excluded"] = time.perf_counter() - started
        request = {"schema_version": "1.0.0", "job_id": "isolated-sam-abba-" + str(uuid.uuid4()),
            "source": {"video_path": str(args.video)}, "output": {"directory": str(output / "placeholder")},
            "models": {"detect": str(args.detect_model), "pose": str(args.checkpoint), "pose_config": str(args.pose_config),
                       "pose_backend": "sam3d", "pose_runtime": "pytorch", "pose_profile": "analysis",
                       "pose_native_keypoint_format": "mhr70", "device": args.device, "detect_imgsz": 960,
                       "detect_confidence": .15, "pose_imgsz": 640, "pose_confidence": .25},
            "processing": {"start_ms": 0, "max_frames": args.frames, "max_players": 2,
                           "frame_stride": 1, "write_annotated_video": False}, "court": {"mode": "disabled"}}
        base_request = output / "base-request.json"
        base_request.write_text(json.dumps(request, indent=2), encoding="utf-8")
        from rallymate_vision.contracts import load_request
        config = load_request(base_request)
        if args.prepare_only:
            report["status"] = "cpu_prepared_no_inference"
            return
        with worker_profile(settings):
            guard(force=True)
            import cv2
            import torch
            from rallymate_vision.inference import Yolo26Perception
            from rallymate_vision.pipeline import run_pipeline
            cv2.setNumThreads(2)
            torch.set_num_threads(2)
            started = time.perf_counter()
            perception = Yolo26Perception(config.models.detect, config.models.pose, device=args.device,
                detect_imgsz=960, pose_imgsz=640, detect_confidence=.15, pose_confidence=.25,
                pose_backend_name="sam3d", pose_runtime="pytorch", pose_profile="analysis",
                pose_config=config.models.pose_config, pose_native_keypoint_format="mhr70")
            report["load_seconds_excluded"] = time.perf_counter() - started
            guard(force=True)
            started = time.perf_counter()
            perception.detect(warmup[0]["frame"])
            engine = perception.pose_backend.engine
            guard(force=True)
            baseline = engine.infer(warmup[0]["frame"], warmup[0]["boxes"])
            guard(force=True)
            snapshots([baseline], warmup[:1], runtime=args.runtime_dir, archive=output / "warmup-1")
            guard(force=True)
            batched = engine.infer_batch([{"frame": item["frame"], "boxes": item["boxes"]} for item in warmup])
            guard(force=True)
            verify_batch_execution(batched, sum(len(item["boxes"]) for item in warmup), batch_preprocess=bool(args.batch_preprocess))
            snapshots(batched, warmup, runtime=args.runtime_dir, archive=output / "warmup-4")
            report["warmup_seconds_excluded"] = time.perf_counter() - started
            del baseline, batched, warmup
            report["status"] = "running"
            save()
            for trial, batch in enumerate(trial_order, start=1):
                guard(force=True)
                os.environ["RALLYMATE_SAM3D_BATCH_FRAMES"] = str(batch)
                name = f"trial-{trial}-frames-{batch}"
                request["output"]["directory"] = str(output / name)
                request_path = output / f"{name}-request.json"
                request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
                current = load_request(request_path)
                guard_seconds = guard.database_seconds
                started = time.perf_counter()
                summary = run_pipeline(current, perception=perception, cancellation_callback=guard)
                elapsed = time.perf_counter() - started
                guard(force=True)
                report["trials"].append({"trial": trial, "batch_frames": batch, "output_dir": str(current.output_dir),
                    "caller_wall_seconds": elapsed, "idle_guard_database_seconds": guard.database_seconds - guard_seconds,
                    "processing": summary["processing"], "counts": summary["counts"],
                    "sam3d_measurement": summary["sam3d_measurement"], "scoring_state": summary["scoring_state"]})
                save()
                print(json.dumps({"trial": trial, "batch_frames": batch, "pipeline_seconds": elapsed,
                                  "pose_seconds": summary["processing"]["stage_seconds"]["pose_seconds"],
                                  "counts": summary["counts"]}), flush=True)
            perception.close()
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
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            if perception is not None:
                perception.close()
        finally:
            guard.close()
            report["idle_guard"].update(polls=guard.polls, database_seconds=guard.database_seconds)
            save()


if __name__ == "__main__":
    main()
