#!/usr/bin/env python3
"""Collapse M45 work items into shared human-evidence acquisition units."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_evidence_plan import (
    build_scoring_truth_evidence_plan,
    write_scoring_truth_evidence_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    targets = [
        args.output_dir / "plan.json",
        args.output_dir / "evidence-units.csv",
        args.output_dir / "index.html",
    ]
    existing = [path for path in targets if path.exists()]
    if existing and not args.overwrite:
        parser.error("refusing to overwrite existing evidence-plan artifacts")
    try:
        plan = build_scoring_truth_evidence_plan(readiness_path=args.readiness)
        outputs = write_scoring_truth_evidence_plan(plan, output_dir=args.output_dir)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": plan["status"],
                "evidence_units": plan["counts"]["evidence_units"],
                "by_status": plan["counts"]["by_status"],
                "outputs": {name: str(path.resolve()) for name, path in outputs.items()},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
