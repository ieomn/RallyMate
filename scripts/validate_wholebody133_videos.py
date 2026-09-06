#!/usr/bin/env python3
"""Validate the browser-facing WholeBody133 demonstration videos.

This validator deliberately decodes samples from the beginning, middle, and end
of each file instead of treating container metadata as proof of playability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import cv2


EXPECTED = {
    "wholebody133": {"resolution": [1280, 960]},
    "three_way_comparison": {"resolution": [1920, 960]},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _fourcc_text(value: float) -> str:
    code = int(value)
    return "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4))


def _inspect(path: Path, expected_resolution: list[int], expected_frames: int) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"path": str(path), "opened": False, "errors": ["video_open_failed"]}

    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    codec = _fourcc_text(capture.get(cv2.CAP_PROP_FOURCC)).lower()
    sample_indices = sorted({0, frames // 2, max(0, frames - 1)})
    sample_decodes = []
    for frame_index in sample_indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        decoded, image = capture.read()
        sample_decodes.append(
            {
                "frame_index": frame_index,
                "decoded": bool(decoded),
                "shape": list(image.shape) if decoded else None,
            }
        )
    capture.release()

    errors = []
    if codec not in {"h264", "avc1"}:
        errors.append(f"unexpected_codec:{codec}")
    if frames != expected_frames:
        errors.append(f"unexpected_frame_count:{frames}")
    if [width, height] != expected_resolution:
        errors.append(f"unexpected_resolution:{width}x{height}")
    if abs(fps - 30.0) > 0.01:
        errors.append(f"unexpected_fps:{fps}")
    if not all(item["decoded"] for item in sample_decodes):
        errors.append("sample_decode_failed")

    return {
        "path": str(path.resolve()),
        "opened": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "codec": codec,
        "frames": frames,
        "fps": round(fps, 6),
        "duration_seconds": round(frames / fps, 6) if fps > 0 else None,
        "resolution": [width, height],
        "expected_resolution": expected_resolution,
        "sample_decodes": sample_decodes,
        "errors": errors,
    }


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".partial"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wholebody", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-frames", type=int, default=600)
    args = parser.parse_args()

    artifacts = {
        "wholebody133": _inspect(
            args.wholebody,
            EXPECTED["wholebody133"]["resolution"],
            args.expected_frames,
        ),
        "three_way_comparison": _inspect(
            args.comparison,
            EXPECTED["three_way_comparison"]["resolution"],
            args.expected_frames,
        ),
    }
    errors = {
        name: artifact["errors"]
        for name, artifact in artifacts.items()
        if artifact.get("errors")
    }
    payload = {
        "schema_version": "1.0.0",
        "validator_version": "wholebody133-h264-validation/1.0.0",
        "status": "passed" if not errors else "failed",
        "checks": [
            "h264_or_avc1_codec",
            "600_frames",
            "30_fps",
            "expected_resolution",
            "first_middle_last_frame_decode",
        ],
        "artifacts": artifacts,
        "errors": errors,
    }
    _write_atomic(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
