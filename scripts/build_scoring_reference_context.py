from __future__ import annotations

import argparse
import json
from pathlib import Path

from rallymate_scoring.scoring_context import build_scoring_reference_worklist


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_no} must contain a JSON object")
        records.append(value)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a blank per-FS02 target-direction worklist. This records a "
            "coach/operator reference, not a grade or inferred movement target."
        )
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = build_scoring_reference_worklist(
        events=_load_jsonl(args.events.resolve()),
        video_id=args.video_id,
        video_sha256=args.video_sha256,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite scoring context: {output}")
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "created_pending_worklist",
                "output": str(output),
                "observation_count": len(payload["observations"]),
                "accepted_count": 0,
                "grades_generated": 0,
                "thresholds_generated": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
