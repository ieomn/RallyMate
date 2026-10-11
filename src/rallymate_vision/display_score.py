"""Recover valid RTMPose response scores for display without changing evidence.

MMPose's RTMCC head emits linear SimCC responses. ``SimCCLabel.decode`` calls
``get_simcc_maximum`` without softmax, so a positive response above one is not
an invalid probability. The legacy adapter retained its coordinates and score
but marked the clamped confidence out of range. Only provenance-bound RTMPose
Halpe26 artifacts may reinterpret that flag here; scoring and raw rows remain
unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any

from rallymate_vision.pose.metadata import keypoint_schema


DISPLAY_SCORE_SEMANTICS = "rtmpose_simcc_response_clipped_for_display"
MAX_SUMMARY_BYTES = 4 * 1024 * 1024
_JOINT_NAMES = tuple(point["name"] for point in keypoint_schema("halpe26")["keypoints"])
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True)
class DisplayScorePolicy:
    job_id: str
    model_name: str
    model_sha256: str
    config_sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate summary key")
        result[name] = value
    return result


def _reject_number(value: str) -> None:
    raise ValueError("non-finite summary number")


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def load_display_score_policy(frames_path: Path) -> DisplayScorePolicy | None:
    """Require the same run's recorded backend identity, failing closed."""
    try:
        with frames_path.with_name("summary.json").open("rb") as handle:
            raw = handle.read(MAX_SUMMARY_BYTES + 1)
        if len(raw) > MAX_SUMMARY_BYTES:
            return None
        summary = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_reject_number)
        if not isinstance(summary, Mapping):
            return None
        job_id = summary.get("job_id")
        models = summary.get("models")
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128 or not isinstance(models, Mapping):
            return None
        backend = models.get("pose_backend")
        if not isinstance(backend, Mapping):
            return None
        if (backend.get("backend") != "rtmpose" or backend.get("runtime") != "pytorch"
                or backend.get("native_keypoint_format") != "halpe26"
                or backend.get("native_keypoint_count") != 26):
            return None
        model_name = backend.get("model_name")
        model_sha, config_sha = backend.get("model_sha256"), backend.get("config_sha256")
        if (not isinstance(model_name, str) or not model_name.startswith("rtmpose-")
                or "halpe26" not in model_name
                or not isinstance(model_sha, str) or not _SHA256.fullmatch(model_sha)
                or not isinstance(config_sha, str) or not _SHA256.fullmatch(config_sha)):
            return None
        return DisplayScorePolicy(job_id, model_name, model_sha, config_sha)
    except (OSError, ValueError, UnicodeError):
        return None


def recover_rtmpose_display_points(
    pose: Mapping[str, Any], width: int, height: int, *, frame_job_id: Any,
    policy: DisplayScorePolicy | None,
) -> list[dict[str, Any]]:
    """Return only genuine measured points rejected for a positive >1 response.

    The caller still owns timestamp and person-selection validation. This
    function never creates a position or accepts an offscreen/clamped point.
    """
    if (policy is None or frame_job_id != policy.job_id
            or not isinstance(width, int) or isinstance(width, bool) or not 0 < width <= 65_536
            or not isinstance(height, int) or isinstance(height, bool) or not 0 < height <= 65_536
            or pose.get("coordinate_space") != "original_frame"
            or pose.get("keypoint_format") != "halpe26"):
        return []
    points = pose.get("keypoints")
    if not isinstance(points, list):
        return []
    selected: dict[str, Mapping[str, Any]] = {}
    conflicts = set()
    for point in points:
        if not isinstance(point, Mapping) or point.get("name") not in _JOINT_NAMES:
            continue
        name = point["name"]
        if name in selected and selected[name] != point:
            conflicts.add(name)
        selected[name] = point
    recovered = []
    for name in _JOINT_NAMES:
        point = selected.get(name)
        if (point is None or name in conflicts or point.get("in_frame") is False
                or "raw_x_px" in point or "raw_y_px" in point
                or point.get("confidence_in_range") is not False):
            continue
        confidence, raw_score = _finite(point.get("confidence")), _finite(point.get("raw_confidence"))
        if confidence != 1.0 or raw_score is None or raw_score <= 1:
            continue
        if "x_px" in point or "y_px" in point:
            x, y = _finite(point.get("x_px")), _finite(point.get("y_px"))
            if x is None or y is None:
                continue
            x, y = x / width, y / height
            normalized = (point.get("x_normalized"), point.get("y_normalized"))
            if any(key in point for key in ("x_normalized", "y_normalized")):
                nx, ny = map(_finite, normalized)
                if nx is None or ny is None or abs(nx - x) > 1e-5 or abs(ny - y) > 1e-5:
                    continue
        else:
            x, y = _finite(point.get("x_normalized")), _finite(point.get("y_normalized"))
        if x is None or y is None or not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        recovered.append({
            "name": name, "x": round(x, 6), "y": round(y, 6), "confidence": 1.0,
            "source": "observed", "score_semantics": DISPLAY_SCORE_SEMANTICS,
            "raw_confidence": raw_score,
        })
    return recovered
