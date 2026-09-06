#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.event_gap_keypoint_truth import evaluate_event_gap_keypoints


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate sealed M75 interpolation against adjudicated truth.")
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    report = evaluate_event_gap_keypoints(args.pack)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
