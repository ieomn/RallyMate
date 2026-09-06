#!/usr/bin/env python3
"""Export ready adjudicated Halpe26 truth to a development MMPose dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_training.mmpose_dataset import (
    MMPoseDatasetExportError,
    export_mmpose_halpe26_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pack",
        action="append",
        required=True,
        type=Path,
        help="Strict adjudicated truth-pack directory; repeat for multiple packs.",
    )
    parser.add_argument("--governance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = export_mmpose_halpe26_dataset(
            args.pack,
            governance_csv=args.governance,
            output_dir=args.output,
        )
    except MMPoseDatasetExportError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output": str(args.output.resolve()),
                "splits": {
                    name: {
                        "image_count": value["image_count"],
                        "annotation_count": value["annotation_count"],
                    }
                    for name, value in manifest["splits"].items()
                },
                "accuracy_claim": manifest["safety"]["accuracy_claim"],
                "holdout_test_used": manifest["safety"]["holdout_test_used"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
