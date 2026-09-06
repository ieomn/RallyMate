#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.blocker_taxonomy import taxonomy_document


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize the canonical RallyMate score-blocker taxonomy."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(taxonomy_document(), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "materialized",
                "output": str(output),
                "taxonomy_version": taxonomy_document()["taxonomy_version"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
