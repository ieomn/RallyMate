#!/usr/bin/env python3
"""Audit replayable raw-versus-smoothed feature calculations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.smoothing_coverage import (
    build_smoothing_counterfactual_coverage,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--features",
        type=Path,
        action="append",
        required=True,
        help="features.jsonl input; repeat for multiple videos",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_smoothing_counterfactual_coverage(
        registry_path=args.registry,
        feature_paths=args.features,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
