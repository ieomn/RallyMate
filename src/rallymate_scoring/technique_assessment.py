"""Evidence-gated assessment for the qualitative technique registry.

This module intentionally does not produce a coaching grade.  It reports what
the current run can observe, which phases are mapped to existing GS/FS events,
and whether ball/racket evidence is strong enough to discuss a contact window.
The contract is designed to remain useful while a custom ball/racket model is
being deployed on AutoDL.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from rallymate_scoring.technique_registry import (
    load_technique_registry_snapshot,
)


TECHNIQUE_ASSESSMENT_VERSION = "rallymate-technique-assessment-v1.1.0"


class TechniqueRegistrySnapshotError(ValueError):
    """Raised when a queued job's pinned qualitative registry is no longer active."""

    def __init__(self, expected: Mapping[str, Any], actual: Mapping[str, Any]):
        self.expected = dict(expected)
        self.actual = dict(actual)
        super().__init__(
            "technique registry changed since submission: "
            f"expected {self.expected.get('registry_sha256')!r}, "
            f"active {self.actual.get('registry_sha256')!r}"
        )

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": "technique_registry_changed_since_submission",
            "message": str(self),
            "expected": self.expected,
            "actual": self.actual,
        }

# Contact is deliberately a *window* hint, not a contact detector.  Keep this
# policy versioned and explicit so a client can distinguish a change in the
# evidence gate from a model or registry change.  In particular, there is no
# arbitrary confidence/fraction cutoff here: any strictly positive observation
# is enough to say that a corresponding source exists.
CONTACT_POLICY_VERSION = "rallymate-contact-window-policy-v1.0.0"
ASSESSMENT_POLICY_VERSION = "rallymate-technique-policy-v1.0.0"
CONTACT_POLICY = {
    "version": CONTACT_POLICY_VERSION,
    "presence_rule": (
        "coverage_fraction > 0; no confidence threshold and no minimum-frame "
        "cutoff"
    ),
    "coverage_scope": "run_level_summary_coverage_until_event_association",
    "requires_phase": "strike",
    "statuses": {
        "not_applicable": (
            "该技术没有 strike 阶段（例如步伐），因此不适用触球窗口判断。"
        ),
        "contact_window_proxy": (
            "球与球拍均有观测，可用于候选时窗复核；不证明真实触球。"
        ),
        "impact_window_only": (
            "仅球或球拍有观测，只能保留冲击时窗线索；不证明触球。"
        ),
        "unavailable": "没有足够的球/球拍观测，不能提出触球窗口线索。",
    },
    "accuracy_claim": False,
    "event_window_detection": False,
    "formal_scoring_impact": "none",
}

_FAMILY_LABELS = {
    "baseline": "底线击球",
    "serve": "发球",
    "return": "接发",
    "net_attack": "网前进攻",
    "footwork": "步伐",
}

_EVENT_TO_TECHNIQUE = {
    "GS01": "baseline_forehand",
    "GS02": "baseline_two_hand_backhand",
    "GS03": "baseline_one_hand_backhand",
    "GS04": "backhand_slice",
    "GS05": "forehand_slice",
    "GS06": "serve",
    "GS07": "return_forehand",
    "GS08": "return_two_hand_backhand",
    "GS09": "return_one_hand_backhand",
    "GS10": "return_backhand_slice",
    "GS11": "return_forehand_slice",
    "FS01": "split_step",
    "FS02": "first_step",
    "FS03": "crossover_step",
    "FS04": "shuffle_step",
    "FS05": "adjustment_steps",
    "FS06": "open_stance",
    "FS07": "closed_stance",
    "FS08": "lunge_support",
    "FS09": "braking_stabilization",
    "FS10": "recovery_positioning",
}

# Model adapters do not all emit the canonical ``FS01``/``GS01`` event code.
# Keep this small, deterministic vocabulary at the assessment boundary so a
# raw inference JSON can still be analysed without making the UI guess.
_EVENT_NAME_ALIASES = {
    "底线正手": "GS01", "正手底线": "GS01", "baseline_forehand": "GS01",
    "底线双手反手": "GS02", "双手反手": "GS02", "双反": "GS02", "baseline_two_hand_backhand": "GS02",
    "底线单手反手": "GS03", "单手反手": "GS03", "单反": "GS03", "baseline_one_hand_backhand": "GS03",
    "反手切削": "GS04", "反手削球": "GS04", "backhand_slice": "GS04",
    "正手切削": "GS05", "正手削球": "GS05", "forehand_slice": "GS05",
    "发球": "GS06", "serve": "GS06",
    "正手接发": "GS07", "return_forehand": "GS07",
    "双手反手接发": "GS08", "return_two_hand_backhand": "GS08",
    "单手反手接发": "GS09", "return_one_hand_backhand": "GS09",
    "反手切削接发": "GS10", "return_backhand_slice": "GS10",
    "正手切削接发": "GS11", "return_forehand_slice": "GS11",
    "分腿垫步": "FS01", "split_step": "FS01", "第一步启动": "FS02", "first_step": "FS02",
    "交叉步": "FS03", "crossover_step": "FS03", "并步移动": "FS04", "并步": "FS04", "shuffle_step": "FS04",
    "小碎步调整": "FS05", "调整步": "FS05", "open_stance": "FS06", "开放式支撑": "FS06", "开放式站位": "FS06",
    "closed_stance": "FS07", "闭合式支撑": "FS07", "关闭式站位": "FS07", "跨步支撑": "FS08", "跨步": "FS08", "lunge_support": "FS08",
    "制动急停与稳定": "FS09", "制动急停": "FS09", "braking_stabilization": "FS09",
    "击球后回位": "FS10", "回位": "FS10", "recovery_positioning": "FS10",
}


def _canonical_event_code(value: Any) -> str | None:
    """Resolve canonical event codes from model JSON labels and aliases."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    upper = text.upper()
    if upper in _EVENT_TO_TECHNIQUE:
        return upper
    folded = text.casefold()
    direct = _EVENT_NAME_ALIASES.get(folded) or _EVENT_NAME_ALIASES.get(text)
    if direct:
        return direct
    compact = "".join(folded.replace("_", " ").split())
    for alias, code in _EVENT_NAME_ALIASES.items():
        if compact == "".join(alias.casefold().replace("_", " ").split()):
            return code
    return None

_PHASE_ALIASES = {
    "observation": {"observation", "tracking", "look", "盯球", "观察"},
    "preparation": {"preparation", "prepare", "准备", "引拍"},
    "stability": {"stability", "stable", "稳定", "蓄力", "支撑"},
    "strike": {"strike", "hit", "击打", "击球", "挥拍"},
    "recovery": {"recovery", "follow_through", "恢复", "随挥", "收拍"},
    "setup": {"setup", "准备", "站位"},
    "toss": {"toss", "抛球"},
    "trophy": {"trophy", "trophy_pose", "引拍蓄力", "侧身上举"},
    "landing": {"landing", "落地"},
    "split_step": {"split_step", "分腿垫步"},
    "first_step": {"first_step", "第一步启动", "启动"},
    "compact_preparation": {"compact_preparation", "短引拍", "短准备"},
    "positioning": {"positioning", "移动定位", "定位"},
    "trigger": {"trigger", "触发", "垫步"},
    "direction_change": {"direction_change", "方向转换", "移动"},
    "support": {"support", "支撑", "站位"},
    "braking": {"braking", "制动", "急停"},
}

_STRIKE_PHASE_ALIASES = {
    "strike",
    "hit",
    "impact",
    "击打",
    "击球",
    "挥拍",
}

_COVERAGE_ALIASES = {
    "pose": ("pose_frame_fraction", "pose_coverage"),
    "ball": ("ball_frame_fraction", "ball_coverage"),
    "racket": ("racket_frame_fraction", "racket_coverage"),
    "court": (
        "court_calibrated_fraction",
        "court_detected_fraction",
        "court_coverage",
    ),
    "tracking": ("player_frame_fraction", "tracking_coverage"),
}

# The primary-player diagnostics are the only coverage values that are scoped
# to the athlete being assessed.  Ball/racket/court remain run-level evidence
# until the upstream artifacts expose an explicit association.
_PRIMARY_COVERAGE_ALIASES = {
    "pose": ("pose_coverage_fraction", "pose_frame_fraction", "pose_coverage"),
    "tracking": (
        "track_coverage_fraction",
        "tracking_coverage_fraction",
        "player_frame_fraction",
        "tracking_coverage",
    ),
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and abs(value) != float("inf") else None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _coverage_with_detail(
    summary: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """Resolve evidence coverage while retaining its scope and provenance.

    ``summary.primary_player`` is preferred for pose/tracking because the
    top-level coverage counters include every detected person.  A number of
    historical summaries do not contain that block, so the old global fields
    remain a documented fallback rather than silently disappearing.
    """

    global_raw = _as_mapping(summary.get("coverage")) or {}
    primary_value = _as_mapping(summary.get("primary_player"))
    # Some producers wrap diagnostics below ``primary_player.diagnostics``;
    # inspect both forms in deterministic priority order.
    primary_sources: list[tuple[str, Mapping[str, Any]]] = []
    if primary_value is not None:
        primary_sources.append(("summary.primary_player", primary_value))
        diagnostics = _as_mapping(primary_value.get("diagnostics"))
        if diagnostics is not None:
            primary_sources.append(
                ("summary.primary_player.diagnostics", diagnostics)
            )

    result: dict[str, float] = {}
    detail: dict[str, dict[str, Any]] = {}
    for key, global_names in _COVERAGE_ALIASES.items():
        candidates: list[tuple[str, str, Any, str]] = []
        if key in _PRIMARY_COVERAGE_ALIASES:
            for source, raw in primary_sources:
                for name in _PRIMARY_COVERAGE_ALIASES[key]:
                    candidates.append((source, name, raw.get(name), "primary_player"))
        for name in global_names:
            candidates.append(("summary.coverage", name, global_raw.get(name), "global"))

        selected_source = "missing"
        selected_field: str | None = None
        selected_scope = "missing"
        selected_value: float | None = None
        for source, name, raw_value, scope in candidates:
            number = _number(raw_value)
            if number is None:
                continue
            selected_value = max(0.0, min(1.0, float(number)))
            selected_source = f"{source}.{name}"
            selected_field = name
            selected_scope = scope
            break
        fraction = selected_value if selected_value is not None else 0.0
        result[key] = fraction
        detail[key] = {
            "fraction": round(fraction, 6),
            "percent": round(fraction * 100, 2),
            "source": selected_source,
            "field": selected_field,
            "scope": selected_scope,
            "source_kind": selected_scope,
            "fallback_used": selected_scope == "global",
        }
    return result, detail


def _coverage(summary: Mapping[str, Any]) -> dict[str, float]:
    """Backward-compatible coverage-only view for internal callers/tests."""

    return _coverage_with_detail(summary)[0]


def _augment_coverage_from_records(
    coverage: dict[str, float],
    detail: dict[str, dict[str, Any]],
    records: list[Mapping[str, Any]],
) -> None:
    """Fill missing pose/tracking coverage from measured model records.

    Some inference JSON exports contain only indicator rows and omit the Stage
    1 ``coverage`` block.  A measured row is direct evidence that the pose and
    player track existed for that row; use it as a scoped fallback while
    preserving explicit summary coverage whenever present.
    """
    measured = False
    for record in records:
        status = record.get("feature_status") or record.get("scoring_feature_status")
        if status == "measured":
            measured = True
            break
        features = record.get("features") or record.get("scoring_features")
        if isinstance(features, list) and any(
            isinstance(item, Mapping) and item.get("valid") is True
            for item in features
        ):
            measured = True
            break
    if not measured:
        return
    for key in ("pose", "tracking"):
        if coverage.get(key, 0.0) > 0.0:
            continue
        coverage[key] = 1.0
        detail[key] = {
            "fraction": 1.0,
            "percent": 100.0,
            "source": "indicator_feature_records",
            "field": None,
            "scope": "record",
            "source_kind": "record",
            "fallback_used": True,
        }


def _event_codes(summary: Mapping[str, Any], records: list[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    loop = summary.get("minimum_scoring_loop")
    count_sources: list[Mapping[str, Any]] = []
    if isinstance(loop, Mapping) and isinstance(loop.get("event_counts"), Mapping):
        count_sources.append(loop["event_counts"])
    # New technique detectors may publish action counts at the summary root;
    # accept both names so serve/return/net actions can become observable
    # without requiring the legacy event namespace.
    for key in ("event_counts", "technique_counts", "action_counts"):
        value = summary.get(key)
        if isinstance(value, Mapping): count_sources.append(value)
    for counts in count_sources:
        for key, value in counts.items():
            code = _canonical_event_code(key)
            if code and _number(value) and float(value) > 0:
                result.add(code)
    for record in records:
        for key in ("event_code", "event", "action", "action_type", "technique_id", "technique", "label", "name", "name_zh"):
            code = _canonical_event_code(record.get(key))
            if code:
                result.add(code)
    # A future detector can publish a stable list without requiring a new
    # version of this module.
    for key in ("technique_events", "observed_techniques", "detected_techniques"):
        values = summary.get(key)
        if isinstance(values, Mapping):
            for item, value in values.items():
                code = _canonical_event_code(item)
                if code and value:
                    result.add(code)
        elif isinstance(values, list):
            for item in values:
                code = _canonical_event_code(item)
                if code:
                    result.add(code)

    # New model exports often nest detections below ``predictions``,
    # ``detections`` or ``actions``. Walk only those known containers (rather
    # than every string in the summary) to avoid treating prose as evidence.
    def visit(value: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, Mapping):
            # A prior assessment may itself be embedded in a model export and
            # contain all registry techniques with ``observed: false``. Do not
            # mistake those catalog labels for detections.
            explicitly_absent = value.get("observed") is False or value.get("detected") is False
            if not explicitly_absent:
                for key in ("event_code", "event", "action", "action_type", "technique_id", "technique", "label", "name", "name_zh", "class_name"):
                    code = _canonical_event_code(value.get(key))
                    if code:
                        result.add(code)
            for key in ("predictions", "detections", "actions", "events", "techniques", "results", "inference", "model_output"):
                child = value.get(key)
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, depth + 1)

    visit(summary)
    for record in records:
        visit(record)
    return result


def _technique_observed(technique: Mapping[str, Any], events: set[str]) -> bool:
    technique_id = str(technique["id"])
    if technique_id in events:
        return True
    for event_code, mapped_id in _EVENT_TO_TECHNIQUE.items():
        if mapped_id == technique_id and event_code in events:
            return True
    # Accept an explicit technique id or alias from an upstream classifier.
    aliases = {str(item).casefold() for item in technique.get("aliases", []) if isinstance(item, str)}
    return bool(aliases & {item.casefold() for item in events})


def _phase_statuses(
    technique: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    observed: bool,
) -> list[dict[str, Any]]:
    phases = [str(item) for item in technique.get("phases", [])]
    explicit: set[str] = set()
    for record in records:
        record_technique = record.get("technique_id") or record.get("action_type")
        if record_technique and str(record_technique) != str(technique["id"]):
            continue
        for key in ("phase_key", "phase", "stage", "stage_code"):
            value = record.get(key)
            if isinstance(value, str):
                explicit.add(value.casefold())
    result: list[dict[str, Any]] = []
    for phase in phases:
        aliases = _PHASE_ALIASES.get(phase, {phase})
        matched = bool({item.casefold() for item in aliases} & explicit)
        result.append(
            {
                "phase": phase,
                "status": "measured" if matched else "proxy" if observed else "unavailable",
                "evidence_source": "explicit_phase_record" if matched else "event_presence" if observed else None,
            }
        )
    return result


def _status(observed: bool, required_score: float) -> str:
    if not observed:
        return "not_observed"
    if required_score < 0.4:
        return "unavailable"
    if required_score < 0.72:
        return "partial"
    return "ready"


def _key_field_analysis(
    required: list[str], enhanced: list[str], coverage: Mapping[str, float], observed: bool
) -> dict[str, Any]:
    """Expose per-field evidence so consumers can explain a not-observed row.

    The prior contract returned only an aggregate readiness percentage.  That
    made it impossible for the frontend/exporter to tell whether a baseline
    action was absent or merely missing tracking.  Values remain evidence
    coverage (never a technique score).
    """
    field_labels = {
        "pose": "人体姿态骨架",
        "tracking": "球员跟踪",
        "ball": "球轨迹",
        "racket": "球拍观测",
        "court": "场地标定",
    }
    fields: list[dict[str, Any]] = []
    for name in required:
        fraction = max(0.0, min(1.0, float(coverage.get(name, 0.0))))
        fields.append({
            "field": name,
            "field_label_zh": field_labels.get(name, name),
            "required": True,
            "coverage_percent": round(fraction * 100),
            "status": "ready" if observed and fraction >= 0.72 else "partial" if observed and fraction > 0 else "missing",
        })
    for name in enhanced:
        fraction = max(0.0, min(1.0, float(coverage.get(name, 0.0))))
        fields.append({
            "field": name,
            "field_label_zh": field_labels.get(name, name),
            "required": False,
            "coverage_percent": round(fraction * 100),
            "status": "available" if observed and fraction > 0 else "optional_missing",
        })
    return {
        "required_fields": fields[: len(required)],
        "enhanced_fields": fields[len(required):],
        "missing_required_fields": [item["field"] for item in fields[: len(required)] if item["status"] == "missing"],
    }


def _has_strike_phase(technique: Mapping[str, Any]) -> bool:
    """Return whether a registry item has a strike/impact phase.

    Footwork entries intentionally have no strike phase.  Checking the phase
    contract rather than the family name also keeps this correct for future
    registry families and aliases.
    """

    phases = technique.get("phases", [])
    if not isinstance(phases, (list, tuple, set)):
        return False
    strike_aliases = {item.casefold() for item in _STRIKE_PHASE_ALIASES}
    return any(
        str(phase).strip().casefold() in strike_aliases
        for phase in phases
    )


def _contact_status(
    technique: Mapping[str, Any], coverage: Mapping[str, float], observed: bool
) -> str:
    """Classify the contact-window evidence without an arbitrary .5 cutoff."""

    if (
        str(technique.get("family", "")).strip().casefold() == "footwork"
        or not _has_strike_phase(technique)
    ):
        return "not_applicable"
    if not observed:
        return "unavailable"
    has_ball = coverage.get("ball", 0.0) > 0.0
    has_racket = coverage.get("racket", 0.0) > 0.0
    if has_ball and has_racket:
        return "contact_window_proxy"
    if has_ball or has_racket:
        return "impact_window_only"
    return "unavailable"


def _expected_registry_snapshot(summary: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Read the immutable submission snapshot from a Stage 1 summary."""

    input_block = _as_mapping(summary.get("input")) or {}
    upstream = _as_mapping(input_block.get("upstream_metadata"))
    if upstream is None:
        upstream = _as_mapping(summary.get("upstream_metadata"))
    if upstream is None or "technique_registry" not in upstream:
        return None
    expected = _as_mapping(upstream.get("technique_registry"))
    if expected is None:
        raise ValueError("input.upstream_metadata.technique_registry must be an object")
    for field in ("registry_id", "registry_version", "registry_sha256"):
        value = expected.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "input.upstream_metadata.technique_registry."
                f"{field} must be a non-empty string"
            )
    return expected


def _resolve_registry_snapshot(
    summary: Mapping[str, Any], registry_path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the active registry and verify a job-pinned snapshot if present."""

    # Read and hash one exact byte snapshot. An operator may atomically replace
    # an audited override while a job is being assessed; loading twice could
    # otherwise pair payload A with digest/version B.
    registry, digest = load_technique_registry_snapshot(registry_path)
    actual = {
        "registry_id": registry["registry_id"],
        "registry_version": registry["registry_version"],
        "registry_sha256": digest,
    }
    expected = _expected_registry_snapshot(summary)
    if expected is None:
        return registry, {
            "status": "legacy_unpinned",
            "expected": None,
            "actual": actual,
        }
    mismatches = {
        field: {"expected": expected.get(field), "actual": actual.get(field)}
        for field in ("registry_id", "registry_version", "registry_sha256")
        if expected.get(field) != actual.get(field)
    }
    if mismatches:
        raise TechniqueRegistrySnapshotError(expected, actual)
    return registry, {
        "status": "verified",
        "expected": dict(expected),
        "actual": actual,
    }


def build_technique_assessment(
    summary: Mapping[str, Any],
    indicator_feature_records: Iterable[Mapping[str, Any]] = (),
    *,
    registry_path=None,
) -> dict[str, Any]:
    """Build a deterministic, evidence-first technique assessment.

    The returned ``evidence_score_0_to_100`` is a readiness signal, not a
    quality grade.  ``score_0_to_100`` is intentionally ``None`` until a
    downstream calibration service supplies coach-grounded labels.
    """

    if not isinstance(summary, Mapping):
        raise ValueError("summary must be an object")
    records = [item for item in indicator_feature_records if isinstance(item, Mapping)]
    registry, registry_snapshot = _resolve_registry_snapshot(summary, registry_path)
    coverage, coverage_detail = _coverage_with_detail(summary)
    _augment_coverage_from_records(coverage, coverage_detail, records)
    events = _event_codes(summary, records)
    observed_ids = {
        _EVENT_TO_TECHNIQUE[event]
        for event in events
        if event in _EVENT_TO_TECHNIQUE
    }
    result_items: list[dict[str, Any]] = []
    family_labels: dict[str, str] = {}
    family_order: list[str] = []
    registry_family_labels = registry.get("family_labels")
    registry_family_labels = (
        registry_family_labels if isinstance(registry_family_labels, Mapping) else {}
    )
    for technique in registry["techniques"]:
        technique_id = str(technique["id"])
        family = str(technique["family"])
        if family not in family_labels:
            family_order.append(family)
            configured_label = registry_family_labels.get(family)
            if not isinstance(configured_label, str) or not configured_label.strip():
                configured_label = technique.get("family_name_zh")
            family_labels[family] = str(
                configured_label
                if isinstance(configured_label, str) and configured_label.strip()
                else _FAMILY_LABELS.get(family, family)
            )
        observed = technique_id in observed_ids or _technique_observed(technique, events)
        required = [str(item) for item in technique["required_evidence"]]
        enhanced = [str(item) for item in technique.get("enhanced_evidence", [])]
        required_score = min((coverage.get(item, 0.0) for item in required), default=0.0)
        enhanced_score = statistics.fmean(coverage.get(item, 0.0) for item in enhanced) if enhanced else 0.0
        evidence_score = round((required_score * 0.7 + enhanced_score * 0.3) * 100)
        item_status = _status(observed, required_score)
        limitations = list(technique.get("proxy_limits", []))
        if not observed:
            limitations.insert(0, "当前运行没有该技术的事件证据；不会根据缺失数据推断动作已发生。")
        if required_score < 0.72 and observed:
            limitations.insert(0, "必需证据覆盖不足，当前只适合做局部复核。")
        result_items.append(
            {
                "technique_id": technique_id,
                "family": family,
                "family_name_zh": family_labels[family],
                "name_zh": technique["name_zh"],
                "status": item_status,
                "observed": observed,
                "evidence_score_0_to_100": evidence_score if observed else 0,
                "score_0_to_100": None,
                "formal_grade": None,
                "phase_statuses": _phase_statuses(technique, records, observed),
                "evidence": {
                    "required_coverage": {key: round(coverage.get(key, 0.0) * 100) for key in required},
                    "enhanced_coverage": {key: round(coverage.get(key, 0.0) * 100) for key in enhanced},
                    "contact_status": _contact_status(technique, coverage, observed),
                    "contact_policy_version": CONTACT_POLICY_VERSION,
                    "event_codes": sorted(events),
                },
                "key_field_analysis": _key_field_analysis(required, enhanced, coverage, observed),
                "core_visual_features": list(technique["core_visual_features"]),
                "reference_constraints": deepcopy(
                    list(technique.get("reference_constraints", []))
                ),
                "limitations_zh": limitations[:5],
                "semantics": "evidence_readiness_reference_only",
            }
        )

    observed_items = [item for item in result_items if item["observed"]]
    aggregate = round(statistics.fmean(item["evidence_score_0_to_100"] for item in observed_items)) if observed_items else None
    family_summary: dict[str, dict[str, Any]] = {}
    for family in family_order:
        label = family_labels[family]
        items = [item for item in result_items if item["family"] == family]
        observed_family = [item for item in items if item["observed"]]
        family_summary[family] = {
            "name_zh": label,
            "observed_count": len(observed_family),
            "total_count": len(items),
            "evidence_score_0_to_100": round(statistics.fmean(item["evidence_score_0_to_100"] for item in observed_family)) if observed_family else None,
        }
    return {
        "assessment_version": TECHNIQUE_ASSESSMENT_VERSION,
        "registry_version": registry["registry_version"],
        "registry_snapshot": registry_snapshot,
        "job_id": summary.get("job_id"),
        "overall_evidence_score_0_to_100": aggregate,
        "formal_score_available": False,
        "formal_score_message_zh": "技术定义已注册，但数值权重与教练标定尚未启用；当前只返回证据就绪度。",
        "coverage": {key: round(value * 100) for key, value in coverage.items()},
        "coverage_detail": coverage_detail,
        "coverage_source": {
            key: value["source"] for key, value in coverage_detail.items()
        },
        "family_summary": family_summary,
        "techniques": result_items,
        "policy": {
            "version": ASSESSMENT_POLICY_VERSION,
            "contact": deepcopy(CONTACT_POLICY),
            "coverage": {
                "units": "fraction_0_to_1",
                "primary_player_preferred_for": ["pose", "tracking"],
                "fallback_source": "summary.coverage",
                "fallback_is_not_primary_identity": True,
                "ball_racket_association": "unassociated_until_explicit_link",
            },
            "readiness": {
                "required_evidence_weight": 0.7,
                "enhanced_evidence_weight": 0.3,
                "partial_lower_bound": 0.4,
                "ready_lower_bound": 0.72,
                "source": "service_operational_gate_not_doc_defined_coach_score",
            },
            "formal_score": {
                "available": False,
                "requires_coach_calibration": True,
            },
        },
        "safety": {
            "is_formal_technique_score": False,
            "is_coach_score": False,
            "missing_evidence_is_not_imputed": True,
            "contact_requires_ball_and_racket": True,
            "contact_is_not_accuracy_claim": True,
        },
    }
