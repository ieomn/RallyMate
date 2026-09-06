#!/usr/bin/env python3
"""Compile all bound truth videos into one immutable calibration portfolio."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rallymate_scoring.calibration_portfolio import (
    build_scoring_truth_calibration_portfolio,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-refresh-latest", type=Path, required=True)
    parser.add_argument("--measurement-sources-latest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--portfolio-id", required=True)
    parser.add_argument("--skip-main-report", action="store_true")
    args = parser.parse_args()
    try:
        manifest = build_scoring_truth_calibration_portfolio(
            truth_refresh_latest_path=args.truth_refresh_latest,
            measurement_sources_latest_path=args.measurement_sources_latest,
            output_root=args.output_root,
            portfolio_id=args.portfolio_id,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if not args.skip_main_report:
        root = Path(__file__).resolve().parents[1]
        try:
            subprocess.run(
                [sys.executable, str(root / "scripts" / "build_pose_scoring_ab_report.py")],
                cwd=root,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            parser.error(f"portfolio published but report rebuild failed: {exc.returncode}")
    print(
        json.dumps(
            {
                "portfolio_id": manifest["portfolio_id"],
                "status": manifest["status"],
                "counts": manifest["counts"],
                "index": manifest["artifacts"]["index_html"]["path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
