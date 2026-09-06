from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from rallymate_vision.pose.metadata import load_keypoint_schemas


TEXT_FIELDS = ("name", "definition", "currentPoints", "requiredPoints", "calculation")
SEMANTIC_RULES = (
    {
        "capability": "hip_center_motion",
        "terms": ("髋中心",),
        "fields": ("currentPoints", "calculation"),
        "halpe26_status": "direct_center_point_plus_bilateral_hips",
        "wholebody133_status": "derived_from_bilateral_hips",
        "notes": "Halpe26 hip is a direct pelvis-center point; bilateral hips remain available for cross-checking.",
        "wholebody133_notes": "COCO-WholeBody retains bilateral hips but does not add a native pelvis-center point; the center must stay marked derived.",
    },
    {
        "capability": "body_center_or_center_of_mass",
        "terms": ("重心", "人体中心", "身体中心"),
        "fields": TEXT_FIELDS,
        "halpe26_status": "derived_2d_proxy_only",
        "wholebody133_status": "derived_2d_proxy_only",
        "notes": "No 2D pose topology directly measures true center of mass; a body center must remain marked as derived.",
        "wholebody133_notes": "More landmarks do not turn a 2D weighted center into biomechanical center of mass.",
    },
    {
        "capability": "knee_ankle_mechanics",
        "terms": ("膝踝",),
        "fields": ("currentPoints", "calculation"),
        "halpe26_status": "improved_by_big_toe_small_toe_and_heel",
        "wholebody133_status": "improved_by_big_toe_small_toe_and_heel",
        "notes": "Knee flexion was already measurable; foot direction now makes a 2D shank-forefoot ankle diagnostic possible.",
        "wholebody133_notes": "The same six COCO-WholeBody foot landmarks support 2D foot direction and ankle proxies, but not pressure or confirmed contact.",
    },
    {
        "capability": "wrist_elbow_motion",
        "terms": ("双肘/双腕", "腕/肘"),
        "fields": ("currentPoints", "calculation"),
        "halpe26_status": "direct_wrist_and_elbow_only",
        "wholebody133_status": "improved_by_42_hand_landmarks",
        "notes": "Arm timing is observable, but wrist articulation and grip are not because palms and fingers are absent.",
        "wholebody133_notes": "Twenty-one landmarks per hand support finger, palm and wrist-shape measurements when visible; racket geometry is still required for grip-to-racket classification.",
    },
    {
        "capability": "shoulder_hip_rotation",
        "terms": ("双肩/双髋", "肩线/髋线"),
        "fields": ("currentPoints", "calculation"),
        "halpe26_status": "direct_source_points",
        "wholebody133_status": "direct_source_points_spine_still_missing",
        "notes": "Shoulder and hip lines are direct; thoracic and lumbar segment rotation are not independently observable.",
        "wholebody133_notes": "WholeBody adds face, feet and hands, not thoracic or lumbar spine segments.",
    },
    {
        "capability": "head_orientation",
        "terms": ("头部位置与朝向",),
        "fields": ("currentPoints", "calculation"),
        "halpe26_status": "improved_head_and_neck_but_gaze_is_proxy",
        "wholebody133_status": "improved_by_68_face_landmarks_true_gaze_missing",
        "notes": "Head and neck improve the axis estimate; true gaze remains unavailable.",
        "wholebody133_notes": "Dense 2D facial geometry improves head-orientation proxies, but does not directly observe eyeball gaze or 3D head pose.",
    },
    {
        "capability": "fine_grip_or_hand_articulation",
        "terms": ("握拍", "握把", "手掌", "手指", "拇指"),
        "fields": TEXT_FIELDS,
        "halpe26_status": "missing",
        "wholebody133_status": "hand_geometry_present_racket_relation_missing",
        "notes": "Halpe26 stops at the wrist and cannot measure palm, finger, thumb or grip geometry.",
        "wholebody133_notes": "Both hands are represented by 21 landmarks. Exact tennis grip still needs racket handle/axis evidence and truth labels.",
    },
)


def _card_text(card: dict[str, Any], fields: tuple[str, ...]) -> str:
    return " ".join(str(card.get(field, "")) for field in fields)


def _semantic_coverage(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for rule in SEMANTIC_RULES:
        matched = [
            card["id"]
            for card in cards
            if any(term in _card_text(card, rule["fields"]) for term in rule["terms"])
        ]
        result.append(
            {
                "capability": rule["capability"],
                "card_text_mention_count": len(matched),
                "example_indicator_ids": matched[:12],
                "halpe26_status": rule["halpe26_status"],
                "notes": rule["notes"],
                "wholebody133_status": rule["wholebody133_status"],
                "wholebody133_notes": rule["wholebody133_notes"],
            }
        )
    return result


def build_report(root: Path) -> dict[str, Any]:
    registry_path = root / "src" / "rallymate_scoring" / "data" / "metric_cards.json"
    schema_path = root / "src" / "rallymate_vision" / "pose" / "data" / "keypoint_schemas.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    # The runtime registry materializes COCO-WholeBody133 from the official
    # ordered group definition while preserving the JSON-backed COCO/Halpe
    # formats.  Audit the same effective registry used by inference.
    schemas = load_keypoint_schemas()["formats"]
    cards = registry["cards"]
    coco = schemas["coco17"]["keypoints"]
    halpe = schemas["halpe26"]["keypoints"]
    wholebody = schemas.get("coco_wholebody133", {}).get("keypoints", [])
    coco_names = {item["name"] for item in coco}
    halpe_names = {item["name"] for item in halpe}
    wholebody_names = {item["name"] for item in wholebody}
    joint_counts: Counter[str] = Counter()
    pose_dependent = 0
    pose_only = 0
    pose_cards_with_explicit_joints = 0
    mapped_pose_cards = 0
    coco_joint_ids = {item["downstream_joint_id"] for item in coco if item["downstream_joint_id"]}
    for card in cards:
        dependencies = set(card.get("dependencies", []))
        required_joints = set(re.findall(r"J\d{3}", card.get("requiredPoints", "")))
        joint_counts.update(required_joints)
        if "pose" in dependencies:
            pose_dependent += 1
            pose_only += dependencies == {"pose"}
            if required_joints:
                pose_cards_with_explicit_joints += 1
                mapped_pose_cards += required_joints.issubset(coco_joint_ids)

    added = [item for item in halpe if item["name"] not in coco_names]
    return {
        "schema_version": "1.0.0",
        "audit_version": "full-body-keypoint-coverage/2.0.0",
        "sources": {
            "metric_registry": str(registry_path.relative_to(root)),
            "metric_registry_version": registry.get("registryVersion"),
            "keypoint_registry": str(schema_path.relative_to(root)),
            "keypoint_schema_version": json.loads(schema_path.read_text(encoding="utf-8")).get("schema_version"),
            "technical_manual_section": "13.4-13.5",
        },
        "indicator_scope": {
            "total": len(cards),
            "pose_dependent": pose_dependent,
            "pose_only": pose_only,
            "pose_cards_with_explicit_joint_ids": pose_cards_with_explicit_joints,
            "explicit_joint_mapping_complete_on_coco17": mapped_pose_cards,
            "warning": "explicit J-ID mapping is not proof that the geometry or temporal feature is measurable",
        },
        "explicit_joint_reference_counts": dict(joint_counts.most_common()),
        "topology_comparison": {
            "coco17": {
                "count": len(coco),
                "points": [item["name"] for item in coco],
            },
            "halpe26": {
                "count": len(halpe),
                "points": [item["name"] for item in halpe],
                "added_over_coco17": [item["name"] for item in added],
                "added_count": len(halpe_names - coco_names),
            },
            "coco_wholebody133": {
                "registered": bool(wholebody),
                "count": len(wholebody),
                "points": [item["name"] for item in wholebody],
                "added_over_coco17": [
                    item["name"] for item in wholebody if item["name"] not in coco_names
                ],
                "added_count": len(wholebody_names - coco_names),
                "groups": {
                    "body": [item["name"] for item in wholebody[:17]],
                    "feet": [item["name"] for item in wholebody[17:23]],
                    "face": [item["name"] for item in wholebody[23:91]],
                    "left_hand": [item["name"] for item in wholebody[91:112]],
                    "right_hand": [item["name"] for item in wholebody[112:133]],
                }
                if len(wholebody) == 133
                else {},
                "warning": "133 visible outputs are not 133 independently score-ready measurements",
            },
        },
        "semantic_requirement_coverage": _semantic_coverage(cards),
        "safe_derived_points": [
            {
                "name": "hip_center",
                "sources": ["left_hip", "right_hip"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "neck_proxy",
                "sources": ["left_shoulder", "right_shoulder"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "shoulder_center",
                "sources": ["left_shoulder", "right_shoulder"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "face_center",
                "sources": ["nose", "left_eye", "right_eye", "left_ear", "right_ear"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "left_forefoot_center",
                "sources": ["left_big_toe", "left_small_toe"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "right_forefoot_center",
                "sources": ["right_big_toe", "right_small_toe"],
                "model_output": False,
                "must_mark_derived": True,
            },
            {
                "name": "body_center_2d",
                "sources": ["shoulder_center", "hip"],
                "model_output": False,
                "must_mark_derived": True,
                "warning": "proxy only; not true center of mass",
            },
        ],
        "not_output_by_halpe26": [
            {
                "group": "spine_segments",
                "points": ["thoracic_spine", "lumbar_spine"],
                "impact": "cannot independently measure thoracic-versus-lumbar flexion or rotation",
            },
            {
                "group": "hands",
                "points": ["palm_center", "thumb", "finger_joints"],
                "impact": "cannot measure grip type, hand articulation or wrist angle beyond a wrist-position proxy",
            },
            {
                "group": "fine_foot_contact",
                "points": ["midfoot", "sole_contact", "foot_arch"],
                "impact": "heel and toes improve foot direction but do not fully identify pressure or contact mechanics",
            },
            {
                "group": "face_and_3d",
                "points": ["chin", "depth_z", "true_gaze"],
                "impact": "head orientation remains a 2D proxy",
            },
        ],
        "not_solved_by_wholebody133": [
            {
                "group": "spine_segments",
                "points": ["thoracic_spine", "lumbar_spine"],
                "impact": "dense face and hand landmarks do not independently measure thoracic-versus-lumbar motion",
            },
            {
                "group": "biomechanics_and_depth",
                "points": ["true_center_of_mass", "depth_z", "joint_axis_3d"],
                "impact": "fixed-camera 2D pose remains view-dependent and cannot provide true 3D biomechanics",
            },
            {
                "group": "foot_contact",
                "points": ["sole_pressure", "foot_arch", "confirmed_ground_contact"],
                "impact": "toe and heel landmarks are kinematic proxies, not force/contact sensors",
            },
            {
                "group": "tennis_equipment_relation",
                "points": ["racket_handle", "racket_axis", "racket_face"],
                "impact": "hand landmarks alone cannot establish grip-to-racket geometry",
            },
            {
                "group": "true_gaze",
                "points": ["eyeball_gaze_vector"],
                "impact": "facial landmarks support head orientation only; true gaze remains unavailable",
            },
        ],
        "decision": {
            "previous_video_label": "complete actual Halpe26 26-point output",
            "current_video_label": "complete actual COCO-WholeBody133 output with confidence filtering",
            "next_validation_label": "truth-annotated, same-clip feature-error comparison across YOLO17, Halpe26 and WholeBody133",
            "must_not_claim": "complete scoring-standard body topology",
            "six_indicator_loop": "shoulder/hip/knee/ankle inputs are structurally present; hip and foot additions improve evidence but do not remove truth/calibration gates",
            "wholebody_effect": "adds hand and dense face evidence plus the same six foot points; it materially increases observable anatomy but does not bypass event truth, feature-error or coach-calibration gates",
            "formal_scoring_status": "calibration_required",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit RallyMate scoring-card body point requirements against COCO17 and Halpe26")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    report = build_report(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": "ok"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
