#!/usr/bin/env python3
"""Build current primary-player timelines and a strict multi-video calibration source set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.calibration_measurement_sources import (
    build_calibration_measurement_sources,
)


def _source(value: str) -> dict[str, str]:
    parts = value.split("|")
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            "--source must be VIDEO_ID|FRAMES_JSONL|POSE_SUMMARY_JSON"
        )
    return {
        "video_id": parts[0],
        "frames_path": parts[1],
        "pose_summary_path": parts[2],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-manifest", type=Path, required=True)
    parser.add_argument("--feasibility-registry", type=Path, required=True)
    parser.add_argument("--source", type=_source, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-set-id", required=True)
    args = parser.parse_args()
    try:
        manifest = build_calibration_measurement_sources(
            truth_manifest_path=args.truth_manifest,
            feasibility_registry_path=args.feasibility_registry,
            source_specs=args.source,
            output_root=args.output_root,
            source_set_id=args.source_set_id,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "source_set_id": manifest["source_set_id"],
                "status": manifest["status"],
                "videos": manifest["video_count"],
                "frames": manifest["total_frame_count"],
                "primary_player_version": manifest[
                    "required_primary_player_version"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
