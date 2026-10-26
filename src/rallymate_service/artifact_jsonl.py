"""Read local event evidence without whole-file text/line-list copies."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

EVENT_FILE_MAX_BYTES = 64 * 1024 * 1024
EVENT_MAX_LINES = 5000


class ArtifactRecordLimit(ValueError):
    pass


def read_event_records(path: Path, *, max_bytes: int = EVENT_FILE_MAX_BYTES,
                       max_lines: int = EVENT_MAX_LINES,
                       object_pairs_hook: Callable | None = None,
                       parse_constant: Callable | None = None) -> list[dict[str, Any]]:
    if path.stat().st_size > max_bytes:
        raise ArtifactRecordLimit("event_file_limit_exceeded")
    options = {}
    if object_pairs_hook is not None:
        options["object_pairs_hook"] = object_pairs_hook
    if parse_constant is not None:
        options["parse_constant"] = parse_constant
    records = []
    consumed = 0
    with path.open("rb") as stream:
        for number in range(1, max_lines + 2):
            # Also bound a file that grows after stat, including one huge line.
            line = stream.readline(max_bytes - consumed + 1)
            if not line:
                break
            consumed += len(line)
            if consumed > max_bytes or number > max_lines:
                raise ArtifactRecordLimit("event_file_limit_exceeded")
            if not line.strip():
                continue
            row = json.loads(line.decode("utf-8"), **options)
            if not isinstance(row, dict):
                raise ValueError("event_record_not_object")
            records.append(row)
    return records
