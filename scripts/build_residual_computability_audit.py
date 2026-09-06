#!/usr/bin/env python3
"""Build a fail-closed computability certificate for residual indicator gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rallymate_evaluation.residual_computability import (
    build_residual_computability_audit,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    report = build_residual_computability_audit(
        comparison=comparison,
        registry=registry,
        frames=_read_jsonl(args.frames),
        timeline=_read_jsonl(args.primary_timeline),
        model_key=args.model_key,
        sources={
            "comparison": _source(args.comparison),
            "registry": _source(args.registry),
            "frames": _source(args.frames),
            "primary_timeline": _source(args.primary_timeline),
        },
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
                "measured_indicators": report["counts"][
                    "indicator_with_measured_instance_count"
                ],
                "residual_instances": report["counts"][
                    "unavailable_indicator_event_instance_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
