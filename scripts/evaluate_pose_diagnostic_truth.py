from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_truth import (
    PoseDiagnosticTruthError,
    evaluate_pose_diagnostic_truth,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Pose diagnostic candidates against explicitly covered, "
            "adjudicated full-timeline truth."
        )
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--positives", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        coverage_path = args.coverage or Path(
            manifest["annotation_files"]["coverage"]["path"]
        )
        positives_path = args.positives or Path(
            manifest["annotation_files"]["positives"]["path"]
        )
        report = evaluate_pose_diagnostic_truth(
            manifest=manifest,
            coverage_csv_text=coverage_path.read_text(encoding="utf-8"),
            positives_csv_text=positives_path.read_text(encoding="utf-8"),
            coverage_path=coverage_path,
            positives_path=positives_path,
        )
    except (OSError, KeyError, ValueError, PoseDiagnosticTruthError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
