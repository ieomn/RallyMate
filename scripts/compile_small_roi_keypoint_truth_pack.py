#!/usr/bin/env python3
"""Compile and validate independent small-ROI keypoint annotations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.small_roi_keypoint_truth import compile_small_roi_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    report = compile_small_roi_truth_pack(args.pack)
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] == "invalid_annotations":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
