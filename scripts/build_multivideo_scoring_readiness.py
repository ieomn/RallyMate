#!/usr/bin/env python3
"""Build a replay-validated multi-video scoring-readiness decomposition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.multivideo_readiness import (
    build_multivideo_scoring_readiness_decomposition,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-latest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--audit-id", required=True)
    args = parser.parse_args()
    try:
        manifest = build_multivideo_scoring_readiness_decomposition(
            coverage_latest_path=args.coverage_latest,
            output_root=args.output_root,
            audit_id=args.audit_id,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "audit_id": manifest["audit_id"],
                "status": manifest["status"],
                "counts": manifest["counts"],
                "index": manifest["artifacts"]["index_html"]["path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
