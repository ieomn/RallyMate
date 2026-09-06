from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_truth_intake import (
    EXPORT_FILENAMES,
    ScoringTruthIntakeError,
    ingest_scoring_truth_exports,
    validate_scoring_truth_intake,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate an immutable PRIVATE scoring-truth intake. "
            "This command never grants operator, calibration, promotion, or "
            "production authorization. Validation requires the original source "
            "pack and all five original export paths to remain byte-exact."
        )
    )
    parser.add_argument(
        "--validate-only",
        type=Path,
        metavar="SESSION",
        help="read-only exact-topology, source-binding, and replay validation",
    )
    parser.add_argument("--source-pack", type=Path)
    parser.add_argument(
        "--exports-dir",
        type=Path,
        help="directory containing the five exact scoring-truth CSV filenames",
    )
    parser.add_argument("--event-annotations", type=Path)
    parser.add_argument("--keypoint-annotations", type=Path)
    parser.add_argument("--semantic-annotations", type=Path)
    parser.add_argument("--coach-labels", type=Path)
    parser.add_argument("--full-video-review-completion", type=Path)
    parser.add_argument("--output-dir", "--output", dest="output_dir", type=Path)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.validate_only is not None:
            conflicting = [
                args.source_pack,
                args.exports_dir,
                args.event_annotations,
                args.keypoint_annotations,
                args.semantic_annotations,
                args.coach_labels,
                args.full_video_review_completion,
                args.output_dir,
            ]
            if any(value is not None for value in conflicting):
                parser.error("--validate-only cannot be combined with intake inputs")
            result = validate_scoring_truth_intake(args.validate_only)
        else:
            if args.source_pack is None or args.output_dir is None:
                parser.error("intake requires --source-pack and --output-dir")
            explicit = {
                "event-annotations.csv": args.event_annotations,
                "keypoint-annotations.csv": args.keypoint_annotations,
                "semantic-annotations.csv": args.semantic_annotations,
                "coach-labels.csv": args.coach_labels,
                "full-video-review-completion.csv": (
                    args.full_video_review_completion
                ),
            }
            if args.exports_dir is not None:
                if any(value is not None for value in explicit.values()):
                    parser.error(
                        "use either --exports-dir or all five explicit CSV options"
                    )
                exports = {
                    name: args.exports_dir / name for name in EXPORT_FILENAMES
                }
            else:
                missing = [name for name, value in explicit.items() if value is None]
                if missing:
                    parser.error(
                        "all five explicit CSV options are required; missing: "
                        + ", ".join(missing)
                    )
                exports = explicit
            result = ingest_scoring_truth_exports(
                args.source_pack,
                exports,
                args.output_dir,
            )
    except (ScoringTruthIntakeError, FileExistsError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
