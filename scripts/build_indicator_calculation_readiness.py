#!/usr/bin/env python3
"""Build per-upload, per-indicator F2 calculation readiness from run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rallymate_scoring.calculation_readiness import (
    build_indicator_calculation_readiness,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
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


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--indicator-features", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    inputs = {
        "feasibility_registry": args.registry,
        "events": args.events,
        "indicator_features": args.indicator_features,
        "scores": args.scores,
    }
    report = build_indicator_calculation_readiness(
        registry=_read_json(args.registry),
        events=_read_jsonl(args.events),
        indicator_records=_read_jsonl(args.indicator_features),
        scores=_read_jsonl(args.scores),
        video_id=args.video_id,
        sources={name: _source(path) for name, path in inputs.items()},
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": report["status"],
                "measured_indicators": report["summary"][
                    "indicator_with_measured_candidate_count"
                ],
                "indicator_count": report["summary"]["registry_indicator_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
