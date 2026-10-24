"""Persist pose rejection evidence without manufacturing missing poses."""
from __future__ import annotations

import math
from numbers import Integral, Real


VERSION = "pose-association-frame-diagnostics-v1.0.0"


def _json_copy(value, path: str, invalid_numbers: list[dict]):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if math.isfinite(number):
            return number
        invalid_numbers.append({"json_pointer": path, "representation": str(number)})
        return None
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("pose diagnostic keys must be strings")
        return {key: _json_copy(item, path + "/" + key.replace("~", "~0").replace("/", "~1"), invalid_numbers)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item, path + "/" + str(index), invalid_numbers)
                for index, item in enumerate(value)]
    raise ValueError("pose diagnostics must contain JSON-compatible evidence")


def bind_pose_association_diagnostics(diagnostics: dict | None, frame: dict) -> dict | None:
    """Bind one completed inference call to its source frame, with owned data.

    Invalid model numbers can occur in rejected candidates. Preserve their exact
    paths and representations instead of emitting non-standard JSON NaN/Inf.
    This never edits accepted poses or treats missing diagnostics as success.
    """
    if diagnostics is None or diagnostics == {}:
        return None
    if not isinstance(diagnostics, dict):
        raise ValueError("pose diagnostics must be an object")
    source_frame = {}
    for key in ("index", "processed_index", "timestamp_ms", "width", "height"):
        value = frame.get(key)
        minimum = 1 if key in {"width", "height"} else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError("pose diagnostics require valid frame." + key)
        source_frame[key] = value
    timestamp_source = frame.get("timestamp_source")
    if not isinstance(timestamp_source, str) or not timestamp_source:
        raise ValueError("pose diagnostics require frame.timestamp_source")
    source_frame["timestamp_source"] = timestamp_source
    # Absolute source PTS is distinct from relative playback time when present.
    if "source_timestamp_ms" in frame:
        value = frame["source_timestamp_ms"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("pose diagnostics require valid frame.source_timestamp_ms")
        source_frame["source_timestamp_ms"] = value
    invalid_numbers: list[dict] = []
    evidence = _json_copy(diagnostics, "/diagnostics", invalid_numbers)
    return {"version": VERSION, "source_frame": source_frame,
            "diagnostics": evidence, "invalid_numeric_values": invalid_numbers,
            "semantics": "same_frame_pose_association_evidence_not_identity_truth"}
