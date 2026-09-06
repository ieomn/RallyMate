from __future__ import annotations

import csv
import hashlib
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_scoring.feasibility import (
    load_feasibility_registry,
    measurement_feature_names,
)
from rallymate_scoring.blocker_taxonomy import truth_requirement_for_flag


SCHEMA_VERSION = "2.0.0"
WORKLIST_VERSION = "scoring-truth-action-worklist-v2.0.0"

POSE_DIAGNOSTIC_REQUIREMENTS = {
    "full_timeline_manual_keypoint_jump_truth",
    "full_timeline_manual_left_right_swap_truth",
    "manual_primary_identity_continuity_truth",
}
TARGET_CONTEXT_FEATURE = "target_direction_alignment_error_deg"
TARGET_REQUIREMENT = "manual_target_direction_semantics"
MEASUREMENT_REQUIREMENT = "manual_keypoint_and_event_feature_truth"


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {line_number} is not an object: {path}")
        rows.append(value)
    if not rows:
        raise ValueError(f"JSONL input is empty: {path}")
    return rows


def _source(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def _phase_requirement(flag: str) -> str:
    return truth_requirement_for_flag(flag)


def _review_type(requirement: str) -> str:
    if requirement in POSE_DIAGNOSTIC_REQUIREMENTS:
        return "pose_diagnostic_truth"
    if requirement == TARGET_REQUIREMENT:
        return "scoring_reference_context"
    if requirement == MEASUREMENT_REQUIREMENT:
        return "manual_keypoint_feature_truth"
    return "manual_event_or_semantic_truth"


def _assert_source_bindings(
    *,
    scores_path: Path,
    events_path: Path,
    summary_path: Path,
    blocker_audit_path: Path,
    diagnostic_queue_path: Path,
    review_video_path: Path,
    reference_context_path: Path,
    registry_path: Path,
    summary: dict[str, Any],
    blocker_audit: dict[str, Any],
    diagnostic_queue: dict[str, Any],
    reference_context: dict[str, Any],
    registry: dict[str, Any],
) -> None:
    hashes = summary.get("artifact_sha256", {})
    if str(hashes.get("scores_jsonl", "")).upper() != sha256_file(scores_path):
        raise ValueError("summary does not bind supplied scores")
    if str(hashes.get("events_jsonl", "")).upper() != sha256_file(events_path):
        raise ValueError("summary does not bind supplied events")
    audit_source = blocker_audit.get("source", {})
    if str(audit_source.get("scores", {}).get("sha256", "")).upper() != sha256_file(scores_path):
        raise ValueError("blocker audit does not bind supplied scores")
    if str(audit_source.get("summary", {}).get("sha256", "")).upper() != sha256_file(summary_path):
        raise ValueError("blocker audit does not bind supplied summary")
    if blocker_audit.get("assertions", {}).get("typed_reason_mapping_consistent") is not True:
        raise ValueError("blocker audit lacks typed reason consistency")
    queue_artifacts = diagnostic_queue.get("source", {}).get("artifacts", {})
    if str(queue_artifacts.get("scores_jsonl", {}).get("sha256", "")).upper() != sha256_file(scores_path):
        raise ValueError("diagnostic queue does not bind supplied scores")
    if str(queue_artifacts.get("events_jsonl", {}).get("sha256", "")).upper() != sha256_file(events_path):
        raise ValueError("diagnostic queue does not bind supplied events")
    if str(diagnostic_queue.get("review_media", {}).get("sha256", "")).upper() != sha256_file(review_video_path):
        raise ValueError("diagnostic queue does not bind supplied review video")
    reference_source = summary.get("provenance", {}).get("scoring_reference_context", {})
    if str(reference_source.get("sha256", "")).upper() != sha256_file(reference_context_path):
        raise ValueError("summary does not bind supplied scoring reference context")
    if reference_context.get("artifact_version") != "scoring-reference-context-v1.0.0":
        raise ValueError("unsupported scoring reference context")
    if summary.get("model_versions", {}).get("feasibility_registry") != registry.get("registry_version"):
        raise ValueError("summary and feasibility registry version mismatch")


def build_scoring_truth_action_worklist(
    *,
    scores_path: Path,
    events_path: Path,
    summary_path: Path,
    blocker_audit_path: Path,
    diagnostic_queue_path: Path,
    review_video_path: Path,
    reference_context_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    scores = _read_jsonl(scores_path)
    events = {str(row["event_id"]): row for row in _read_jsonl(events_path)}
    summary = _read_json(summary_path)
    blocker_audit = _read_json(blocker_audit_path)
    diagnostic_queue = _read_json(diagnostic_queue_path)
    reference_context = _read_json(reference_context_path)
    registry = load_feasibility_registry(registry_path)
    _assert_source_bindings(
        scores_path=scores_path,
        events_path=events_path,
        summary_path=summary_path,
        blocker_audit_path=blocker_audit_path,
        diagnostic_queue_path=diagnostic_queue_path,
        review_video_path=review_video_path,
        reference_context_path=reference_context_path,
        registry_path=registry_path,
        summary=summary,
        blocker_audit=blocker_audit,
        diagnostic_queue=diagnostic_queue,
        reference_context=reference_context,
        registry=registry,
    )

    registry_by_id = {
        str(item["indicator_id"]): item for item in registry["indicators"]
    }
    expected_indicator_ids = set(registry_by_id)
    actual_indicator_ids = {str(row.get("indicator_id")) for row in scores}
    if actual_indicator_ids != expected_indicator_ids:
        raise ValueError("score indicator set does not match registry")

    queue_by_event_flag: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in diagnostic_queue.get("tasks", []):
        flag = str(task.get("diagnostic_flag", ""))
        for event_ref in task.get("event_refs", []):
            queue_by_event_flag[(str(event_ref.get("event_id")), flag)].append(task)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    unavailable_instances: set[str] = set()
    actionable_instances: set[str] = set()
    instance_requirements: dict[str, set[str]] = defaultdict(set)
    status_counts: Counter[str] = Counter()

    def add_cause(
        *,
        event_id: str,
        instance_id: str,
        requirement: str,
        signal: str,
        flag: str | None = None,
        invalid_feature: dict[str, Any] | None = None,
    ) -> None:
        key = (event_id, requirement)
        group = groups.setdefault(
            key,
            {
                "event_id": event_id,
                "truth_requirement": requirement,
                "source_signals": set(),
                "diagnostic_flags": set(),
                "invalid_features": {},
                "affected_indicator_instances": set(),
            },
        )
        group["source_signals"].add(signal)
        if flag:
            group["diagnostic_flags"].add(flag)
        if invalid_feature:
            name = str(invalid_feature["feature_name"])
            reasons = group["invalid_features"].setdefault(name, set())
            reasons.add(str(invalid_feature.get("reason", "unknown")))
        group["affected_indicator_instances"].add(instance_id)
        instance_requirements[instance_id].add(requirement)
        actionable_instances.add(instance_id)

    seen_instances: set[str] = set()
    for row in scores:
        event_id = str(row.get("event_id", ""))
        indicator_id = str(row.get("indicator_id", ""))
        instance_id = f"{event_id}::{indicator_id}"
        if instance_id in seen_instances:
            raise ValueError(f"duplicate score instance: {instance_id}")
        seen_instances.add(instance_id)
        if event_id not in events:
            raise ValueError(f"score references unknown event: {event_id}")
        if indicator_id not in registry_by_id:
            raise ValueError(f"score references unknown indicator: {indicator_id}")
        status = str(row.get("status"))
        status_counts[status] += 1
        if status != "unavailable":
            continue
        unavailable_instances.add(instance_id)
        indicator = registry_by_id[indicator_id]
        expected_scoring = list(indicator["required_features"])
        expected_measurement = set(measurement_feature_names(indicator))
        feature_items = row.get("feature", {}).get("items", [])
        if [item.get("feature_name") for item in feature_items] != expected_scoring:
            raise ValueError(f"score feature order does not match registry: {instance_id}")
        invalid_context: set[str] = set()
        for feature in feature_items:
            if feature.get("valid") is True:
                continue
            name = str(feature["feature_name"])
            if name in expected_measurement:
                add_cause(
                    event_id=event_id,
                    instance_id=instance_id,
                    requirement=MEASUREMENT_REQUIREMENT,
                    signal=f"measurement_feature:{name}:{feature.get('reason')}",
                    invalid_feature=feature,
                )
            else:
                invalid_context.add(name)
                requirement = (
                    TARGET_REQUIREMENT
                    if name == TARGET_CONTEXT_FEATURE
                    else f"manual_scoring_context_truth:{name}"
                )
                add_cause(
                    event_id=event_id,
                    instance_id=instance_id,
                    requirement=requirement,
                    signal=f"scoring_context_feature:{name}:{feature.get('reason')}",
                    invalid_feature=feature,
                )
        gate = row.get("quality_gate", {})
        for flag in sorted(str(value) for value in gate.get("scoring_block_flags", [])):
            if flag == "tactical_target_direction_not_observed" and TARGET_CONTEXT_FEATURE in invalid_context:
                add_cause(
                    event_id=event_id,
                    instance_id=instance_id,
                    requirement=TARGET_REQUIREMENT,
                    signal=f"scoring_block_flag:{flag}",
                    flag=flag,
                )
                continue
            add_cause(
                event_id=event_id,
                instance_id=instance_id,
                requirement=_phase_requirement(flag),
                signal=f"scoring_block_flag:{flag}",
                flag=flag,
            )
        for flag in sorted(str(value) for value in gate.get("hard_fail_flags", [])):
            add_cause(
                event_id=event_id,
                instance_id=instance_id,
                requirement=_phase_requirement(flag),
                signal=f"hard_fail_flag:{flag}",
                flag=flag,
            )
        if instance_id not in actionable_instances:
            raise ValueError(f"unavailable score has no actionable truth cause: {instance_id}")

    expected_status_counts = {
        str(key): int(value)
        for key, value in blocker_audit.get("counts", {}).get("status_counts", {}).items()
    }
    if dict(status_counts) != expected_status_counts:
        raise ValueError("score status counts do not match blocker audit")
    if actionable_instances != unavailable_instances:
        raise ValueError("not every unavailable score instance has an action")

    items: list[dict[str, Any]] = []
    for (event_id, requirement), group in groups.items():
        event = events[event_id]
        affected = sorted(group["affected_indicator_instances"])
        sole = sorted(
            instance_id
            for instance_id in affected
            if instance_requirements[instance_id] == {requirement}
        )
        diagnostic_flags = sorted(group["diagnostic_flags"])
        tasks = [
            task
            for flag in diagnostic_flags
            for task in queue_by_event_flag.get((event_id, flag), [])
        ]
        task_ids = sorted({str(task["task_id"]) for task in tasks})
        review_times = sorted({int(task["review_media_time_ms"]) for task in tasks})
        review_type = _review_type(requirement)
        if review_type == "pose_diagnostic_truth":
            link_status = "linked" if task_ids else "missing_pose_diagnostic_task"
        else:
            link_status = "not_applicable"
        invalid_features = [
            {"feature_name": name, "reasons": sorted(reasons)}
            for name, reasons in sorted(group["invalid_features"].items())
        ]
        digest = hashlib.sha256(f"{event_id}|{requirement}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "work_item_id": f"staw-{digest}",
                "event_id": event_id,
                "event_code": str(event["event_code"]),
                "start_ms": int(event["start_ms"]),
                "end_ms": int(event["end_ms"]),
                "person_track_id": int(event["person_track_id"]),
                "review_type": review_type,
                "truth_requirement": requirement,
                "source_signals": sorted(group["source_signals"]),
                "diagnostic_flags": diagnostic_flags,
                "invalid_features": invalid_features,
                "diagnostic_queue_link_status": link_status,
                "diagnostic_task_ids": task_ids,
                "candidate_review_times_ms": review_times,
                "affected_indicator_instances": affected,
                "affected_indicator_count": len(affected),
                "sole_action_indicator_instances": sole,
                "sole_action_indicator_count": len(sole),
                "concurrent_truth_requirements": sorted(
                    {
                        other
                        for instance_id in affected
                        for other in instance_requirements[instance_id]
                        if other != requirement
                    }
                ),
                "review_status": "annotation_required",
            }
        )

    items.sort(
        key=lambda item: (
            -int(item["sole_action_indicator_count"]),
            -int(item["affected_indicator_count"]),
            int(item["start_ms"]),
            str(item["truth_requirement"]),
        )
    )
    for rank, item in enumerate(items, 1):
        item["priority_rank"] = rank

    by_review_type = Counter(str(item["review_type"]) for item in items)
    by_requirement = Counter(str(item["truth_requirement"]) for item in items)
    linked_pose = sum(
        item["diagnostic_queue_link_status"] == "linked" for item in items
    )
    missing_pose = sum(
        item["diagnostic_queue_link_status"] == "missing_pose_diagnostic_task"
        for item in items
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "worklist_version": WORKLIST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "annotation_required",
        "source": {
            "video_id": str(summary.get("video_id", "")),
            "scores": _source(scores_path),
            "events": _source(events_path),
            "summary": _source(summary_path),
            "blocker_audit": _source(blocker_audit_path),
            "feasibility_registry": {
                **_source(registry_path),
                "registry_version": str(registry["registry_version"]),
            },
            "diagnostic_queue": _source(diagnostic_queue_path),
            "scoring_reference_context": _source(reference_context_path),
            "review_video": _source(review_video_path),
        },
        "counts": {
            "indicator_record_count": len(scores),
            "calibration_required_indicator_instances": status_counts[
                "calibration_required"
            ],
            "unavailable_indicator_instances": len(unavailable_instances),
            "actionable_unavailable_indicator_instances": len(actionable_instances),
            "instance_action_links": sum(len(value) for value in instance_requirements.values()),
            "work_items": len(items),
            "by_review_type": dict(sorted(by_review_type.items())),
            "by_truth_requirement": dict(sorted(by_requirement.items())),
            "pose_diagnostic_items_linked": linked_pose,
            "pose_diagnostic_items_missing_queue_task": missing_pose,
            "accepted_annotations": 0,
        },
        "items": items,
        "safety": {
            "model_candidates_are_truth": False,
            "quality_gate_modified": False,
            "scoring_state_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "coverage_is_accuracy": False,
            "clearing_one_action_automatically_changes_score": False,
            "maximum_post_review_status_without_calibration": "calibration_required",
        },
    }


def validate_scoring_truth_action_worklist(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported scoring truth action schema version")
    if report.get("worklist_version") != WORKLIST_VERSION:
        raise ValueError("unsupported scoring truth action worklist version")
    if report.get("status") != "annotation_required":
        raise ValueError("scoring truth action worklist must remain annotation_required")
    safety = report.get("safety", {})
    for field in (
        "model_candidates_are_truth",
        "quality_gate_modified",
        "scoring_state_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
        "coverage_is_accuracy",
        "clearing_one_action_automatically_changes_score",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe worklist assertion: {field}")
    if safety.get("maximum_post_review_status_without_calibration") != "calibration_required":
        raise ValueError("worklist must not imply formal scoring")
    counts = report.get("counts", {})
    items = report.get("items", [])
    if int(counts.get("work_items", -1)) != len(items):
        raise ValueError("work item count mismatch")
    if int(counts.get("unavailable_indicator_instances", -1)) != int(
        counts.get("actionable_unavailable_indicator_instances", -2)
    ):
        raise ValueError("worklist does not cover every unavailable instance")
    if int(counts.get("accepted_annotations", -1)) != 0:
        raise ValueError("generated worklist must not contain accepted annotations")
    ranks = [int(item.get("priority_rank", -1)) for item in items]
    if ranks != list(range(1, len(items) + 1)):
        raise ValueError("work item ranks are not contiguous")
    instance_ids = {
        instance_id
        for item in items
        for instance_id in item.get("affected_indicator_instances", [])
    }
    if len(instance_ids) != int(counts["actionable_unavailable_indicator_instances"]):
        raise ValueError("item instance union does not match actionable coverage")
    work_item_ids = [str(item.get("work_item_id", "")) for item in items]
    if len(work_item_ids) != len(set(work_item_ids)):
        raise ValueError("duplicate work item id")
    event_requirements = [
        (str(item.get("event_id", "")), str(item.get("truth_requirement", "")))
        for item in items
    ]
    if len(event_requirements) != len(set(event_requirements)):
        raise ValueError("duplicate event/truth requirement work item")
    if sum(len(item.get("affected_indicator_instances", [])) for item in items) != int(
        counts.get("instance_action_links", -1)
    ):
        raise ValueError("instance action link count mismatch")
    by_review_type = Counter(str(item.get("review_type")) for item in items)
    by_requirement = Counter(str(item.get("truth_requirement")) for item in items)
    if dict(sorted(by_review_type.items())) != counts.get("by_review_type"):
        raise ValueError("review type count mismatch")
    if dict(sorted(by_requirement.items())) != counts.get("by_truth_requirement"):
        raise ValueError("truth requirement count mismatch")
    for item in items:
        affected = set(item.get("affected_indicator_instances", []))
        sole = set(item.get("sole_action_indicator_instances", []))
        if not sole.issubset(affected):
            raise ValueError("sole-action instances must be affected instances")
        if len(affected) != int(item.get("affected_indicator_count", -1)):
            raise ValueError("affected indicator count mismatch")
        if len(sole) != int(item.get("sole_action_indicator_count", -1)):
            raise ValueError("sole-action indicator count mismatch")
    missing_pose = sum(
        item.get("diagnostic_queue_link_status") == "missing_pose_diagnostic_task"
        for item in items
    )
    if missing_pose != int(counts.get("pose_diagnostic_items_missing_queue_task", -1)):
        raise ValueError("pose diagnostic missing-link count mismatch")
    linked_pose = sum(
        item.get("diagnostic_queue_link_status") == "linked" for item in items
    )
    if linked_pose != int(counts.get("pose_diagnostic_items_linked", -1)):
        raise ValueError("pose diagnostic linked count mismatch")


def write_scoring_truth_action_csv(report: dict[str, Any], path: Path) -> None:
    fields = [
        "priority_rank",
        "work_item_id",
        "event_id",
        "event_code",
        "start_ms",
        "end_ms",
        "review_type",
        "truth_requirement",
        "affected_indicator_count",
        "sole_action_indicator_count",
        "source_signals",
        "diagnostic_flags",
        "invalid_features",
        "diagnostic_task_ids",
        "affected_indicator_instances",
        "concurrent_truth_requirements",
        "review_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in report["items"]:
            row = {field: item.get(field, "") for field in fields}
            for field in (
                "source_signals",
                "diagnostic_flags",
                "diagnostic_task_ids",
                "affected_indicator_instances",
                "concurrent_truth_requirements",
            ):
                row[field] = "|".join(item.get(field, []))
            row["invalid_features"] = "|".join(
                f"{entry['feature_name']}:{','.join(entry['reasons'])}"
                for entry in item.get("invalid_features", [])
            )
            writer.writerow(row)


def write_scoring_truth_action_html(
    report: dict[str, Any], path: Path, review_video_path: Path
) -> None:
    video_href = Path(
        os.path.relpath(review_video_path.resolve(), path.parent.resolve())
    ).as_posix()
    rows: list[str] = []
    for item in report["items"]:
        seek_ms = (
            item["candidate_review_times_ms"][0]
            if item["candidate_review_times_ms"]
            else item["start_ms"]
        )
        invalid = "、".join(
            f"{entry['feature_name']} ({'/'.join(entry['reasons'])})"
            for entry in item["invalid_features"]
        ) or "—"
        rows.append(
            "<tr>"
            f"<td>{item['priority_rank']}</td>"
            f"<td><button data-ms=\"{seek_ms}\">{item['start_ms']/1000:.3f}s</button><br><code>{html.escape(item['event_id'])}</code></td>"
            f"<td><strong>{html.escape(item['review_type'])}</strong><br><code>{html.escape(item['truth_requirement'])}</code></td>"
            f"<td>{item['affected_indicator_count']} / {item['sole_action_indicator_count']}</td>"
            f"<td>{html.escape(invalid)}</td>"
            f"<td>{html.escape('、'.join(item['diagnostic_flags']) or '—')}<br>{html.escape(item['diagnostic_queue_link_status'])} / {len(item['diagnostic_task_ids'])} task(s)</td>"
            f"<td>{html.escape('、'.join(item['affected_indicator_instances']))}</td>"
            "</tr>"
        )
    counts = report["counts"]
    path.write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>RallyMate 当前评分真值行动清单</title><style>body{font-family:system-ui;margin:20px;background:#f5f7fb;color:#18202b}main{max-width:1600px;margin:auto}video{width:min(100%,1100px);background:#000}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #ccd3dd;padding:7px;vertical-align:top}th{background:#e9eef6;position:sticky;top:0}code{font-size:12px;word-break:break-all}.warn{background:#fff4d6;border-left:4px solid #d69e00;padding:12px}.good{background:#eaf8ef;border-left:4px solid #24844d;padding:12px}button{cursor:pointer}</style></head><body><main>"
        "<h1>RallyMate 当前评分真值行动清单（v2）</h1>"
        f"<p class=\"good\">当前 {counts['unavailable_indicator_instances']} 条 unavailable 已全部分配至少一个人工动作；共 {counts['work_items']} 个去重 event×truth-requirement 工作项、{counts['instance_action_links']} 条实例×动作关联。</p>"
        "<p class=\"warn\">覆盖完整不代表诊断正确，也不代表处理一个动作就会自动解除门禁。页面只负责视频定位和路由；必须在对应工作台完成人工标注、独立复核、Python 编译与预注册评测。没有教练标定时，完成全部真值后最高仍是 calibration_required。</p>"
        f"<video id=\"reviewVideo\" controls preload=\"metadata\"><source src=\"{html.escape(video_href)}\" type=\"video/mp4\"></video>"
        "<p><a href=\"worklist.json\">机器 JSON</a> · <a href=\"worklist.csv\">CSV</a> · <a href=\"../pose-diagnostic-review/halpe26-full-m42/index.html\">当前 Pose 诊断队列</a> · <a href=\"../../data/annotations/scoring-truth-pack-v1/review.html\">评分真值工作台</a> · <a href=\"../../data/annotations/scoring-reference-context-v1/850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json\">目标方向文件</a></p>"
        "<table><thead><tr><th>优先级</th><th>视频定位 / 事件</th><th>动作类型 / 真值要求</th><th>影响实例 / 唯一动作</th><th>无效特征</th><th>诊断 flag / 队列</th><th>指标实例</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table><script>const v=document.getElementById('reviewVideo');document.querySelectorAll('button[data-ms]').forEach(b=>b.addEventListener('click',()=>{v.currentTime=Number(b.dataset.ms)/1000;v.play().catch(()=>{});window.scrollTo({top:0,behavior:'smooth'});}));</script></main></body></html>\n",
        encoding="utf-8",
    )
