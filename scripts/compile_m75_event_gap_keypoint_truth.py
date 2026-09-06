#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.event_gap_keypoint_truth import compile_event_gap_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile independent M75 keypoint annotations.")
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    report = compile_event_gap_truth_pack(args.pack)
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] == "invalid_annotations":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
