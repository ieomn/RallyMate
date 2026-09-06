#!/usr/bin/env python3
"""Audit strict Halpe26 truth packs before any fine-tune dataset export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_training.pose_finetune_readiness import (
    audit_pose_finetune_readiness,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pack",
        action="append",
        required=True,
        type=Path,
        help="Strict truth-pack directory; repeat for more than one pack.",
    )
    parser.add_argument("--governance", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit with status 2 unless dataset export is allowed.",
    )
    args = parser.parse_args()
    report = audit_pose_finetune_readiness(
        args.pack,
        governance_csv=args.governance,
    )
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        output = args.output.resolve()
        if any(output.is_relative_to(pack.resolve()) for pack in args.pack):
            parser.error("--output must be outside every source truth-pack directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    if args.require_ready and report["status"] != "ready_for_dataset_export":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
