#!/usr/bin/env python3
"""Render residual unavailable events with their fail-closed evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2


SKELETON = (
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"),
    ("right_knee", "right_ankle"),
    ("left_ankle", "left_big_toe"),
    ("left_ankle", "left_small_toe"),
    ("left_ankle", "left_heel"),
    ("right_ankle", "right_big_toe"),
    ("right_ankle", "right_small_toe"),
    ("right_ankle", "right_heel"),
)

LABELS = {
    "video_start_boundary_censored": "VIDEO START: PRE-EVENT CONTEXT ABSENT",
    "primary_source_track_transition": "SOURCE TRACK TRANSITION: IDENTITY NOT VERIFIED",
    "primary_bbox_clipped_at_image_boundary": "PLAYER CLIPPED BY IMAGE BOUNDARY",
    "pose_observation_insufficient_without_boundary_or_track_failure": "REQUIRED JOINT COVERAGE INSUFFICIENT",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve_ffmpeg(explicit: Path | None = None) -> Path:
    if explicit is not None:
        candidate = explicit.resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"explicit ffmpeg executable does not exist: {candidate}")
        return candidate
    candidate = shutil.which("ffmpeg")
    if candidate:
        return Path(candidate).resolve()
    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
        if bundled.is_file():
            return bundled
    except (ImportError, RuntimeError):
        pass
    raise FileNotFoundError("ffmpeg unavailable; pass --ffmpeg")


def merge_residual_ranges(
    events: list[dict[str, Any]], padding_ms: int
) -> list[dict[str, Any]]:
    ordered = sorted(
        (
            max(0, int(item["start_ms"]) - padding_ms),
            int(item["end_ms"]) + padding_ms,
            str(item["event_id"]),
            str(item["classification"]),
            int(item["affected_indicator_instance_count"]),
        )
        for item in events
    )
    merged: list[dict[str, Any]] = []
    for start, end, event_id, classification, indicator_count in ordered:
        if not merged or start > int(merged[-1]["end_ms"]):
            merged.append(
                {
                    "start_ms": start,
                    "end_ms": end,
                    "event_ids": [event_id],
                    "classifications": [classification],
                    "affected_indicator_instance_count": indicator_count,
                }
            )
            continue
        merged[-1]["end_ms"] = max(int(merged[-1]["end_ms"]), end)
        if event_id not in merged[-1]["event_ids"]:
            merged[-1]["event_ids"].append(event_id)
        if classification not in merged[-1]["classifications"]:
            merged[-1]["classifications"].append(classification)
        merged[-1]["affected_indicator_instance_count"] += indicator_count
    return merged


def _pose(record: dict[str, Any], track_id: Any) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in record.get("poses", [])
            if item.get("person_track_id") == track_id
        ),
        None,
    )


def _detection(record: dict[str, Any], track_id: Any) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in record.get("detections", [])
            if item.get("class_name") == "player" and item.get("track_id") == track_id
        ),
        None,
    )


def _points(pose: dict[str, Any] | None) -> dict[str, tuple[int, int]]:
    result = {}
    for item in (pose or {}).get("keypoints", []):
        if item.get("in_frame") is False or float(item.get("confidence", 0.0)) < 0.25:
            continue
        result[str(item["name"])] = (
            int(round(float(item["x_px"]))),
            int(round(float(item["y_px"]))),
        )
    return result


def _draw_pose(image: Any, pose: dict[str, Any] | None) -> int:
    points = _points(pose)
    for first, second in SKELETON:
        if first in points and second in points:
            cv2.line(image, points[first], points[second], (0, 210, 255), 2, cv2.LINE_AA)
    for name, point in points.items():
        foot = name.endswith(("ankle", "toe", "heel"))
        cv2.circle(
            image,
            point,
            4 if foot else 3,
            (255, 80, 220) if foot else (0, 230, 255),
            -1,
            cv2.LINE_AA,
        )
    return len(points)


def _annotate(
    image: Any,
    frame_record: dict[str, Any],
    timeline_record: dict[str, Any],
    active: dict[str, Any],
) -> Any:
    canvas = image.copy()
    track_id = timeline_record.get("source_track_id")
    detection = _detection(frame_record, track_id)
    pose = _pose(frame_record, track_id)
    valid_count = _draw_pose(canvas, pose)
    clipped = False
    if detection is not None:
        x1, y1, x2, y2 = [int(round(float(value))) for value in detection["bbox_px"]]
        height, width = canvas.shape[:2]
        clipped = x1 <= 1 or y1 <= 1 or x2 >= width - 1 or y2 >= height - 1
        cv2.rectangle(
            canvas,
            (x1, y1),
            (x2, y2),
            (40, 40, 255) if clipped else (255, 210, 40),
            3,
        )
    labels = [LABELS[value] for value in active["classifications"]]
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 104), (10, 12, 16), -1)
    cv2.putText(
        canvas,
        "M36 RESIDUAL COMPUTABILITY AUDIT - KEEP UNAVAILABLE",
        (22, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        " / ".join(labels),
        (22, 63),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.56,
        (60, 190, 255),
        2,
        cv2.LINE_AA,
    )
    timestamp_ms = int(frame_record["frame"]["timestamp_ms"])
    detail = (
        f"source t={timestamp_ms / 1000:.3f}s | Track={track_id} | "
        f"valid pose points={valid_count}/26 | affected indicator instances="
        f"{active['affected_indicator_instance_count']}"
    )
    cv2.putText(
        canvas,
        detail,
        (22, 91),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (235, 235, 235),
        1,
        cv2.LINE_AA,
    )
    cv2.rectangle(
        canvas,
        (0, canvas.shape[0] - 45),
        (canvas.shape[1], canvas.shape[0]),
        (10, 12, 16),
        -1,
    )
    cv2.putText(
        canvas,
        "No zero-fill / no quality-gate lowering / no A-E grade / candidate events are not truth",
        (22, canvas.shape[0] - 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (80, 220, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--padding-ms", type=int, default=150)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.validation_output.exists():
        parser.error("output already exists")
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    if audit.get("safety", {}).get("production_route_changed") is not False:
        parser.error("audit is not safely fail-closed")
    for name, path in (
        ("frames", args.frames),
        ("primary_timeline", args.primary_timeline),
    ):
        if audit.get("sources", {}).get(name, {}).get("sha256", "").upper() != _sha256(path):
            parser.error(f"audit source hash mismatch: {name}")
    ranges = merge_residual_ranges(audit["residual_events"], args.padding_ms)
    frames_rows = _read_jsonl(args.frames)
    frame_by_source = {
        int(item["frame"].get("source_frame_index", item["frame"].get("index"))): item
        for item in frames_rows
    }
    timeline = {
        int(item["source_frame_index"]): item for item in _read_jsonl(args.primary_timeline)
    }
    selected = {
        source_index
        for source_index, item in frame_by_source.items()
        if any(
            int(segment["start_ms"])
            <= int(item["frame"]["timestamp_ms"])
            <= int(segment["end_ms"])
            for segment in ranges
        )
    }
    if not selected:
        parser.error("no frames fall inside residual ranges")
    capture = cv2.VideoCapture(str(args.video))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ffmpeg_path = resolve_ffmpeg(args.ffmpeg)
    process = subprocess.Popen(
        [
            str(ffmpeg_path),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            f"{fps:.8f}",
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(args.output),
        ],
        stdin=subprocess.PIPE,
    )
    last = max(selected)
    source_index = written = 0
    while source_index <= last:
        ok, image = capture.read()
        if not ok:
            break
        if source_index in selected:
            frame_record = frame_by_source[source_index]
            timestamp_ms = int(frame_record["frame"]["timestamp_ms"])
            active = next(
                item
                for item in ranges
                if int(item["start_ms"]) <= timestamp_ms <= int(item["end_ms"])
            )
            rendered = _annotate(image, frame_record, timeline[source_index], active)
            assert process.stdin is not None
            process.stdin.write(rendered.tobytes())
            written += 1
        source_index += 1
    capture.release()
    assert process.stdin is not None
    process.stdin.close()
    return_code = process.wait()
    if return_code != 0 or written <= 0:
        raise RuntimeError(f"video render failed: return={return_code}, frames={written}")
    check = cv2.VideoCapture(str(args.output))
    samples = []
    for index in (0, written // 2, written - 1):
        check.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, _ = check.read()
        samples.append({"frame_index": index, "decoded": bool(ok)})
    codec_value = int(check.get(cv2.CAP_PROP_FOURCC))
    codec = "".join(chr((codec_value >> (8 * index)) & 0xFF) for index in range(4))
    actual_width = int(check.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(check.get(cv2.CAP_PROP_FRAME_HEIGHT))
    check.release()
    validation = {
        "schema_version": "1.0.0",
        "status": (
            "passed"
            if all(item["decoded"] for item in samples)
            and codec.lower() in {"h264", "avc1"}
            else "failed"
        ),
        "video": {
            "path": str(args.output.resolve()),
            "sha256": _sha256(args.output),
            "codec": codec,
            "frame_count": written,
            "fps": round(fps, 6),
            "duration_seconds": round(written / fps, 6),
            "width": actual_width,
            "height": actual_height,
            "sample_decode": samples,
        },
        "sources": {
            "audit": {"path": str(args.audit.resolve()), "sha256": _sha256(args.audit)},
            "video": {"path": str(args.video.resolve()), "sha256": _sha256(args.video)},
            "frames": {"path": str(args.frames.resolve()), "sha256": _sha256(args.frames)},
            "primary_timeline": {"path": str(args.primary_timeline.resolve()), "sha256": _sha256(args.primary_timeline)},
            "ffmpeg": {"path": str(ffmpeg_path), "sha256": _sha256(ffmpeg_path)},
        },
        "segments": ranges,
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "production_route_changed": False,
            "feature_quality_gate_modified": False,
            "zero_fill_used": False,
            "grade_generated": False,
            "threshold_generated": False,
        },
    }
    args.validation_output.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if validation["status"] != "passed":
        raise RuntimeError(f"video validation failed: {validation}")
    print(
        json.dumps(
            {
                "video": str(args.output),
                "frames": written,
                "duration_seconds": validation["video"]["duration_seconds"],
                "sha256": validation["video"]["sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
