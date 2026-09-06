#!/usr/bin/env python3
"""Build a source-replayed F2 error-budget readiness report without scoring."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from rallymate_evaluation.f2_error_budget_readiness import (
    build_f2_error_budget_readiness,
    validate_f2_error_budget_readiness_sources,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--smoothing-coverage", type=Path, required=True)
    parser.add_argument("--calculation-coverage", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path, action="append", required=True)
    parser.add_argument("--manual-events", type=Path, required=True)
    parser.add_argument("--manual-keypoints", type=Path, required=True)
    parser.add_argument("--manual-semantics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    temporary: Path | None = None
    try:
        report = build_f2_error_budget_readiness(
            registry_path=args.registry,
            smoothing_coverage_path=args.smoothing_coverage,
            calculation_coverage_path=args.calculation_coverage,
            run_directories=args.run_directory,
            manual_events_path=args.manual_events,
            manual_keypoints_path=args.manual_keypoints,
            manual_semantics_path=args.manual_semantics,
        )
        validate_f2_error_budget_readiness_sources(report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=args.output.parent,
            prefix=f".{args.output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, args.output)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "status": report["status"],
                "counts": report["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
