#!/usr/bin/env python3
"""Run an immutable RTMPose-X development smoke without opening the holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_shadow_smoke import run_shadow_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("models/rtmpose/m95-shadow-candidates.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/m95-rtmpose-x-shadow/smoke-report.json"),
    )
    parser.add_argument("--samples-per-video", type=int, default=2)
    parser.add_argument("--warmup-calls", type=int, default=1)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    report = run_shadow_smoke(
        workspace=args.root,
        registry_path=args.registry,
        output_path=args.output,
        samples_per_video=args.samples_per_video,
        warmup_calls=args.warmup_calls,
        device=args.device,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "candidate_id": report["candidate_id"],
                "output": str((args.root / args.output).resolve()),
                "sample_calls": report["totals"]["sample_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
