#!/usr/bin/env python3
"""Build a hash-bound action worklist covering every unavailable score instance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_action_worklist import (
    build_scoring_truth_action_worklist,
    validate_scoring_truth_action_worklist,
    write_scoring_truth_action_csv,
    write_scoring_truth_action_html,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--blocker-audit", type=Path, required=True)
    parser.add_argument("--diagnostic-queue", type=Path, required=True)
    parser.add_argument("--review-video", type=Path, required=True)
    parser.add_argument("--reference-context", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    outputs = [
        args.output_dir / "worklist.json",
        args.output_dir / "worklist.csv",
        args.output_dir / "index.html",
    ]
    existing = [path for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise ValueError(
            "refusing to overwrite existing action worklist: "
            + ", ".join(str(path) for path in existing)
        )
    report = build_scoring_truth_action_worklist(
        scores_path=args.scores,
        events_path=args.events,
        summary_path=args.summary,
        blocker_audit_path=args.blocker_audit,
        diagnostic_queue_path=args.diagnostic_queue,
        review_video_path=args.review_video,
        reference_context_path=args.reference_context,
        registry_path=args.registry,
    )
    validate_scoring_truth_action_worklist(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs[0].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_scoring_truth_action_csv(report, outputs[1])
    write_scoring_truth_action_html(report, outputs[2], args.review_video)
    print(
        json.dumps(
            {
                "status": report["status"],
                "unavailable_instances": report["counts"][
                    "unavailable_indicator_instances"
                ],
                "actionable_instances": report["counts"][
                    "actionable_unavailable_indicator_instances"
                ],
                "work_items": report["counts"]["work_items"],
                "output": str(outputs[2].resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
