from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rallymate_scoring.registry_lifecycle import DEFAULT_REGISTRY_LIFECYCLE_PATH
from rallymate_vision.contracts import ContractError, load_request
from rallymate_vision.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RallyMate first-stage tennis vision demo"
    )
    parser.add_argument("--request", required=True, help="Path to request JSON")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Override request max_frames for a quick smoke run",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Override device: auto, cpu, 0, 1, ...",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="Skip annotated MP4 generation",
    )
    parser.add_argument(
        "--registry-lifecycle-manifest",
        type=Path,
        default=DEFAULT_REGISTRY_LIFECYCLE_PATH,
        help=(
            "Operator-controlled lifecycle authority selecting the sole current "
            "runtime feasibility registry; request JSON cannot select it."
        ),
    )
    parser.add_argument(
        "--trusted-promotion-ledger",
        type=Path,
        default=None,
        help=(
            "Operator-controlled trusted promotion ledger. This CLI caller is "
            "part of the local filesystem trust boundary; job request JSON cannot "
            "select this path."
        ),
    )
    parser.add_argument(
        "--trusted-runtime-profile-bindings",
        type=Path,
        default=None,
        help=(
            "Operator-controlled allow-list binding production calibrations to "
            "exact pose/pipeline/view profiles; job JSON cannot select this path."
        ),
    )
    parser.add_argument(
        "--runtime-view-evidence",
        type=Path,
        default=None,
        help=(
            "Accepted operator-supplied view evidence for this exact video_id "
            "and video SHA-256."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        request = load_request(args.request)
        if args.max_frames is not None:
            if args.max_frames < 1:
                raise ContractError("--max-frames must be >= 1")
            request.processing.max_frames = args.max_frames
        if args.device is not None:
            request.models.device = args.device
        if args.no_video:
            request.processing.write_annotated_video = False
        summary = run_pipeline(
            request,
            trusted_promotion_ledger_path=args.trusted_promotion_ledger,
            trusted_runtime_binding_registry_path=(
                args.trusted_runtime_profile_bindings
            ),
            runtime_view_evidence_path=args.runtime_view_evidence,
            registry_lifecycle_manifest_path=args.registry_lifecycle_manifest,
        )
    except (ContractError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "status": summary["status"],
                "job_id": summary["job_id"],
                "output_dir": str(request.output_dir),
                "processed_frames": summary["processing"]["processed_frames"],
                "elapsed_seconds": summary["processing"]["elapsed_seconds"],
                "next_stage_ready": summary["next_stage"]["ready"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
