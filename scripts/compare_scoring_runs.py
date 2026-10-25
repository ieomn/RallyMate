"""Rebuild and compare two local inference runs without running the GPU again."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.score_comparison import compare_card_reports
from rallymate_service.card_execution import build_card_execution_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left_run", type=Path)
    parser.add_argument("right_run", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for run in (args.left_run, args.right_run):
        if not run.is_dir():
            parser.error(f"Run directory does not exist: {run}")
    left = build_card_execution_report(args.left_run)
    right = build_card_execution_report(args.right_run)
    result = compare_card_reports(left, right)
    result["source_runs"] = [str(args.left_run.resolve()), str(args.right_run.resolve())]
    result["rebuilt_from_run_artifacts"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "matched_card_count", "left_matched_score", "right_matched_score", "difference_left_minus_right")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
