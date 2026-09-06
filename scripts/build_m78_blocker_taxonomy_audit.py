from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.blocker_taxonomy_audit import (
    build_blocker_taxonomy_replay_audit,
    write_blocker_taxonomy_replay_audit,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the M78 typed scoring-blocker replay audit"
    )
    parser.add_argument(
        "--taxonomy", type=Path, default=ROOT / "scoring-blocker-taxonomy.json"
    )
    parser.add_argument(
        "--readiness-audit",
        type=Path,
        default=ROOT
        / "reports"
        / "multivideo-scoring-readiness"
        / "m65-halpe26-three-video-typed-blocker-recovery-v1"
        / "audit.json",
    )
    parser.add_argument(
        "--baseline-runs-root",
        type=Path,
        default=ROOT / "reports" / "scoring-candidate-multivideo-m63" / "runs",
    )
    parser.add_argument(
        "--replay-runs-root",
        type=Path,
        default=ROOT / "reports" / "scoring-candidate-multivideo-m78" / "runs",
    )
    parser.add_argument(
        "--audit-id",
        default="m78-halpe26-three-video-blocker-taxonomy-v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "reports"
        / "measurement-recovery-m78"
        / "blocker-taxonomy-audit.json",
    )
    args = parser.parse_args()
    payload = build_blocker_taxonomy_replay_audit(
        taxonomy_path=args.taxonomy,
        readiness_audit_path=args.readiness_audit,
        baseline_runs_root=args.baseline_runs_root,
        replay_runs_root=args.replay_runs_root,
        audit_id=args.audit_id,
    )
    write_blocker_taxonomy_replay_audit(payload, args.output)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "output": str(args.output),
                "indicator_event_instances": payload["scope"][
                    "indicator_event_instance_count"
                ],
                "max_abs_confidence_delta": payload["replay_comparison"][
                    "max_abs_confidence_delta"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
