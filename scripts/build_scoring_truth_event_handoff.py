#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_annotation.scoring_truth_event_handoff import (
    ScoringTruthEventHandoffError,
    build_scoring_truth_event_handoff,
    validate_scoring_truth_event_handoff,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build or source-replay-validate the three-video full-length event/phase "
            "technical handoff. This version never authorizes annotation."
        )
    )
    parser.add_argument("--source-pack", type=Path, required=True)
    parser.add_argument("--video-directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            snapshot = validate_scoring_truth_event_handoff(
                args.output,
                source_pack_dir=args.source_pack,
                video_dir=args.video_directory,
            )
            result = {
                "ok": True,
                "operation": "source_replay_validation",
                "output": str(snapshot["bundle_dir"]),
                "status": snapshot["manifest"]["status"],
                "bundle_id": snapshot["bundle_id"],
                "manifest_sha256": snapshot["manifest_sha256"],
                "content_root_sha256": snapshot["content_root_sha256"],
                "source_replayed": snapshot["source_replayed"],
            }
        else:
            manifest = build_scoring_truth_event_handoff(
                args.source_pack,
                args.output,
                video_dir=args.video_directory,
            )
            result = {
                "ok": True,
                "operation": "build",
                "output": str(args.output.resolve()),
                "status": manifest["status"],
                "bundle_id": manifest["bundle_id"],
                "content_root_sha256": manifest["content_root_sha256"],
                "artifact_count": len(manifest["artifacts"]),
            }
    except ScoringTruthEventHandoffError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
