#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.fs09_phase_truth import (
    FS09PhaseTruthError,
    evaluate_fs09_phase_truth,
    validate_fs09_phase_truth_report_sources,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate M77 candidate FS09 phases and boundary-conditioned features against adjudicated truth.")
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    pack = args.pack.resolve()
    output = args.output.resolve()
    try:
        output.relative_to(pack)
    except ValueError:
        pass
    else:
        parser.error("output must be outside the immutable M77 intake session")
    if output.exists():
        parser.error("output already exists")
    try:
        report = evaluate_fs09_phase_truth(pack)
        validate_fs09_phase_truth_report_sources(report)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n"
            )
    except (FS09PhaseTruthError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "output": str(output), **report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
