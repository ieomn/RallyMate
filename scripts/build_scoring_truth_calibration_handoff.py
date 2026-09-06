#!/usr/bin/env python3
"""Build an immutable M47 truth-refresh to calibration-dataset handoff."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rallymate_scoring.calibration_handoff import (
    build_scoring_truth_calibration_handoff,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-refresh-latest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--handoff-id")
    parser.add_argument(
        "--skip-main-report",
        action="store_true",
        help="Publish the handoff without rebuilding the aggregate report",
    )
    args = parser.parse_args()
    try:
        manifest = build_scoring_truth_calibration_handoff(
            truth_refresh_latest_path=args.truth_refresh_latest,
            output_root=args.output_root,
            handoff_id=args.handoff_id,
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
            parser.error(
                "calibration handoff published, but aggregate report rebuild failed; "
                f"rerun build_pose_scoring_ab_report.py (exit {exc.returncode})"
            )
    print(
        json.dumps(
            {
                "handoff_id": manifest["handoff_id"],
                "status": manifest["status"],
                "states": manifest["states"],
                "counts": manifest["counts"],
                "index": manifest["artifacts"]["index_html"]["path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
