from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.truth_pack import (
    SEMANTIC_REQUIREMENTS_BY_INDICATOR,
    TRUTH_PACK_VERSION,
)
from rallymate_scoring.feasibility import load_feasibility_registry
from rallymate_scoring.indicator_requirements import (
    build_indicator_requirements_snapshot,
    validate_indicator_requirements_snapshot,
)


DEFAULT_REQUIREMENTS_VERSION = (
    "pose-wave-indicator-requirements-2026-08-22.17"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the versioned requirements snapshot used to stop sealed "
            "independent-test samples from shrinking phases, semantics or features"
        )
    )
    parser.add_argument(
        "--registry", type=Path, default=Path("metric-feasibility-pose-wave-v2.json")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("indicator-scoring-requirements-pose-wave-v1.json"),
    )
    parser.add_argument(
        "--requirements-version",
        default=DEFAULT_REQUIREMENTS_VERSION,
    )
    args = parser.parse_args()
    registry = load_feasibility_registry(args.registry)
    payload = build_indicator_requirements_snapshot(
        registry=registry,
        semantic_requirements=SEMANTIC_REQUIREMENTS_BY_INDICATOR,
        requirements_version=args.requirements_version,
        semantic_contract_version=TRUTH_PACK_VERSION,
    )
    validate_indicator_requirements_snapshot(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "indicator_count": len(payload["indicators"]),
                "requirements_version": payload["requirements_version"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
