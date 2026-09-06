from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMA_DATA_PATH = Path(__file__).parent / "data" / "keypoint_schemas.json"


def _coco_wholebody133_schema(coco17: dict[str, Any]) -> dict[str, Any]:
    """Build the official COCO-WholeBody 133-point order.

    The generated names follow MMPose's ``coco_wholebody.py`` dataset metadata:
    body 17, foot 6, face 68, left hand 21 and right hand 21.  Only the first
    17 points inherit the existing RallyMate joint IDs; every additional point
    remains explicitly unmapped until a downstream scoring contract adopts it.
    """

    foot = (
        "left_big_toe",
        "left_small_toe",
        "left_heel",
        "right_big_toe",
        "right_small_toe",
        "right_heel",
    )
    finger_names = (
        "thumb1",
        "thumb2",
        "thumb3",
        "thumb4",
        "forefinger1",
        "forefinger2",
        "forefinger3",
        "forefinger4",
        "middle_finger1",
        "middle_finger2",
        "middle_finger3",
        "middle_finger4",
        "ring_finger1",
        "ring_finger2",
        "ring_finger3",
        "ring_finger4",
        "pinky_finger1",
        "pinky_finger2",
        "pinky_finger3",
        "pinky_finger4",
    )
    names = (
        [point["name"] for point in coco17["keypoints"]]
        + list(foot)
        + [f"face-{index}" for index in range(68)]
        + ["left_hand_root"]
        + [f"left_{name}" for name in finger_names]
        + ["right_hand_root"]
        + [f"right_{name}" for name in finger_names]
    )
    downstream = {
        point["name"]: point["downstream_joint_id"]
        for point in coco17["keypoints"]
    }
    return {
        "count": 133,
        "source": "OpenMMLab MMPose configs/_base_/datasets/coco_wholebody.py",
        "source_url": (
            "https://github.com/open-mmlab/mmpose/blob/main/"
            "configs/_base_/datasets/coco_wholebody.py"
        ),
        "groups": {
            "body": [0, 16],
            "foot": [17, 22],
            "face": [23, 90],
            "left_hand": [91, 111],
            "right_hand": [112, 132],
        },
        "keypoints": [
            {
                "index": index,
                "name": name,
                "downstream_joint_id": downstream.get(name),
            }
            for index, name in enumerate(names)
        ],
    }


@lru_cache(maxsize=1)
def load_keypoint_schemas() -> dict[str, Any]:
    payload = json.loads(SCHEMA_DATA_PATH.read_text(encoding="utf-8"))
    formats = payload.get("formats")
    if not isinstance(formats, dict):
        raise ValueError("keypoint schema registry is missing formats")
    formats.setdefault(
        "coco_wholebody133",
        _coco_wholebody133_schema(formats["coco17"]),
    )
    for format_name, definition in formats.items():
        keypoints = definition.get("keypoints", [])
        if len(keypoints) != definition.get("count"):
            raise ValueError(f"{format_name} count does not match keypoint metadata")
        indexes = [point.get("index") for point in keypoints]
        if indexes != list(range(len(keypoints))):
            raise ValueError(f"{format_name} keypoint indexes must be contiguous")
    return payload


def keypoint_schema(format_name: str) -> dict[str, Any]:
    try:
        return load_keypoint_schemas()["formats"][format_name]
    except KeyError as exc:
        raise ValueError(f"unknown keypoint format: {format_name}") from exc


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


_COCO17 = keypoint_schema("coco17")["keypoints"]
COCO_POSE_KEYPOINTS = [point["name"] for point in _COCO17]
COCO_TO_RALLYMATE_JOINT = [point["downstream_joint_id"] for point in _COCO17]
