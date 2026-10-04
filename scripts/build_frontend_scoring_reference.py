"""Build the display-only scoring reference from the immutable Word audit.

This deliberately does not read or write any production scoring configuration.
Original text is copied verbatim; implementation caveats are separate notes.
Run with --check to verify the committed frontend artifact without writing it.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "contracts/scoring-reference-20261004.json"
DEFAULT_OUTPUT = ROOT / "scoring-demo-web/app/data/scoring-reference.json"
REFERENCE_ID = "scoring-reference-20261004"
VERSION = f"{REFERENCE_ID}:frontend-v1"
TEXT_FIELDS = {
    "id": "指标编号",
    "name": "指标名称",
    "definition": "技术定义",
    "calculation": "计算方式",
    "required_points": "所需点",
    "unevaluable": "不可评价",
}
SPECIAL_DIFFERENCES = (
    "FS01-M02-DELTA",
    "FS01-M04-SCALE",
    "FS01-M05-CROSS-EVENT",
    "FS02-M02-TARGET",
    "FS09-M05-CROSS-EVENT",
)
IMPLEMENTATION_NOTES = {
    "FS01-M02-DELTA": "原文要求髋部下降幅度、膝角变化和移动速度的连续性；当前主要测量髋部高度典型值、膝屈曲峰值、站宽与支撑位置，不能替代这些变化过程。",
    "FS01-M04-SCALE": "原文以髋宽衡量落地后双踝间距；当前以人体尺度衡量脚部间距，分母不同。脚部减速只能提示可能落地，不能确认真实触地。",
    "FS01-M05-CROSS-EVENT": "原文要求确认后续第一步启动并测量两者间隔；当前未关联独立的第一步启动事件，只能查看当前人体中心与支撑测量。",
    "FS02-M02-TARGET": "原卡名称要求向来球方向转换，定义却写目标方向。当前可使用外部设定的训练目标，但它不等于视频里观测到的来球方向，不能据此完整判定方向是否正确。",
    "FS09-M05-CROSS-EVENT": "原文要求制动后连续进入启动或回位；当前仅测量稳定、减速等表现，没有关联后续动作，不能证明衔接已完成。",
}
BOUNDARY_ISSUE = "原文所属阶段未给出开始和结束标志；尚不能据此确定完整阶段边界。"
RELATED_NOTE = (
    "只完成部分二维测量，尚不能完整判断原文要求或给出 A～E 技术等级。"
    "画面中的人体中心和脚部运动不能直接证明真实重心、触地或发力。"
)
UNIMPLEMENTED_NOTE = (
    "尚未接入这项原文指标的独立测量与判级；动作识别或整段视频统计不代表本项已经实现。"
    "缺少证据不能判为未完成或 E 级。"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text_field(fields: dict[str, Any], key: str) -> str:
    value = fields[key]["text"]
    require(isinstance(value, str), f"{key}: expected source text")
    return value


def unique_by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {item[key]: item for item in items}
    require(len(result) == len(items), f"Duplicate {key}")
    return result


def source_reference(locator: str, cell: str | None, document_ids: set[str]) -> dict[str, str]:
    # Allow only document-relative Word locators, never private paths or XPath.
    require(isinstance(locator, str), "Missing source locator")
    match = re.fullmatch(r"([A-Z]+-[A-Z]+):word/document\.xml:P[0-9]{4,}", locator)
    require(match is not None, f"Invalid source locator: {locator!r}")
    document_id = match.group(1)
    require(document_id in document_ids, f"Unknown source document: {document_id}")
    result = {"document_id": document_id, "location": locator}
    if cell is not None:
        require(isinstance(cell, str) and re.fullmatch(r"T[0-9]+\.R[0-9]+\.C[0-9]+", cell) is not None,
                "Invalid source cell")
        result["cell"] = cell
    return result


def field_sources(field: dict[str, Any], document_ids: set[str]) -> list[dict[str, str]]:
    if "locator" in field:
        return [source_reference(field["locator"], field.get("table_cell"), document_ids)]
    locators, cells = field["locators"], field["cells"]
    require(bool(locators) and len(locators) == len(cells), "Ambiguous source field references")
    return [source_reference(location, cell, document_ids) for location, cell in zip(locators, cells)]


def build_visual_techniques(audit: dict[str, Any], document_ids: set[str]) -> list[dict[str, Any]]:
    stages = unique_by(audit["visual_stage_event_mappings"], "id")
    rules = unique_by(audit["visual_rules"], "id")
    techniques = unique_by(audit["technique_mappings"], "technique_id")
    require((len(stages), len(rules), len(techniques)) == (68, 246, 24), "Unexpected visual source coverage")
    seen_stages: set[str] = set()
    seen_rules: set[str] = set()
    result = []
    for technique_id, technique in techniques.items():
        phase_ids = technique["source_stage_ids"]
        require(len(phase_ids) == len(set(phase_ids)), "Duplicate visual phase within technique")
        phases = []
        for stage_id in phase_ids:
            stage = stages[stage_id]
            require(technique_id in stage["technique_ids"], "Visual stage belongs to another technique")
            require(isinstance(stage["optional_in_source"], bool), "Missing optional-stage declaration")
            seen_stages.add(stage_id)
            clauses = []
            # Keep definitions and recognition descriptions, then the audited
            # semicolon clauses. Do not turn any of these into scoring weights.
            for role in ("definition", "recognition"):
                for paragraph in stage["source_role_paragraphs"][role]:
                    require(isinstance(paragraph["text"], str), "Invalid visual paragraph")
                    reference = paragraph["source_ref"]
                    clauses.append({"text": paragraph["text"], "source": source_reference(
                        reference["locator"], reference.get("table_cell"), document_ids)})
            for rule_id in stage["rule_ids"]:
                rule = rules[rule_id]
                require(rule["stage_id"] == stage_id and technique_id in rule["technique_ids"],
                        "Visual clause belongs to another stage or technique")
                require(isinstance(rule["source_text"], str), "Invalid visual clause")
                reference = rule["source_ref"]
                source = source_reference(reference["locator"], reference.get("table_cell"), document_ids)
                require(source["document_id"] == rule["source_document_id"], "Visual clause source mismatch")
                clauses.append({"text": rule["source_text"], "source": source})
                seen_rules.add(rule_id)
            require(bool(clauses), "Visual phase has no source clauses")
            phases.append({"id": stage_id, "name": stage["source_heading"],
                           "optional": stage["optional_in_source"], "clauses": clauses})
        result.append({"id": technique_id, "name": technique["name"], "phases": phases})
    require(seen_stages == set(stages) and seen_rules == set(rules), "Visual source coverage incomplete")
    return result


def build_catalog(audit: dict[str, Any]) -> dict[str, Any]:
    require(audit["schema_version"] == "1.0.0" and audit["reference_id"] == REFERENCE_ID,
            "Unsupported source reference version")
    require(audit["artifact_scope"] == "source_reference_audit"
            and audit["production_scoring_configuration"] is False, "Expected a display-only source audit")
    documents = []
    for source in audit["source_documents"]:
        require(re.fullmatch(r"[a-f0-9]{64}", source["sha256"]) is not None, "Invalid document hash")
        require(not any(char in source["filename"] for char in ("/", "\\", ":")), "Document name is a path")
        documents.append({"id": source["source_id"], "name": source["filename"], "sha256": source["sha256"]})
    document_ids = set(unique_by(documents, "id"))
    require(len(documents) == 7, "Expected seven source documents")
    indicators = unique_by(audit["indicators"], "indicator_id")
    require(len(indicators) == 298, "Expected 298 formal indicator cards")
    events = {item["event_code"]: item["name_zh"] for item in audit["formal_events"]["GS"]}
    events.update({item["event_id"]: item["name"] for item in audit["formal_events"]["FS"]})
    require(len(events) == 15, "Expected five GS and ten FS events")
    fs_event_sources = {item["event_id"]: item["event_heading"] for item in audit["formal_events"]["FS"]}
    differences = unique_by(audit["implementation_differences"], "id")
    conflicts = unique_by(audit["conflicts"], "id")
    special_notes: dict[str, list[str]] = {}
    for difference_id in SPECIAL_DIFFERENCES:
        difference = differences[difference_id]
        for indicator_id in difference["indicator_ids"]:
            special_notes.setdefault(indicator_id, []).append(IMPLEMENTATION_NOTES[difference_id])
    issues: dict[str, list[str]] = {}
    for conflict_id in ("GS-CONFLICT-01", "GS-CONFLICT-02"):
        conflict = conflicts[conflict_id]
        for indicator_id in conflict["indicator_ids"]:
            issues.setdefault(indicator_id, []).append(conflict["detail"] + "保留原文，待规则维护者确认。")
    require(set(issues) == {"GS01-M10-04", "GS02-M01-01", "GS02-M01-02"}, "Unexpected source-conflict scope")
    result = []
    related_count = 0
    missing_boundary_count = 0
    for indicator_id, indicator in indicators.items():
        fields = indicator["source_fields"]
        rule: dict[str, Any] = {key: text_field(fields, source_key) for key, source_key in TEXT_FIELDS.items()}
        require(rule["id"] == indicator_id, "Indicator ID disagrees with source text")
        event_code = indicator["event_code"]
        rule.update(event_code=event_code, event_name=events[event_code],
                    stage=text_field(fields, "所属阶段") if "所属阶段" in fields else "")
        rule["grades"] = [{"grade": grade, "definition": text_field(fields, f"{grade}级")} for grade in "ABCDE"]
        displayed_fields = list(TEXT_FIELDS.values()) + [f"{grade}级" for grade in "ABCDE"]
        if "所属阶段" in fields:
            displayed_fields.append("所属阶段")
        sources = [source for key in displayed_fields for source in field_sources(fields[key], document_ids)]
        if event_code in fs_event_sources:
            sources.extend(field_sources(fs_event_sources[event_code], document_ids))
        # The same paragraph may supply multiple fields; retain each location once.
        rule["sources"] = list({(source["document_id"], source["location"], source.get("cell")): source
                                for source in sources}.values())
        summary = indicator["runtime_summary"]
        require(summary["formal_scoring_enabled"] is False, "Source audit unexpectedly enables formal scoring")
        related = summary["current_feasibility_level"] == "F2"
        features = []
        if related:
            feasibility = indicator["implementation"]["current_feasibility"]
            require(feasibility["feasibility_level"] == "F2", "Conflicting feasibility declarations")
            features = feasibility["required_features"]
            require(bool(features) and all(isinstance(feature, str) and feature for feature in features)
                    and len(features) == len(set(features)), "Invalid related-feature list")
            related_count += 1
        rule["implementation"] = {
            "kind": "related_2d_measurement" if related else "not_implemented",
            "features": list(features),
            "note": (RELATED_NOTE if related else UNIMPLEMENTED_NOTE) + "".join(special_notes.get(indicator_id, [])),
        }
        rule_issues = list(issues.get(indicator_id, []))
        if indicator["domain"] == "GS" and not text_field(fields, "开始标志性动作") and not text_field(fields, "结束标志性动作"):
            rule_issues.append(BOUNDARY_ISSUE)
            missing_boundary_count += 1
        if rule_issues:
            rule["source_issues"] = rule_issues
        result.append(rule)
    require(related_count == 13, "Expected thirteen partial F2 measurement associations")
    require(missing_boundary_count == 97, "Unexpected number of missing source phase boundaries")
    return {
        "version": VERSION,
        "source_documents": documents,
        "rules": result,
        "findings": [
            {"title": "原文没有百分制换算", "detail": "298 项原文给出 A～E 定性描述，没有技术百分制换算、扣分公式、指标权重或等级分界。测量证据参考分说明证据质量，不能替代技术评级。"},
            {"title": "13 项只有部分关联测量", "detail": "298 项中仅 13 项步伐指标完成部分二维测量，尚无完成标定的正式技术评分项；其余 285 项不能因未接入而记为零分或 E 级。这是本轮规则核验的范围，不代表全部规则都已通过视频验证。"},
            {"title": "原文中有三项需要纠错", "detail": "GS01-M10-04 的技术定义混入下一节标题；GS02-M01-01 与 GS02-M01-02 的名称、定义及识别内容错配。页面保留原文，不能自行交换名称或补写定义作为正式依据。"},
            {"title": "97 项缺少阶段边界", "detail": "底线 20 个阶段涉及 97 项指标，原文开始与结束标志均为空。阶段边界尚需澄清，整段视频的姿态统计不能替代这些阶段的独立证据。"},
            {"title": "五阶段与十阶段并存", "detail": "视觉定义的五个主阶段与 GS 评分卡的十个细阶段同时存在，原文未声明互相替代。盯球可能贯穿全程；不能静默合并、平均或当作互斥时间片段。网前明确可选的稳定调整或蓄力阶段，未出现时不能自动扣分。"},
            {"title": "测量含义必须逐项对照", "detail": "FS01-M02 的统计量不能替代原文变化量；FS01-M04 的人体尺度不能替代髋宽；FS02-M02 的外部目标方向不能冒充观测到的来球方向。FS01-M05、FS09-M05 还缺跨事件关联，内部稳定或减速不能证明后续衔接完成。"},
            {"title": "缺少证据与 E 级不同", "detail": "原文的有效帧低于 70% 是相应关节与事件窗口的不可评价条件，不是技术得分。E 级的未观察到动作仍需要足够有效证据；无法测量时应保留不可评价。"},
            {"title": "来源版本仍需确认", "detail": "步伐评分卡文件名写第二版，正文标题写第一版；保留文件名与 SHA-256 供核对，不能按文件名自行决定版本优先级。"},
        ],
        "visual_techniques": build_visual_techniques(audit, document_ids),
    }


def render_catalog(catalog: dict[str, Any]) -> bytes:
    return (json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Compare generated bytes without modifying files")
    args = parser.parse_args(argv)
    if args.source.resolve() == args.output.resolve():
        parser.error("The source audit is read-only; output must be a different file")
    audit = json.loads(args.source.read_text(encoding="utf-8"))
    catalog = build_catalog(audit)
    content = render_catalog(catalog)
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != content:
            print("Frontend scoring reference is missing or stale; regenerate it.")
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(content)
    print(f"{'Verified' if args.check else 'Generated'} 298 rules, 24 visual techniques, 7 sources ({len(content)} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
