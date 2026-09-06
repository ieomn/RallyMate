#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_truth_event_execution import (
    ScoringTruthEventExecutionError,
    build_scoring_truth_event_execution_bundle,
    validate_scoring_truth_event_execution_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build or validate one role-locked local A/B full-video event/phase "
            "execution bundle after an existing operator release. This command "
            "does not create a release, label, calibration, or production change."
        )
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--operator-release", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--role-slot", choices=("A", "B"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            snapshot = validate_scoring_truth_event_execution_bundle(
                args.output, expected_role_slot=args.role_slot
            )
            result = {
                "ok": True,
                "operation": "validation",
                "output": str(snapshot["bundle_dir"]),
                "status": snapshot["manifest"]["status"],
                "bundle_id": snapshot["bundle_id"],
                "execution_id": snapshot["execution_id"],
                "role_slot": snapshot["role_slot"],
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
                    ("--plan", args.plan),
                    ("--operator-release", args.operator_release),
                    ("--handoff", args.handoff),
                    ("--role-slot", args.role_slot),
                )
                if value is None
            ]
            if missing:
                parser.error(f"build requires: {', '.join(missing)}")
            manifest = build_scoring_truth_event_execution_bundle(
                plan_path=args.plan,
                release_record_path=args.operator_release,
                handoff_dir=args.handoff,
                role_slot=args.role_slot,
                output_dir=args.output,
            )
            result = {
                "ok": True,
                "operation": "build",
                "output": str(args.output.resolve()),
                "status": manifest["status"],
                "bundle_id": manifest["bundle_id"],
                "execution_id": manifest["execution_id"],
                "role_slot": manifest["role"]["slot"],
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
