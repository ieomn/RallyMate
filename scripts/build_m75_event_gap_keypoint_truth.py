#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.event_gap_keypoint_truth import build_event_gap_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the blind M75 event-gap keypoint truth pack.")
    parser.add_argument("--m74-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = build_event_gap_truth_pack(
        m74_report_path=args.m74_report,
        output_dir=args.output,
        asset_directory=root / "src" / "rallymate_annotation" / "assets",
    )
    print(json.dumps({"status": manifest["status"], "output": str(args.output.resolve()), **manifest["scope"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
