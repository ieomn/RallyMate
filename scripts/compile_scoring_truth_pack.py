from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation import compile_truth_pack


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile and validate completed RallyMate truth-pack CSV files"
    )
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = compile_truth_pack(args.pack)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_complete and report["status"] != "ready_for_evaluation_and_calibration_review":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
