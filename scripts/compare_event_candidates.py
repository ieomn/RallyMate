#!/usr/bin/env python3
"""Compare two model-produced event JSONL files without treating either as truth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rallymate_events.disagreement import (
    DEFAULT_MATCH_IOU_THRESHOLDS,
    compare_event_candidates,
)


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Measure cross-model candidate-event agreement. This is not an accuracy "
            "evaluation because neither input is ground truth."
        )
    )
    parser.add_argument("--left-events", type=Path, required=True)
    parser.add_argument("--right-events", type=Path, required=True)
    parser.add_argument("--left-label", required=True)
    parser.add_argument("--right-label", required=True)
    parser.add_argument(
        "--minimum-iou",
        action="append",
        type=float,
        dest="minimum_ious",
        help="Repeat to select sensitivity thresholds; defaults to 0.1, 0.3, 0.5",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    left_path = args.left_events.resolve()
    right_path = args.right_events.resolve()
    report = compare_event_candidates(
        _load_jsonl(left_path),
        _load_jsonl(right_path),
        left_label=args.left_label,
        right_label=args.right_label,
        minimum_iou_thresholds=(
            args.minimum_ious
            if args.minimum_ious is not None
            else DEFAULT_MATCH_IOU_THRESHOLDS
        ),
        left_input_sha256=_sha256(left_path),
        right_input_sha256=_sha256(right_path),
        left_source=str(left_path),
        right_source=str(right_path),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "accuracy_claim": report["semantics"]["accuracy_claim"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
