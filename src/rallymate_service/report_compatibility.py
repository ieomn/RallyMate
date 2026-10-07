"""Explicit read-only projections for the original web client's report contract.

Measurement builders and immutable artifacts keep their native schemas. Only
API representations opt into this adapter; ``report_contract=current`` bypasses
it. A projection copies existing legacy fields, never promotes independently
available features to a fully measured indicator or invents technical grades.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Literal

ReportContract = Literal["legacy-v1", "current"]
DEFAULT_REPORT_CONTRACT: ReportContract = "legacy-v1"
PROJECTION_VERSION = "legacy-web-report-v1"


def _fields(value: dict, names: str) -> dict:
    return {name: deepcopy(value[name]) for name in names.split() if name in value}


def _provenance(value: dict) -> dict:
    return {
        "projection_version": PROJECTION_VERSION,
        "source_schema_version": value["schema_version"],
        "target_schema_version": "1.0.0",
        "native_representation_query": "report_contract=current",
        "measurement_values_preserved": True,
    }


def _footwork(value: dict) -> dict:
    result = _fields(value, "schema_version review_version status reason reason_zh episodes "
                     "episode_count returned_episode_count is_truncated rejected_event_count "
                     "rejected_indicator_count limitations_zh")
    result["episodes"] = []
    for episode in value.get("episodes", []):
        projected = _fields(episode, "event_id video_id event_code name_zh person_track_id "
                            "start_ms end_ms")
        projected["indicators"] = [
            _fields(indicator, "indicator_id name_zh feature_status scoring_status features")
            for indicator in episode.get("indicators", [])
        ]
        result["episodes"].append(projected)
    result["schema_version"] = "1.0.0"
    result["compatibility"] = _provenance(value)
    return result


def _motion(value: dict) -> dict:
    result = _fields(value, "schema_version analysis_version method status contact_confirmed limitations_zh")
    families = {}
    omitted = 0
    for name in ("baseline", "serve", "return"):
        source = value.get("families", {}).get(name)
        if not isinstance(source, dict):
            continue
        family = _fields(source, "status reason_zh summary")
        episodes = []
        for episode in source.get("episodes", []):
            # Model-derived classifications cannot be relabelled as legacy
            # rule inferences. They remain available in the native view.
            if episode.get("classification", {}).get("status") not in {"rule_inferred", "unclassified"}:
                omitted += 1
                continue
            episodes.append(_fields(episode, "episode_id family person_track_id start_ms peak_ms end_ms "
                            "candidate_peak_ms phase_timing_status analysis_status classification method "
                            "contact_confirmed racket_hand phases metrics rotation_analysis metric_notes_zh "
                            "evidence limitations_zh"))
        family["episodes"] = episodes
        if len(episodes) != len(source.get("episodes", [])):
            family["status"] = "analyzed" if episodes else "insufficient_evidence"
            family["reason_zh"] = "此兼容视图仅展示旧版支持的规则候选；其他识别结果请读取 current 报告。"
            family["summary"] = {"analyzed_count": len(episodes), "classifications": dict(
                Counter(episode["classification"]["label"] for episode in episodes))}
        families[name] = family
    result["families"] = families
    if omitted:
        result["status"] = "available" if any(f["episodes"] for f in families.values()) else "insufficient_evidence"
    result["schema_version"] = "1.0.0"
    result["compatibility"] = {**_provenance(value), "omitted_unsupported_episode_count": omitted}
    return result


def project_report(value: Any, contract: ReportContract = DEFAULT_REPORT_CONTRACT) -> Any:
    """Project nested API report views without mutating cached/native data.

    Only the two known 1.1 schemas are projected. Historical 1.0 values and
    unknown future versions retain their version, so old parsers fail closed.
    """
    if contract == "current":
        return value
    if contract != "legacy-v1":
        raise ValueError("unsupported report contract")
    if isinstance(value, list):
        return [project_report(item, contract) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if isinstance(item, dict) and item.get("schema_version") == "1.1.0":
            if key == "footwork_review":
                result[key] = _footwork(item)
                continue
            if key == "motion_analysis":
                result[key] = _motion(item)
                continue
        result[key] = project_report(item, contract)
    return result
