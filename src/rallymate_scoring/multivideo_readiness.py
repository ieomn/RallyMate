from __future__ import annotations

import html
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from rallymate_scoring.multivideo_coverage import (
    load_latest_multivideo_indicator_calculation_coverage,
    validate_multivideo_indicator_calculation_coverage,
)
from rallymate_scoring.blocker_taxonomy import (
    exact_typed_reason_support_flags,
    known_typed_reason_codes,
    truth_requirement_for_flag,
    typed_reason_for_flag as _typed_reason_for_flag,
    typed_reason_supports_flag as _typed_reason_supports_flag,
)


SCHEMA_VERSION = "1.1.0"
AUDIT_VERSION = "multivideo-scoring-readiness-decomposition-v1.1.0"
LATEST_VERSION = "multivideo-scoring-readiness-decomposition-latest-v1.1.0"

EXCLUSIVE_CATEGORIES = (
    "measurement_hard_fail",
    "measurement_vector_incomplete",
    "scoring_context_incomplete",
    "scoring_evidence_blocked",
    "calibration_only_missing",
)

TYPED_REASON_SUPPORT_FLAGS = exact_typed_reason_support_flags()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record must be an object: {path}:{line_number}")
            rows.append(value)
    return rows


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    return {"path": str(resolved), "sha256": _sha256_file(resolved)}


def _verify_artifact(binding: Mapping[str, Any], name: str) -> Path:
    path = Path(str(binding.get("path", ""))).resolve()
    if not path.is_file() or _sha256_file(path) != str(
        binding.get("sha256", "")
    ).upper():
        raise ValueError(f"multivideo readiness artifact mismatch: {name}")
    return path


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _complete_feature_vector(features: Any) -> bool:
    return bool(features) and all(
        isinstance(feature, Mapping)
        and feature.get("valid") is True
        and feature.get("value") is not None
        for feature in features
    )


def _classify(record: Mapping[str, Any]) -> dict[str, Any]:
    gate = record["quality_gate"]
    measurement_complete = _complete_feature_vector(record["features"])
    scoring_complete = _complete_feature_vector(record["scoring_features"])
    measurement_allowed = gate.get("measurement_allowed") is True
    scoring_allowed = gate.get("scoring_allowed") is True
    if not measurement_allowed:
        category = "measurement_hard_fail"
    elif not measurement_complete:
        category = "measurement_vector_incomplete"
    elif not scoring_complete:
        category = "scoring_context_incomplete"
    elif not scoring_allowed:
        category = "scoring_evidence_blocked"
    else:
        category = "calibration_only_missing"
    expected_status = (
        "calibration_required"
        if category == "calibration_only_missing"
        else "unavailable"
    )
    if record.get("scoring_status") != expected_status:
        raise ValueError(
            "indicator scoring status differs from readiness decomposition: "
            f"{record.get('indicator_id')}:{record.get('event_id')}"
        )
    expected_feature_status = (
        "measured" if measurement_complete and measurement_allowed else "unavailable"
    )
    if record.get("feature_status") != expected_feature_status:
        raise ValueError(
            "indicator feature status differs from raw vector and measurement gate: "
            f"{record.get('indicator_id')}:{record.get('event_id')}"
        )
    if record.get("grade") is not None:
        raise ValueError("readiness source unexpectedly contains a grade")
    return {
        "measurement_complete": measurement_complete,
        "scoring_complete": scoring_complete,
        "measurement_allowed": measurement_allowed,
        "scoring_allowed": scoring_allowed,
        "category": category,
    }


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _base_counts(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    facts = [_classify(row) for row in rows]
    exclusive = Counter(str(fact["category"]) for fact in facts)
    score_status = Counter(str(row["scoring_status"]) for row in rows)
    total = len(rows)
    counts = {
        "indicator_event_instances": total,
        "raw_measurement_vector_complete": sum(
            bool(fact["measurement_complete"]) for fact in facts
        ),
        "raw_measurement_vector_incomplete": sum(
            not bool(fact["measurement_complete"]) for fact in facts
        ),
        "measurement_gate_allowed": sum(
            bool(fact["measurement_allowed"]) for fact in facts
        ),
        "measurement_hard_fail": sum(
            not bool(fact["measurement_allowed"]) for fact in facts
        ),
        "operational_feature_measured": sum(
            bool(fact["measurement_complete"] and fact["measurement_allowed"])
            for fact in facts
        ),
        "operational_feature_unavailable": sum(
            not bool(fact["measurement_complete"] and fact["measurement_allowed"])
            for fact in facts
        ),
        "full_scoring_vector_complete": sum(
            bool(fact["scoring_complete"]) for fact in facts
        ),
        "full_scoring_vector_incomplete": sum(
            not bool(fact["scoring_complete"]) for fact in facts
        ),
        "scoring_gate_allowed": sum(
            bool(fact["scoring_allowed"]) for fact in facts
        ),
        "scoring_gate_blocked": sum(
            not bool(fact["scoring_allowed"]) for fact in facts
        ),
        "ready_for_calibration_application": sum(
            bool(fact["scoring_complete"] and fact["scoring_allowed"])
            for fact in facts
        ),
        "score_status_counts": _counter_dict(score_status),
        "exclusive_decomposition": {
            category: int(exclusive.get(category, 0))
            for category in EXCLUSIVE_CATEGORIES
        },
        "grade_count": 0,
        "threshold_version_count": 0,
    }
    if sum(counts["exclusive_decomposition"].values()) != total:
        raise ValueError("exclusive readiness decomposition is not exhaustive")
    return counts


def _metric_rows(
    records: list[dict[str, Any]],
    *,
    source_field: str,
    invalid_features: bool = False,
) -> list[dict[str, Any]]:
    state: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "feature_occurrence_count": 0,
            "instance_keys": set(),
            "event_keys": set(),
            "videos": set(),
            "indicators": set(),
            "feature_names": set(),
        }
    )
    for record in records:
        video_id = str(record["video_id"])
        indicator_id = str(record["indicator_id"])
        event_id = str(record["event_id"])
        if invalid_features:
            values = [
                (str(feature["reason"]), str(feature["feature_name"]))
                for feature in record["features"]
                if feature.get("valid") is not True
            ]
        else:
            values = [(str(value), None) for value in record["quality_gate"][source_field]]
        for name, feature_name in values:
            item = state[name]
            item["feature_occurrence_count"] += 1
            item["instance_keys"].add((video_id, indicator_id, event_id))
            item["event_keys"].add((video_id, event_id))
            item["videos"].add(video_id)
            item["indicators"].add(indicator_id)
            if feature_name is not None:
                item["feature_names"].add(feature_name)
    rows = []
    for name, value in state.items():
        rows.append(
            {
                "reason_or_flag": name,
                "feature_occurrence_count": int(value["feature_occurrence_count"]),
                "indicator_instance_count": len(value["instance_keys"]),
                "unique_event_count": len(value["event_keys"]),
                "video_count": len(value["videos"]),
                "indicator_count": len(value["indicators"]),
                "feature_names": sorted(value["feature_names"]),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -int(item["indicator_instance_count"]),
            str(item["reason_or_flag"]),
        ),
    )


def _combination_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combinations: Counter[tuple[str, ...]] = Counter()
    events: dict[tuple[str, ...], set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        flags = tuple(str(value) for value in record["quality_gate"]["scoring_block_flags"])
        if not flags:
            continue
        combinations[flags] += 1
        events[flags].add((str(record["video_id"]), str(record["event_id"])))
    return [
        {
            "flags": list(flags),
            "indicator_instance_count": int(count),
            "unique_event_count": len(events[flags]),
        }
        for flags, count in sorted(
            combinations.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _typed_reason_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reason_records: Counter[str] = Counter()
    supported_records: Counter[str] = Counter()
    known_reasons = set(known_typed_reason_codes())
    mismatches: list[str] = []
    for record in records:
        instance = (
            f"{record['video_id']}:{record['event_id']}:{record['indicator_id']}"
        )
        flags = {
            str(value)
            for value in record["quality_gate"].get("scoring_block_flags", [])
        }
        reasons = {str(value) for value in record.get("reason_codes", [])}
        for flag in flags:
            expected = _typed_reason_for_flag(flag)
            if expected is None:
                mismatches.append(f"{instance}:unmapped:{flag}")
            elif expected not in reasons:
                mismatches.append(f"{instance}:missing:{expected}:{flag}")
        for reason in sorted(reasons.intersection(known_reasons)):
            reason_records[reason] += 1
            if any(_typed_reason_supports_flag(reason, flag) for flag in flags):
                supported_records[reason] += 1
            else:
                mismatches.append(f"{instance}:unsupported:{reason}")
    if mismatches:
        raise ValueError(
            "typed score blocker reasons differ from active flags: "
            + ", ".join(mismatches[:3])
        )
    return [
        {
            "reason_code": reason,
            "indicator_instance_count": int(reason_records[reason]),
            "supported_indicator_instance_count": int(supported_records[reason]),
        }
        for reason in sorted(reason_records)
    ]


def _scoring_block_recovery_rows(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    state: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "instances": set(),
            "events": set(),
            "videos": set(),
            "indicators": set(),
            "sole_instances": set(),
            "sole_events": set(),
        }
    )
    for record in records:
        if _classify(record)["category"] != "scoring_evidence_blocked":
            continue
        video_id = str(record["video_id"])
        event_id = str(record["event_id"])
        indicator_id = str(record["indicator_id"])
        instance = (video_id, event_id, indicator_id)
        event = (video_id, event_id)
        flags = tuple(
            str(value)
            for value in record["quality_gate"].get("scoring_block_flags", [])
        )
        if not flags:
            raise ValueError(
                "scoring_evidence_blocked record has no scoring block flags"
            )
        for flag in flags:
            item = state[flag]
            item["instances"].add(instance)
            item["events"].add(event)
            item["videos"].add(video_id)
            item["indicators"].add(indicator_id)
            if len(flags) == 1:
                item["sole_instances"].add(instance)
                item["sole_events"].add(event)
    rows = [
        {
            "flag": flag,
            "truth_requirement": truth_requirement_for_flag(flag),
            "indicator_instance_count": len(value["instances"]),
            "unique_event_count": len(value["events"]),
            "video_count": len(value["videos"]),
            "indicator_count": len(value["indicators"]),
            "sole_block_to_calibration_required_indicator_instance_count": len(
                value["sole_instances"]
            ),
            "sole_block_to_calibration_required_unique_event_count": len(
                value["sole_events"]
            ),
        }
        for flag, value in state.items()
    ]
    return sorted(
        rows,
        key=lambda item: (
            -int(
                item[
                    "sole_block_to_calibration_required_indicator_instance_count"
                ]
            ),
            -int(item["indicator_instance_count"]),
            str(item["flag"]),
        ),
    )


def _load_records(coverage: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    expected_videos = set(coverage["scope"]["video_ids"])
    expected_indicators = set(coverage["scope"]["indicator_ids"])
    for video_id, bindings in sorted(coverage["source"]["source_reports"].items()):
        indicator_path = _verify_artifact(
            bindings["indicator_features"], f"{video_id}.indicator_features"
        )
        score_path = _verify_artifact(bindings["scores"], f"{video_id}.scores")
        indicators = _read_jsonl(indicator_path)
        scores = _read_jsonl(score_path)
        score_by_key = {
            (str(row["indicator_id"]), str(row["event_id"])): row for row in scores
        }
        if len(score_by_key) != len(scores):
            raise ValueError(f"duplicate score key in readiness source: {video_id}")
        for record in indicators:
            if record.get("video_id") != video_id:
                raise ValueError(f"readiness record video mismatch: {video_id}")
            if record.get("indicator_id") not in expected_indicators:
                raise ValueError(f"readiness record indicator mismatch: {video_id}")
            key = (str(record["indicator_id"]), str(record["event_id"]))
            score = score_by_key.pop(key, None)
            if score is None:
                raise ValueError(f"readiness record has no score: {video_id}:{key}")
            if (
                score.get("status") != record.get("scoring_status")
                or score.get("grade") != record.get("grade")
                or score.get("threshold_version") is not None
                or score.get("quality_gate") != record.get("quality_gate")
                or score.get("reason_codes") != record.get("reason_codes")
                or score.get("feature", {}).get("items")
                != record.get("scoring_features")
            ):
                raise ValueError(f"readiness indicator/score mismatch: {video_id}:{key}")
            records.append(record)
        if score_by_key:
            raise ValueError(f"orphan score rows in readiness source: {video_id}")
    if {str(row["video_id"]) for row in records} != expected_videos:
        raise ValueError("readiness records do not cover the exact video set")
    return records


def _assemble(
    coverage: Mapping[str, Any], coverage_binding: Mapping[str, Any]
) -> dict[str, Any]:
    records = _load_records(coverage)
    counts = _base_counts(records)
    if counts["indicator_event_instances"] != coverage["counts"][
        "indicator_event_instances"
    ]:
        raise ValueError("readiness instance count differs from coverage")
    if counts["operational_feature_measured"] != coverage["counts"][
        "measured_feature_vectors"
    ]:
        raise ValueError("readiness feature count differs from coverage")
    if counts["score_status_counts"] != coverage["counts"]["score_status_counts"]:
        raise ValueError("readiness score counts differ from coverage")

    video_lookup = {str(row["video_id"]): row for row in coverage["videos"]}
    video_rows = []
    for video_id in coverage["scope"]["video_ids"]:
        selected = [row for row in records if row["video_id"] == video_id]
        video_rows.append(
            {
                "video_id": video_id,
                "frame_count": int(video_lookup[video_id]["frame_count"]),
                "candidate_event_count": int(
                    video_lookup[video_id]["candidate_event_count"]
                ),
                "counts": _base_counts(selected),
            }
        )
    indicator_rows = []
    for indicator_id in coverage["scope"]["indicator_ids"]:
        selected = [row for row in records if row["indicator_id"] == indicator_id]
        indicator_rows.append(
            {"indicator_id": indicator_id, "counts": _base_counts(selected)}
        )
    return {
        "source": {
            "coverage": dict(coverage_binding),
            "coverage_id": coverage["coverage_id"],
            "coverage_version": coverage["coverage_version"],
        },
        "scope": {
            "video_ids": list(coverage["scope"]["video_ids"]),
            "indicator_ids": list(coverage["scope"]["indicator_ids"]),
            "event_codes": list(coverage["scope"]["event_codes"]),
            "registry_version": coverage["source"]["registry_version"],
            "pose_contract": coverage["scope"]["pose_contract"],
            "primary_player_version": coverage["scope"]["primary_player_version"],
        },
        "counts": counts,
        "videos": video_rows,
        "indicators": indicator_rows,
        "measurement_failure_reasons": _metric_rows(
            records, source_field="", invalid_features=True
        ),
        "measurement_hard_fail_flags": _metric_rows(
            records, source_field="hard_fail_flags"
        ),
        "scoring_block_flags": _metric_rows(
            records, source_field="scoring_block_flags"
        ),
        "scoring_block_combinations": _combination_rows(records),
        "scoring_block_recovery_priority": _scoring_block_recovery_rows(records),
        "typed_reason_metrics": _typed_reason_rows(records),
        "assertions": {
            "typed_reason_mapping_consistent": True,
            "quality_gate_modified": False,
            "recovery_counterfactual_changes_scores": False,
            "recovery_means_calibration_required_not_scored": True,
            "priority_is_diagnostic_accuracy": False,
        },
    }


def _render_index(manifest: Mapping[str, Any], output_path: Path) -> None:
    total = int(manifest["counts"]["indicator_event_instances"])
    labels = {
        "measurement_hard_fail": "事件测量 hard fail",
        "measurement_vector_incomplete": "Pose 测量向量不完整",
        "scoring_context_incomplete": "评分上下文不完整",
        "scoring_evidence_blocked": "评分证据未验证",
        "calibration_only_missing": "仅缺教练标定/独立测试",
    }
    decomposition_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{:.2f}%</td></tr>".format(
            html.escape(labels[category]),
            manifest["counts"]["exclusive_decomposition"][category],
            100.0
            * manifest["counts"]["exclusive_decomposition"][category]
            / total,
        )
        for category in EXCLUSIVE_CATEGORIES
    )
    video_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(row["video_id"]),
            row["frame_count"],
            row["candidate_event_count"],
            row["counts"]["operational_feature_measured"],
            row["counts"]["exclusive_decomposition"]["scoring_evidence_blocked"],
            row["counts"]["exclusive_decomposition"]["calibration_only_missing"],
        )
        for row in manifest["videos"]
    )
    indicator_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}/{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(row["indicator_id"]),
            row["counts"]["operational_feature_measured"],
            row["counts"]["indicator_event_instances"],
            row["counts"]["exclusive_decomposition"]["measurement_vector_incomplete"],
            row["counts"]["exclusive_decomposition"]["scoring_context_incomplete"],
            row["counts"]["exclusive_decomposition"]["scoring_evidence_blocked"],
            row["counts"]["exclusive_decomposition"]["calibration_only_missing"],
        )
        for row in manifest["indicators"]
    )
    reason_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(row["reason_or_flag"]),
            row["feature_occurrence_count"],
            row["indicator_instance_count"],
            row["unique_event_count"],
            html.escape(", ".join(row["feature_names"]) or "—"),
        )
        for row in manifest["measurement_failure_reasons"]
    )
    recovery_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td></tr>".format(
            html.escape(row["flag"]),
            row["indicator_instance_count"],
            row["unique_event_count"],
            row["sole_block_to_calibration_required_indicator_instance_count"],
            row["sole_block_to_calibration_required_unique_event_count"],
            html.escape(row["truth_requirement"]),
        )
        for row in manifest["scoring_block_recovery_priority"]
    )
    typed_reason_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
            html.escape(row["reason_code"]),
            row["indicator_instance_count"],
            row["supported_indicator_instance_count"],
        )
        for row in manifest["typed_reason_metrics"]
    )
    output_path.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>RallyMate 多视频评分就绪拆解</title><style>body{{font:15px/1.55 system-ui;margin:28px;max-width:1320px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #bbb;padding:6px;text-align:left}}code{{word-break:break-all}}.warn{{background:#fff4d6;padding:12px}}.good{{background:#e9f8ee;padding:12px}}</style></head><body>
<h1>三视频评分就绪状态互斥拆解</h1>
<p class="good">{total} 条指标实例中，{manifest['counts']['raw_measurement_vector_complete']} 条原始 Pose 测量向量完整，{manifest['counts']['operational_feature_measured']} 条通过事件测量门禁；{manifest['counts']['ready_for_calibration_application']} 条完整评分向量已通过评分证据门禁，当前只缺教练标定和独立测试。</p>
<p class="warn">本报告不解除任何门禁、不补 0、不生成等级或阈值。候选事件和诊断尚无人工真值；“向量完整”不是事件、关键点、特征或评分准确率。</p>
<h2>互斥状态</h2><table><thead><tr><th>最先阻断层</th><th>实例</th><th>占比</th></tr></thead><tbody>{decomposition_rows}</tbody></table>
<h2>逐视频</h2><table><thead><tr><th>视频</th><th>帧</th><th>候选事件</th><th>特征 measured</th><th>评分证据阻断</th><th>仅缺标定</th></tr></thead><tbody>{video_rows}</tbody></table>
<h2>逐指标</h2><table><thead><tr><th>指标</th><th>特征 measured/实例</th><th>测量向量缺失</th><th>评分上下文缺失</th><th>评分证据阻断</th><th>仅缺标定</th></tr></thead><tbody>{indicator_rows}</tbody></table>
<h2>无效 Pose 特征原因</h2><table><thead><tr><th>原因</th><th>特征出现次数</th><th>指标实例</th><th>事件</th><th>特征</th></tr></thead><tbody>{reason_rows}</tbody></table>
<h2>评分证据人工复核优先级</h2><p class="warn">“单一阻断”只表示若该记录的唯一活动阻断经真值确认并由后续版本化策略解除，它将前进到 <code>calibration_required</code>；不表示诊断错误，也不表示可以评分。</p><table><thead><tr><th>活动阻断</th><th>参与实例</th><th>事件</th><th>单一阻断实例</th><th>单一阻断事件</th><th>所需真值</th></tr></thead><tbody>{recovery_rows}</tbody></table>
<h2>类型化原因反查</h2><table><thead><tr><th>原因码</th><th>出现实例</th><th>有原始 flag 支撑</th></tr></thead><tbody>{typed_reason_rows}</tbody></table>
</body></html>""",
        encoding="utf-8",
    )


def build_multivideo_scoring_readiness_decomposition(
    *,
    coverage_latest_path: str | Path,
    output_root: str | Path,
    audit_id: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    latest_path = Path(coverage_latest_path).resolve()
    output_root = Path(output_root).resolve()
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    if not audit_id or any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in audit_id
    ):
        raise ValueError("readiness audit_id is invalid")
    run_dir = output_root / audit_id
    if run_dir.exists():
        raise FileExistsError(f"multivideo readiness audit already exists: {run_dir}")
    coverage = load_latest_multivideo_indicator_calculation_coverage(latest_path)
    latest = _read_json(latest_path)
    coverage_binding = latest["manifest"]
    assembled = _assemble(coverage, coverage_binding)
    run_dir.mkdir(parents=True)
    try:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "audit_version": AUDIT_VERSION,
            "audit_id": audit_id,
            "generated_at": generated_at,
            "status": "decomposed_no_gate_change_candidate_events_unvalidated",
            **assembled,
            "source_fingerprint_sha256": _canonical_sha256(
                {
                    "coverage_id": coverage["coverage_id"],
                    "coverage_sha256": coverage_binding["sha256"],
                }
            ),
            "safety": {
                "quality_gate_modified": False,
                "measurement_gate_modified": False,
                "scoring_gate_modified": False,
                "missing_values_zero_filled": False,
                "candidate_events_are_ground_truth": False,
                "event_accuracy_claim": False,
                "feature_accuracy_claim": False,
                "score_accuracy_claim": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
                "calculation_completeness_is_accuracy": False,
                "ready_for_calibration_means_scored": False,
            },
        }
        index_path = run_dir / "index.html"
        manifest["artifacts"] = {
            "index_html": {"path": str(index_path), "sha256": "0" * 64}
        }
        _render_index(manifest, index_path)
        manifest["artifacts"]["index_html"] = _artifact(index_path)
        manifest_path = run_dir / "audit.json"
        _write_json(manifest_path, manifest)
        validate_multivideo_scoring_readiness_decomposition(
            manifest, verify_sources=True
        )
        latest_payload = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": generated_at,
            "audit_id": audit_id,
            "manifest": _artifact(manifest_path),
        }
        temporary = output_root / f".latest-{audit_id}.tmp"
        _write_json(temporary, latest_payload)
        os.replace(temporary, output_root / "latest.json")
        return manifest
    except Exception:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


def validate_multivideo_scoring_readiness_decomposition(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("audit_version") != AUDIT_VERSION
        or manifest.get("status")
        != "decomposed_no_gate_change_candidate_events_unvalidated"
    ):
        raise ValueError("unsupported multivideo readiness decomposition")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(name) is not False
        for name in (
            "quality_gate_modified",
            "measurement_gate_modified",
            "scoring_gate_modified",
            "missing_values_zero_filled",
            "candidate_events_are_ground_truth",
            "event_accuracy_claim",
            "feature_accuracy_claim",
            "score_accuracy_claim",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
            "calculation_completeness_is_accuracy",
            "ready_for_calibration_means_scored",
        )
    ):
        raise ValueError("unsafe multivideo readiness decomposition claim")
    assertions = manifest.get("assertions")
    if (
        not isinstance(assertions, Mapping)
        or assertions.get("typed_reason_mapping_consistent") is not True
        or assertions.get("recovery_means_calibration_required_not_scored")
        is not True
        or any(
            assertions.get(name) is not False
            for name in (
                "quality_gate_modified",
                "recovery_counterfactual_changes_scores",
                "priority_is_diagnostic_accuracy",
            )
        )
    ):
        raise ValueError("unsafe multivideo readiness assertions")
    counts = manifest.get("counts", {})
    total = counts.get("indicator_event_instances")
    if not isinstance(total, int) or total < 1:
        raise ValueError("readiness decomposition has no indicator instances")
    if (
        sum(counts.get("exclusive_decomposition", {}).values()) != total
        or counts.get("operational_feature_measured")
        + counts.get("operational_feature_unavailable")
        != total
        or counts.get("raw_measurement_vector_complete")
        + counts.get("raw_measurement_vector_incomplete")
        != total
        or counts.get("full_scoring_vector_complete")
        + counts.get("full_scoring_vector_incomplete")
        != total
        or counts.get("grade_count") != 0
        or counts.get("threshold_version_count") != 0
    ):
        raise ValueError("readiness decomposition counts are inconsistent")
    blocked_count = int(
        counts["exclusive_decomposition"]["scoring_evidence_blocked"]
    )
    recovery_rows = manifest.get("scoring_block_recovery_priority")
    if not isinstance(recovery_rows, list):
        raise ValueError("scoring block recovery priority is missing")
    seen_flags: set[str] = set()
    for row in recovery_rows:
        if not isinstance(row, Mapping):
            raise ValueError("scoring block recovery row is malformed")
        flag = row.get("flag")
        if not isinstance(flag, str) or not flag or flag in seen_flags:
            raise ValueError("scoring block recovery flag is invalid or duplicated")
        seen_flags.add(flag)
        instance_count = row.get("indicator_instance_count")
        sole_count = row.get(
            "sole_block_to_calibration_required_indicator_instance_count"
        )
        if (
            not isinstance(instance_count, int)
            or not isinstance(sole_count, int)
            or not 0 <= sole_count <= instance_count <= blocked_count
            or not isinstance(row.get("truth_requirement"), str)
            or not row["truth_requirement"]
        ):
            raise ValueError("scoring block recovery counts are invalid")
    typed_rows = manifest.get("typed_reason_metrics")
    if not isinstance(typed_rows, list) or any(
        not isinstance(row, Mapping)
        or not isinstance(row.get("reason_code"), str)
        or not row["reason_code"]
        or row.get("indicator_instance_count")
        != row.get("supported_indicator_instance_count")
        for row in typed_rows
    ):
        raise ValueError("typed score blocker reason support is inconsistent")
    if not verify_sources:
        return
    coverage_path = _verify_artifact(manifest["source"]["coverage"], "coverage")
    coverage = _read_json(coverage_path)
    validate_multivideo_indicator_calculation_coverage(
        coverage, verify_sources=True
    )
    if coverage.get("coverage_id") != manifest["source"].get("coverage_id"):
        raise ValueError("readiness coverage ID mismatch")
    assembled = _assemble(coverage, manifest["source"]["coverage"])
    for name in (
        "source",
        "scope",
        "counts",
        "videos",
        "indicators",
        "measurement_failure_reasons",
        "measurement_hard_fail_flags",
        "scoring_block_flags",
        "scoring_block_combinations",
        "scoring_block_recovery_priority",
        "typed_reason_metrics",
        "assertions",
    ):
        if manifest.get(name) != assembled[name]:
            raise ValueError(f"multivideo readiness differs from replay: {name}")
    expected_fingerprint = _canonical_sha256(
        {
            "coverage_id": coverage["coverage_id"],
            "coverage_sha256": manifest["source"]["coverage"]["sha256"],
        }
    )
    if manifest.get("source_fingerprint_sha256") != expected_fingerprint:
        raise ValueError("multivideo readiness fingerprint mismatch")
    _verify_artifact(manifest["artifacts"]["index_html"], "index_html")


def load_latest_multivideo_scoring_readiness_decomposition(
    latest_path: str | Path,
) -> dict[str, Any]:
    latest = _read_json(Path(latest_path).resolve())
    if (
        latest.get("schema_version") != SCHEMA_VERSION
        or latest.get("latest_version") != LATEST_VERSION
    ):
        raise ValueError("unsupported multivideo readiness latest pointer")
    manifest_path = _verify_artifact(latest["manifest"], "latest.manifest")
    manifest = _read_json(manifest_path)
    if manifest.get("audit_id") != latest.get("audit_id"):
        raise ValueError("multivideo readiness latest ID mismatch")
    validate_multivideo_scoring_readiness_decomposition(
        manifest, verify_sources=True
    )
    return manifest


__all__ = [
    "AUDIT_VERSION",
    "LATEST_VERSION",
    "build_multivideo_scoring_readiness_decomposition",
    "load_latest_multivideo_scoring_readiness_decomposition",
    "validate_multivideo_scoring_readiness_decomposition",
]
