#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_truth_event_execution import (
    ScoringTruthEventExecutionError,
    build_scoring_truth_event_adjudication_bundle,
    validate_scoring_truth_event_adjudication_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build or validate a local reviewer-C bundle from exact verified A/B "
            "execution bundles and canonical submissions. This command does not "
            "perform private intake, calibration, promotion, or production changes."
        )
    )
    parser.add_argument("--annotator-a-bundle", type=Path)
    parser.add_argument("--annotator-a-submission", type=Path)
    parser.add_argument("--annotator-b-bundle", type=Path)
    parser.add_argument("--annotator-b-submission", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            snapshot = validate_scoring_truth_event_adjudication_bundle(args.output)
            result = {
                "ok": True,
                "operation": "validation",
                "output": str(snapshot["bundle_dir"]),
                "status": snapshot["manifest"]["status"],
                "bundle_id": snapshot["bundle_id"],
                "execution_id": snapshot["execution_id"],
                "manifest_sha256": snapshot["manifest_sha256"],
                "manifest_binding_sha256": snapshot[
                    "manifest_binding_sha256"
                ],
                "content_root_sha256": snapshot["content_root_sha256"],
            }
        else:
            missing = [
                name
                for name, value in (
                    ("--annotator-a-bundle", args.annotator_a_bundle),
                    ("--annotator-a-submission", args.annotator_a_submission),
                    ("--annotator-b-bundle", args.annotator_b_bundle),
                    ("--annotator-b-submission", args.annotator_b_submission),
                )
                if value is None
            ]
            if missing:
                parser.error(f"build requires: {', '.join(missing)}")
            manifest = build_scoring_truth_event_adjudication_bundle(
                annotator_a_bundle_dir=args.annotator_a_bundle,
                annotator_a_submission_path=args.annotator_a_submission,
                annotator_b_bundle_dir=args.annotator_b_bundle,
                annotator_b_submission_path=args.annotator_b_submission,
                output_dir=args.output,
            )
            result = {
                "ok": True,
                "operation": "build",
                "output": str(args.output.resolve()),
                "status": manifest["status"],
                "bundle_id": manifest["bundle_id"],
                "execution_id": manifest["execution_id"],
                "manifest_binding_sha256": manifest[
                    "manifest_binding_sha256"
                ],
                "content_root_sha256": manifest["content_root_sha256"],
            }
    except ScoringTruthEventExecutionError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
