from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation import build_truth_pack
from rallymate_annotation.truth_pack import DEFAULT_CANDIDATE_EVENTS_TEMPLATE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a no-fabrication annotation pack for RallyMate scoring truth"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/annotations/scoring-truth-pack-v1"),
    )
    parser.add_argument("--events-per-code-per-video", type=int, default=1)
    parser.add_argument(
        "--candidate-events-template",
        default=DEFAULT_CANDIDATE_EVENTS_TEMPLATE,
        help=(
            "Root-relative or absolute events.jsonl path template containing "
            "{video_id}; every source path and SHA-256 is recorded in the manifest"
        ),
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else args.root / args.output
    manifest = build_truth_pack(
        args.root,
        output,
        events_per_code_per_video=args.events_per_code_per_video,
        candidate_events_template=args.candidate_events_template,
    )
    print(
        json.dumps(
            {"output": str(output.resolve()), "status": manifest["status"], "counts": manifest["counts"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
