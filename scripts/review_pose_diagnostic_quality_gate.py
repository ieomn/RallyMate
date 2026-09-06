from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.diagnostic_policy_review import (
    DiagnosticPolicyReviewError,
    review_pose_diagnostic_quality_gate,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Review adjudicated Pose diagnostic metrics against an external "
            "preregistered protocol. This never edits the quality policy."
        )
    )
    parser.add_argument(
        "--bundle",
        nargs=2,
        action="append",
        metavar=("MANIFEST", "EVALUATION"),
        required=True,
    )
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    try:
        bundles = []
        for manifest_value, evaluation_value in args.bundle:
            manifest_path = Path(manifest_value).resolve()
            evaluation_path = Path(evaluation_value).resolve()
            bundles.append(
                {
                    "manifest": json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    ),
                    "evaluation": json.loads(
                        evaluation_path.read_text(encoding="utf-8")
                    ),
                    "manifest_path": manifest_path,
                    "evaluation_path": evaluation_path,
                }
            )
        protocol = (
            json.loads(args.protocol.read_text(encoding="utf-8"))
            if args.protocol is not None
            else None
        )
        report = review_pose_diagnostic_quality_gate(
            evaluation_bundles=bundles,
            protocol=protocol,
            protocol_path=args.protocol,
        )
    except (OSError, ValueError, DiagnosticPolicyReviewError) as exc:
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
