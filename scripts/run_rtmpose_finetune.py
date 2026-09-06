from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rallymate_training.mmpose_finetune import (  # noqa: E402
    MMPoseFineTuneError,
    prepare_or_run_rtmpose_finetune,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and materialize an RTMPose-X Halpe26 fine-tune run. "
            "Dry-run is the default; --execute is the only training switch."
        )
    )
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument(
        "--dataset-manifest-sha256",
        required=True,
        help="Expected raw-byte SHA-256 for the exported manifest.json",
    )
    parser.add_argument("--output-dir", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and write the immutable run plan without training (default)",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Start mmengine Runner only after every gate passes",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = prepare_or_run_rtmpose_finetune(
            args.dataset_manifest,
            args.dataset_manifest_sha256,
            args.output_dir,
            execute=args.execute,
            workspace=ROOT,
        )
    except (MMPoseFineTuneError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": getattr(exc, "status", "rejected"),
                    "training_started": getattr(exc, "training_started", False),
                    "accuracy_claim": False,
                    "model_promoted": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
