#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from rallymate_evaluation.m96_pose_pilot import (
    validate_m96_adjudication_submission,
    validate_m96_annotation_submission,
)


def main() -> int:
    root = _ROOT
    parser = argparse.ArgumentParser(
        description="Validate an M96 A, B, or C CSV without rewriting its raw bytes"
    )
    parser.add_argument("--workspace", type=Path, default=root)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--role", choices=("A", "B", "C"), required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    if args.role == "C":
        result = validate_m96_adjudication_submission(
            args.workspace, args.bundle, args.csv
        )
        role_id = result["reviewer_id"]
    else:
        result = validate_m96_annotation_submission(
            args.workspace, args.bundle, args.role, args.csv
        )
        role_id = result["annotator_id"]
    print(
        json.dumps(
            {
                "ok": True,
                "role": args.role,
                "role_id": role_id,
                "row_count": len(result["rows"]),
                "raw_sha256": result["raw_sha256"],
                "submission_revision_sha256": result["submission_revision_sha256"],
                "accuracy_metrics": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
