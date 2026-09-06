#!/usr/bin/env python3
"""Build a video-first human-truth worklist from complete-feature score blocks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rallymate_scoring.blocker_taxonomy import (
    blocker_group_for_flag,
    truth_requirement_for_flag,
)

SCHEMA_VERSION = "1.0.0"
WORKLIST_VERSION = "scoring-truth-priority-worklist-v1.0.0"

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _feature_complete(row: dict[str, Any]) -> bool:
    items = row.get("feature", {}).get("items", [])
    return bool(items) and all(item.get("valid") is True for item in items)


def build_truth_priority_worklist(
    *,
    scores_path: Path,
    events_path: Path,
    summary_path: Path,
    blocker_audit_path: Path,
    diagnostic_queue_path: Path,
    review_video_path: Path,
) -> dict[str, Any]:
    scores = _jsonl(scores_path)
    events = {str(row["event_id"]): row for row in _jsonl(events_path)}
    summary = _json(summary_path)
    blocker_audit = _json(blocker_audit_path)
    queue = _json(diagnostic_queue_path)
    if blocker_audit.get("assertions", {}).get("quality_gate_modified") is not False:
        raise ValueError("blocker audit is not safe for worklist generation")
    if blocker_audit.get("source", {}).get("scores", {}).get("sha256") != _sha256(scores_path):
        raise ValueError("blocker audit does not bind the supplied scores")
    if blocker_audit.get("source", {}).get("summary", {}).get("sha256") != _sha256(summary_path):
        raise ValueError("blocker audit does not bind the supplied summary")
    queue_artifacts = queue.get("source", {}).get("artifacts", {})
    if str(queue_artifacts.get("scores_jsonl", {}).get("sha256", "")).upper() != _sha256(scores_path):
        raise ValueError("diagnostic queue does not bind the supplied scores")
    if str(queue_artifacts.get("events_jsonl", {}).get("sha256", "")).upper() != _sha256(events_path):
        raise ValueError("diagnostic queue does not bind the supplied events")
    if str(queue.get("review_media", {}).get("sha256", "")).upper() != _sha256(review_video_path):
        raise ValueError("diagnostic queue does not bind the supplied review video")

    eligible: dict[str, dict[str, Any]] = {}
    for row in scores:
        flags = sorted(str(flag) for flag in row.get("quality_gate", {}).get("scoring_block_flags", []))
        if (
            row.get("status") == "unavailable"
            and _feature_complete(row)
            and row.get("quality_gate", {}).get("hard_fail") is not True
            and flags
        ):
            instance_id = f"{row['event_id']}::{row['indicator_id']}"
            eligible[instance_id] = {"row": row, "flags": flags}
    expected_eligible = int(
        blocker_audit["counts"][
            "complete_feature_score_only_recoverable_to_calibration_required_if_all_truth_clears_count"
        ]
    )
    if len(eligible) != expected_eligible:
        raise ValueError("eligible score-only record count does not match blocker audit")

    queue_tasks_by_event_flag: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in queue.get("tasks", []):
        flag = str(task.get("diagnostic_flag"))
        affected = set(str(value) for value in task.get("affected_indicator_instances", []))
        for event_ref in task.get("event_refs", []):
            event_id = str(event_ref.get("event_id"))
            if any(value.startswith(event_id + "::") for value in affected):
                queue_tasks_by_event_flag[(event_id, flag)].append(task)

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for instance_id, payload in eligible.items():
        row = payload["row"]
        event_id = str(row["event_id"])
        if event_id not in events:
            raise ValueError(f"score references unknown event: {event_id}")
        for flag in payload["flags"]:
            key = (event_id, flag)
            group = groups.setdefault(
                key,
                {
                    "event_id": event_id,
                    "flag": flag,
                    "affected_indicator_instances": set(),
                    "sole_block_indicator_instances": set(),
                    "concurrent_flags": set(),
                },
            )
            group["affected_indicator_instances"].add(instance_id)
            group["concurrent_flags"].update(payload["flags"])
            if len(payload["flags"]) == 1:
                group["sole_block_indicator_instances"].add(instance_id)

    items: list[dict[str, Any]] = []
    for (event_id, flag), group in groups.items():
        event = events[event_id]
        tasks = queue_tasks_by_event_flag.get((event_id, flag), [])
        affected = sorted(group["affected_indicator_instances"])
        sole = sorted(group["sole_block_indicator_instances"])
        task_ids = sorted({str(task["task_id"]) for task in tasks})
        candidate_times = sorted({int(task["review_media_time_ms"]) for task in tasks})
        review_type = (
            "pose_diagnostic_queue"
            if blocker_group_for_flag(flag)
            in {"pose_diagnostic", "identity_continuity"}
            else "event_or_semantic_truth"
        )
        queue_status = "not_applicable"
        if review_type == "pose_diagnostic_queue":
            queue_status = "linked" if task_ids else "missing_pose_diagnostic_task"
        digest = hashlib.sha256(f"{event_id}|{flag}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "work_item_id": f"stpw-{digest}",
                "event_id": event_id,
                "event_code": str(event["event_code"]),
                "start_ms": int(event["start_ms"]),
                "end_ms": int(event["end_ms"]),
                "person_track_id": int(event["person_track_id"]),
                "diagnostic_flag": flag,
                "truth_requirement": truth_requirement_for_flag(flag),
                "review_type": review_type,
                "diagnostic_queue_link_status": queue_status,
                "diagnostic_task_ids": task_ids,
                "candidate_review_times_ms": candidate_times,
                "affected_indicator_instances": affected,
                "affected_indicator_count": len(affected),
                "sole_block_indicator_instances": sole,
                "sole_block_indicator_count": len(sole),
                "concurrent_flags": sorted(group["concurrent_flags"] - {flag}),
                "review_status": "annotation_required",
            }
        )
    items.sort(
        key=lambda item: (
            -int(item["sole_block_indicator_count"]),
            -int(item["affected_indicator_count"]),
            int(item["start_ms"]),
            str(item["diagnostic_flag"]),
        )
    )
    for rank, item in enumerate(items, 1):
        item["priority_rank"] = rank

    by_flag = Counter(str(item["diagnostic_flag"]) for item in items)
    linked_pose_items = sum(
        item["diagnostic_queue_link_status"] == "linked" for item in items
    )
    missing_pose_items = sum(
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
            "scores": {"path": str(scores_path.resolve()), "sha256": _sha256(scores_path)},
            "events": {"path": str(events_path.resolve()), "sha256": _sha256(events_path)},
            "summary": {"path": str(summary_path.resolve()), "sha256": _sha256(summary_path)},
            "blocker_audit": {"path": str(blocker_audit_path.resolve()), "sha256": _sha256(blocker_audit_path)},
            "diagnostic_queue": {"path": str(diagnostic_queue_path.resolve()), "sha256": _sha256(diagnostic_queue_path)},
            "review_video": {"path": str(review_video_path.resolve()), "sha256": _sha256(review_video_path)},
        },
        "counts": {
            "eligible_complete_feature_score_only_indicator_instances": len(eligible),
            "work_items": len(items),
            "by_flag": dict(sorted(by_flag.items())),
            "pose_diagnostic_items_linked": linked_pose_items,
            "pose_diagnostic_items_missing_queue_task": missing_pose_items,
            "accepted_annotations": 0,
        },
        "items": items,
        "safety": {
            "model_candidates_are_truth": False,
            "quality_gate_modified": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "priority_is_accuracy": False,
            "clearing_one_item_automatically_changes_score": False,
            "maximum_post_review_status_without_calibration": "calibration_required",
        },
    }


def validate_truth_priority_worklist(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION or report.get("worklist_version") != WORKLIST_VERSION:
        raise ValueError("unsupported truth priority worklist version")
    if report.get("status") != "annotation_required":
        raise ValueError("truth priority worklist must remain annotation_required")
    safety = report.get("safety", {})
    for field in (
        "model_candidates_are_truth",
        "quality_gate_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
        "priority_is_accuracy",
        "clearing_one_item_automatically_changes_score",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe truth priority worklist field: {field}")
    if safety.get("maximum_post_review_status_without_calibration") != "calibration_required":
        raise ValueError("worklist must not imply post-review scoring")
    items = report.get("items", [])
    counts = report.get("counts", {})
    if int(counts.get("work_items", -1)) != len(items):
        raise ValueError("work item count mismatch")
    ranks = [int(item.get("priority_rank", -1)) for item in items]
    if ranks != list(range(1, len(items) + 1)):
        raise ValueError("work item ranks are not contiguous")
    if int(counts.get("accepted_annotations", -1)) != 0:
        raise ValueError("generated worklist must not contain accepted annotations")


def _write_csv(report: dict[str, Any], path: Path) -> None:
    fields = [
        "priority_rank", "work_item_id", "event_id", "event_code", "start_ms", "end_ms",
        "diagnostic_flag", "truth_requirement", "review_type", "diagnostic_queue_link_status",
        "affected_indicator_count", "sole_block_indicator_count", "diagnostic_task_ids",
        "affected_indicator_instances", "concurrent_flags", "review_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in report["items"]:
            row = {key: item.get(key, "") for key in fields}
            for key in ("diagnostic_task_ids", "affected_indicator_instances", "concurrent_flags"):
                row[key] = "|".join(item.get(key, []))
            writer.writerow(row)


def _write_html(report: dict[str, Any], path: Path, review_video_path: Path) -> None:
    video_href = Path(os.path.relpath(review_video_path.resolve(), path.parent.resolve())).as_posix()
    rows = []
    for item in report["items"]:
        seek_ms = item["candidate_review_times_ms"][0] if item["candidate_review_times_ms"] else item["start_ms"]
        rows.append(
            "<tr>"
            f"<td>{item['priority_rank']}</td>"
            f"<td><button data-ms=\"{seek_ms}\">{item['start_ms']/1000:.3f}s</button><br><code>{html.escape(item['event_id'])}</code></td>"
            f"<td><code>{html.escape(item['diagnostic_flag'])}</code></td>"
            f"<td>{item['affected_indicator_count']} / {item['sole_block_indicator_count']}</td>"
            f"<td>{html.escape(item['truth_requirement'])}</td>"
            f"<td>{html.escape(item['diagnostic_queue_link_status'])}<br>{len(item['diagnostic_task_ids'])} task(s)</td>"
            f"<td>{html.escape('、'.join(item['affected_indicator_instances']))}</td>"
            "</tr>"
        )
    path.write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>RallyMate 人工真值优先工作清单</title><style>body{font-family:system-ui;margin:20px;background:#f5f7fb;color:#18202b}main{max-width:1500px;margin:auto}video{width:min(100%,1100px);background:#000}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #ccd3dd;padding:7px;vertical-align:top}th{background:#e9eef6;position:sticky;top:0}code{font-size:12px;word-break:break-all}.warn{background:#fff4d6;border-left:4px solid #d69e00;padding:12px}button{cursor:pointer}</style></head><body><main>"
        "<h1>RallyMate 人工真值优先工作清单</h1>"
        f"<p>共有 {report['counts']['work_items']} 个 event×阻断工作项，覆盖 {report['counts']['eligible_complete_feature_score_only_indicator_instances']} 条特征完整但正式评分受阻的指标实例。</p>"
        "<p class=\"warn\">这是候选优先级，不是真值。点击时间只定位视频；必须进入绑定的 Pose 诊断工作台或评分真值工作台完成人工标注、独立复核和 Python 编译。任何条目都不会自动解除门禁，完成后且没有教练标定时最高仍是 calibration_required。</p>"
        f"<video id=\"reviewVideo\" controls preload=\"metadata\"><source src=\"{html.escape(video_href)}\" type=\"video/mp4\"></video>"
        "<p><a href=\"worklist.json\">机器 JSON</a> · <a href=\"worklist.csv\">CSV</a> · <a href=\"../pose-diagnostic-review/halpe26-full/index.html\">Pose 诊断工作台</a> · <a href=\"../../data/annotations/scoring-truth-pack-v1/review.html\">评分真值工作台</a></p>"
        "<table><thead><tr><th>优先级</th><th>视频定位 / 事件</th><th>阻断 flag</th><th>影响实例 / 单一阻断</th><th>所需真值</th><th>现有队列绑定</th><th>指标实例</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table><script>const v=document.getElementById('reviewVideo');document.querySelectorAll('button[data-ms]').forEach(b=>b.addEventListener('click',()=>{v.currentTime=Number(b.dataset.ms)/1000;v.play().catch(()=>{});window.scrollTo({top:0,behavior:'smooth'});}));</script></main></body></html>\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--blocker-audit", type=Path, required=True)
    parser.add_argument("--diagnostic-queue", type=Path, required=True)
    parser.add_argument("--review-video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build_truth_priority_worklist(
        scores_path=args.scores,
        events_path=args.events,
        summary_path=args.summary,
        blocker_audit_path=args.blocker_audit,
        diagnostic_queue_path=args.diagnostic_queue,
        review_video_path=args.review_video,
    )
    validate_truth_priority_worklist(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "worklist.json"
    csv_path = args.output_dir / "worklist.csv"
    html_path = args.output_dir / "index.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _write_csv(report, csv_path)
    _write_html(report, html_path, args.review_video)
    print(json.dumps({"status": report["status"], "work_items": report["counts"]["work_items"], "eligible_instances": report["counts"]["eligible_complete_feature_score_only_indicator_instances"], "output": str(html_path.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
