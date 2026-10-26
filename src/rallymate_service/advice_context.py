"""Small, family-scoped views of the existing evidence-gated assessment.

This transport does not calculate new measurements or author coaching facts.
The advice consumer still validates the projected evidence before using it.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from rallymate_service.report_compatibility import project_report

AdviceTechnique = Literal["底线击球", "发球", "接发", "网前进攻", "步伐"]
ADVICE_CONTEXT_VERSION = "advice-context-v1"
ADVICE_CONTEXT_MAX_BYTES = 512_000
_FAMILIES = {"底线击球": "baseline", "发球": "serve", "接发": "return",
             "网前进攻": "net_attack", "步伐": "footwork"}


class AdviceContextTooLarge(ValueError):
    """The complete requested-family context exceeds its transport budget."""


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _scalars(value: Any, fields: str) -> dict:
    source = _object(value)
    return {key: source[key] for key in fields.split()
            if key in source and (source[key] is None or isinstance(source[key], (str, int, float, bool)))}


def project_advice_context(job_id: str, assessment: dict, technique: AdviceTechnique) -> dict:
    """Preserve every field read by advice-evidence, without large proof graphs.

    Use the established legacy motion projection because that is the advice
    consumer's contract. Unsupported model classifications remain unsupported.
    Never sample episodes: a bounded response either contains all requested
    evidence or fails explicitly, so its episode count cannot be understated.
    """
    if technique not in _FAMILIES:
        raise ValueError("unsupported advice technique")
    family = _FAMILIES[technique]
    source = _object(assessment)
    selected = {**_scalars(source, "job_id"), "techniques": []}
    family_summary = _object(source.get("family_summary"))
    if family in family_summary:
        selected["family_summary"] = {family: _scalars(
            family_summary[family], "name_zh observed_count total_count recognition_status recognition_reason_zh")}
    for row in _rows(source.get("techniques")):
        if row.get("family") != family:
            continue
        selected["techniques"].append({
            **_scalars(row, "family name_zh observed status"),
            "phase_statuses": [_scalars(phase, "phase status evidence_source")
                               for phase in _rows(row.get("phase_statuses"))],
        })
    motion = _object(_object(source.get("action_recognition")).get("motion_analysis"))
    motion_view = _scalars(motion, "schema_version status contact_confirmed")
    source_families = _object(motion.get("families"))
    if family in source_families:
        motion_family = _object(source_families[family])
        motion_view["families"] = {family: {
            **_scalars(motion_family, "status reason_zh"),
            "episodes": [{
                **_scalars(episode, "episode_id family start_ms peak_ms end_ms method "
                           "contact_confirmed analysis_status"),
                "classification": _scalars(episode.get("classification"), "status label"),
                "evidence": _scalars(episode.get("evidence"), "pose_samples"),
                "metrics": _scalars(episode.get("metrics"), "peak_wrist_speed_torso_per_s "
                                    "wrist_path_torso elbow_extension_deg shoulder_line_change_deg"),
                "phases": [_scalars(phase, "phase status start_ms end_ms")
                           for phase in _rows(episode.get("phases"))],
            } for episode in _rows(motion_family.get("episodes"))],
        }}
    result = {
        "advice_context_version": ADVICE_CONTEXT_VERSION,
        "job_id": job_id, "status": "ready", "technique": technique, "family": family,
        "technique_assessment": selected,
        "action_recognition": project_report({"motion_analysis": motion_view}, "legacy-v1"),
    }
    try:
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except (ValueError, OverflowError) as exc:
        raise AdviceContextTooLarge("advice context is not finite JSON") from exc
    if size > ADVICE_CONTEXT_MAX_BYTES:
        raise AdviceContextTooLarge("complete advice context exceeds byte limit")
    return result
