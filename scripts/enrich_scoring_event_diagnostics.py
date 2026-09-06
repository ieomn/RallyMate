from __future__ import annotations

import json
from pathlib import Path

from rallymate_tracking import diagnose_primary_timeline


VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    root = Path.cwd()
    counts = {}
    for video_id in VIDEO_IDS:
        records = _load(root / "runs" / "full-test" / video_id / "frames.jsonl")
        timeline = _load(root / "reports" / "primary-player" / video_id / "primary-player.jsonl")
        path = root / "reports" / "scoring-loop" / video_id / "events.jsonl"
        events = _load(path)
        for event in events:
            diagnostics = diagnose_primary_timeline(
                records,
                timeline,
                start_ms=int(event["start_ms"]),
                end_ms=int(event["end_ms"]),
            )
            event["track_diagnostics"] = diagnostics
            event["quality_flags"] = sorted(set(event["quality_flags"]) | set(diagnostics["quality_flags"]))
        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )
        counts[video_id] = len(events)
    print(json.dumps({"updated_events": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
