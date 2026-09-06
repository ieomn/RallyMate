from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rallymate_scoring.cycle_measurement import build_scoring_cycle_measurement


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build one coherent FS01->FS02->FS09 measurement cycle report."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        "feasibility_registry": args.registry.resolve(),
        "events": args.events.resolve(),
        "indicator_features": args.indicator_features.resolve(),
        "scores": args.scores.resolve(),
    }
    report = build_scoring_cycle_measurement(
        registry=json.loads(inputs["feasibility_registry"].read_text(encoding="utf-8")),
        events=_jsonl(inputs["events"]),
        indicator_records=_jsonl(inputs["indicator_features"]),
        scores=_jsonl(inputs["scores"]),
        video_id=args.video_id,
        sources={
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in inputs.items()
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "status": report["status"],
                **report["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
