"""A proposed display grouping of audited Word cards, never a scoring rubric.

The source defines FS events and technique-specific GS stages, not five axes.
Only identifiers and source issues are projected here; the audited card text
and grades remain in technical_review_rules.json without a second transcription.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import json
import re
from typing import Any


VERSION = "training-dimensions-v1.0.0"
GROUPING_ORIGIN = "proposed_source_stage_grouping"
DIMENSION_NAMES = {
    "movement": "移动质量",
    "preparation": "准备与引拍",
    "swing": "挥拍协调",
    "contact": "触球控制",
    "follow_through": "随挥与衔接",
}
EXPECTED_CARD_COUNTS = {"movement": 70, "preparation": 78, "swing": 72,
                        "contact": 25, "follow_through": 53}
_GROUPING_RULES = {
    "movement": "FS01–FS09；全部 GS 技术的 M02 启动移动。",
    "preparation": "全部 GS 技术的 M01/M03；另含 GS04/GS05 切削的 M04 重心支撑、M05 高位引拍、M06 拍面打开。",
    "swing": "GS01/GS02/GS03 的 M04–M07；GS04/GS05 切削的 M07。",
    "contact": "全部 GS 技术的 M08；评价触球动作与控制，不包含独立落点目标命中率。",
    "follow_through": "全部 GS 技术的 M09/M10，加 FS10 回位与下一拍衔接。",
}
# Anchors locate the source purpose supporting this *proposed* grouping. They
# do not establish that a runtime three-phase candidate is any source stage.
_ANCHORS = {
    "movement": [("FS02-M01", "CARD-FS:word/document.xml:P0258", "来球方向与启动方向；无球轨迹时不判方向正确性。"),
                 ("GS01-M02", "CARD-GS:word/document.xml:P0592", "启动、调整步伐并移动到击球位置。")],
    "preparation": [("GS01-M01", "CARD-GS:word/document.xml:P0369", "准备姿态、观察来球与垫步启动。"),
                    ("GS01-M03", "CARD-GS:word/document.xml:P0815", "单位转体、辅助引拍、后摆与加载支撑腿。"),
                    ("GS04-M05", "CARD-GS:word/document.xml:P7869", "建立切削高位准备状态。"),
                    ("GS04-M06", "CARD-GS:word/document.xml:P8093", "拍面打开阶段结束才进入切削方向。")],
    "swing": [("GS01-M04", "CARD-GS:word/document.xml:P1038", "蹬地与身体运动。"),
              ("GS01-M05", "CARD-GS:word/document.xml:P1305", "骨盆旋转与动力传递。"),
              ("GS01-M06", "CARD-GS:word/document.xml:P1572", "胸廓旋转与手臂加速准备。"),
              ("GS01-M07", "CARD-GS:word/document.xml:P1795", "手臂加速与球拍释放。")],
    "contact": [("GS01-M08", "CARD-GS:word/document.xml:P2018", "拍球接触，控制方向、速度及旋转。"),
                ("GS01-M08-01", "CARD-GS:word/document.xml:P2036", "稳定击球姿态。"),
                ("GS02-M08-02", "CARD-GS:word/document.xml:P4270", "稳定拍面、触球及方向控制。")],
    "follow_through": [("GS01-M09", "CARD-GS:word/document.xml:P2241", "继续旋转挥拍并完成收拍。"),
                       ("GS01-M10", "CARD-GS:word/document.xml:P2464", "恢复平衡、持拍控制与下一拍准备。"),
                       ("FS10-M05", "CARD-FS:word/document.xml:P1822", "回位后稳定及后续 FS01/FS02 衔接。")],
}


def _group_card(card: dict[str, Any]) -> str:
    identifier = card.get("indicator_id", "")
    fs = re.fullmatch(r"(FS(?:0[1-9]|10))-M0[1-5]", identifier)
    if fs and card.get("event_code") == fs[1]:
        return "follow_through" if fs[1] == "FS10" else "movement"
    gs = re.fullmatch(r"(GS0[1-5])-(M(?:0[1-9]|10))-0[1-6]", identifier)
    if not gs or card.get("event_code") != gs[1] or card.get("stage_code") != f"{gs[1]}-{gs[2]}":
        raise ValueError(f"Unsupported source card for training grouping: {identifier}")
    stage = int(gs[2][1:])
    if stage == 2:
        return "movement"
    if stage in (1, 3) or gs[1] in ("GS04", "GS05") and stage in (4, 5, 6):
        return "preparation"
    if stage in (4, 5, 6, 7):
        return "swing"
    return "contact" if stage == 8 else "follow_through"


@lru_cache(maxsize=1)
def _source_bases() -> dict[str, dict[str, Any]]:
    catalog = json.loads(files("rallymate_scoring").joinpath("data/technical_review_rules.json").read_text(encoding="utf-8"))
    groups = {identifier: [] for identifier in DIMENSION_NAMES}
    seen = set()
    for card in catalog["rules"]:
        identifier = card["indicator_id"]
        if identifier in seen:
            raise ValueError(f"Duplicate source card in training grouping: {identifier}")
        seen.add(identifier)
        groups[_group_card(card)].append(card)
    if {key: len(cards) for key, cards in groups.items()} != EXPECTED_CARD_COUNTS:
        raise ValueError("Audited source catalogue changed; review the proposed five-axis grouping")
    result = {}
    for identifier, cards in groups.items():
        result[identifier] = {
            "version": VERSION, "card_ids": sorted(card["indicator_id"] for card in cards),
            "card_count": len(cards), "grouping_origin": GROUPING_ORIGIN,
            "proposed_not_original": True, "grouping_rule_zh": _GROUPING_RULES[identifier],
            "semantics": "source_catalogue_membership_not_runtime_measurement_or_score_coverage",
            "source_reference_version": catalog["source_reference_version"],
            "source_reference_sha256": catalog["source_reference_sha256"],
            "source_documents": [source for source in catalog["source_documents"] if source["id"] in ("CARD-FS", "CARD-GS")],
            "source_anchors": [{"source_id": source_id, "locator": locator, "reason_zh": reason}
                               for source_id, locator, reason in _ANCHORS[identifier]],
            "source_blockers": [{"indicator_id": card["indicator_id"],
                                 "manual_grading_allowed": card["manual_grading_allowed"],
                                 "blockers": card["blockers"]}
                                for card in cards if card["blockers"]],
        }
    return result


def source_basis_for_dimension(identifier: str) -> dict[str, Any]:
    """Return an independent source projection, preserving all source blockers."""
    if identifier == "transition":
        return {"version": VERSION, "card_ids": [], "card_count": 0,
                "grouping_origin": GROUPING_ORIGIN, "proposed_not_original": True,
                "parent_dimension_id": "follow_through", "source_blockers": [],
                "semantics": "supplementary_observation_no_duplicate_card_membership",
                "grouping_rule_zh": "衔接作为随挥与衔接维度的补充观测；来源卡只在该主维度登记一次。"}
    return deepcopy(_source_bases()[identifier])


def dimension_for_card(indicator_id: str) -> str | None:
    """Resolve catalogue membership only, without claiming runtime applicability."""
    return next((identifier for identifier, basis in _source_bases().items()
                 if indicator_id in basis["card_ids"]), None)
