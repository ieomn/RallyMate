from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_review import (
    PoseDiagnosticReviewError,
    build_pose_diagnostic_review_queue_from_run,
    write_pose_diagnostic_review_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deduplicated, video-seekable manual review queue for Pose "
            "jump/swap/identity diagnostic candidates. Does not alter scoring."
        )
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--review-video", required=True, type=Path)
    parser.add_argument("--review-video-offset-ms", type=int, default=0)
    parser.add_argument("--context-ms", type=int, default=800)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--html-output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        queue = build_pose_diagnostic_review_queue_from_run(
            run_dir=args.run_dir,
            review_video_path=args.review_video,
            review_video_offset_ms=args.review_video_offset_ms,
            context_ms=args.context_ms,
        )
        write_pose_diagnostic_review_artifacts(
            queue,
            json_path=args.output,
            html_path=args.html_output,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, PoseDiagnosticReviewError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": queue["status"],
                "tasks": queue["counts"]["tasks"],
                "by_diagnostic_type": queue["counts"]["by_diagnostic_type"],
                "output": str(args.output),
                "html": str(args.html_output),
                "accuracy_claim": queue["safety"]["accuracy_claim"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
