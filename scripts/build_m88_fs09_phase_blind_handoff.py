#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.fs09_phase_handoff import (
    FS09PhaseBlindHandoffError,
    build_fs09_phase_blind_handoff,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the portable M88 FS09 technical handoff without exact candidate "
            "boundaries/phases; the disclosed coarse anchor is candidate-selected."
        )
    )
    parser.add_argument("--source-pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_fs09_phase_blind_handoff(args.source_pack, args.output)
    except FS09PhaseBlindHandoffError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output": str(args.output.resolve()),
                "bundle_id": manifest["bundle_id"],
                "content_root_sha256": manifest["content_root_sha256"],
                "artifacts": len(manifest["artifacts"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
