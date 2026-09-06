from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rallymate_events.schemas import validate_event_record
from rallymate_scoring.calibration import compute_annotator_agreement, validate_coach_label
from rallymate_scoring.indicator_requirements import required_phase_keys as registry_required_phase_keys
from rallymate_scoring.indicator_feature_qualification import (
    DIAGNOSTIC_SOURCE_STATUS,
    SYNTHETIC_SOURCE_STATUS as SYNTHETIC_FEATURE_SOURCE_STATUS,
    VERIFIED_SOURCE_STATUS as VERIFIED_FEATURE_SOURCE_STATUS,
    IndicatorFeatureQualificationError,
    VerifiedIndicatorFeatureSourceMetadata,
    assert_indicator_feature_source_metadata_unchanged,
    build_qualification_snapshot,
    derive_feature_qualification,
    nonproduction_source_metadata,
    require_verified_indicator_feature_source_metadata,
)
from rallymate_scoring.scoring_truth_calibration_authorization import (
    DIAGNOSTIC_STATUS,
    SYNTHETIC_STATUS,
    ScoringTruthCalibrationAuthorizationError,
    VerifiedScoringTruthCalibrationAuthorization,
    build_nonproduction_truth_authorization_marker,
    require_verified_scoring_truth_calibration_authorization,
)
from rallymate_scoring.scoring_context import (
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
    TARGET_DIRECTION_INDICATOR_ID,
    build_target_direction_alignment_feature,
)


SCHEMA_VERSION = "1.0.0"
SAMPLE_SCHEMA_VERSION = "1.1.0"
COMPILER_VERSION = "rallymate-calibration-dataset-v1.2.0"
CANONICALIZATION = "rallymate-canonical-json-v1"
SPLITS = ("train", "validation", "independent_test")
LABEL_SCALE = ("E", "D", "C", "B", "A")


@dataclass(frozen=True)
class _FrozenInput:
    path: Path
    raw: bytes
    sha256: str
    parsed: Any


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json_bytes(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be strict UTF-8 JSON") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON {label}: {exc}") from exc


def _parse_jsonl_bytes(raw: bytes, *, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be strict UTF-8 JSONL") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number: {value}")
                ),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid JSONL {label}:{line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record must be an object: {label}:{line_number}")
        records.append(record)
    return records


def _freeze_input(
    path: Path,
    *,
    label: str,
    parser: Callable[[bytes], Any],
) -> _FrozenInput:
    requested = Path(path)
    if requested.is_symlink():
        raise ValueError(
            f"calibration input is missing or not a plain file: {label}={requested}"
        )
    resolved = requested.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"calibration input is missing or not a plain file: {label}={resolved}")
    raw = resolved.read_bytes()
    return _FrozenInput(
        path=resolved,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        parsed=parser(raw),
    )


def _assert_frozen_inputs_unchanged(inputs: Mapping[str, _FrozenInput]) -> None:
    for label, frozen in inputs.items():
        if not frozen.path.is_file() or frozen.path.is_symlink():
            raise ValueError(f"calibration input disappeared before commit: {label}")
        if frozen.path.read_bytes() != frozen.raw:
            raise ValueError(f"calibration input changed before atomic commit: {label}")


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON used for lineage and independent-test seals."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    raw = Path(path).read_bytes()
    return _parse_jsonl_bytes(raw, label=str(path))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _event_code_for_indicator(indicator_id: str) -> str:
    return indicator_id.split("-M", 1)[0]


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _normalize_video_id(raw: str, known_video_ids: set[str]) -> str:
    if raw in known_video_ids:
        return raw
    matches = sorted(video_id for video_id in known_video_ids if raw.startswith(video_id + ":"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"ambiguous feature video_id {raw!r}: {matches}")
    return raw


def _load_semantic_requirements(
    validation_report: Mapping[str, Any] | None,
    indicator_ids: list[str],
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    requirements: dict[str, set[str]] = {indicator_id: set() for indicator_id in indicator_ids}
    seen: set[str] = set()
    if validation_report is None:
        return {key: tuple() for key in indicator_ids}, set(indicator_ids)
    for item in validation_report.get("readiness", {}).get("semantic_coverage", []):
        indicator_id = item.get("indicator_id")
        if indicator_id not in requirements:
            continue
        seen.add(indicator_id)
        values = item.get("required_semantic_keys", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("truth validation semantic requirements must be string arrays")
        requirements[indicator_id].update(values)
    return (
        {key: tuple(sorted(value)) for key, value in requirements.items()},
        set(indicator_ids) - seen,
    )


def _validate_manual_event(record: dict[str, Any]) -> None:
    required = {
        "video_id",
        "event_id",
        "event_code",
        "person_track_id",
        "start_ms",
        "end_ms",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"manual event missing fields: {sorted(missing)}")
    if record.get("annotation_source") != "manual":
        raise ValueError("calibration dataset accepts manual event truth only")
    if record.get("adjudication_status") != "accepted":
        raise ValueError("manual event must be adjudicated and accepted")
    if record["event_code"] not in {"FS01", "FS02", "FS09"}:
        raise ValueError("manual event_code must be FS01, FS02 or FS09")
    if not isinstance(record["person_track_id"], int) or record["person_track_id"] < 1:
        raise ValueError("manual event person_track_id must be a positive integer")
    if int(record["end_ms"]) <= int(record["start_ms"]):
        raise ValueError("manual event end_ms must be greater than start_ms")
    validate_event_record(record, annotation=True)


def _validate_semantic(record: dict[str, Any]) -> None:
    required = {
        "annotation_id",
        "video_id",
        "event_id",
        "indicator_id",
        "semantic_key",
        "observable",
        "adjudication_status",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"semantic truth missing fields: {sorted(missing)}")
    if record["adjudication_status"] != "accepted":
        raise ValueError("semantic truth must be adjudicated and accepted")
    if not isinstance(record["observable"], bool):
        raise ValueError("semantic observable must be boolean")
    if record["observable"]:
        if not isinstance(record.get("value"), dict):
            raise ValueError("observable semantic truth requires a value object")
    elif record.get("value") is not None or not record.get("null_reason"):
        raise ValueError("unobservable semantic truth requires null value and null_reason")


def _load_group_metadata(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise ValueError("group metadata must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("group metadata schema_version must be 1.0.0")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("group metadata must be an array or an object with records")
    seen: set[tuple[str, int | None]] = set()
    result = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("video_id"), str):
            raise ValueError("group metadata records require video_id")
        track = record.get("person_track_id")
        if track is not None and (not isinstance(track, int) or track < 1):
            raise ValueError("group metadata person_track_id must be a positive integer")
        for field in ("player_id", "session_id", "view_group"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"group metadata {field} must be null or non-empty string")
        key = (record["video_id"], track)
        if key in seen:
            raise ValueError(f"duplicate group metadata key: {key}")
        seen.add(key)
        result.append(
            {
                "video_id": record["video_id"],
                "person_track_id": track,
                "player_id": record.get("player_id"),
                "session_id": record.get("session_id"),
                "view_group": record.get("view_group"),
            }
        )
    return result


def _metadata_for_event(
    metadata: list[dict[str, Any]], video_id: str, person_track_id: int
) -> dict[str, Any]:
    exact = [
        item
        for item in metadata
        if item["video_id"] == video_id and item["person_track_id"] == person_track_id
    ]
    fallback = [
        item
        for item in metadata
        if item["video_id"] == video_id and item["person_track_id"] is None
    ]
    if len(exact) > 1 or (not exact and len(fallback) > 1):
        raise ValueError(f"ambiguous group metadata for {video_id}:{person_track_id}")
    return (exact or fallback or [{}])[0]


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def _build_leakage_components(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    video_ids = sorted({sample["video_id"] for sample in samples})
    union = _UnionFind(video_ids)
    by_player: dict[str, list[str]] = defaultdict(list)
    by_session: dict[str, list[str]] = defaultdict(list)
    unknown_identity_videos: list[str] = []
    for sample in samples:
        groups = sample["groups"]
        video_id = sample["video_id"]
        if groups.get("player_id"):
            by_player[str(groups["player_id"])].append(video_id)
        if groups.get("session_id"):
            by_session[str(groups["session_id"])].append(video_id)
        if not groups.get("player_id") and not groups.get("session_id"):
            unknown_identity_videos.append(video_id)
    for values in list(by_player.values()) + list(by_session.values()):
        values = sorted(set(values))
        for value in values[1:]:
            union.union(values[0], value)
    # Unknown identities are conservatively inseparable; this prevents an unverified
    # same-player relationship from leaking across splits, at the cost of readiness.
    unknown_identity_videos = sorted(set(unknown_identity_videos))
    for value in unknown_identity_videos[1:]:
        union.union(unknown_identity_videos[0], value)
    component_videos: dict[str, set[str]] = defaultdict(set)
    for video_id in video_ids:
        component_videos[union.find(video_id)].add(video_id)
    result: dict[str, dict[str, Any]] = {}
    for videos in component_videos.values():
        members = [sample for sample in samples if sample["video_id"] in videos]
        players = sorted(
            {str(item["groups"]["player_id"]) for item in members if item["groups"].get("player_id")}
        )
        sessions = sorted(
            {str(item["groups"]["session_id"]) for item in members if item["groups"].get("session_id")}
        )
        views = sorted(
            {str(item["groups"]["view_group"]) for item in members if item["groups"].get("view_group")}
        )
        identity = {"video_ids": sorted(videos), "player_ids": players, "session_ids": sessions}
        component_id = "lg-" + canonical_sha256(identity)[:16]
        result[component_id] = {
            "leakage_group_id": component_id,
            **identity,
            "view_groups": views,
            "identity_metadata_complete": all(
                item["groups"].get("player_id") and item["groups"].get("session_id")
                for item in members
            ),
        }
        for sample in members:
            sample["groups"]["leakage_group_id"] = component_id
    return result


def _load_split_policy(
    policy: Mapping[str, Any] | None,
    *,
    source_sha256: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if policy is None:
        return None, None
    policy = dict(policy)
    required = {"schema_version", "policy_id", "policy_version", "assignments"}
    missing = required - set(policy)
    if missing:
        raise ValueError(f"split policy missing fields: {sorted(missing)}")
    if policy["schema_version"] != SCHEMA_VERSION:
        raise ValueError("split policy schema_version must be 1.0.0")
    if not isinstance(policy["assignments"], list):
        raise ValueError("split policy assignments must be an array")
    for assignment in policy["assignments"]:
        if assignment.get("split") not in SPLITS:
            raise ValueError("split assignment has invalid split")
        selectors = (
            "video_ids",
            "player_ids",
            "session_ids",
            "view_groups",
            "leakage_group_ids",
        )
        for field in selectors:
            if field not in assignment:
                continue
            values = assignment[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError(
                    f"split assignment {field} must be a non-empty unique string array"
                )
        if not any(assignment.get(field) for field in selectors):
            raise ValueError("split assignment needs at least one group selector")
    return policy, source_sha256


def _assign_splits(
    samples: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
    policy: dict[str, Any] | None,
) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    assignments: dict[str, str | None] = {}
    conflicts: list[dict[str, Any]] = []
    for component_id, component in sorted(components.items()):
        matched: set[str] = set()
        if policy is not None:
            for assignment in policy["assignments"]:
                selectors = (
                    bool(set(assignment.get("video_ids", [])) & set(component["video_ids"])),
                    bool(set(assignment.get("player_ids", [])) & set(component["player_ids"])),
                    bool(set(assignment.get("session_ids", [])) & set(component["session_ids"])),
                    bool(set(assignment.get("view_groups", [])) & set(component["view_groups"])),
                    component_id in set(assignment.get("leakage_group_ids", [])),
                )
                if any(selectors):
                    matched.add(assignment["split"])
        if len(matched) == 1:
            assignments[component_id] = next(iter(matched))
        elif len(matched) > 1:
            assignments[component_id] = None
            conflicts.append(
                {"leakage_group_id": component_id, "conflicting_splits": sorted(matched)}
            )
        else:
            assignments[component_id] = None
    for sample in samples:
        sample["split"] = assignments[sample["groups"]["leakage_group_id"]]
    return assignments, conflicts


def _manual_target_alignment_feature(
    feature_record: dict[str, Any] | None,
    semantics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    launch_feature = None
    if feature_record is not None:
        for item in feature_record.get("features", []):
            if item.get("feature_name") == "launch_direction_deg":
                launch_feature = item
                break
    semantic = semantics.get("target_direction")
    if semantic is None:
        return build_target_direction_alignment_feature(
            launch_direction_feature=launch_feature,
            target_direction_deg=None,
            reference_confidence=None,
            unavailable_reason="manual_target_direction_missing",
        )
    if semantic.get("observable") is not True:
        return build_target_direction_alignment_feature(
            launch_direction_feature=launch_feature,
            target_direction_deg=None,
            reference_confidence=None,
            unavailable_reason=str(
                semantic.get("null_reason") or "manual_target_direction_unobservable"
            ),
        )
    value = semantic.get("value")
    if not isinstance(value, dict) or value.get("coordinate_frame") != "image_plane":
        return build_target_direction_alignment_feature(
            launch_direction_feature=launch_feature,
            target_direction_deg=None,
            reference_confidence=None,
            unavailable_reason="target_direction_image_plane_transform_required",
        )
    return build_target_direction_alignment_feature(
        launch_direction_feature=launch_feature,
        target_direction_deg=value.get("direction_deg"),
        reference_confidence=semantic.get("annotation_confidence"),
        unavailable_reason="manual_target_direction_or_launch_direction_unavailable",
    )


def _feature_vector(
    feature_record: dict[str, Any] | None,
    required_features: list[str],
    *,
    derived_items: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_items: dict[str, dict[str, Any]] = {}
    if feature_record is not None:
        for item in feature_record.get("scoring_features", feature_record.get("features", [])):
            name = item.get("feature_name")
            if isinstance(name, str):
                if name in raw_items:
                    raise ValueError(f"duplicate feature_name in indicator record: {name}")
                raw_items[name] = item
    for name, item in (derived_items or {}).items():
        raw_items[name] = item
    result = []
    for name in required_features:
        item = raw_items.get(name)
        if item is None:
            result.append(
                {
                    "feature_name": name,
                    "feature_version": None,
                    "value": None,
                    "unit": None,
                    "confidence": 0.0,
                    "valid": False,
                    "reason": "required_feature_missing",
                    "source_frames": [],
                }
            )
            continue
        valid = bool(item.get("valid")) and _finite_number(item.get("value"))
        result.append(
            {
                "feature_name": name,
                "feature_version": item.get("feature_version"),
                "value": float(item["value"]) if _finite_number(item.get("value")) else None,
                "unit": item.get("unit"),
                "confidence": (
                    float(item.get("confidence", 0.0))
                    if _finite_number(item.get("confidence", 0.0))
                    else 0.0
                ),
                "valid": valid,
                "reason": item.get("reason") or ("valid" if valid else "feature_invalid"),
                "source_frames": sorted(
                    int(value)
                    for value in item.get("source_frames", [])
                    if isinstance(value, int) and value >= 0
                ),
            }
        )
    return result


def _label_summary(labels: list[dict[str, Any]]) -> dict[str, Any]:
    grades = sorted(
        (label for label in labels if label["label_type"] == "grade"),
        key=lambda item: (item["annotator_id"], item["annotation_id"]),
    )
    rankings = sorted(
        (label for label in labels if label["label_type"] == "ranking"),
        key=lambda item: (item["rank_group_id"], item["annotator_id"], item["annotation_id"]),
    )
    annotators = sorted({item["annotator_id"] for item in grades})
    unique_grades = sorted({item["grade"] for item in grades})
    if not grades:
        status = "missing"
        resolved = None
    elif len(annotators) < 2:
        status = "single_annotator"
        resolved = None
    elif len(unique_grades) == 1:
        status = "unanimous"
        resolved = unique_grades[0]
    else:
        status = "conflict"
        resolved = None
    return {
        "resolution_policy": "unanimous_multi_coach_only_v1",
        "resolution_semantics": (
            "unanimous agreement is conservative label resolution, not proof of "
            "ground-truth correctness; explicit independent adjudication is preferred"
        ),
        "grade_status": status,
        "resolved_grade": resolved,
        "contributing_annotation_ids": [item["annotation_id"] for item in grades],
        "contributing_annotator_ids": annotators,
        "conflicting_grades": unique_grades if status == "conflict" else [],
        "grade_label_count": len(grades),
        "ranking_label_count": len(rankings),
        "ranking_annotator_count": len({item["annotator_id"] for item in rankings}),
    }


def _distribution(samples: list[dict[str, Any]], field: str) -> dict[str, int]:
    values = []
    for sample in samples:
        if field == "grade":
            value = sample["label_summary"].get("resolved_grade")
        else:
            value = sample["groups"].get(field)
        if value is not None:
            values.append(str(value))
    return dict(sorted(Counter(values).items()))


def recompute_independent_test_seal(
    samples: list[dict[str, Any]], indicator_id: str
) -> dict[str, Any]:
    selected = sorted(
        (
            sample
            for sample in samples
            if sample["indicator_id"] == indicator_id
            and sample.get("split") == "independent_test"
        ),
        key=lambda item: item["sample_id"],
    )
    digest = canonical_sha256(selected)
    return {
        "seal_id": "seal-" + digest[:16],
        "indicator_id": indicator_id,
        "record_count": len(selected),
        "groups": sorted({item["groups"]["leakage_group_id"] for item in selected}),
        "sample_ids": [item["sample_id"] for item in selected],
        "content_sha256": digest,
        "canonicalization": CANONICALIZATION,
        "labels_withheld": True,
    }


def _prepared_indicator(
    *,
    indicator: dict[str, Any],
    samples: list[dict[str, Any]],
    dataset_id: str,
    dataset_version: str,
    dataset_source_sha256: str,
    source_manifest_id: str,
    prepared_at: str,
    split_policy: dict[str, Any] | None,
    agreement: dict[str, Any],
    indicator_blockers: list[str],
    artifact_scope: str,
    source_kind: str,
    truth_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    indicator_id = indicator["indicator_id"]
    required_features = list(indicator["required_features"])
    selected = [sample for sample in samples if sample["indicator_id"] == indicator_id]
    units_by_name: dict[str, set[str]] = defaultdict(set)
    versions_by_name: dict[str, set[str]] = defaultdict(set)
    for sample in selected:
        for item in sample["feature_vector"]:
            if item["unit"]:
                units_by_name[item["feature_name"]].add(item["unit"])
            if item["feature_version"]:
                versions_by_name[item["feature_name"]].add(item["feature_version"])
    local_blockers = list(indicator_blockers)
    if not any(
        sample["label_summary"]["grade_status"] == "unanimous"
        for sample in selected
    ):
        local_blockers.append("resolved_grade_calibration_records_missing")
    for name in required_features:
        if len(units_by_name[name]) > 1:
            local_blockers.append(f"feature_unit_mismatch:{name}")
        elif not units_by_name[name]:
            local_blockers.append(f"feature_unit_missing:{name}")
        if len(versions_by_name[name]) > 1:
            local_blockers.append(f"feature_version_mismatch:{name}")
        elif not versions_by_name[name]:
            local_blockers.append(f"feature_version_missing:{name}")
    unit_by_feature = {
        name: next(iter(units_by_name[name])) if len(units_by_name[name]) == 1 else None
        for name in required_features
    }
    feature_version_by_feature = {
        name: next(iter(versions_by_name[name])) if len(versions_by_name[name]) == 1 else None
        for name in required_features
    }
    records = []
    for sample in selected:
        if sample.get("split") not in {"train", "validation"}:
            continue
        if not sample["readiness"]["eligible_for_grade_modeling"]:
            continue
        records.append(
            {
                "record_id": sample["sample_id"],
                "video_id": sample["video_id"],
                "event_id": sample["event_id"],
                "group_id": sample["groups"]["leakage_group_id"],
                "leakage_component_id": sample["groups"]["leakage_group_id"],
                "split": sample["split"],
                "features": {
                    item["feature_name"]: item["value"] for item in sample["feature_vector"]
                },
                "grade": sample["label_summary"]["resolved_grade"],
                "consensus_status": "unanimous",
                "annotator_ids": sample["label_summary"]["contributing_annotator_ids"],
                "label_source_ids": sample["label_summary"]["contributing_annotation_ids"],
                "athlete_id": sample["groups"].get("player_id"),
                "session_id": sample["groups"].get("session_id"),
                "view_group": sample["groups"].get("view_group"),
            }
        )
    records.sort(key=lambda item: item["record_id"])
    groups_by_split = {
        split: sorted(
            {
                sample["groups"]["leakage_group_id"]
                for sample in selected
                if sample.get("split") == split
            }
        )
        for split in SPLITS
    }
    grade_pairs = agreement.get("grade_agreement", {}).get("pairwise", [])
    evaluated_pairs = [
        item for item in grade_pairs if item.get("quadratic_weighted_kappa") is not None
    ]
    prepared_agreement = {
        "status": "evaluated" if evaluated_pairs else "not_evaluated",
        "metric": "quadratic_weighted_kappa",
        "value": agreement.get("grade_agreement", {}).get("mean_quadratic_weighted_kappa"),
        "annotator_count": agreement.get("annotator_count", 0),
        "shared_item_count": sum(
            len(sample["label_summary"]["contributing_annotator_ids"]) >= 2
            for sample in selected
        ),
        "pairwise_shared_item_count_sum": sum(
            item.get("shared_items", 0) for item in grade_pairs
        ),
        "pairwise": grade_pairs,
    }
    seal = recompute_independent_test_seal(samples, indicator_id)
    split_counts = Counter(item["split"] for item in records)
    grade_counts_by_split = {
        split: dict(
            sorted(Counter(item["grade"] for item in records if item["split"] == split).items())
        )
        for split in ("train", "validation")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id + ":" + indicator_id,
        "dataset_version": dataset_version,
        "artifact_scope": artifact_scope,
        "source": {
            "kind": source_kind,
            "manifest_id": source_manifest_id,
            "source_sha256": dataset_source_sha256,
            "prepared_at": prepared_at,
        },
        "truth_authorization": dict(truth_authorization),
        "indicator_id": indicator_id,
        "feature_order": required_features,
        "unit_by_feature": unit_by_feature,
        "feature_version_by_feature": feature_version_by_feature,
        "label_scale": list(LABEL_SCALE),
        "label_resolution_policy": "unanimous_multi_coach_only_v1",
        "label_resolution_semantics": (
            "unanimous labels are resolved inputs, not proof of ground-truth correctness; "
            "independent explicit adjudication is preferred"
        ),
        "split_policy": {
            "strategy": "group_holdout",
            "group_key": "leakage_component_id",
            "policy_id": split_policy.get("policy_id") if split_policy else None,
            "policy_version": split_policy.get("policy_version") if split_policy else None,
            "train_groups": groups_by_split["train"],
            "validation_groups": groups_by_split["validation"],
            "independent_test_groups": groups_by_split["independent_test"],
        },
        "agreement": prepared_agreement,
        "records": records,
        "independent_test_seal": seal,
        "readiness": {
            "status": "prepared_for_external_protocol_review" if not local_blockers else "insufficient",
            "blockers": sorted(set(local_blockers)),
            "F3_claimed": False,
        },
        "integrity": {
            "record_count": len(records),
            "split_counts": dict(sorted(split_counts.items())),
            "grade_counts_by_split": grade_counts_by_split,
            "source_dataset_sha256": dataset_source_sha256,
            "canonicalization": CANONICALIZATION,
        },
    }


def _compile_calibration_dataset_in_place(
    *,
    feasibility_registry_path: str | Path,
    indicator_feature_paths: Iterable[str | Path],
    manual_events_path: str | Path,
    manual_semantics_path: str | Path,
    coach_labels_path: str | Path,
    output_dir: str | Path,
    truth_intake_manifest_path: str | Path | None = None,
    truth_manifest_path: str | Path | None = None,
    truth_validation_report_path: str | Path | None = None,
    group_metadata_path: str | Path | None = None,
    split_policy_path: str | Path | None = None,
    generated_at: str | None = None,
    frozen_inputs: Mapping[str, _FrozenInput] | None = None,
    prepared_artifact_scope: str = "unverified_truth_diagnostic_input",
    prepared_source_kind: str = "unverified_private_truth",
    truth_authorization: Mapping[str, Any] | None = None,
    indicator_feature_source_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile traceable calibration/evaluation data without fitting scores.

    Joins are exact on canonical video_id + manual event_id + indicator_id. Candidate
    event overlap is deliberately not used, because that would silently promote a model
    boundary to truth. Feature records therefore need to be recomputed using accepted
    manual event IDs before they can become modeling-eligible.
    """

    registry_path = Path(feasibility_registry_path).resolve()
    feature_paths = [Path(path).resolve() for path in indicator_feature_paths]
    if not feature_paths:
        raise ValueError("at least one indicator-features JSONL path is required")
    manual_events_path = Path(manual_events_path).resolve()
    manual_semantics_path = Path(manual_semantics_path).resolve()
    coach_labels_path = Path(coach_labels_path).resolve()
    output_dir = Path(output_dir).resolve()
    truth_intake_manifest = (
        Path(truth_intake_manifest_path).resolve()
        if truth_intake_manifest_path
        else None
    )
    truth_manifest = Path(truth_manifest_path).resolve() if truth_manifest_path else None
    truth_validation = (
        Path(truth_validation_report_path).resolve()
        if truth_validation_report_path
        else None
    )
    group_metadata = Path(group_metadata_path).resolve() if group_metadata_path else None
    split_policy_file = Path(split_policy_path).resolve() if split_policy_path else None
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()

    if frozen_inputs is None:
        raise ValueError("internal frozen calibration input snapshot is required")
    if truth_authorization is None:
        raise ValueError("internal truth authorization lineage is required")
    if (
        not isinstance(indicator_feature_source_metadata, list)
        or len(indicator_feature_source_metadata) != len(feature_paths)
    ):
        raise ValueError(
            "indicator-feature source metadata must exactly match feature inputs"
        )

    registry = frozen_inputs["feasibility_registry"].parsed
    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("feasibility registry must contain a non-empty indicators array")
    indicator_ids = [item["indicator_id"] for item in indicators]
    if len(set(indicator_ids)) != len(indicator_ids):
        raise ValueError("feasibility registry indicator_id values must be unique")
    indicator_by_id = {item["indicator_id"]: item for item in indicators}
    semantic_requirements, semantic_contract_missing = _load_semantic_requirements(
        frozen_inputs["truth_validation_report"].parsed if truth_validation else None,
        indicator_ids,
    )

    manifest_payload: dict[str, Any] = {}
    if truth_manifest:
        manifest_payload = frozen_inputs["truth_manifest"].parsed
    known_video_ids = {
        str(item["video_id"])
        for item in manifest_payload.get("videos", [])
        if isinstance(item, dict) and item.get("video_id")
    }

    events = frozen_inputs["manual_events"].parsed
    event_keys: set[tuple[str, str]] = set()
    for event in events:
        _validate_manual_event(event)
        key = (event["video_id"], event["event_id"])
        if key in event_keys:
            raise ValueError(f"duplicate manual event: {key}")
        event_keys.add(key)
        if manifest_payload.get("videos") and event["video_id"] not in known_video_ids:
            raise ValueError(
                f"manual event video_id is absent from truth manifest: {event['video_id']}"
            )
        known_video_ids.add(event["video_id"])

    semantics = frozen_inputs["manual_semantics"].parsed
    semantic_by_item: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in semantics:
        _validate_semantic(record)
        event = next(
            (
                item
                for item in events
                if item["video_id"] == record["video_id"]
                and item["event_id"] == record["event_id"]
            ),
            None,
        )
        if event is None:
            raise ValueError("semantic truth must reference an accepted manual event")
        if record["indicator_id"] not in indicator_by_id:
            raise ValueError("semantic truth indicator_id is absent from registry")
        if _event_code_for_indicator(record["indicator_id"]) != event["event_code"]:
            raise ValueError("semantic truth indicator_id does not match manual event_code")
        key = (record["video_id"], record["event_id"], record["indicator_id"])
        semantic_key = record["semantic_key"]
        if semantic_key in semantic_by_item[key]:
            raise ValueError(f"duplicate semantic truth: {key}:{semantic_key}")
        semantic_by_item[key][semantic_key] = record

    labels = frozen_inputs["coach_labels"].parsed
    labels_by_item: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    annotation_ids: set[str] = set()
    coach_item_keys: set[tuple[str, str, str, str, str]] = set()
    for record in labels:
        validate_coach_label(record)
        if record["annotation_id"] in annotation_ids:
            raise ValueError(f"duplicate coach annotation_id: {record['annotation_id']}")
        event = next(
            (
                item
                for item in events
                if item["video_id"] == record["video_id"]
                and item["event_id"] == record["event_id"]
            ),
            None,
        )
        if event is None:
            raise ValueError("coach label must reference an accepted manual event")
        if record["indicator_id"] not in indicator_by_id:
            raise ValueError("coach label indicator_id is absent from registry")
        if _event_code_for_indicator(record["indicator_id"]) != event["event_code"]:
            raise ValueError("coach label indicator_id does not match manual event_code")
        coach_item_key = (
            record["video_id"],
            record["event_id"],
            record["indicator_id"],
            record["annotator_id"],
            record["label_type"],
        )
        if coach_item_key in coach_item_keys:
            raise ValueError(f"duplicate coach label item: {coach_item_key}")
        annotation_ids.add(record["annotation_id"])
        coach_item_keys.add(coach_item_key)
        labels_by_item[(record["video_id"], record["event_id"], record["indicator_id"])].append(record)

    feature_by_item: dict[tuple[str, str, str], dict[str, Any]] = {}
    feature_record_sha: dict[tuple[str, str, str], str] = {}
    feature_source_index_by_item: dict[tuple[str, str, str], int] = {}
    feature_source_indexes_by_video: dict[str, set[int]] = defaultdict(set)
    for feature_index, path in enumerate(feature_paths):
        for record in frozen_inputs[f"indicator_features:{feature_index}"].parsed:
            indicator_id = record.get("indicator_id")
            if indicator_id not in indicator_by_id:
                continue
            raw_video_id = record.get("video_id")
            if not isinstance(raw_video_id, str) or not isinstance(record.get("event_id"), str):
                raise ValueError(f"indicator feature record in {path} lacks video_id/event_id")
            video_id = _normalize_video_id(raw_video_id, known_video_ids)
            feature_source_indexes_by_video[video_id].add(feature_index)
            key = (video_id, record["event_id"], indicator_id)
            digest = canonical_sha256(record)
            if key in feature_by_item and feature_record_sha[key] != digest:
                raise ValueError(f"conflicting indicator feature records for {key}")
            if (
                key in feature_source_index_by_item
                and feature_source_index_by_item[key] != feature_index
                and prepared_artifact_scope == "calibration_input"
            ):
                raise ValueError(
                    f"real indicator feature record appears in multiple sources: {key}"
                )
            feature_by_item[key] = record
            feature_record_sha[key] = digest
            feature_source_index_by_item[key] = feature_index

    if prepared_artifact_scope == "calibration_input":
        verified_video_sources: dict[str, int] = {}
        for index, metadata_item in enumerate(indicator_feature_source_metadata):
            video_id = metadata_item.get("video_id")
            if not isinstance(video_id, str) or not video_id:
                raise ValueError("real indicator-feature source metadata requires video_id")
            if video_id in verified_video_sources:
                raise ValueError(
                    f"real indicator-feature source video_id is duplicated: {video_id}"
                )
            verified_video_sources[video_id] = index
        missing_video_sources = sorted(
            {event["video_id"] for event in events} - set(verified_video_sources)
        )
        if missing_video_sources:
            raise ValueError(
                "real calibration events lack indicator-feature source metadata: "
                f"{missing_video_sources}"
            )
        for video_id, indexes in feature_source_indexes_by_video.items():
            if video_id in verified_video_sources and indexes != {
                verified_video_sources[video_id]
            }:
                raise ValueError(
                    "indicator feature records do not match their per-video source metadata: "
                    f"{video_id}"
                )
    else:
        verified_video_sources = {}

    metadata = _load_group_metadata(
        frozen_inputs["group_metadata"].parsed if group_metadata else None
    )
    split_policy, split_policy_sha = _load_split_policy(
        frozen_inputs["split_policy"].parsed if split_policy_file else None,
        source_sha256=(
            frozen_inputs["split_policy"].sha256 if split_policy_file else None
        ),
    )
    source_files = {
        "feasibility_registry": {
            "path": str(registry_path),
            "sha256": frozen_inputs["feasibility_registry"].sha256,
        },
        "indicator_features": [
            {
                "path": str(path),
                "sha256": frozen_inputs[f"indicator_features:{index}"].sha256,
                "source_metadata": dict(
                    indicator_feature_source_metadata[index]
                ),
            }
            for index, path in enumerate(feature_paths)
        ],
        "manual_events": {
            "path": str(manual_events_path),
            "sha256": frozen_inputs["manual_events"].sha256,
        },
        "manual_semantics": {
            "path": str(manual_semantics_path),
            "sha256": frozen_inputs["manual_semantics"].sha256,
        },
        "coach_labels": {
            "path": str(coach_labels_path),
            "sha256": frozen_inputs["coach_labels"].sha256,
        },
        "truth_intake_manifest": (
            {
                "path": str(truth_intake_manifest),
                "sha256": frozen_inputs["truth_intake_manifest"].sha256,
            }
            if truth_intake_manifest
            else None
        ),
        "truth_manifest": (
            {
                "path": str(truth_manifest),
                "sha256": frozen_inputs["truth_manifest"].sha256,
            }
            if truth_manifest
            else None
        ),
        "truth_validation_report": (
            {
                "path": str(truth_validation),
                "sha256": frozen_inputs["truth_validation_report"].sha256,
            }
            if truth_validation
            else None
        ),
        "group_metadata": (
            {
                "path": str(group_metadata),
                "sha256": frozen_inputs["group_metadata"].sha256,
            }
            if group_metadata
            else None
        ),
        "split_policy": (
            {"path": str(split_policy_file), "sha256": split_policy_sha}
            if split_policy_file
            else None
        ),
    }

    samples: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: (item["video_id"], item["start_ms"], item["event_id"])):
        video_id = event["video_id"]
        event_id = event["event_id"]
        track_id = int(event["person_track_id"])
        group_item = _metadata_for_event(metadata, video_id, track_id)
        for indicator in indicators:
            indicator_id = indicator["indicator_id"]
            if _event_code_for_indicator(indicator_id) != event["event_code"]:
                continue
            item_key = (video_id, event_id, indicator_id)
            required_phase_keys = registry_required_phase_keys(indicator)
            missing_phase_keys = sorted(
                phase_key
                for phase_key in required_phase_keys
                if event.get("key_phases_ms", {}).get(phase_key) is None
            )
            item_semantics = semantic_by_item.get(item_key, {})
            feature_record = feature_by_item.get(item_key)
            derived_items: dict[str, dict[str, Any]] = {}
            if indicator_id == TARGET_DIRECTION_INDICATOR_ID:
                derived_items[TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME] = (
                    _manual_target_alignment_feature(feature_record, item_semantics)
                )
            resolved_target_direction = bool(
                derived_items.get(TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME, {}).get(
                    "valid"
                )
            )
            feature_vector = _feature_vector(
                feature_record,
                list(indicator["required_features"]),
                derived_items=derived_items,
            )
            source_index = feature_source_index_by_item.get(item_key)
            if source_index is None:
                if prepared_artifact_scope == "calibration_input":
                    source_index = verified_video_sources[video_id]
                else:
                    matching_indexes = feature_source_indexes_by_video.get(
                        video_id, set()
                    )
                    if len(matching_indexes) == 1:
                        source_index = next(iter(matching_indexes))
                    elif len(feature_paths) == 1:
                        source_index = 0
            if source_index is None:
                raise ValueError(
                    f"cannot identify indicator-feature source for {item_key}"
                )
            source_status = {
                "calibration_input": VERIFIED_FEATURE_SOURCE_STATUS,
                "synthetic_test_only_calibration_input": (
                    SYNTHETIC_FEATURE_SOURCE_STATUS
                ),
                "unverified_truth_diagnostic_input": DIAGNOSTIC_SOURCE_STATUS,
            }[prepared_artifact_scope]
            try:
                qualification_snapshot = build_qualification_snapshot(
                    indicator_id=indicator_id,
                    feature_record=feature_record,
                    source_feature_canonical_sha256=feature_record_sha.get(item_key),
                    source_status=source_status,
                    source_metadata=indicator_feature_source_metadata[source_index],
                    resolved_target_direction=resolved_target_direction,
                )
                vector_complete, feature_gate_reasons = derive_feature_qualification(
                    snapshot=qualification_snapshot,
                    indicator_id=indicator_id,
                    feature_vector=feature_vector,
                    require_verified_source=(
                        prepared_artifact_scope == "calibration_input"
                    ),
                )
            except IndicatorFeatureQualificationError as exc:
                raise ValueError(str(exc)) from exc
            required_semantics = semantic_requirements[indicator_id]
            missing_semantics = sorted(set(required_semantics) - set(item_semantics))
            unobservable_semantics = sorted(
                key
                for key in required_semantics
                if key in item_semantics and not item_semantics[key]["observable"]
            )
            raw_labels = labels_by_item.get(item_key, [])
            summary = _label_summary(raw_labels)
            event_hash = canonical_sha256(event)
            semantics_hash = canonical_sha256(
                [item_semantics[key] for key in sorted(item_semantics)]
            )
            labels_hash = canonical_sha256(
                sorted(raw_labels, key=lambda item: item["annotation_id"])
            )
            sample_identity = {
                "video_id": video_id,
                "event_id": event_id,
                "indicator_id": indicator_id,
                "person_track_id": track_id,
            }
            sample_id = "sample-" + canonical_sha256(sample_identity)[:20]
            eligible = (
                vector_complete
                and indicator_id not in semantic_contract_missing
                and not missing_phase_keys
                and not missing_semantics
                and not unobservable_semantics
                and summary["grade_status"] == "unanimous"
            )
            samples.append(
                {
                    "schema_version": SAMPLE_SCHEMA_VERSION,
                    "sample_id": sample_id,
                    "dataset_version": None,
                    "video_id": video_id,
                    "event_id": event_id,
                    "event_code": event["event_code"],
                    "indicator_id": indicator_id,
                    "person_track_id": track_id,
                    "split": None,
                    "feature_vector": feature_vector,
                    "feature_vector_complete": vector_complete,
                    "qualification_snapshot": qualification_snapshot,
                    "manual_event": {
                        "start_ms": event["start_ms"],
                        "end_ms": event["end_ms"],
                        "key_phases_ms": event.get("key_phases_ms", {}),
                        "required_phase_keys": required_phase_keys,
                        "missing_required_phase_keys": missing_phase_keys,
                        "required_phases_complete": not missing_phase_keys,
                        "boundary_uncertainty_ms": event.get("boundary_uncertainty_ms"),
                        "annotator_id": event.get("annotator_id"),
                        "reviewer_id": event.get("reviewer_id"),
                        "adjudication_status": event.get("adjudication_status"),
                    },
                    "semantics": {
                        "requirements_contract_status": (
                            "declared"
                            if indicator_id not in semantic_contract_missing
                            else "missing"
                        ),
                        "required_keys": list(required_semantics),
                        "records": [item_semantics[key] for key in sorted(item_semantics)],
                        "missing_keys": missing_semantics,
                        "unobservable_keys": unobservable_semantics,
                        "complete": not missing_semantics,
                        "fully_observable": not missing_semantics and not unobservable_semantics,
                    },
                    "labels": {
                        "grades": sorted(
                            [item for item in raw_labels if item["label_type"] == "grade"],
                            key=lambda item: item["annotation_id"],
                        ),
                        "rankings": sorted(
                            [item for item in raw_labels if item["label_type"] == "ranking"],
                            key=lambda item: item["annotation_id"],
                        ),
                    },
                    "label_summary": summary,
                    "groups": {
                        "player_id": group_item.get("player_id"),
                        "session_id": group_item.get("session_id"),
                        "view_group": event.get("view_group") or group_item.get("view_group"),
                        "leakage_group_id": None,
                    },
                    "readiness": {
                        "eligible_for_grade_modeling": eligible,
                        "eligible_for_grade_evaluation": eligible,
                        "ranking_labels_available": bool(
                            [item for item in raw_labels if item["label_type"] == "ranking"]
                        ),
                        "reason_codes": sorted(
                            [
                                *( [] if feature_record else ["indicator_feature_join_missing"] ),
                                *( [] if vector_complete else ["required_feature_vector_incomplete"] ),
                                *feature_gate_reasons,
                                *( [] if not missing_semantics else ["required_semantic_truth_missing"] ),
                                *( [] if not unobservable_semantics else ["required_semantic_truth_unobservable"] ),
                                *(
                                    []
                                    if not missing_phase_keys
                                    else ["required_manual_event_phase_missing"]
                                ),
                                *(
                                    []
                                    if indicator_id not in semantic_contract_missing
                                    else ["semantic_requirements_contract_missing"]
                                ),
                                *(
                                    []
                                    if summary["grade_status"] == "unanimous"
                                    else [f"grade_{summary['grade_status']}"]
                                ),
                            ]
                        ),
                    },
                    "lineage": {
                        "manual_event_sha256": event_hash,
                        "indicator_feature_sha256": feature_record_sha.get(item_key),
                        "semantic_truth_sha256": semantics_hash,
                        "coach_labels_sha256": labels_hash,
                        "registry_indicator_sha256": canonical_sha256(indicator),
                        "join_key": {
                            "video_id": video_id,
                            "event_id": event_id,
                            "indicator_id": indicator_id,
                        },
                        "join_policy": "exact_manual_event_id_only_v1",
                    },
                }
            )

    samples.sort(key=lambda item: item["sample_id"])
    components = _build_leakage_components(samples)
    component_splits, split_conflicts = _assign_splits(samples, components, split_policy)
    for component_id, component in components.items():
        component["split"] = component_splits[component_id]

    source_hash_payload = {
        key: (
            [
                {
                    "sha256": item["sha256"],
                    "source_metadata": item["source_metadata"],
                }
                for item in value
            ]
            if isinstance(value, list)
            else value.get("sha256") if isinstance(value, dict) else None
        )
        for key, value in source_files.items()
    }
    source_hash_payload["truth_authorization_binding_sha256"] = truth_authorization[
        "binding_sha256"
    ]
    dataset_source_sha = canonical_sha256(source_hash_payload)
    dataset_content_seed = {
        "compiler_version": COMPILER_VERSION,
        "registry_version": registry.get("registry_version"),
        "source_sha256": dataset_source_sha,
        "sample_ids": [item["sample_id"] for item in samples],
        "split_assignments": {
            key: component_splits[key] for key in sorted(component_splits)
        },
    }
    dataset_digest = canonical_sha256(dataset_content_seed)
    dataset_id = "rallymate-calibration-" + dataset_digest[:16]
    dataset_version = "calibration-dataset-" + dataset_digest[:16]
    for sample in samples:
        sample["dataset_version"] = dataset_version

    agreement_by_indicator: dict[str, dict[str, Any]] = {}
    for indicator_id in indicator_ids:
        selected_labels = []
        for label in labels:
            if label["indicator_id"] != indicator_id:
                continue
            normalized = dict(label)
            normalized["event_id"] = f"{label['video_id']}::{label['event_id']}"
            selected_labels.append(normalized)
        agreement_by_indicator[indicator_id] = compute_annotator_agreement(selected_labels)

    split_sections: dict[str, dict[str, Any]] = {}
    for split in SPLITS:
        selected = [sample for sample in samples if sample.get("split") == split]
        split_sections[split] = {
            "sample_ids": [sample["sample_id"] for sample in selected],
            "leakage_group_ids": sorted(
                {sample["groups"]["leakage_group_id"] for sample in selected}
            ),
            "counts": {"samples": len(selected)},
            "indicator_distribution": dict(
                sorted(Counter(sample["indicator_id"] for sample in selected).items())
            ),
            "grade_distribution": _distribution(selected, "grade"),
            "view_distribution": _distribution(selected, "view_group"),
            "player_distribution": _distribution(selected, "player_id"),
            "session_distribution": _distribution(selected, "session_id"),
        }
    unassigned = [sample for sample in samples if sample.get("split") is None]
    split_manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "canonicalization": CANONICALIZATION,
        "policy": {
            "status": "preregistered" if split_policy else "missing",
            "policy_id": split_policy.get("policy_id") if split_policy else None,
            "policy_version": split_policy.get("policy_version") if split_policy else None,
            "policy_sha256": split_policy_sha,
            "strategy": "explicit_group_constraints",
            "group_key": "leakage_component_id",
        },
        "splits": split_sections,
        "unassigned_sample_ids": [sample["sample_id"] for sample in unassigned],
        "sample_assignments": [
            {
                "sample_id": sample["sample_id"],
                "indicator_id": sample["indicator_id"],
                "split": sample.get("split"),
                "leakage_group_id": sample["groups"]["leakage_group_id"],
            }
            for sample in samples
        ],
        "leakage_components": [components[key] for key in sorted(components)],
        "leakage_audit": {
            "status": (
                "pass"
                if split_policy
                and not split_conflicts
                and not unassigned
                and all(component["identity_metadata_complete"] for component in components.values())
                else "insufficient_identity_or_split_metadata"
            ),
            "component_cross_split_count": len(split_conflicts),
            "conflicts": split_conflicts,
            "unassigned_component_count": sum(
                value is None for value in component_splits.values()
            ),
            "identity_metadata_complete": all(
                component["identity_metadata_complete"] for component in components.values()
            ),
            "unknown_identity_policy": "all_unknown_identity_videos_are_one_component",
            "data_leakage_detected": bool(split_conflicts),
        },
    }

    readiness_indicators = []
    for indicator in indicators:
        indicator_id = indicator["indicator_id"]
        selected = [sample for sample in samples if sample["indicator_id"] == indicator_id]
        blockers = []
        if prepared_artifact_scope == "unverified_truth_diagnostic_input":
            blockers.append("verified_authorized_truth_intake_required")
        if not selected:
            blockers.append("manual_event_truth_missing")
        if selected and not any(sample["feature_vector_complete"] for sample in selected):
            blockers.append("valid_indicator_features_missing")
        if selected and any(not sample["semantics"]["complete"] for sample in selected):
            blockers.append("required_semantic_truth_missing")
        if selected and any(not sample["semantics"]["fully_observable"] for sample in selected):
            blockers.append("required_semantic_truth_unobservable")
        if selected and any(
            not sample["manual_event"]["required_phases_complete"]
            for sample in selected
        ):
            blockers.append("required_manual_event_phase_missing")
        if indicator_id in semantic_contract_missing:
            blockers.append("semantic_requirements_contract_missing")
        has_grade_labels = any(
            sample["label_summary"]["grade_label_count"] for sample in selected
        )
        has_ranking_labels = any(
            sample["label_summary"]["ranking_label_count"] for sample in selected
        )
        if not has_grade_labels and not has_ranking_labels:
            blockers.append("coach_grade_or_ranking_labels_missing")
        if has_ranking_labels and not has_grade_labels:
            # The ordinary prepared dataset is the A-E threshold/ordinal-model input.
            # Rankings can supervise the separate relative-order backend, but do not
            # identify an absolute A-E origin or four grade boundaries.
            blockers.append("absolute_grade_anchor_missing_ranking_only")
        if has_grade_labels and not any(
            len(sample["label_summary"]["contributing_annotator_ids"]) >= 2
            for sample in selected
        ):
            blockers.append("multi_coach_grade_overlap_missing")
        if has_ranking_labels and not any(
            sample["label_summary"]["ranking_annotator_count"] >= 2
            for sample in selected
        ):
            blockers.append("multi_coach_ranking_overlap_missing")
        if any(sample["label_summary"]["grade_status"] == "conflict" for sample in selected):
            blockers.append("multi_coach_grade_conflict_requires_explicit_adjudication")
        if split_policy is None:
            blockers.append("preregistered_split_policy_missing")
        if unassigned:
            blockers.append("split_assignment_incomplete")
        for split in SPLITS:
            if not any(sample.get("split") == split for sample in selected):
                blockers.append(f"{split}_split_missing")
        if any(not sample["groups"].get("player_id") for sample in selected):
            blockers.append("player_identity_metadata_missing")
        if any(not sample["groups"].get("session_id") for sample in selected):
            blockers.append("session_metadata_missing")
        if any(not sample["groups"].get("view_group") for sample in selected):
            blockers.append("view_group_metadata_missing")
        selected_labels = [
            label for label in labels if label["indicator_id"] == indicator_id
        ]
        resolved = [
            sample
            for sample in selected
            if sample["label_summary"]["grade_status"] == "unanimous"
        ]
        readiness_indicators.append(
            {
                "indicator_id": indicator_id,
                "registry_feasibility_level": indicator.get("feasibility_level"),
                "status": "prepared_for_external_protocol_review" if not blockers else "insufficient",
                "counts": {
                    "samples": len(selected),
                    "feature_record_joined": sum(
                        sample["lineage"]["indicator_feature_sha256"] is not None
                        for sample in selected
                    ),
                    "complete_valid_feature_vectors": sum(
                        sample["feature_vector_complete"] for sample in selected
                    ),
                    "semantic_complete": sum(
                        sample["semantics"]["complete"] for sample in selected
                    ),
                    "semantic_fully_observable": sum(
                        sample["semantics"]["fully_observable"] for sample in selected
                    ),
                    "grade_labels": sum(
                        sample["label_summary"]["grade_label_count"] for sample in selected
                    ),
                    "ranking_labels": sum(
                        sample["label_summary"]["ranking_label_count"] for sample in selected
                    ),
                    "multi_coach_grade_overlap_samples": sum(
                        len(sample["label_summary"]["contributing_annotator_ids"]) >= 2
                        for sample in selected
                    ),
                    "unanimous_resolved_grade_samples": len(resolved),
                    "grade_conflict_samples": sum(
                        sample["label_summary"]["grade_status"] == "conflict"
                        for sample in selected
                    ),
                },
                "grade_distribution": _distribution(resolved, "grade"),
                "split_distribution": dict(
                    sorted(Counter(str(sample.get("split") or "unassigned") for sample in selected).items())
                ),
                "view_distribution": _distribution(selected, "view_group"),
                "player_distribution": _distribution(selected, "player_id"),
                "session_distribution": _distribution(selected, "session_id"),
                "agreement": agreement_by_indicator[indicator_id],
                "raw_label_count": len(selected_labels),
                "blockers": sorted(set(blockers)),
                "acceptance_thresholds_applied": False,
                "F3_claimed": False,
            }
        )

    if not events:
        overall_status = "annotation_required"
    elif all(item["status"] == "prepared_for_external_protocol_review" for item in readiness_indicators):
        overall_status = "prepared_for_external_protocol_review"
    else:
        overall_status = "insufficient"
    readiness_report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "status": overall_status,
        "registry_version": registry.get("registry_version"),
        "indicator_count": len(indicator_ids),
        "indicators": readiness_indicators,
        "raw_counts": {
            "manual_events": len(events),
            "semantic_records": len(semantics),
            "coach_labels": len(labels),
            "samples": len(samples),
        },
        "safety": {
            "generated_thresholds": False,
            "trained_scoring_model": False,
            "automatic_F3_or_F4_promotion": False,
            "candidate_events_promoted_to_truth": False,
            "majority_or_median_grade_resolution": False,
            "acceptance_thresholds_applied": False,
            "prepared_artifact_scope": prepared_artifact_scope,
            "verified_authorized_truth_intake": (
                prepared_artifact_scope == "calibration_input"
            ),
        },
    }

    samples_path = output_dir / "samples.jsonl"
    split_path = output_dir / "split-manifest.json"
    readiness_path = output_dir / "readiness-report.json"
    _write_jsonl(samples_path, samples)
    _write_json(split_path, split_manifest)
    _write_json(readiness_path, readiness_report)
    source_manifest_id = manifest_payload.get("pack_version") or (
        "truth-manifest-" + frozen_inputs["truth_manifest"].sha256[:16]
        if truth_manifest
        else "unversioned-manual-truth"
    )
    prepared_paths: dict[str, str] = {}
    for indicator, readiness in zip(indicators, readiness_indicators):
        prepared = _prepared_indicator(
            indicator=indicator,
            samples=samples,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_source_sha256=dataset_source_sha,
            source_manifest_id=source_manifest_id,
            prepared_at=generated_at,
            split_policy=split_policy,
            agreement=agreement_by_indicator[indicator["indicator_id"]],
            indicator_blockers=readiness["blockers"],
            artifact_scope=prepared_artifact_scope,
            source_kind=prepared_source_kind,
            truth_authorization=truth_authorization,
        )
        path = output_dir / "prepared" / "by-indicator" / f"{indicator['indicator_id']}.json"
        _write_json(path, prepared)
        prepared_paths[indicator["indicator_id"]] = str(path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "artifact_scope": prepared_artifact_scope,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "generated_at": generated_at,
        "status": overall_status,
        "registry": {
            "version": registry.get("registry_version"),
            "indicator_ids": indicator_ids,
            "indicator_count": len(indicator_ids),
        },
        "canonicalization": {
            "id": CANONICALIZATION,
            "specification": (
                "UTF-8 JSON; ensure_ascii=false; sort_keys=true; separators=(',',':'); "
                "allow_nan=false"
            ),
            "independent_test_seal_payload": (
                "complete samples where indicator_id matches and split=independent_test, "
                "sorted by sample_id, serialized as one JSON array"
            ),
        },
        "source_files": source_files,
        "source_files_sha256": dataset_source_sha,
        "truth_authorization": dict(truth_authorization),
        "content_seed_sha256": dataset_digest,
        "outputs": {
            "samples": str(samples_path),
            "split_manifest": str(split_path),
            "readiness_report": str(readiness_path),
            "prepared_by_indicator": prepared_paths,
        },
        "counts": {
            "samples": len(samples),
            "indicators": len(indicator_ids),
            "prepared_indicator_files": len(prepared_paths),
        },
        "safety": readiness_report["safety"],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest


def _replace_path_prefix(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_path_prefix(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_path_prefix(item, old, new) for item in value]
    if isinstance(value, str) and value.startswith(old):
        return new + value[len(old) :]
    return value


def _freeze_compiler_inputs(
    *,
    feasibility_registry_path: str | Path,
    indicator_feature_paths: Iterable[str | Path],
    manual_events_path: str | Path,
    manual_semantics_path: str | Path,
    coach_labels_path: str | Path,
    truth_intake_manifest_path: str | Path | None,
    truth_manifest_path: str | Path | None,
    truth_validation_report_path: str | Path | None,
    group_metadata_path: str | Path | None,
    split_policy_path: str | Path | None,
) -> tuple[dict[str, _FrozenInput], list[Path]]:
    frozen: dict[str, _FrozenInput] = {}

    def freeze_json(label: str, path: str | Path) -> None:
        frozen[label] = _freeze_input(
            Path(path),
            label=label,
            parser=lambda raw, name=label: _parse_json_bytes(raw, label=name),
        )

    def freeze_jsonl(label: str, path: str | Path) -> None:
        frozen[label] = _freeze_input(
            Path(path),
            label=label,
            parser=lambda raw, name=label: _parse_jsonl_bytes(raw, label=name),
        )

    freeze_json("feasibility_registry", feasibility_registry_path)
    feature_paths = [Path(path).resolve() for path in indicator_feature_paths]
    if not feature_paths:
        raise ValueError("at least one indicator-features JSONL path is required")
    for index, path in enumerate(feature_paths):
        freeze_jsonl(f"indicator_features:{index}", path)
    freeze_jsonl("manual_events", manual_events_path)
    freeze_jsonl("manual_semantics", manual_semantics_path)
    freeze_jsonl("coach_labels", coach_labels_path)
    for label, path in (
        ("truth_intake_manifest", truth_intake_manifest_path),
        ("truth_manifest", truth_manifest_path),
        ("truth_validation_report", truth_validation_report_path),
        ("group_metadata", group_metadata_path),
        ("split_policy", split_policy_path),
    ):
        if path is not None:
            freeze_json(label, path)
    return frozen, feature_paths


def _authorization_input_hashes(
    frozen: Mapping[str, _FrozenInput],
) -> dict[str, str]:
    return {
        "intake_manifest": frozen["truth_intake_manifest"].sha256.upper()
        if "truth_intake_manifest" in frozen
        else "0" * 64,
        "truth_manifest": frozen["truth_manifest"].sha256.upper()
        if "truth_manifest" in frozen
        else "0" * 64,
        "truth_validation_report": frozen["truth_validation_report"].sha256.upper()
        if "truth_validation_report" in frozen
        else "0" * 64,
        "manual_events": frozen["manual_events"].sha256.upper(),
        "manual_semantics": frozen["manual_semantics"].sha256.upper(),
        "coach_labels": frozen["coach_labels"].sha256.upper(),
    }


def compile_calibration_dataset(
    *,
    feasibility_registry_path: str | Path,
    indicator_feature_paths: Iterable[str | Path],
    manual_events_path: str | Path,
    manual_semantics_path: str | Path,
    coach_labels_path: str | Path,
    output_dir: str | Path,
    truth_intake_manifest_path: str | Path | None = None,
    truth_manifest_path: str | Path | None = None,
    truth_validation_report_path: str | Path | None = None,
    group_metadata_path: str | Path | None = None,
    split_policy_path: str | Path | None = None,
    generated_at: str | None = None,
    verified_truth_authorization: VerifiedScoringTruthCalibrationAuthorization
    | None = None,
    verified_indicator_feature_sources: Iterable[
        VerifiedIndicatorFeatureSourceMetadata
    ]
    | None = None,
    synthetic_test_only: bool = False,
) -> dict[str, Any]:
    """Atomically compile one immutable calibration dataset directory.

    Existing targets are refused rather than partially overwritten. All artifacts are
    first written to a sibling staging directory and renamed only after validation and
    manifest creation complete.
    """

    if verified_truth_authorization is not None and synthetic_test_only:
        raise ValueError(
            "verified production truth authorization and synthetic_test_only are mutually exclusive"
        )
    verified_feature_sources = (
        list(verified_indicator_feature_sources)
        if verified_indicator_feature_sources is not None
        else None
    )
    frozen_inputs, feature_paths = _freeze_compiler_inputs(
        feasibility_registry_path=feasibility_registry_path,
        indicator_feature_paths=indicator_feature_paths,
        manual_events_path=manual_events_path,
        manual_semantics_path=manual_semantics_path,
        coach_labels_path=coach_labels_path,
        truth_intake_manifest_path=truth_intake_manifest_path,
        truth_manifest_path=truth_manifest_path,
        truth_validation_report_path=truth_validation_report_path,
        group_metadata_path=group_metadata_path,
        split_policy_path=split_policy_path,
    )
    input_hashes = _authorization_input_hashes(frozen_inputs)
    if verified_truth_authorization is not None:
        if any(
            value is None
            for value in (
                truth_intake_manifest_path,
                truth_manifest_path,
                truth_validation_report_path,
            )
        ):
            raise ValueError(
                "real calibration input requires intake, truth manifest, and validation bindings"
            )
        try:
            truth_authorization = (
                require_verified_scoring_truth_calibration_authorization(
                    verified_truth_authorization
                )
            )
        except ScoringTruthCalibrationAuthorizationError as exc:
            raise ValueError(str(exc)) from exc
        authorized_hashes = {
            name: str(value).upper()
            for name, value in truth_authorization["input_files"].items()
        }
        if authorized_hashes != input_hashes:
            raise ValueError(
                "calibration truth bytes do not match the same-process verified authorized intake"
            )
        intake_payload = frozen_inputs["truth_intake_manifest"].parsed
        if not isinstance(intake_payload, dict):
            raise ValueError("authorized truth intake manifest must be an object")
        expected_intake = truth_authorization["intake"]
        for field in ("intake_id", "intake_version", "content_root_sha256"):
            actual = intake_payload.get(field)
            expected = expected_intake[field]
            if field.endswith("sha256") and isinstance(actual, str):
                actual = actual.upper()
                expected = str(expected).upper()
            if actual != expected:
                raise ValueError(
                    f"authorized truth intake manifest {field} differs from live binding"
                )
        prepared_artifact_scope = "calibration_input"
        prepared_source_kind = "human_coach_ground_truth"
        if verified_feature_sources is None or len(verified_feature_sources) != len(
            feature_paths
        ):
            raise ValueError(
                "real calibration input requires one same-process verified "
                "indicator-feature source metadata object per feature file"
            )
        indicator_feature_source_metadata = []
        for index, (path, source) in enumerate(
            zip(feature_paths, verified_feature_sources)
        ):
            try:
                indicator_feature_source_metadata.append(
                    require_verified_indicator_feature_source_metadata(
                        source,
                        expected_indicator_features_path=path,
                        expected_indicator_features_raw=frozen_inputs[
                            f"indicator_features:{index}"
                        ].raw,
                    )
                )
            except IndicatorFeatureQualificationError as exc:
                raise ValueError(str(exc)) from exc
    else:
        if verified_feature_sources is not None:
            raise ValueError(
                "non-production calibration inputs cannot accept verified "
                "indicator-feature source metadata"
            )
        status = SYNTHETIC_STATUS if synthetic_test_only else DIAGNOSTIC_STATUS
        truth_authorization = build_nonproduction_truth_authorization_marker(
            status=status,
            input_files=input_hashes,
        )
        prepared_artifact_scope = (
            "synthetic_test_only_calibration_input"
            if synthetic_test_only
            else "unverified_truth_diagnostic_input"
        )
        prepared_source_kind = (
            "synthetic_test_fixture"
            if synthetic_test_only
            else "unverified_private_truth"
        )
        indicator_feature_source_metadata = []
        for index, _path in enumerate(feature_paths):
            records = frozen_inputs[f"indicator_features:{index}"].parsed
            video_ids = sorted(
                {
                    str(record["video_id"])
                    for record in records
                    if isinstance(record.get("video_id"), str)
                    and record.get("video_id")
                }
            )
            indicator_feature_source_metadata.append(
                nonproduction_source_metadata(
                    indicator_features_raw_sha256=frozen_inputs[
                        f"indicator_features:{index}"
                    ].sha256,
                    record_count=len(records),
                    video_id=video_ids[0] if len(video_ids) == 1 else None,
                )
            )

    final_output = Path(output_dir).resolve()
    if final_output.exists():
        raise FileExistsError(
            f"calibration dataset output already exists; choose a new immutable path: {final_output}"
        )
    final_output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{final_output.name}.staging-", dir=str(final_output.parent)
        )
    ).resolve()
    try:
        manifest = _compile_calibration_dataset_in_place(
            feasibility_registry_path=feasibility_registry_path,
            indicator_feature_paths=feature_paths,
            manual_events_path=manual_events_path,
            manual_semantics_path=manual_semantics_path,
            coach_labels_path=coach_labels_path,
            output_dir=staging,
            truth_intake_manifest_path=truth_intake_manifest_path,
            truth_manifest_path=truth_manifest_path,
            truth_validation_report_path=truth_validation_report_path,
            group_metadata_path=group_metadata_path,
            split_policy_path=split_policy_path,
            generated_at=generated_at,
            frozen_inputs=frozen_inputs,
            prepared_artifact_scope=prepared_artifact_scope,
            prepared_source_kind=prepared_source_kind,
            truth_authorization=truth_authorization,
            indicator_feature_source_metadata=indicator_feature_source_metadata,
        )
        manifest = _replace_path_prefix(manifest, str(staging), str(final_output))
        _write_json(staging / "manifest.json", manifest)
        _assert_frozen_inputs_unchanged(frozen_inputs)
        for source in verified_feature_sources or []:
            try:
                assert_indicator_feature_source_metadata_unchanged(source)
            except IndicatorFeatureQualificationError as exc:
                raise ValueError(str(exc)) from exc
        staging.rename(final_output)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "CANONICALIZATION",
    "COMPILER_VERSION",
    "SAMPLE_SCHEMA_VERSION",
    "canonical_json_bytes",
    "canonical_sha256",
    "compile_calibration_dataset",
    "file_sha256",
    "recompute_independent_test_seal",
]
