#!/usr/bin/env python3
"""Run immutable same-frame RTMPose M/L/X development diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_same_frame_diagnostic import run_same_frame_diagnostic


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("models/rtmpose/m96-same-frame-diagnostic.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("reports/m96-rtmpose-same-frame-diagnostic/summary.md"),
    )
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    report = run_same_frame_diagnostic(
        workspace=args.root,
        registry_path=args.registry,
        output_path=args.output,
        summary_path=args.summary,
        device=args.device,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str((args.root / args.output).resolve()),
                "summary": str((args.root / args.summary).resolve()),
                "sample_count": len(report["sample_manifest"]),
                "families": list(report["models"]),
                "production_default_changed": report["claims"]["production_default_changed"],
                "RallyMate_accuracy_improved": report["claims"]["RallyMate_accuracy_improved"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
