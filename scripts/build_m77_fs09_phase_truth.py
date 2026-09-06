#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.fs09_phase_truth import build_fs09_phase_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="Build blind M77 FS09 event/phase truth pack.")
    parser.add_argument("--m74-report", type=Path, required=True)
    parser.add_argument("--review-clips-manifest", type=Path, required=True)
    parser.add_argument("--analysis-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path("src/rallymate_annotation/assets"),
    )
    args = parser.parse_args()
    manifest = build_fs09_phase_truth_pack(
        m74_report_path=args.m74_report,
        review_clip_manifest_path=args.review_clips_manifest,
        analysis_plan_path=args.analysis_plan,
        output_dir=args.output,
        asset_directory=args.assets,
    )
    print(json.dumps({"status": manifest["status"], "output": str(args.output.resolve()), **manifest["scope"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
