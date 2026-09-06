from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_truth import (
    PoseDiagnosticTruthError,
    write_blank_pose_diagnostic_truth_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a blank full-timeline Pose diagnostic truth pack. The pack "
            "contains no labels and cannot change scoring gates."
        )
    )
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--overwrite-unchanged-blank",
        action="store_true",
        help=(
            "rebuild only when the existing coverage/positives files still match "
            "their blank template hashes"
        ),
    )
    args = parser.parse_args()
    try:
        manifest = write_blank_pose_diagnostic_truth_pack(
            queue_path=args.queue,
            output_dir=args.output_dir,
            overwrite_unchanged_blank=args.overwrite_unchanged_blank,
        )
    except (OSError, ValueError, PoseDiagnosticTruthError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
