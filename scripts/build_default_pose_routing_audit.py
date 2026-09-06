#!/usr/bin/env python3
"""Build a fail-closed certificate for the normal service Pose default."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rallymate_evaluation.default_pose_routing import build_default_pose_routing_audit


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-registry", type=Path, required=True)
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument("--worker-smoke", type=Path, required=True)
    parser.add_argument("--full-video-comparison", type=Path, required=True)
    parser.add_argument("--full-video-scoring-summary", type=Path, required=True)
    parser.add_argument("--residual-computability", type=Path, required=True)
    parser.add_argument("--production-model-key", required=True)
    parser.add_argument("--experimental-model-key", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    input_paths = {
        "deployment_registry": args.deployment_registry,
        "feasibility_registry": args.feasibility_registry,
        "worker_smoke": args.worker_smoke,
        "full_video_comparison": args.full_video_comparison,
        "full_video_scoring_summary": args.full_video_scoring_summary,
        "residual_computability": args.residual_computability,
    }
    report = build_default_pose_routing_audit(
        deployment_registry=_read(args.deployment_registry),
        feasibility_registry=_read(args.feasibility_registry),
        worker_smoke=_read(args.worker_smoke),
        full_video_comparison=_read(args.full_video_comparison),
        full_video_scoring_summary=_read(args.full_video_scoring_summary),
        residual_computability=_read(args.residual_computability),
        production_model_key=args.production_model_key,
        experimental_model_key=args.experimental_model_key,
        sources={name: _source(path) for name, path in input_paths.items()},
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": report["status"],
                "default_preset": report["deployment_default"]["preset_id"],
                "production_measured": report["full_video_production_measurement"][
                    "measured_indicator_event_instance_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
