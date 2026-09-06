#!/usr/bin/env python3
"""Build a hash-bound multi-video Pose indicator calculation coverage report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.multivideo_coverage import (
    build_multivideo_indicator_calculation_coverage,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurement-sources-latest", type=Path, required=True)
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--coverage-id", required=True)
    args = parser.parse_args()
    try:
        manifest = build_multivideo_indicator_calculation_coverage(
            measurement_sources_latest_path=args.measurement_sources_latest,
            feasibility_registry_path=args.feasibility_registry,
            source_report_paths=args.source_report,
            output_root=args.output_root,
            coverage_id=args.coverage_id,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "coverage_id": manifest["coverage_id"],
                "status": manifest["status"],
                "counts": manifest["counts"],
                "index": manifest["artifacts"]["index_html"]["path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
