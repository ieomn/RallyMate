from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rallymate_visualization.scoring_observer import (  # noqa: E402
    build_observer_manifest,
    build_video_observer_payload,
    validate_observer_manifest,
)


VIDEO_NAMES = {
    "3ae77ee3271d67de171585a5c39ddd69": "视频 1 · 近景移动",
    "850cb0006b406c7176eeda8d711cd065": "视频 2 · 97 秒完整动作",
    "8d7754d0de6d315674013d5b69a0b6ba": "视频 3 · 长时训练片段",
}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the M81 synchronized scoring observer")
    parser.add_argument(
        "--runs-root",
        type=Path,
        required=True,
        help=(
            "Explicit scoring run root containing one bundle per video. "
            "There is intentionally no implicit 'current' or historical default."
        ),
    )
    parser.add_argument("--videos-root", type=Path, default=ROOT / "FULL-TEST")
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "metric-feasibility-pose-wave-v2.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "scoring-visual-observer-m81",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        if not args.replace:
            raise SystemExit(f"output already exists: {output}; pass --replace")
        shutil.rmtree(output)
    (output / "data").mkdir(parents=True)

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    payloads = []
    for video_id, display_name in VIDEO_NAMES.items():
        payload = build_video_observer_payload(
            bundle_dir=args.runs_root / video_id,
            video_path=args.videos_root / f"{video_id}.mp4",
            registry=registry,
            display_name=display_name,
            video_href=f"../../FULL-TEST/{video_id}.mp4",
        )
        payloads.append(payload)
        _write_json(output / "data" / f"{video_id}.json", payload)

    data_file_sha256 = {
        item["video_id"]: _sha256(output / "data" / f"{item['video_id']}.json")
        for item in payloads
    }
    manifest = build_observer_manifest(
        payloads,
        data_file_sha256_by_video=data_file_sha256,
    )
    _write_json(output / "manifest.json", manifest)
    assets = SRC / "rallymate_visualization" / "assets"
    shutil.copy2(assets / "scoring-observer.html", output / "index.html")
    shutil.copy2(assets / "scoring-observer.css", output / "scoring-observer.css")
    shutil.copy2(assets / "scoring-observer.js", output / "scoring-observer.js")
    validate_observer_manifest(manifest, output, registry)
    print(
        json.dumps(
            {
                "output": str(output),
                "videos": len(payloads),
                "frames": sum(item["video"]["frame_count"] for item in payloads),
                "events": sum(item["summary"]["event_count"] for item in payloads),
                "indicator_instances": sum(
                    item["summary"]["indicator_instance_count"] for item in payloads
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
