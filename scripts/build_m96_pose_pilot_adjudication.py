#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from rallymate_evaluation.m96_pose_pilot import build_m96_adjudication_bundle


def main() -> int:
    root = _ROOT
    parser = argparse.ArgumentParser(
        description="Atomically intake complete M96 A/B CSV bytes and build the C disagreement bundle"
    )
    parser.add_argument("--workspace", type=Path, default=root)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--annotator-a-csv", type=Path, required=True)
    parser.add_argument("--annotator-b-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_m96_adjudication_bundle(
        args.workspace,
        args.pilot,
        args.annotator_a_csv,
        args.annotator_b_csv,
        args.output,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output.resolve()),
                "status": manifest["status"],
                "bundle_id": manifest["bundle_id"],
                "agreement_count": manifest["scope"]["agreement_count"],
                "C_disagreement_count": manifest["scope"]["disagreement_count"],
                "accuracy_metrics": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
