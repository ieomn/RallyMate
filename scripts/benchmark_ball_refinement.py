"""Compare same-frame tennis-ball detector scales without claiming accuracy.

Uses only local video/model files. Additional detections are observations, not
ground truth, confirmed active balls, contact events or trajectory predictions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from rallymate_vision.inference import Yolo26Perception
from rallymate_vision.utils import box_iou


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def summary(rows, key):
    values = [row[key] for row in rows]
    adjacent = [(a, b) for a, b in zip(rows, rows[1:])
                if a["window"] == b["window"] and b["frame"] == a["frame"] + 1]
    supported = 0
    for a, b in adjacent:
        if any(np.linalg.norm(np.array(x["center_normalized"]) - y["center_normalized"]) <= .08
               for x in a[key] for y in b[key]):
            supported += 1
    return {"frame_count": len(rows), "observed_frames": sum(bool(v) for v in values),
            "detection_count": sum(len(v) for v in values),
            "multiple_candidate_frames": sum(len(v) > 1 for v in values),
            "adjacent_pairs_with_nearby_candidates": supported,
            "adjacent_pairs_total": len(adjacent),
            "continuity_semantics": "nearby_candidates_within_0.08_width_height_normalized_distance_not_verified_ball_identity",
            "box_confidence_median": float(np.median([d["confidence"] for v in values for d in v])) if any(values) else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=Path("models/yolo26n.pt"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--windows", type=int, default=12)
    parser.add_argument("--frames-per-window", type=int, default=8)
    parser.add_argument("--verify-adaptive", action="store_true", help="Also run the implemented perception.detect path")
    args = parser.parse_args()
    if not args.video.is_file() or not args.model.is_file():
        raise ValueError("Local source video and detector checkpoint must exist")
    if not 2 <= args.windows <= 30 or not 2 <= args.frames_per_window <= 60:
        raise ValueError("Sampling limits exceeded")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(args.video))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    starts = np.linspace(0, frame_count - args.frames_per_window, args.windows, dtype=int)
    frames = []
    for window, start in enumerate(starts):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(start))
        for offset in range(args.frames_per_window):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("Sampled source frame could not be decoded")
            frames.append((window, int(start) + offset, frame))
    capture.release()
    model = YOLO(str(args.model))
    all_classes = [k for k, name in model.names.items() if name in Yolo26Perception.TARGET_ALIASES]
    ball_classes = [k for k, name in model.names.items() if Yolo26Perception.TARGET_ALIASES.get(name) == "ball"]
    if not ball_classes:
        raise ValueError("Detector has no ball category")
    perception = Yolo26Perception.__new__(Yolo26Perception)
    perception.detect_model, perception.device = model, args.device
    perception.detect_imgsz, perception.detect_confidence = 960, .15
    perception.ball_refinement_imgsz, perception.target_class_ids = 1536, all_classes
    cuda = str(args.device).casefold() != "cpu" and torch.cuda.is_available()
    sizes = (960, 1280, 1536)
    for size in sizes:
        for _ in range(3):
            model.predict(frames[0][2], imgsz=size, conf=.15 if size == 960 else .25,
                          classes=all_classes if size == 960 else ball_classes, device=args.device, verbose=False)
    rows, times, thumbnails = [], {size: [] for size in sizes}, []
    adaptive_times, adaptive_calls = [], 0
    for window, index, frame in frames:
        height, width = frame.shape[:2]
        decoded = {}
        # Alternate candidate order to avoid assigning all warm/cache effects
        # to one candidate. The baseline is always identical for both merges.
        order = sizes if index % 2 else (1536, 1280, 960)
        for size in order:
            if cuda:
                torch.cuda.synchronize()
            started = time.perf_counter()
            output = model.predict(frame, imgsz=size, conf=.15 if size == 960 else .25,
                                   classes=all_classes if size == 960 else ball_classes,
                                   device=args.device, verbose=False)[0]
            if cuda:
                torch.cuda.synchronize()
            times[size].append((time.perf_counter() - started) * 1000)
            decoded[size] = Yolo26Perception._decode_detections(output, width, height)
        row = {"window": window, "frame": index, "timestamp_ms": round(index * 1000 / fps),
               "baseline": [d for d in decoded[960] if d["class_name"] == "ball"]}
        if args.verify_adaptive:
            adaptive_calls += bool(perception._ball_refinement_policy(width, height, decoded[960])[0])
            if cuda:
                torch.cuda.synchronize()
            started = time.perf_counter()
            actual = perception.detect(frame)
            if cuda:
                torch.cuda.synchronize()
            adaptive_times.append((time.perf_counter() - started) * 1000)
            row["adaptive"] = [d for d in actual if d["class_name"] == "ball"]
        for size in sizes[1:]:
            extras = [d for d in decoded[size] if d["class_name"] == "ball"
                      and Yolo26Perception._small_ball_refinement(d, width, height)]
            merged = Yolo26Perception._deduplicate(Yolo26Perception._merge_ball_scales(decoded[960], extras))
            row[f"merged_{size}"] = [d for d in merged if d["class_name"] == "ball"]
            row[f"refined_{size}"] = extras
            if size == 1280:
                unmatched = [d for d in extras if not any(box_iou(d["bbox_px"], b["bbox_px"]) >= .1 for b in row["baseline"])]
                # A spread of first additional observations per sampled window
                # permits visual background-error review without cherry-picking.
                if unmatched and sum(t[0] == window for t in thumbnails) < 3:
                    canvas = frame.copy()
                    for detection in row["baseline"]:
                        x1, y1, x2, y2 = map(round, detection["bbox_px"])
                        cv2.rectangle(canvas, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3), (0, 220, 0), 2)
                    for detection in unmatched:
                        x1, y1, x2, y2 = map(round, detection["bbox_px"])
                        cv2.rectangle(canvas, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (0, 0, 255), 2)
                        cv2.putText(canvas, f'{detection["confidence"]:.2f}', (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 255), 1)
                    canvas = cv2.resize(canvas, (640, 360))
                    cv2.putText(canvas, f'frame {index}: green base / red extra 1280', (8, 22), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
                    thumbnails.append((window, canvas))
        rows.append(row)
    keys = ["baseline", "merged_1280", "merged_1536"] + (["adaptive"] if args.verify_adaptive else [])
    reports = {key: summary(rows, key) for key in keys}
    payload = {"semantics": "same_frame_detector_scale_observability_not_accuracy", "ground_truth_used": False,
               "video_sha256": digest(args.video), "model_sha256": digest(args.model),
               "source": {"video": str(args.video), "width": frames[0][2].shape[1], "height": frames[0][2].shape[0],
                          "fps": fps, "frame_count": frame_count},
               "protocol": {"window_starts": starts.tolist(), "frames_per_window": args.frames_per_window,
                            "base_confidence": .15, "refinement_confidence": .25, "warmup_per_scale": 3,
                            "same_original_pixels": True, "super_resolution": False},
               "environment": {"torch": torch.__version__, "device": torch.cuda.get_device_name() if cuda else "cpu"},
               "latency_ms": {str(size): {"median": float(np.median(values)), "p95": float(np.percentile(values, 95))}
                              for size, values in times.items()}, "summary": reports, "rows": rows}
    if args.verify_adaptive:
        payload["adaptive_policy"] = perception.detection_metadata()
        payload["adaptive_refinement_calls"] = adaptive_calls
        payload["latency_ms"]["adaptive_total"] = {"median": float(np.median(adaptive_times)),
                                                   "p95": float(np.percentile(adaptive_times, 95)),
                                                   "mean": float(np.mean(adaptive_times))}
        payload["inference_source_sha256"] = digest(Path(__file__).resolve().parents[1] / "src/rallymate_vision/inference.py")
    (args.output_dir / "diagnostic.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for page, start in enumerate(range(0, len(thumbnails), 8)):
        images = [entry[1] for entry in thumbnails[start:start + 8]]
        images.extend([np.zeros((360, 640, 3), dtype=np.uint8)] * (8 - len(images)))
        sheet = np.vstack([np.hstack(images[i:i + 2]) for i in range(0, 8, 2)])
        cv2.imwrite(str(args.output_dir / f"extra-candidates-{page + 1}.jpg"), sheet)
    print(json.dumps({"summary": reports, "latency_ms": payload["latency_ms"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
