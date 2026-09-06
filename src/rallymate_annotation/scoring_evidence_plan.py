from __future__ import annotations

import csv
import hashlib
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_annotation.scoring_action_readiness import (
    PARTIAL,
    PENDING,
    SATISFIED,
    STATUSES,
    validate_scoring_truth_action_readiness,
)
from rallymate_annotation.scoring_action_worklist import (
    validate_scoring_truth_action_worklist,
)


SCHEMA_VERSION = "1.0.0"
PLAN_VERSION = "scoring-truth-evidence-acquisition-plan-v1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _unit_id(key: str) -> str:
    return "steu-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _status_from_rows(values: list[str]) -> str:
    if values and all(value == SATISFIED for value in values):
        return SATISFIED
    if any(value in {SATISFIED, PARTIAL} for value in values):
        return PARTIAL
    return PENDING


def _evidence_status(item: Mapping[str, Any], unit: Mapping[str, Any]) -> str:
    evidence = item.get("evidence", [])
    if not isinstance(evidence, list):
        return PENDING
    kind = unit["evidence_type"]
    if kind == "full_timeline_pose_diagnostic_truth":
        diagnostic = unit["diagnostic_type"]
        row = next(
            (
                value
                for value in evidence
                if value.get("kind") == kind
                and value.get("diagnostic_type") == diagnostic
            ),
            None,
        )
        if row and row.get("coverage_status") == "full_timeline":
            return SATISFIED
        if row and int(row.get("reviewed_frame_count", 0)) > 0:
            return PARTIAL
        return PENDING
    if kind == "accepted_manual_event_boundary":
        return (
            SATISFIED
            if any(value.get("kind") == "matched_manual_event" for value in evidence)
            else PENDING
        )
    if kind == "accepted_manual_phase":
        phase = unit["phase_name"]
        row = next(
            (
                value
                for value in evidence
                if value.get("kind") == "manual_phase"
                and value.get("phase_name") == phase
            ),
            None,
        )
        return (
            SATISFIED
            if row is not None
            and isinstance(row.get("timestamp_ms"), int)
            and not isinstance(row.get("timestamp_ms"), bool)
            else PENDING
        )
    if kind == "accepted_manual_side_semantics":
        rows = [value for value in evidence if value.get("kind") == "manual_semantic"]
        if item.get("status") == SATISFIED:
            return SATISFIED
        return PARTIAL if rows else PENDING
    if kind == "accepted_dense_keypoint_truth":
        row = next(
            (value for value in evidence if value.get("kind") == "dense_keypoint_truth_cell"),
            None,
        )
        return SATISFIED if row and row.get("covered") is True else PENDING
    if kind == "accepted_manual_feature_truth":
        rows = [value for value in evidence if value.get("kind") == "feature_error_detail"]
        if rows and all(value.get("ground_truth_valid") is True for value in rows):
            return SATISFIED
        return PARTIAL if rows else PENDING
    if kind == "accepted_target_direction":
        row = next(
            (value for value in evidence if value.get("kind") == "scoring_reference_context"),
            None,
        )
        if row and row.get("status") in {"accepted", "unobservable"}:
            return SATISFIED
        return PARTIAL if row and row.get("status") != "pending" else PENDING
    raise ValueError(f"unsupported evidence unit type: {kind}")


def _base_spec(
    *,
    key: str,
    evidence_type: str,
    scope: str,
    target_artifact: str,
    human_roles: list[str],
    event: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "unit_id": _unit_id(key),
        "deduplication_key": key,
        "evidence_type": evidence_type,
        "scope": scope,
        "event_id": event.get("event_id") if event else None,
        "event_code": event.get("event_code") if event else None,
        "start_ms": event.get("start_ms") if event else None,
        "end_ms": event.get("end_ms") if event else None,
        "target_artifact": target_artifact,
        "required_human_roles": human_roles,
        **extra,
    }


def _required_units(
    item: Mapping[str, Any], work_item: Mapping[str, Any]
) -> list[dict[str, Any]]:
    event_id = str(item["event_id"])
    event = {
        "event_id": event_id,
        "event_code": item["event_code"],
        "start_ms": work_item["start_ms"],
        "end_ms": work_item["end_ms"],
    }
    requirement = str(item["truth_requirement"])
    standard_roles = ["annotator", "independent_reviewer"]
    units: list[dict[str, Any]] = []

    if requirement == "full_timeline_manual_keypoint_jump_truth":
        units.append(
            _base_spec(
                key="full_timeline_pose::keypoint_jump",
                evidence_type="full_timeline_pose_diagnostic_truth",
                scope="full_timeline",
                target_artifact="pose-diagnostic-coverage.csv + pose-diagnostic-positives.csv",
                human_roles=["annotator_1", "annotator_2", "independent_reviewer"],
                diagnostic_type="keypoint_jump",
            )
        )
    elif requirement == "full_timeline_manual_left_right_swap_truth":
        units.append(
            _base_spec(
                key="full_timeline_pose::left_right_swap",
                evidence_type="full_timeline_pose_diagnostic_truth",
                scope="full_timeline",
                target_artifact="pose-diagnostic-coverage.csv + pose-diagnostic-positives.csv",
                human_roles=["annotator_1", "annotator_2", "independent_reviewer"],
                diagnostic_type="left_right_swap",
            )
        )
    elif requirement == "manual_primary_identity_continuity_truth":
        for diagnostic in ("primary_identity_ambiguity", "source_track_switch"):
            units.append(
                _base_spec(
                    key=f"full_timeline_pose::{diagnostic}",
                    evidence_type="full_timeline_pose_diagnostic_truth",
                    scope="full_timeline",
                    target_artifact="pose-diagnostic-coverage.csv + pose-diagnostic-positives.csv",
                    human_roles=["annotator_1", "annotator_2", "independent_reviewer"],
                    diagnostic_type=diagnostic,
                )
            )
    elif requirement == "manual_target_direction_semantics":
        units.append(
            _base_spec(
                key=f"event::{event_id}::target_direction",
                evidence_type="accepted_target_direction",
                scope="event",
                target_artifact="scoring-reference-context target_directions",
                human_roles=standard_roles,
                event=event,
                semantic_key="target_direction",
            )
        )
    else:
        units.append(
            _base_spec(
                key=f"event::{event_id}::boundary",
                evidence_type="accepted_manual_event_boundary",
                scope="event",
                target_artifact="event-annotations.csv",
                human_roles=standard_roles,
                event=event,
            )
        )
        phase_by_requirement = {
            "manual_landing_phase_boundary": "landing_proxy_ms",
            "manual_first_step_slowdown_phase_boundary": "first_step_slowdown_proxy_ms",
            "manual_restabilization_phase_and_keypoint_truth": "restabilization_onset_ms",
        }
        phase = phase_by_requirement.get(requirement)
        if requirement.startswith("manual_phase_boundary:"):
            phase = requirement.split(":", 1)[1]
        if phase:
            units.append(
                _base_spec(
                    key=f"event::{event_id}::phase::{phase}",
                    evidence_type="accepted_manual_phase",
                    scope="event",
                    target_artifact="event-annotations.csv key_phases",
                    human_roles=standard_roles,
                    event=event,
                    phase_name=phase,
                )
            )
        if requirement == "manual_launch_side_semantics":
            indicator_ids = sorted(
                value.split("::", 1)[1]
                for value in item["affected_indicator_instances"]
            )
            semantics = sorted(
                {
                    "support_side" if value == "FS02-M03" else "launch_side"
                    for value in indicator_ids
                }
            )
            units.append(
                _base_spec(
                    key=f"event::{event_id}::semantics::{'_'.join(semantics)}",
                    evidence_type="accepted_manual_side_semantics",
                    scope="event",
                    target_artifact="semantic-truth.csv",
                    human_roles=standard_roles,
                    event=event,
                    semantic_keys=semantics,
                )
            )
        if requirement in {
            "manual_pose_observation_and_event_boundary_review",
            "manual_restabilization_phase_and_keypoint_truth",
        }:
            event_code = str(item["event_code"])
            units.append(
                _base_spec(
                    key=f"video_event_code::{event_code}::dense_keypoints",
                    evidence_type="accepted_dense_keypoint_truth",
                    scope="video_event_code",
                    target_artifact="keypoint-annotations.csv",
                    human_roles=standard_roles,
                    event={"event_id": None, "event_code": event_code, "start_ms": None, "end_ms": None},
                )
            )
        if requirement == "manual_keypoint_and_event_feature_truth":
            invalid_features = sorted(
                {
                    str(value["feature_name"])
                    for value in work_item.get("invalid_features", [])
                }
            )
            units.append(
                _base_spec(
                    key=f"event::{event_id}::feature_truth::{'|'.join(invalid_features)}",
                    evidence_type="accepted_manual_feature_truth",
                    scope="event",
                    target_artifact="keypoint-annotations.csv + feature-evaluation.json",
                    human_roles=standard_roles,
                    event=event,
                    feature_names=invalid_features,
                )
            )
    return units


def build_scoring_truth_evidence_plan(
    *, readiness_path: Path, generated_at: str | None = None
) -> dict[str, Any]:
    readiness = _read_json(readiness_path)
    validate_scoring_truth_action_readiness(readiness)
    worklist_source = readiness["source"]["worklist"]
    worklist_path = Path(str(worklist_source["path"])).resolve()
    if sha256_file(worklist_path) != str(worklist_source["sha256"]).upper():
        raise ValueError("action readiness does not bind the current worklist")
    worklist = _read_json(worklist_path)
    validate_scoring_truth_action_worklist(worklist)

    readiness_items = {str(row["work_item_id"]): row for row in readiness["items"]}
    worklist_items = {str(row["work_item_id"]): row for row in worklist["items"]}
    if set(readiness_items) != set(worklist_items):
        raise ValueError("action readiness/worklist item sets differ")
    for item_id, item in readiness_items.items():
        source = worklist_items[item_id]
        for field in ("event_id", "event_code", "review_type", "truth_requirement"):
            if item.get(field) != source.get(field):
                raise ValueError(f"action readiness/worklist field mismatch: {field}")
        if set(item.get("affected_indicator_instances", [])) != set(
            source.get("affected_indicator_instances", [])
        ):
            raise ValueError("action readiness/worklist instance links differ")

    grouped: dict[str, dict[str, Any]] = {}
    assessments: dict[str, list[str]] = defaultdict(list)
    for item_id, item in readiness_items.items():
        for spec in _required_units(item, worklist_items[item_id]):
            key = str(spec["deduplication_key"])
            if key not in grouped:
                grouped[key] = {
                    **spec,
                    "status": PENDING,
                    "linked_work_item_ids": [],
                    "affected_indicator_instances": [],
                    "affected_indicator_ids": [],
                }
            existing = grouped[key]
            immutable = {
                name: value
                for name, value in spec.items()
                if name not in {"unit_id", "deduplication_key"}
            }
            for name, value in immutable.items():
                if existing.get(name) != value:
                    raise ValueError(f"evidence unit collision changes {name}: {key}")
            existing["linked_work_item_ids"].append(item_id)
            existing["affected_indicator_instances"].extend(
                item["affected_indicator_instances"]
            )
            assessments[key].append(_evidence_status(item, spec))

    units: list[dict[str, Any]] = []
    for key, unit in grouped.items():
        unit["linked_work_item_ids"] = sorted(set(unit["linked_work_item_ids"]))
        unit["affected_indicator_instances"] = sorted(
            set(unit["affected_indicator_instances"])
        )
        unit["affected_indicator_ids"] = sorted(
            {value.split("::", 1)[1] for value in unit["affected_indicator_instances"]}
        )
        unit["status"] = _status_from_rows(assessments[key])
        unit["work_item_count"] = len(unit["linked_work_item_ids"])
        unit["indicator_instance_count"] = len(unit["affected_indicator_instances"])
        units.append(unit)
    units.sort(
        key=lambda row: (
            row["status"] == SATISFIED,
            -int(row["indicator_instance_count"]),
            -int(row["work_item_count"]),
            row["start_ms"] is None,
            row["start_ms"] if row["start_ms"] is not None else 0,
            row["deduplication_key"],
        )
    )
    for index, unit in enumerate(units, 1):
        unit["priority_rank"] = index

    status_counts = Counter(str(row["status"]) for row in units)
    by_type: dict[str, dict[str, int]] = {}
    type_groups: dict[str, Counter[str]] = defaultdict(Counter)
    for unit in units:
        type_groups[str(unit["evidence_type"])][str(unit["status"])] += 1
    for name, counts in sorted(type_groups.items()):
        by_type[name] = {
            "total": sum(counts.values()),
            SATISFIED: counts[SATISFIED],
            PARTIAL: counts[PARTIAL],
            PENDING: counts[PENDING],
        }
    all_satisfied = bool(units) and status_counts[SATISFIED] == len(units)
    any_progress = bool(status_counts[SATISFIED] or status_counts[PARTIAL])
    plan = {
        "schema_version": SCHEMA_VERSION,
        "plan_version": PLAN_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "status": (
            "evidence_units_satisfied_pending_scoring_recompute"
            if all_satisfied
            else "evidence_acquisition_in_progress"
            if any_progress
            else "annotation_required"
        ),
        "source": {
            "video_id": readiness["source"]["video_id"],
            "action_readiness": {
                "path": str(readiness_path.resolve()),
                "sha256": sha256_file(readiness_path),
            },
            "worklist": {
                "path": str(worklist_path),
                "sha256": sha256_file(worklist_path),
            },
            "review_video": dict(worklist["source"]["review_video"]),
            "pose_truth_manifest": dict(readiness["source"]["pose_truth_manifest"]),
            "scoring_reference_context": dict(
                readiness["source"]["scoring_reference_context"]
            ),
        },
        "counts": {
            "input_work_items": len(readiness_items),
            "input_indicator_instances": len(readiness["indicator_instances"]),
            "input_instance_action_links": readiness["counts"]["instance_action_links"],
            "evidence_units": len(units),
            "work_item_to_evidence_unit_links": sum(
                len(unit["linked_work_item_ids"]) for unit in units
            ),
            "by_status": {value: status_counts[value] for value in STATUSES},
            "by_evidence_type": by_type,
        },
        "units": units,
        "safety": {
            "dependency_collapse_is_evidence_completion": False,
            "priority_is_accuracy_ranking": False,
            "coverage_is_accuracy": False,
            "quality_gate_modified": False,
            "scoring_state_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "completion_requires_scoring_recompute": True,
            "maximum_status_without_calibration": "calibration_required",
        },
    }
    validate_scoring_truth_evidence_plan(plan)
    return plan


def validate_scoring_truth_evidence_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported evidence acquisition plan schema")
    if plan.get("plan_version") != PLAN_VERSION:
        raise ValueError("unsupported evidence acquisition plan version")
    units = plan.get("units")
    counts = plan.get("counts")
    if not isinstance(units, list) or not units or not isinstance(counts, Mapping):
        raise ValueError("evidence acquisition units/counts are missing")
    unit_ids: set[str] = set()
    keys: set[str] = set()
    work_items: set[str] = set()
    instances: set[str] = set()
    statuses = Counter()
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    links = 0
    previous_sort: tuple[Any, ...] | None = None
    for index, unit in enumerate(units, 1):
        unit_id = unit.get("unit_id")
        key = unit.get("deduplication_key")
        status = unit.get("status")
        if not isinstance(unit_id, str) or not unit_id or unit_id in unit_ids:
            raise ValueError("evidence unit ID invalid or duplicate")
        if not isinstance(key, str) or not key or key in keys or unit_id != _unit_id(key):
            raise ValueError("evidence unit deduplication key invalid or duplicate")
        unit_ids.add(unit_id)
        keys.add(key)
        if status not in STATUSES:
            raise ValueError("evidence unit status invalid")
        if unit.get("priority_rank") != index:
            raise ValueError("evidence unit priority ranks are not contiguous")
        item_ids = unit.get("linked_work_item_ids")
        affected = unit.get("affected_indicator_instances")
        if not isinstance(item_ids, list) or not item_ids or len(item_ids) != len(set(item_ids)):
            raise ValueError("evidence unit work-item links invalid")
        if not isinstance(affected, list) or not affected or len(affected) != len(set(affected)):
            raise ValueError("evidence unit instance links invalid")
        if unit.get("work_item_count") != len(item_ids):
            raise ValueError("evidence unit work-item count mismatch")
        if unit.get("indicator_instance_count") != len(affected):
            raise ValueError("evidence unit instance count mismatch")
        expected_indicators = sorted({value.split("::", 1)[1] for value in affected})
        if unit.get("affected_indicator_ids") != expected_indicators:
            raise ValueError("evidence unit indicator IDs mismatch")
        work_items.update(item_ids)
        instances.update(affected)
        links += len(item_ids)
        statuses[str(status)] += 1
        by_type[str(unit.get("evidence_type"))][str(status)] += 1
        sort_key = (
            status == SATISFIED,
            -len(affected),
            -len(item_ids),
            unit.get("start_ms") is None,
            unit.get("start_ms") if unit.get("start_ms") is not None else 0,
            key,
        )
        if previous_sort is not None and sort_key < previous_sort:
            raise ValueError("evidence acquisition priority order is invalid")
        previous_sort = sort_key
    if counts.get("evidence_units") != len(units):
        raise ValueError("evidence unit count mismatch")
    if counts.get("input_work_items") != len(work_items):
        raise ValueError("evidence plan does not cover every input work item")
    if counts.get("input_indicator_instances") != len(instances):
        raise ValueError("evidence plan does not cover every input indicator instance")
    if counts.get("work_item_to_evidence_unit_links") != links:
        raise ValueError("evidence unit link count mismatch")
    if counts.get("by_status") != {value: statuses[value] for value in STATUSES}:
        raise ValueError("evidence unit status counts mismatch")
    expected_by_type = {
        name: {
            "total": sum(values.values()),
            SATISFIED: values[SATISFIED],
            PARTIAL: values[PARTIAL],
            PENDING: values[PENDING],
        }
        for name, values in sorted(by_type.items())
    }
    if counts.get("by_evidence_type") != expected_by_type:
        raise ValueError("evidence type counts mismatch")
    expected_status = (
        "evidence_units_satisfied_pending_scoring_recompute"
        if statuses[SATISFIED] == len(units)
        else "evidence_acquisition_in_progress"
        if statuses[SATISFIED] or statuses[PARTIAL]
        else "annotation_required"
    )
    if plan.get("status") != expected_status:
        raise ValueError("evidence acquisition aggregate status mismatch")
    safety = plan.get("safety")
    false_fields = (
        "dependency_collapse_is_evidence_completion",
        "priority_is_accuracy_ranking",
        "coverage_is_accuracy",
        "quality_gate_modified",
        "scoring_state_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    )
    if (
        not isinstance(safety, Mapping)
        or any(safety.get(name) is not False for name in false_fields)
        or safety.get("completion_requires_scoring_recompute") is not True
        or safety.get("maximum_status_without_calibration") != "calibration_required"
    ):
        raise ValueError("evidence acquisition safety invariant failed")


def validate_scoring_truth_evidence_plan_sources(plan: Mapping[str, Any]) -> None:
    """Replay the plan from its hash-bound M45 input.

    The structural validator is intentionally filesystem independent.  This
    stronger validator is for local artifacts and rejects synchronized edits
    to unit status, priority, dependencies, or source lineage.
    """

    validate_scoring_truth_evidence_plan(plan)
    source = plan.get("source")
    readiness = source.get("action_readiness") if isinstance(source, Mapping) else None
    if not isinstance(readiness, Mapping):
        raise ValueError("evidence plan action-readiness source is missing")
    path_value = readiness.get("path")
    sha_value = readiness.get("sha256")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("evidence plan action-readiness path is missing")
    if not isinstance(sha_value, str) or len(sha_value) != 64:
        raise ValueError("evidence plan action-readiness SHA is invalid")
    readiness_path = Path(path_value).resolve()
    if sha256_file(readiness_path) != sha_value.upper():
        raise ValueError("evidence plan action-readiness source SHA mismatch")
    rebuilt = build_scoring_truth_evidence_plan(
        readiness_path=readiness_path,
        generated_at=str(plan.get("generated_at")),
    )
    if dict(plan) != rebuilt:
        raise ValueError("evidence acquisition plan differs from source replay")


def write_scoring_truth_evidence_plan(
    plan: Mapping[str, Any], *, output_dir: Path
) -> dict[str, Path]:
    validate_scoring_truth_evidence_plan(plan)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "plan.json"
    csv_path = output_dir / "evidence-units.csv"
    html_path = output_dir / "index.html"
    json_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    fields = [
        "priority_rank",
        "unit_id",
        "status",
        "evidence_type",
        "scope",
        "event_id",
        "event_code",
        "start_ms",
        "end_ms",
        "work_item_count",
        "indicator_instance_count",
        "affected_indicator_ids",
        "required_human_roles",
        "target_artifact",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for unit in plan["units"]:
            writer.writerow(
                {
                    name: (
                        "|".join(unit[name])
                        if isinstance(unit.get(name), list)
                        else unit.get(name)
                    )
                    for name in fields
                }
            )
    base = html_path.parent.resolve()
    video_path = Path(str(plan["source"]["review_video"]["path"])).resolve()
    video_url = Path(os.path.relpath(video_path, base)).as_posix()
    rows = []
    for unit in plan["units"]:
        seek = ""
        if unit.get("start_ms") is not None:
            seek = (
                f'<button type="button" data-ms="{int(unit["start_ms"])}">跳转</button>'
            )
        rows.append(
            "<tr>"
            f'<td>{unit["priority_rank"]}</td>'
            f'<td><code>{html.escape(unit["status"])}</code></td>'
            f'<td>{html.escape(unit["evidence_type"])}</td>'
            f'<td>{html.escape(unit["event_id"] or "全时间线 / 共用")}</td>'
            f'<td>{html.escape(", ".join(unit["affected_indicator_ids"]))}</td>'
            f'<td>{unit["work_item_count"]} / {unit["indicator_instance_count"]}</td>'
            f'<td>{html.escape(unit["target_artifact"])}</td>'
            f"<td>{seek}</td>"
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RallyMate M46 共享证据批次</title>
<style>body{{font:15px/1.55 system-ui;margin:20px;background:#f5f7fb;color:#172033}}main{{max-width:1500px;margin:auto}}video{{width:100%;max-height:66vh;background:#000}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{border:1px solid #d7deea;padding:7px;vertical-align:top}}th{{position:sticky;top:0;background:#e8eef9}}code{{font-size:12px}}.warn{{padding:12px;background:#fff3cd;border-left:4px solid #b7791f}}button{{cursor:pointer}}</style></head>
<body><main><h1>M46 共享证据获取计划</h1>
<p class="warn">该页把重复依赖折叠为共享证据单元，只用于减少人工重复劳动。优先级不是准确率排名，完成证据不会自动修改评分；没有教练标定时最高仍为 calibration_required。</p>
<p>输入 {plan["counts"]["input_work_items"]} 个 work item / {plan["counts"]["input_indicator_instances"]} 个指标实例，折叠为 {plan["counts"]["evidence_units"]} 个证据单元。当前状态：<code>{html.escape(str(plan["status"]))}</code>。</p>
<p><a href="plan.json">机器计划 JSON</a> · <a href="evidence-units.csv">证据单元 CSV</a></p>
<video id="video" controls preload="metadata" src="{html.escape(video_url)}"></video>
<table><thead><tr><th>优先级</th><th>状态</th><th>证据类型</th><th>事件</th><th>指标</th><th>work item / 实例</th><th>写入目标</th><th>视频</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<script>const v=document.getElementById('video');document.querySelectorAll('button[data-ms]').forEach(b=>b.addEventListener('click',()=>{{v.currentTime=Number(b.dataset.ms)/1000;v.play().catch(()=>{{}});scrollTo({{top:0,behavior:'smooth'}});}}));</script>
</main></body></html>"""
    html_path.write_text(document, encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "html": html_path}
