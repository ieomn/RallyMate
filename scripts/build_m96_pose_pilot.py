#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from rallymate_evaluation.m96_pose_pilot import (
    build_m96_pose_pilot,
    validate_m96_pose_pilot,
)


def main() -> int:
    root = _ROOT
    parser = argparse.ArgumentParser(
        description="Build or validate the immutable M96 Halpe26 evaluation-only pilot"
    )
    parser.add_argument("--workspace", type=Path, default=root)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "data" / "annotations" / "m96-halpe26-development-pilot-v1",
    )
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args()
    if args.validate_existing:
        result = validate_m96_pose_pilot(args.workspace, args.output)["manifest"]
    else:
        result = build_m96_pose_pilot(args.workspace, args.output)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output.resolve()),
                "status": result["status"],
                "task_contract_sha256": result["task_contract_sha256"],
                "video_count": result["scope"]["video_count"],
                "frame_count": result["scope"]["frame_count"],
                "joint_task_count": result["scope"]["joint_task_count"],
                "human_annotation_rows": 0,
                "accuracy_metrics": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
