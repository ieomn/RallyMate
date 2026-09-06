#!/usr/bin/env python3
"""Evaluate experimental small-ROI Pose against independently adjudicated truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.small_roi_keypoint_truth import evaluate_small_roi_keypoints


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    report = evaluate_small_roi_keypoints(args.pack)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "status": report["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
