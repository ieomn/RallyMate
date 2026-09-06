#!/usr/bin/env python3
"""Aggregate the immutable three-video M97 RTMPose-X operational experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_x_operational import (
    build_multivideo_summary,
    load_x_operational_protocol,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("models/rtmpose/m97-x-operational-protocol.json"),
    )
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output.resolve() if args.output.is_absolute() else (root / args.output).resolve()
    if not output.is_relative_to(root):
        parser.error("output must stay inside the workspace")
    if output.exists():
        parser.error("output already exists; reports are immutable")
    report_paths = [
        path.resolve() if path.is_absolute() else (root / path).resolve()
        for path in args.report
    ]
    verified = load_x_operational_protocol(root, args.protocol)
    report = build_multivideo_summary(
        verified_protocol=verified,
        report_paths=report_paths,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(output),
                "status": report["status"],
                "X_projection": report["X_projection"],
                "comparison_to_M70": report["comparison_to_M70"],
                "comparison_to_M71": report["comparison_to_M71"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
