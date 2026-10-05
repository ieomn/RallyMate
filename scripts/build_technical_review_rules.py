"""Compile the immutable Word audit into a packaged human-review contract.

No source text, numeric cutoffs or grades are inferred. This is the selection
and provenance contract for human reviews, not a machine calibration asset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/scoring-reference-20261004.json"
OUTPUT = ROOT / "src/rallymate_scoring/data/technical_review_rules.json"


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def build(raw: bytes) -> dict:
    audit = json.loads(raw)
    if audit.get("reference_id") != "scoring-reference-20261004" or audit.get("production_scoring_configuration") is not False:
        raise ValueError("Unexpected source reference")
    documents = {item["stable_id"]: item for item in audit["source_documents"]}
    source_docs = {item["source_id"]: item for item in audit["source_documents"]}
    conflicts = {item["id"]: item for item in audit["conflicts"]}
    rules = []
    for card in audit["indicators"]:
        fields, blockers, warnings = card["source_fields"], [], []
        for conflict_id, conflict in conflicts.items():
            if card["indicator_id"] not in conflict.get("indicator_ids", []) and conflict_id not in card.get("conflict_ids", []):
                continue
            message = conflict.get("detail")
            if not message:
                continue
            target = blockers if conflict_id in {"GS-CONFLICT-01", "GS-CONFLICT-02"} else warnings
            target.append({"code": "source_rule_conflict" if target is blockers else conflict_id, "message": message})
        if card["domain"] == "GS" and not fields["开始标志性动作"]["text"] and not fields["结束标志性动作"]["text"]:
            blockers.append({"code": "source_boundaries_missing", "message": "原文缺少阶段开始和结束定义，人工选择时段不能补正原文。"})
        doc = documents[card.get("source_document_id", card.get("source_stable_id"))]
        rules.append({
            "indicator_id": card["indicator_id"], "event_code": card["event_code"],
            "name_zh": card["name_zh"], "stage_code": card["stage_code"],
            "manual_grading_allowed": not blockers, "blockers": blockers, "warnings": warnings,
            "source_document_id": doc["source_id"], "source_document_sha256": doc["sha256"].lower(),
            "source_table": card.get("source_table") or fields["指标编号"].get("table_cell", "").split(".")[0], "rubric_sha256": canonical_sha(fields),
            "grades": {g: fields[f"{g}级"]["text"] for g in "ABCDE"},
            "definition": fields["技术定义"]["text"], "calculation": fields["计算方式"]["text"],
            "required_points": fields["所需点"]["text"], "unevaluable": fields["不可评价"]["text"],
        })
    stages = {item["id"]: item for item in audit["visual_stage_event_mappings"]}
    visual = []
    for rule in audit["visual_rules"]:
        stage = stages[rule["stage_id"]]
        doc = source_docs[rule["source_document_id"]]
        visual.append({
            "visual_rule_id": rule["id"], "technique_ids": rule["technique_ids"],
            "stage_id": rule["stage_id"], "phase_id": rule["phase_id"],
            "optional_in_source": stage["optional_in_source"],
            "optional_positive_observation": rule["optional_positive_observation"],
            "source_document_id": doc["source_id"], "source_document_sha256": doc["sha256"].lower(),
            "source_locator": rule["source_ref"]["locator"], "source_text": rule["source_text"],
            "rubric_sha256": canonical_sha({"source_ref": rule["source_ref"], "source_text": rule["source_text"],
                                              "optional_in_source": stage["optional_in_source"]}),
        })
    if len(rules) != 298 or len({r["indicator_id"] for r in rules}) != 298 or len(visual) != 246:
        raise ValueError("Incomplete source contract")
    if sum(not r["manual_grading_allowed"] for r in rules) != 100:
        raise ValueError("Unexpected source conflict scope")
    return {
        "schema_version": "technical-review-rules-v1",
        "source_reference_version": audit["reference_id"],
        "source_reference_sha256": hashlib.sha256(raw).hexdigest(),
        "source_documents": [{"id": d["source_id"], "sha256": d["sha256"].lower()} for d in audit["source_documents"]],
        "rules": rules, "visual_rules": visual,
        "policy": {"machine_calibration_asset": False, "numeric_grade_mapping": None,
                   "visual_requirements_have_grades": False, "optional_absence_deducts_points": False},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build(SOURCE.read_bytes())
    content = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != content:
            raise SystemExit("Packaged source rules are stale")
    else:
        OUTPUT.write_bytes(content)
    print(f"Verified 298 indicator rules, 246 visual requirements, 7 sources ({len(content)} bytes)")


if __name__ == "__main__":
    main()
