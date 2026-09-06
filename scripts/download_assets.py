from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path


SAMPLES = {
    "pexels-tennis-serve-4902773.mp4": (
        "https://videos.pexels.com/video-files/4902773/"
        "4902773-hd_1920_1080_25fps.mp4"
    ),
    "pexels-tennis-match-992693.mp4": (
        "https://videos.pexels.com/video-files/992693/"
        "992693-hd_1920_1080_25fps.mp4"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def download(
    url: str,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        if expected_bytes is not None and destination.stat().st_size != expected_bytes:
            raise RuntimeError(f"asset size mismatch: {destination}")
        if expected_sha256 is not None and _sha256(destination) != expected_sha256.upper():
            raise RuntimeError(f"asset SHA-256 mismatch: {destination}")
        print(f"exists: {destination}")
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"downloading: {url}")
    try:
        urllib.request.urlretrieve(url, temporary)
        if expected_bytes is not None and temporary.stat().st_size != expected_bytes:
            raise RuntimeError(f"downloaded asset size mismatch: {destination}")
        if expected_sha256 is not None and _sha256(temporary) != expected_sha256.upper():
            raise RuntimeError(f"downloaded asset SHA-256 mismatch: {destination}")
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    root = args.root.resolve()

    for filename, url in SAMPLES.items():
        download(url, root / "data" / "samples" / filename)

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics is missing; run this script with the yolo environment"
        ) from exc

    model_dir = root / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("yolo26n.pt", "yolo26n-pose.pt"):
        path = model_dir / filename
        if path.exists() and path.stat().st_size > 0:
            print(f"exists: {path}")
            continue
        print(f"downloading model: {filename}")
        YOLO(str(path))

    deployment_registry = json.loads(
        (root / "models" / "rtmpose" / "deployment-presets.json").read_text(
            encoding="utf-8"
        )
    )
    default_preset = next(
        item
        for item in deployment_registry["presets"]
        if item["preset_id"] == deployment_registry["default_preset"]
    )
    if default_preset["pose_backend"] == "rtmpose":
        candidates = json.loads(
            (root / "models" / "rtmpose" / "model-candidates.json").read_text(
                encoding="utf-8"
            )
        )["candidates"]
        checkpoint_name = Path(default_preset["model_relative_path"]).name
        candidate = next(
            item for item in candidates if item["checkpoint"] == checkpoint_name
        )
        download(
            candidate["checkpoint_url"],
            root / default_preset["model_relative_path"],
            expected_sha256=candidate["checkpoint_sha256"],
            expected_bytes=int(candidate["checkpoint_bytes"]),
        )

    print("assets ready")


if __name__ == "__main__":
    main()
