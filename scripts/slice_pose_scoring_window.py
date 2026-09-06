#!/usr/bin/env python3
"""Create an exact processed-index window for a fair pose scoring comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _slice_jsonl(source: Path, output: Path, start: int, end: int, key: str) -> int:
    selected = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        value = int(record[key] if key in record else record["frame"][key])
        if start <= value < end:
            selected.append(record)
    expected = end - start
    indexes = [
        int(item[key] if key in item else item["frame"][key]) for item in selected
    ]
    if indexes != list(range(start, end)):
        raise RuntimeError(
            f"{source} does not contain exact contiguous window {start}:{end}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected),
        encoding="utf-8",
    )
    return expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-source", type=Path, required=True)
    parser.add_argument("--timeline-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-processed-index", type=int, required=True)
    parser.add_argument("--end-processed-index", type=int, required=True)
    args = parser.parse_args()
    frame_count = _slice_jsonl(
        args.frames_source,
        args.output_dir / "frames.jsonl",
        args.start_processed_index,
        args.end_processed_index,
        "processed_index",
    )
    timeline_count = _slice_jsonl(
        args.timeline_source,
        args.output_dir / "primary-player.jsonl",
        args.start_processed_index,
        args.end_processed_index,
        "processed_index",
    )
    print(json.dumps({"frames": frame_count, "timeline": timeline_count}))


if __name__ == "__main__":
    main()
