from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_truth_event_phase_intake import (
    ScoringTruthEventPhaseIntakeError,
    ingest_scoring_truth_event_phase,
    validate_scoring_truth_event_phase_intake,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or replay one immutable PRIVATE event/phase annotation intake. "
            "This command only compiles reviewer-C event/phase decisions; it does "
            "not authorize calibration, promotion, grades, thresholds, or runtime changes."
        )
    )
    parser.add_argument(
        "--validate-only",
        type=Path,
        metavar="INTAKE",
        help="replay one existing intake without writing",
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--release-record", "--operator-release-record", dest="release_record", type=Path)
    parser.add_argument("--handoff", "--m89-handoff", dest="handoff", type=Path)
    parser.add_argument("--execution-a-bundle", type=Path)
    parser.add_argument("--execution-a-submission", type=Path)
    parser.add_argument("--execution-b-bundle", type=Path)
    parser.add_argument("--execution-b-submission", type=Path)
    parser.add_argument("--adjudication-bundle", type=Path)
    parser.add_argument("--adjudication-submission", type=Path)
    parser.add_argument("--output-dir", "--output", dest="output_dir", type=Path)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.validate_only is not None:
            conflicts = (
                args.plan,
                args.release_record,
                args.handoff,
                args.execution_a_bundle,
                args.execution_a_submission,
                args.execution_b_bundle,
                args.execution_b_submission,
                args.adjudication_bundle,
                args.adjudication_submission,
                args.output_dir,
            )
            if any(value is not None for value in conflicts):
                parser.error("--validate-only cannot be combined with intake inputs")
            result = validate_scoring_truth_event_phase_intake(args.validate_only)
            printable = result["manifest"]
        else:
            required = {
                "--plan": args.plan,
                "--release-record": args.release_record,
                "--handoff": args.handoff,
                "--execution-a-bundle": args.execution_a_bundle,
                "--execution-a-submission": args.execution_a_submission,
                "--execution-b-bundle": args.execution_b_bundle,
                "--execution-b-submission": args.execution_b_submission,
                "--adjudication-bundle": args.adjudication_bundle,
                "--adjudication-submission": args.adjudication_submission,
                "--output-dir": args.output_dir,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                parser.error("intake is missing required options: " + ", ".join(missing))
            printable = ingest_scoring_truth_event_phase(
                plan_path=args.plan,
                release_record_path=args.release_record,
                handoff_dir=args.handoff,
                execution_a_bundle_dir=args.execution_a_bundle,
                execution_a_submission_path=args.execution_a_submission,
                execution_b_bundle_dir=args.execution_b_bundle,
                execution_b_submission_path=args.execution_b_submission,
                adjudication_bundle_dir=args.adjudication_bundle,
                adjudication_submission_path=args.adjudication_submission,
                output_dir=args.output_dir,
            )
    except (ScoringTruthEventPhaseIntakeError, FileExistsError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(printable, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
