"""Compare bounded YOLO frame batches without loading or changing the pose model.

This is a detector benchmark, not an end-to-end video scoring benchmark. It
reads only a bounded prefix and reports decoded pixels, detection differences,
wall time and CUDA memory. Run when the service GPU worker is idle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sqlite3
import time

import cv2
import torch
from ultralytics import YOLO

from rallymate_vision.inference import Yolo26Perception


def _detector(args):
    # The application facade normally loads both models. This isolated benchmark
    # deliberately constructs only its stateless detection portion.
    model = Yolo26Perception.__new__(Yolo26Perception)
    model.detect_model = YOLO(str(args.model))
    model.device = args.device
    model.detect_imgsz = args.imgsz
    model.detect_confidence = args.confidence
    model.ball_refinement_imgsz = args.refinement_imgsz or None
    model.target_class_ids = [index for index, name in model.detect_model.names.items()
                              if name in model.TARGET_ALIASES]
    return model


def _frames(path, count, batch_size):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot decode video: {path}")
    try:
        remaining = count
        while remaining:
            batch = []
            started = time.perf_counter()
            for _ in range(min(batch_size, remaining)):
                ok, frame = capture.read()
                if not ok:
                    break
                batch.append(frame)
            decode_seconds = time.perf_counter() - started
            if not batch:
                break
            remaining -= len(batch)
            yield batch, decode_seconds
    finally:
        capture.release()


def _differences(reference, actual):
    if len(reference) != len(actual):
        raise ValueError("Compared trials decoded different frame counts")
    changed = counts = ordering = 0
    max_box = max_conf = 0.
    for old, new in zip(reference, actual):
        changed += old != new
        if len(old) != len(new):
            counts += 1
            continue
        if [item["class_name"] for item in old] != [item["class_name"] for item in new]:
            ordering += 1
            continue
        for left, right in zip(old, new):
            max_box = max(max_box, *(abs(a - b) for a, b in zip(left["bbox_px"], right["bbox_px"])))
            max_conf = max(max_conf, abs(left["confidence"] - right["confidence"]))
    return {
        "changed_frames_exact_comparison": changed,
        "detection_count_changed_frames": counts,
        "class_order_changed_frames": ordering,
        "max_position_matched_bbox_delta_px": max_box,
        "max_position_matched_confidence_delta": max_conf,
        "comparison_semantics": "ordered_detections_not_identity_matching_or_scoring_accuracy",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--refinement-imgsz", type=int, default=1536)
    parser.add_argument("--confidence", type=float, default=.15)
    parser.add_argument("--frames", type=int, default=32)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--idle-jobs-db", type=Path,
                        help="Optional service jobs SQLite file; abort if a queued/running job appears")
    args = parser.parse_args()
    if (not 1 <= args.frames <= 1024 or not 1 <= args.repeats <= 20
            or not 1 <= args.cpu_threads <= 64
            or any(not 1 <= value <= 16 for value in args.batch_sizes)):
        parser.error("frames 1..1024, repeats 1..20, cpu-threads 1..64, batch-sizes 1..16 required")
    if not args.model.is_file() or not args.video.is_file():
        parser.error("model and video must be existing local files")
    if args.idle_jobs_db is not None and not args.idle_jobs_db.is_file():
        parser.error("idle-jobs-db must be an existing local SQLite file")
    batches = list(dict.fromkeys([1, *args.batch_sizes]))
    torch.set_num_threads(args.cpu_threads)
    cv2.setNumThreads(args.cpu_threads)
    cuda = args.device != "cpu"
    device = torch.device(f"cuda:{args.device}" if args.device.isdigit() else args.device)
    if cuda and not torch.cuda.is_available():
        parser.error("CUDA requested but unavailable")

    def sync():
        if cuda:
            torch.cuda.synchronize(device)

    def check_idle():
        if args.idle_jobs_db is not None:
            with sqlite3.connect(args.idle_jobs_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]
            if count:
                raise RuntimeError("Service has queued/running work; benchmark stopped to release the GPU")

    check_idle()
    model = _detector(args)
    warmup = []
    for batch_size in batches:
        for frames, _ in _frames(args.video, batch_size, batch_size):
            check_idle()
            started = time.perf_counter()
            model.detect_batch(frames)
            sync()
            warmup.append({"batch_size": batch_size, "frames": len(frames),
                           "wall_seconds": time.perf_counter() - started})
    trials = []
    reference = None
    for repeat in range(args.repeats):
        for batch_size in (batches if repeat % 2 == 0 else list(reversed(batches))):
            sync()
            if cuda:
                torch.cuda.reset_peak_memory_stats(device)
            detection_seconds = decode_seconds = 0.
            outputs = []
            peak_decoded_bytes = 0
            source_shapes = set()
            for frames, decode in _frames(args.video, args.frames, batch_size):
                check_idle()
                decode_seconds += decode
                source_shapes.update(tuple(frame.shape[:2]) for frame in frames)
                peak_decoded_bytes = max(peak_decoded_bytes, sum(frame.nbytes for frame in frames))
                sync()
                started = time.perf_counter()
                outputs.extend(model.detect_batch(frames))
                sync()
                detection_seconds += time.perf_counter() - started
                del frames
            if not outputs:
                raise RuntimeError("No source frames decoded")
            if reference is None:
                reference = outputs
            trial = {"repeat": repeat, "batch_size": batch_size, "frames": len(outputs),
                     "detection_seconds": detection_seconds, "decode_seconds": decode_seconds,
                     "detector_frames_per_second": len(outputs) / detection_seconds,
                     "decode_and_detector_frames_per_second": len(outputs) / (decode_seconds + detection_seconds),
                     "maximum_source_batch_bytes": peak_decoded_bytes,
                     "source_shapes_height_width": [list(shape) for shape in sorted(source_shapes)],
                     "difference_from_first_single_frame_trial": _differences(reference, outputs)}
            if cuda:
                trial.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(device),
                             cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(device))
            trials.append(trial)
            print(f"batch={batch_size} repeat={repeat + 1}: {trial['detector_frames_per_second']:.2f} detector FPS", flush=True)
    report = {
        "scope": "bounded_source_prefix_decode_and_detector_only_excludes_pose_tracking_scoring_upload",
        "torch_version": torch.__version__, "cuda_runtime": torch.version.cuda,
        "device": torch.cuda.get_device_name(device) if cuda else "cpu",
        "video": str(args.video.resolve()), "model": str(args.model.resolve()),
        "source_video_bytes": args.video.stat().st_size,
        "imgsz": args.imgsz, "refinement_imgsz": args.refinement_imgsz,
        "confidence": args.confidence, "cpu_threads": args.cpu_threads,
        "warmup_excluded_from_trials": warmup, "trials": trials,
        "summary": [{"batch_size": batch_size,
                     "median_detector_frames_per_second": statistics.median(
                         row["detector_frames_per_second"] for row in trials if row["batch_size"] == batch_size),
                     "all_trials_exact_detections_match": all(
                         row["difference_from_first_single_frame_trial"]["changed_frames_exact_comparison"] == 0
                         for row in trials if row["batch_size"] == batch_size)} for batch_size in batches],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
