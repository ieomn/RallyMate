#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_clip_truth import (
    compile_clip_truth,
    json_dump,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile two-annotator/third-party M80 diagnostic truth CSVs"
    )
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    pack = args.pack.resolve()
    if not (pack / "manifest.json").is_file():
        parser.error(f"pose diagnostic clip truth pack is missing manifest.json: {pack}")
    report = compile_clip_truth(pack)
    compiled = pack / "compiled"
    compiled.mkdir(exist_ok=True)
    output = compiled / "evaluation.json"
    temporary = compiled / ".evaluation.json.building"
    temporary.write_text(json_dump(report), encoding="utf-8")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "coverage_annotations": report["counts"][
                    "coverage_annotations"
                ],
                "candidate_adjudications": report["counts"][
                    "candidate_adjudications"
                ],
                "metrics_available": report["metrics_by_diagnostic_type"]
                is not None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
