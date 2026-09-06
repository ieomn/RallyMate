#!/usr/bin/env python3
"""Build an immutable multi-video small-ROI measurement recovery audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.multivideo_measurement_recovery import (
    build_multivideo_measurement_recovery_report,
    render_multivideo_measurement_recovery_html,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--gap-audit", type=Path, action="append", required=True)
    parser.add_argument(
        "--experiment-report", type=Path, action="append", default=[]
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    if args.output_directory.exists():
        parser.error("output directory already exists; recovery reports are immutable")
    report = build_multivideo_measurement_recovery_report(
        recovery_id=args.recovery_id,
        gap_audit_paths=args.gap_audit,
        experiment_report_paths=args.experiment_report,
    )
    args.output_directory.mkdir(parents=True, exist_ok=False)
    json_path = args.output_directory / "report.json"
    html_path = args.output_directory / "index.html"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(
        render_multivideo_measurement_recovery_html(report, output_path=html_path),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(json_path),
                "index": str(html_path),
                "baseline": report["baseline"],
                "experiment": report["experiment"],
                "projection": report["experimental_projection"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
