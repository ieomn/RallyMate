#!/usr/bin/env python3
"""Compile truth and publish one immutable, fail-closed M45/M46 refresh bundle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rallymate_annotation.scoring_truth_refresh import (
    build_scoring_truth_refresh,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--primary-timeline", type=Path, required=True)
    parser.add_argument("--predicted-events", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--worklist", type=Path, required=True)
    parser.add_argument("--pose-truth-manifest", type=Path, required=True)
    parser.add_argument("--reference-context", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--refresh-id")
    parser.add_argument(
        "--skip-main-report",
        action="store_true",
        help="Publish the refresh bundle without rebuilding the aggregate HTML/Markdown report",
    )
    args = parser.parse_args()
    try:
        manifest = build_scoring_truth_refresh(
            pack_dir=args.pack,
            frames_path=args.frames,
            primary_timeline_path=args.primary_timeline,
            predicted_events_path=args.predicted_events,
            registry_path=args.registry,
            worklist_path=args.worklist,
            pose_truth_manifest_path=args.pose_truth_manifest,
            reference_context_path=args.reference_context,
            output_root=args.output_root,
            refresh_id=args.refresh_id,
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
                "truth refresh published, but aggregate report rebuild failed; "
                f"rerun build_pose_scoring_ab_report.py (exit {exc.returncode})"
            )
    print(
        json.dumps(
            {
                "refresh_id": manifest["refresh_id"],
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
