from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.measurement_portfolio import (
    build_indicator_measurement_portfolio,
)


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build one traceable representative F2 measurement per registry indicator"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.registry, args.events, args.indicator_features, args.scores):
        if not path.is_file():
            parser.error(f"input does not exist: {path}")
    if args.output.exists():
        parser.error(f"refusing to overwrite: {args.output}")
    registry = load_feasibility_registry(args.registry)
    inputs = {
        "feasibility_registry": args.registry.resolve(),
        "events": args.events.resolve(),
        "indicator_features": args.indicator_features.resolve(),
        "scores": args.scores.resolve(),
    }
    report = build_indicator_measurement_portfolio(
        registry=registry,
        events=_jsonl(args.events),
        indicator_records=_jsonl(args.indicator_features),
        scores=_jsonl(args.scores),
        video_id=args.video_id,
        sources={
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in inputs.items()
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
