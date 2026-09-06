from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_review import (
    PoseDiagnosticReviewError,
    validate_decisions_csv,
    validate_pose_diagnostic_review_queue_sources,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a browser-exported Pose diagnostic review CSV. The "
            "result remains non-adjudicated and never changes scoring gates."
        )
    )
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"refusing to overwrite {args.output}")
    try:
        queue = json.loads(args.queue.read_text(encoding="utf-8"))
        source_validation = validate_pose_diagnostic_review_queue_sources(queue)
        validation = validate_decisions_csv(
            args.decisions.read_text(encoding="utf-8"), queue
        )
    except (OSError, ValueError, PoseDiagnosticReviewError) as exc:
        parser.error(str(exc))
    report = {
        "schema_version": "1.0.0",
        "report_version": "pose-diagnostic-review-decisions-v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": validation["status"],
        "queue": {
            "path": str(args.queue.resolve()),
            "sha256": _sha256(args.queue),
            "artifact_binding_sha256": queue["source"]["artifact_binding_sha256"],
            "task_count": queue["counts"]["tasks"],
        },
        "decisions": {
            "path": str(args.decisions.resolve()),
            "sha256": _sha256(args.decisions),
            "decision_counts": validation["decision_counts"],
        },
        "truth_status": validation["truth_status"],
        "source_validation": source_validation,
        "safety": {
            "accuracy_claim": False,
            "decisions_are_adjudicated_truth": False,
            "quality_gate_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
