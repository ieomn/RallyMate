from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_scoring.calibration import compute_annotator_agreement, validate_coach_label


RANKING_DATASET_SCHEMA_VERSION = "1.0.0"
RANKING_FIT_PROTOCOL_SCHEMA_VERSION = "1.0.0"
RANKING_CANDIDATE_SCHEMA_VERSION = "1.0.0"
RANKING_TEST_PROTOCOL_SCHEMA_VERSION = "1.0.0"
RANKING_TEST_REPORT_SCHEMA_VERSION = "1.0.0"
CANONICALIZATION = "rallymate-canonical-json-v1"
BACKEND = "pairwise_logistic_ranker"

_REAL_DATASET_SCOPE = "ranking_calibration_input"
_TEST_DATASET_SCOPE = "synthetic_test_only_ranking_calibration_input"
_REAL_PROTOCOL_SCOPE = "ranking_calibration_fit_protocol"
_TEST_PROTOCOL_SCOPE = "synthetic_test_only_ranking_fit_protocol"
_REAL_CANDIDATE_SCOPE = "ranking_calibration_candidate"
_TEST_CANDIDATE_SCOPE = "synthetic_test_only_ranking_calibration_candidate"
_REAL_TEST_PROTOCOL_SCOPE = "ranking_calibration_independent_test_protocol"
_TEST_TEST_PROTOCOL_SCOPE = "synthetic_test_only_ranking_independent_test_protocol"
_SHA256_LENGTH = 64


class RankingCalibrationError(ValueError):
    """Raised when a ranking-only calibration artifact fails closed."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_string(payload: dict[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RankingCalibrationError(f"{context}.{field} must be a non-empty string")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdefABCDEF" for char in value)
    ):
        raise RankingCalibrationError(f"{field} must be a 64-character SHA-256")
    return value.lower()


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RankingCalibrationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise RankingCalibrationError(f"{field} must be a finite number")
    return result


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RankingCalibrationError(f"{field} must be a non-negative integer")
    return value


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RankingCalibrationError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RankingCalibrationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RankingCalibrationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise RankingCalibrationError(f"{field} must be a string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise RankingCalibrationError(f"{field} values must be non-empty strings")
    if len(value) != len(set(value)):
        raise RankingCalibrationError(f"{field} must not contain duplicates")
    return list(value)


def _exact_feature_maps(
    payload: dict[str, Any], feature_order: list[str], context: str
) -> tuple[dict[str, str], dict[str, str]]:
    result: list[dict[str, str]] = []
    for field in ("unit_by_feature", "feature_version_by_feature"):
        mapping = payload.get(field)
        if not isinstance(mapping, dict) or set(mapping) != set(feature_order):
            raise RankingCalibrationError(
                f"{context}.{field} must exactly cover feature_order"
            )
        if any(not isinstance(value, str) or not value for value in mapping.values()):
            raise RankingCalibrationError(
                f"{context}.{field} values must be non-empty strings"
            )
        result.append(dict(mapping))
    return result[0], result[1]


def _ranking_labels(sample: dict[str, Any], indicator_id: str) -> list[dict[str, Any]]:
    labels = sample.get("labels")
    if not isinstance(labels, dict):
        raise RankingCalibrationError("source sample.labels must be an object")
    grades = labels.get("grades")
    rankings = labels.get("rankings")
    if not isinstance(grades, list) or not isinstance(rankings, list):
        raise RankingCalibrationError("source sample labels must contain arrays")
    if grades:
        raise RankingCalibrationError(
            "ranking-only calibration rejects grade labels; use the A-E calibration path"
        )
    result = []
    seen: set[str] = set()
    for label in rankings:
        if not isinstance(label, dict):
            raise RankingCalibrationError("ranking label must be an object")
        try:
            validate_coach_label(label)
        except ValueError as exc:
            raise RankingCalibrationError(str(exc)) from exc
        if label["label_type"] != "ranking":
            raise RankingCalibrationError("ranking-only source contains a non-ranking label")
        for field in ("video_id", "event_id", "indicator_id"):
            if label[field] != sample[field]:
                raise RankingCalibrationError(
                    f"ranking label {field} does not exactly match its source sample"
                )
        if label["indicator_id"] != indicator_id:
            raise RankingCalibrationError("ranking label indicator_id mismatch")
        if label["annotation_id"] in seen:
            raise RankingCalibrationError("duplicate ranking annotation_id in one sample")
        seen.add(label["annotation_id"])
        result.append(dict(label))
    return sorted(result, key=lambda item: item["annotation_id"])


def _source_item(
    sample: dict[str, Any], indicator_id: str, feature_order: list[str] | None
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(sample, dict):
        raise RankingCalibrationError("source sample must be an object")
    for field in ("sample_id", "video_id", "event_id", "indicator_id"):
        _require_string(sample, field, "source_sample")
    if sample["indicator_id"] != indicator_id:
        raise RankingCalibrationError("source sample indicator_id mismatch")
    split = sample.get("split")
    if split not in {"train", "validation", "independent_test"}:
        raise RankingCalibrationError("every ranking sample must have a sealed split")
    groups = sample.get("groups")
    if not isinstance(groups, dict):
        raise RankingCalibrationError("source sample.groups is required")
    leakage_group_id = _require_string(groups, "leakage_group_id", "source_sample.groups")
    for identity_field in ("player_id", "session_id", "view_group"):
        _require_string(groups, identity_field, "source_sample.groups")
    if sample.get("feature_vector_complete") is not True:
        raise RankingCalibrationError("ranking sample requires a complete feature vector")
    manual_event = sample.get("manual_event")
    semantics = sample.get("semantics")
    if not isinstance(manual_event, dict) or manual_event.get("required_phases_complete") is not True:
        raise RankingCalibrationError("ranking sample requires complete manual event phases")
    if not isinstance(semantics, dict) or semantics.get("fully_observable") is not True:
        raise RankingCalibrationError("ranking sample requires fully observable semantics")
    vector = sample.get("feature_vector")
    if not isinstance(vector, list) or not vector:
        raise RankingCalibrationError("ranking sample.feature_vector must be non-empty")
    names = []
    features: dict[str, float] = {}
    units: dict[str, str] = {}
    versions: dict[str, str] = {}
    for index, feature in enumerate(vector):
        if not isinstance(feature, dict):
            raise RankingCalibrationError("feature vector item must be an object")
        name = _require_string(feature, "feature_name", f"feature_vector[{index}]")
        if name in features:
            raise RankingCalibrationError(f"duplicate feature {name}")
        if feature.get("valid") is not True:
            raise RankingCalibrationError(f"ranking feature {name} is not valid")
        names.append(name)
        features[name] = _finite(feature.get("value"), f"feature_vector.{name}.value")
        units[name] = _require_string(feature, "unit", f"feature_vector.{name}")
        versions[name] = _require_string(
            feature, "feature_version", f"feature_vector.{name}"
        )
    if feature_order is not None and names != feature_order:
        raise RankingCalibrationError("feature order differs across ranking samples")
    labels = _ranking_labels(sample, indicator_id)
    return (
        {
            "sample_id": sample["sample_id"],
            "video_id": sample["video_id"],
            "event_id": sample["event_id"],
            "split": split,
            "leakage_group_id": leakage_group_id,
            "player_id": groups["player_id"],
            "session_id": groups["session_id"],
            "view_group": groups["view_group"],
            "features": features,
            "unit_by_feature": units,
            "feature_version_by_feature": versions,
            "ranking_labels": labels,
            "source_sample_sha256": canonical_sha256(sample),
        },
        names,
    )


def _pair_records(items: list[dict[str, Any]], feature_order: list[str]) -> list[dict[str, Any]]:
    sample_by_id = {item["sample_id"]: item for item in items}
    grouped: dict[tuple[str, str, str], dict[str, tuple[int, str]]] = defaultdict(dict)
    group_splits: dict[str, set[str]] = defaultdict(set)
    annotation_ids: set[str] = set()
    for item in items:
        for label in item["ranking_labels"]:
            annotation_id = label["annotation_id"]
            if annotation_id in annotation_ids:
                raise RankingCalibrationError(f"duplicate ranking annotation_id: {annotation_id}")
            annotation_ids.add(annotation_id)
            group_id = label["rank_group_id"]
            group_splits[group_id].add(item["split"])
            key = (item["split"], group_id, label["annotator_id"])
            if item["sample_id"] in grouped[key]:
                raise RankingCalibrationError(
                    "one annotator supplied duplicate ranks for the same item/group"
                )
            grouped[key][item["sample_id"]] = (label["rank"], annotation_id)
    crossing = sorted(group for group, splits in group_splits.items() if len(splits) > 1)
    if crossing:
        raise RankingCalibrationError(
            f"rank_group_id crosses train/validation/test splits: {crossing}"
        )
    pairs = []
    for (split, rank_group_id, annotator_id), ranks in sorted(grouped.items()):
        if len(ranks) < 2:
            continue
        for left_id, right_id in itertools.combinations(sorted(ranks), 2):
            left_rank, left_annotation = ranks[left_id]
            right_rank, right_annotation = ranks[right_id]
            if left_rank < right_rank:
                outcome = "left_preferred"
                target = 1.0
            elif right_rank < left_rank:
                outcome = "right_preferred"
                target = 0.0
            else:
                outcome = "tie"
                target = 0.5
            left = sample_by_id[left_id]
            right = sample_by_id[right_id]
            seed = {
                "split": split,
                "rank_group_id": rank_group_id,
                "annotator_id": annotator_id,
                "left_sample_id": left_id,
                "right_sample_id": right_id,
            }
            pairs.append(
                {
                    "pair_id": "rank-pair-" + canonical_sha256(seed)[:20],
                    **seed,
                    "left_rank": left_rank,
                    "right_rank": right_rank,
                    "outcome": outcome,
                    "target_probability_left_preferred": target,
                    "feature_difference_left_minus_right": {
                        name: left["features"][name] - right["features"][name]
                        for name in feature_order
                    },
                    "source_annotation_ids": [left_annotation, right_annotation],
                }
            )
    return sorted(pairs, key=lambda item: item["pair_id"])


def _agreement(items: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    labels = []
    for item in items:
        for label in item["ranking_labels"]:
            normalized = dict(label)
            normalized["event_id"] = f"{item['video_id']}::{item['event_id']}"
            labels.append(normalized)
    base = compute_annotator_agreement(labels)
    shared: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        key = (
            pair["split"],
            pair["rank_group_id"],
            pair["left_sample_id"],
            pair["right_sample_id"],
        )
        shared[key].append(pair)
    shared_pairs = [values for values in shared.values() if len(values) >= 2]
    unanimous = sum(
        len({pair["outcome"] for pair in values}) == 1 for values in shared_pairs
    )
    return {
        "status": base["status"],
        "metric": "kendall_tau",
        "mean_kendall_tau": base["ranking_agreement"]["mean_kendall_tau"],
        "annotator_count": base["annotator_count"],
        "shared_pair_count": len(shared_pairs),
        "unanimous_shared_pair_count": unanimous,
        "pairwise": base["ranking_agreement"]["pairwise"],
    }


def _seal(samples: list[dict[str, Any]], indicator_id: str) -> dict[str, Any]:
    selected = sorted(samples, key=lambda item: item["sample_id"])
    digest = canonical_sha256(selected)
    return {
        "seal_id": "ranking-seal-" + digest[:16],
        "indicator_id": indicator_id,
        "record_count": len(selected),
        "groups": sorted({item["groups"]["leakage_group_id"] for item in selected}),
        "sample_ids": [item["sample_id"] for item in selected],
        "content_sha256": digest,
        "canonicalization": CANONICALIZATION,
        "labels_withheld_from_fitter": True,
    }


def prepare_ranking_dataset(
    samples: list[dict[str, Any]],
    *,
    indicator_id: str,
    source_dataset_id: str,
    source_dataset_version: str,
    source_kind: str,
    prepared_at: str,
) -> dict[str, Any]:
    """Prepare ranking-only train/validation pairs and seal independent-test labels.

    The data-custodian step sees complete source samples.  Its output contains no
    independent-test item or label; only their canonical seal is exposed to fitting.
    """

    _parse_time(prepared_at, "prepared_at")
    if source_kind not in {"human_coach_ranking_ground_truth", "synthetic_test_fixture"}:
        raise RankingCalibrationError("source_kind is invalid")
    if not isinstance(samples, list):
        raise RankingCalibrationError("samples must be an array")
    selected_source = sorted(
        (
            sample
            for sample in samples
            if isinstance(sample, dict) and sample.get("indicator_id") == indicator_id
        ),
        key=lambda item: item.get("sample_id", ""),
    )
    if not selected_source:
        raise RankingCalibrationError(f"no source samples for {indicator_id}")
    source_ids: set[str] = set()
    items_all: list[dict[str, Any]] = []
    feature_order: list[str] | None = None
    units: dict[str, str] | None = None
    versions: dict[str, str] | None = None
    leakage_splits: dict[str, set[str]] = defaultdict(set)
    identity_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    for sample in selected_source:
        item, names = _source_item(sample, indicator_id, feature_order)
        if item["sample_id"] in source_ids:
            raise RankingCalibrationError(f"duplicate source sample_id: {item['sample_id']}")
        source_ids.add(item["sample_id"])
        feature_order = names if feature_order is None else feature_order
        if units is None:
            units = item["unit_by_feature"]
            versions = item["feature_version_by_feature"]
        elif item["unit_by_feature"] != units or item["feature_version_by_feature"] != versions:
            raise RankingCalibrationError("feature units/versions differ across ranking samples")
        leakage_splits[item["leakage_group_id"]].add(item["split"])
        for field in ("video_id", "player_id", "session_id"):
            identity_splits[(field, item[field])].add(item["split"])
        items_all.append(item)
    crossing = sorted(group for group, splits in leakage_splits.items() if len(splits) > 1)
    if crossing:
        raise RankingCalibrationError(
            f"leakage group crosses train/validation/test splits: {crossing}"
        )
    identity_crossing = sorted(
        f"{field}:{value}"
        for (field, value), splits in identity_splits.items()
        if len(splits) > 1
    )
    if identity_crossing:
        raise RankingCalibrationError(
            "video/player/session identity crosses train/validation/test splits: "
            f"{identity_crossing}"
        )
    assert feature_order is not None and units is not None and versions is not None
    # Generate once over all items to audit that no ranking group crosses splits.
    all_pairs = _pair_records(items_all, feature_order)
    visible_items = [item for item in items_all if item["split"] != "independent_test"]
    visible_pairs = [pair for pair in all_pairs if pair["split"] != "independent_test"]
    independent_source = [
        sample for sample in selected_source if sample.get("split") == "independent_test"
    ]
    agreement = _agreement(visible_items, visible_pairs)
    counts_by_split = Counter(pair["split"] for pair in visible_pairs)
    annotators_by_split = {
        split: sorted(
            {pair["annotator_id"] for pair in visible_pairs if pair["split"] == split}
        )
        for split in ("train", "validation")
    }
    blockers = []
    for split in ("train", "validation"):
        if not counts_by_split[split]:
            blockers.append(f"{split}_ranking_pairs_missing")
    if not independent_source:
        blockers.append("independent_test_samples_missing")
    if agreement["annotator_count"] < 2:
        blockers.append("multi_coach_rankings_missing")
    if agreement["shared_pair_count"] < 1:
        blockers.append("multi_coach_shared_pair_missing")
    if agreement["mean_kendall_tau"] is None:
        blockers.append("ranking_agreement_not_evaluable")
    scope = (
        _REAL_DATASET_SCOPE
        if source_kind == "human_coach_ranking_ground_truth"
        else _TEST_DATASET_SCOPE
    )
    source_hash = canonical_sha256(selected_source)
    seed = {
        "source_hash": source_hash,
        "indicator_id": indicator_id,
        "feature_order": feature_order,
        "pair_ids": [pair["pair_id"] for pair in visible_pairs],
    }
    digest = canonical_sha256(seed)
    payload = {
        "schema_version": RANKING_DATASET_SCHEMA_VERSION,
        "artifact_scope": scope,
        "dataset_id": "ranking-calibration-" + digest[:16],
        "dataset_version": "ranking-calibration-v" + digest[:16],
        "indicator_id": indicator_id,
        "target_semantics": {
            "target": "relative_order_only",
            "rank_convention": "rank_1_is_best_equal_rank_is_tie",
            "absolute_grade_anchor_present": False,
            "A_E_grade_identified": False,
        },
        "source": {
            "kind": source_kind,
            "source_dataset_id": source_dataset_id,
            "source_dataset_version": source_dataset_version,
            "source_samples_sha256": source_hash,
            "prepared_at": prepared_at,
        },
        "feature_order": feature_order,
        "unit_by_feature": units,
        "feature_version_by_feature": versions,
        "split_policy": {
            "strategy": "inherited_group_holdout_no_cross_split_rank_groups",
            "leakage_group_field": "groups.leakage_group_id",
            "train_groups": sorted(
                group for group, splits in leakage_splits.items() if splits == {"train"}
            ),
            "validation_groups": sorted(
                group for group, splits in leakage_splits.items() if splits == {"validation"}
            ),
            "independent_test_groups": sorted(
                group
                for group, splits in leakage_splits.items()
                if splits == {"independent_test"}
            ),
        },
        "items": visible_items,
        "pairs": visible_pairs,
        "agreement": agreement,
        "independent_test_seal": _seal(independent_source, indicator_id),
        "readiness": {
            "status": "prepared_for_ranking_fit" if not blockers else "insufficient",
            "blockers": sorted(blockers),
            "F3_claimed": False,
        },
        "integrity": {
            "item_count": len(visible_items),
            "pair_count": len(visible_pairs),
            "pair_counts_by_split": {
                split: counts_by_split[split] for split in ("train", "validation")
            },
            "annotators_by_split": annotators_by_split,
            "source_samples_sha256": source_hash,
            "canonicalization": CANONICALIZATION,
        },
        "safety": {
            "generated_A_E_thresholds": False,
            "generated_A_E_cutpoints": False,
            "trained_absolute_grade_model": False,
            "production_scoring_allowed": False,
        },
    }
    validate_ranking_dataset(payload)
    return payload


def validate_ranking_dataset(
    payload: dict[str, Any], *, require_fit_ready: bool = False
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RankingCalibrationError("ranking dataset must be an object")
    if payload.get("schema_version") != RANKING_DATASET_SCHEMA_VERSION:
        raise RankingCalibrationError("unsupported ranking dataset schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {_REAL_DATASET_SCOPE, _TEST_DATASET_SCOPE}:
        raise RankingCalibrationError("ranking dataset artifact_scope is invalid")
    for field in ("dataset_id", "dataset_version", "indicator_id"):
        _require_string(payload, field, "ranking_dataset")
    semantics = payload.get("target_semantics")
    if semantics != {
        "target": "relative_order_only",
        "rank_convention": "rank_1_is_best_equal_rank_is_tie",
        "absolute_grade_anchor_present": False,
        "A_E_grade_identified": False,
    }:
        raise RankingCalibrationError("ranking dataset target semantics are unsafe")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise RankingCalibrationError("ranking dataset.source is required")
    expected_kind = (
        "human_coach_ranking_ground_truth"
        if scope == _REAL_DATASET_SCOPE
        else "synthetic_test_fixture"
    )
    if source.get("kind") != expected_kind:
        raise RankingCalibrationError(f"ranking dataset source.kind must be {expected_kind}")
    for field in ("source_dataset_id", "source_dataset_version"):
        _require_string(source, field, "ranking_dataset.source")
    _require_sha256(source.get("source_samples_sha256"), "source.source_samples_sha256")
    _parse_time(source.get("prepared_at"), "source.prepared_at")
    order = _string_list(payload.get("feature_order"), "feature_order")
    units, versions = _exact_feature_maps(payload, order, "ranking_dataset")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RankingCalibrationError("ranking dataset.items must be an array")
    item_ids: set[str] = set()
    leakage_splits: dict[str, set[str]] = defaultdict(set)
    identity_splits: dict[tuple[str, str], set[str]] = defaultdict(set)
    rank_group_splits: dict[str, set[str]] = defaultdict(set)
    normalized_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RankingCalibrationError(f"items[{index}] must be an object")
        for field in (
            "sample_id", "video_id", "event_id", "leakage_group_id", "player_id",
            "session_id", "view_group", "source_sample_sha256",
        ):
            _require_string(item, field, f"items[{index}]")
        if item["sample_id"] in item_ids:
            raise RankingCalibrationError(f"duplicate ranking item: {item['sample_id']}")
        item_ids.add(item["sample_id"])
        if item.get("split") not in {"train", "validation"}:
            raise RankingCalibrationError("prepared ranking items cannot expose test data")
        _require_sha256(item["source_sample_sha256"], "item.source_sample_sha256")
        features = item.get("features")
        if not isinstance(features, dict) or set(features) != set(order):
            raise RankingCalibrationError("ranking item features must exactly cover feature_order")
        for name in order:
            _finite(features[name], f"item.features.{name}")
        if item.get("unit_by_feature") != units or item.get("feature_version_by_feature") != versions:
            raise RankingCalibrationError("ranking item feature contract mismatch")
        labels = item.get("ranking_labels")
        if not isinstance(labels, list):
            raise RankingCalibrationError("ranking item labels must be an array")
        seen_labels: set[str] = set()
        for label in labels:
            try:
                validate_coach_label(label)
            except ValueError as exc:
                raise RankingCalibrationError(str(exc)) from exc
            if label.get("label_type") != "ranking":
                raise RankingCalibrationError("ranking item contains a grade label")
            if any(label.get(field) != item[field] for field in ("video_id", "event_id")):
                raise RankingCalibrationError("ranking label/item exact identity mismatch")
            if label.get("indicator_id") != payload["indicator_id"]:
                raise RankingCalibrationError("ranking label indicator mismatch")
            if label["annotation_id"] in seen_labels:
                raise RankingCalibrationError("duplicate ranking label in item")
            seen_labels.add(label["annotation_id"])
            rank_group_splits[label["rank_group_id"]].add(item["split"])
        leakage_splits[item["leakage_group_id"]].add(item["split"])
        for field in ("video_id", "player_id", "session_id"):
            identity_splits[(field, item[field])].add(item["split"])
        normalized_items.append(item)
    if any(len(splits) > 1 for splits in leakage_splits.values()):
        raise RankingCalibrationError("leakage group crosses fit splits")
    if any(len(splits) > 1 for splits in identity_splits.values()):
        raise RankingCalibrationError("video/player/session identity crosses fit splits")
    if any(len(splits) > 1 for splits in rank_group_splits.values()):
        raise RankingCalibrationError("rank group crosses fit splits")
    expected_pairs = _pair_records(normalized_items, order)
    if canonical_sha256(payload.get("pairs")) != canonical_sha256(expected_pairs):
        raise RankingCalibrationError("ranking pairs do not exactly derive from raw labels/items")
    expected_agreement = _agreement(normalized_items, expected_pairs)
    if payload.get("agreement") != expected_agreement:
        raise RankingCalibrationError("ranking agreement does not recompute")
    split_policy = payload.get("split_policy")
    if not isinstance(split_policy, dict) or split_policy.get("strategy") != (
        "inherited_group_holdout_no_cross_split_rank_groups"
    ):
        raise RankingCalibrationError("ranking split policy is invalid")
    actual_groups = {
        split: sorted(group for group, splits in leakage_splits.items() if splits == {split})
        for split in ("train", "validation")
    }
    if split_policy.get("train_groups") != actual_groups["train"]:
        raise RankingCalibrationError("ranking train group manifest mismatch")
    if split_policy.get("validation_groups") != actual_groups["validation"]:
        raise RankingCalibrationError("ranking validation group manifest mismatch")
    test_groups = _string_list(
        split_policy.get("independent_test_groups"),
        "split_policy.independent_test_groups",
        allow_empty=True,
    )
    if set(test_groups) & set(actual_groups["train"] + actual_groups["validation"]):
        raise RankingCalibrationError("independent-test group overlaps fit groups")
    seal = payload.get("independent_test_seal")
    if not isinstance(seal, dict) or seal.get("indicator_id") != payload["indicator_id"]:
        raise RankingCalibrationError("ranking independent-test seal is invalid")
    _require_string(seal, "seal_id", "independent_test_seal")
    seal_count = _non_negative_integer(seal.get("record_count"), "seal.record_count")
    seal_groups = _string_list(seal.get("groups"), "seal.groups", allow_empty=True)
    if seal_groups != test_groups:
        raise RankingCalibrationError("ranking independent-test seal groups mismatch")
    sample_ids = _string_list(seal.get("sample_ids"), "seal.sample_ids", allow_empty=True)
    if len(sample_ids) != seal_count:
        raise RankingCalibrationError("ranking seal sample count mismatch")
    _require_sha256(seal.get("content_sha256"), "seal.content_sha256")
    if seal.get("canonicalization") != CANONICALIZATION:
        raise RankingCalibrationError("ranking seal canonicalization is invalid")
    if seal.get("labels_withheld_from_fitter") is not True:
        raise RankingCalibrationError("independent ranking labels must be withheld")
    pair_counts = Counter(pair["split"] for pair in expected_pairs)
    annotators_by_split = {
        split: sorted({pair["annotator_id"] for pair in expected_pairs if pair["split"] == split})
        for split in ("train", "validation")
    }
    integrity = payload.get("integrity")
    expected_integrity = {
        "item_count": len(items),
        "pair_count": len(expected_pairs),
        "pair_counts_by_split": {
            split: pair_counts[split] for split in ("train", "validation")
        },
        "annotators_by_split": annotators_by_split,
        "source_samples_sha256": source["source_samples_sha256"],
        "canonicalization": CANONICALIZATION,
    }
    if integrity != expected_integrity:
        raise RankingCalibrationError("ranking dataset integrity does not recompute")
    readiness = payload.get("readiness")
    if not isinstance(readiness, dict) or readiness.get("status") not in {
        "insufficient", "prepared_for_ranking_fit"
    }:
        raise RankingCalibrationError("ranking readiness is invalid")
    blockers = _string_list(readiness.get("blockers"), "readiness.blockers", allow_empty=True)
    if readiness.get("F3_claimed") is not False:
        raise RankingCalibrationError("ranking dataset cannot claim F3")
    if payload.get("safety") != {
        "generated_A_E_thresholds": False,
        "generated_A_E_cutpoints": False,
        "trained_absolute_grade_model": False,
        "production_scoring_allowed": False,
    }:
        raise RankingCalibrationError("ranking dataset safety declaration is invalid")
    if require_fit_ready:
        if readiness["status"] != "prepared_for_ranking_fit" or blockers:
            raise RankingCalibrationError("ranking dataset is not fit-ready")
        if not pair_counts["train"] or not pair_counts["validation"] or seal_count < 1:
            raise RankingCalibrationError("all sealed ranking splits must be non-empty")
    return {
        "scope": scope,
        "indicator_id": payload["indicator_id"],
        "pair_counts": dict(pair_counts),
        "annotator_count": expected_agreement["annotator_count"],
        "shared_pair_count": expected_agreement["shared_pair_count"],
        "mean_kendall_tau": expected_agreement["mean_kendall_tau"],
    }


def validate_ranking_fit_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RankingCalibrationError("ranking fit protocol must be an object")
    if payload.get("schema_version") != RANKING_FIT_PROTOCOL_SCHEMA_VERSION:
        raise RankingCalibrationError("unsupported ranking fit protocol schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {_REAL_PROTOCOL_SCOPE, _TEST_PROTOCOL_SCOPE}:
        raise RankingCalibrationError("ranking fit protocol scope is invalid")
    for field in ("protocol_id", "protocol_version", "indicator_id"):
        _require_string(payload, field, "ranking_fit_protocol")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise RankingCalibrationError("ranking fit protocol source is required")
    expected_kind = "preregistered_ranking_protocol" if scope == _REAL_PROTOCOL_SCOPE else "synthetic_test_fixture"
    if source.get("kind") != expected_kind:
        raise RankingCalibrationError(f"ranking protocol source.kind must be {expected_kind}")
    _require_sha256(source.get("source_sha256"), "protocol.source.source_sha256")
    _parse_time(source.get("registered_at"), "protocol.source.registered_at")
    binding = payload.get("dataset_binding")
    if not isinstance(binding, dict):
        raise RankingCalibrationError("ranking protocol dataset_binding is required")
    for field in ("dataset_id", "dataset_version", "independent_test_seal_id"):
        _require_string(binding, field, "protocol.dataset_binding")
    for field in ("content_sha256", "independent_test_content_sha256"):
        _require_sha256(binding.get(field), f"protocol.dataset_binding.{field}")
    if payload.get("target_semantics") != {
        "target": "relative_order_only",
        "absolute_grade_anchor_present": False,
        "A_E_output_allowed": False,
    }:
        raise RankingCalibrationError("ranking protocol target semantics are unsafe")
    requirements = payload.get("requirements")
    if not isinstance(requirements, dict):
        raise RankingCalibrationError("ranking protocol requirements are required")
    for field in (
        "min_train_pairs", "min_validation_pairs", "min_independent_test_samples",
        "min_annotators", "min_shared_pairs",
    ):
        _non_negative_integer(requirements.get(field), f"requirements.{field}")
    if requirements["min_annotators"] < 2:
        raise RankingCalibrationError("ranking protocol requires at least two annotators")
    if requirements.get("agreement_metric") != "kendall_tau":
        raise RankingCalibrationError("ranking agreement metric must be kendall_tau")
    agreement = _finite(requirements.get("min_agreement_value"), "requirements.min_agreement_value")
    if not -1 <= agreement <= 1:
        raise RankingCalibrationError("minimum ranking agreement must be between -1 and 1")
    backend = payload.get("backend")
    if not isinstance(backend, dict) or backend.get("name") != BACKEND:
        raise RankingCalibrationError(f"ranking backend must be {BACKEND}")
    optimizer = backend.get("optimizer")
    if not isinstance(optimizer, dict) or optimizer.get("algorithm") != "batch_gradient_descent_v1":
        raise RankingCalibrationError("ranking optimizer is invalid")
    if _non_negative_integer(optimizer.get("max_iterations"), "optimizer.max_iterations") < 1:
        raise RankingCalibrationError("ranking max_iterations must be positive")
    for field in ("learning_rate", "tolerance"):
        if _finite(optimizer.get(field), f"optimizer.{field}") <= 0:
            raise RankingCalibrationError(f"optimizer.{field} must be positive")
    if _finite(optimizer.get("l2"), "optimizer.l2") < 0:
        raise RankingCalibrationError("optimizer.l2 must be non-negative")
    if backend.get("include_equal_rank_ties") not in {True, False}:
        raise RankingCalibrationError("include_equal_rank_ties must be boolean")
    return {"scope": scope, "indicator_id": payload["indicator_id"]}


def _protocol_gates(dataset: dict[str, Any], protocol: dict[str, Any]) -> None:
    audit = validate_ranking_dataset(dataset, require_fit_ready=True)
    validate_ranking_fit_protocol(protocol)
    if dataset["indicator_id"] != protocol["indicator_id"]:
        raise RankingCalibrationError("ranking dataset/protocol indicator mismatch")
    real = dataset["artifact_scope"] == _REAL_DATASET_SCOPE and protocol["artifact_scope"] == _REAL_PROTOCOL_SCOPE
    test = dataset["artifact_scope"] == _TEST_DATASET_SCOPE and protocol["artifact_scope"] == _TEST_PROTOCOL_SCOPE
    if not (real or test):
        raise RankingCalibrationError("ranking dataset/protocol real-vs-test scope mismatch")
    if _parse_time(
        protocol["source"]["registered_at"], "protocol.source.registered_at"
    ) < _parse_time(dataset["source"]["prepared_at"], "dataset.source.prepared_at"):
        raise RankingCalibrationError(
            "ranking fit protocol registration predates the exact prepared dataset it binds"
        )
    binding = protocol["dataset_binding"]
    expected = {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "content_sha256": canonical_sha256(dataset),
        "independent_test_seal_id": dataset["independent_test_seal"]["seal_id"],
        "independent_test_content_sha256": dataset["independent_test_seal"]["content_sha256"],
    }
    if binding != expected:
        raise RankingCalibrationError("ranking fit protocol is not bound to this exact dataset/seal")
    requirements = protocol["requirements"]
    for split, field in (("train", "min_train_pairs"), ("validation", "min_validation_pairs")):
        if audit["pair_counts"].get(split, 0) < requirements[field]:
            raise RankingCalibrationError(f"ranking protocol gate failed: {field}")
    if dataset["independent_test_seal"]["record_count"] < requirements["min_independent_test_samples"]:
        raise RankingCalibrationError("ranking protocol gate failed: min_independent_test_samples")
    if audit["annotator_count"] < requirements["min_annotators"]:
        raise RankingCalibrationError("ranking protocol gate failed: min_annotators")
    if audit["shared_pair_count"] < requirements["min_shared_pairs"]:
        raise RankingCalibrationError("ranking protocol gate failed: min_shared_pairs")
    if audit["mean_kendall_tau"] is None or audit["mean_kendall_tau"] < requirements["min_agreement_value"]:
        raise RankingCalibrationError("ranking protocol gate failed: min_agreement_value")


def _matrix(
    pairs: list[dict[str, Any]], feature_order: list[str], split: str, include_ties: bool
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    selected = [
        pair for pair in pairs
        if pair["split"] == split and (include_ties or pair["outcome"] != "tie")
    ]
    if not selected:
        raise RankingCalibrationError(f"no {split} ranking pairs after tie policy")
    x = np.asarray(
        [[pair["feature_difference_left_minus_right"][name] for name in feature_order] for pair in selected],
        dtype=np.float64,
    )
    y = np.asarray([pair["target_probability_left_preferred"] for pair in selected], dtype=np.float64)
    return x, y, selected


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_pairwise(
    x: np.ndarray, y: np.ndarray, optimizer: dict[str, Any]
) -> tuple[np.ndarray, dict[str, Any]]:
    scale = np.std(x, axis=0)
    scale = np.where(scale < 1e-12, 1.0, scale)
    standardized = x / scale
    coefficients = np.zeros(x.shape[1], dtype=np.float64)
    rate = float(optimizer["learning_rate"])
    l2 = float(optimizer["l2"])
    tolerance = float(optimizer["tolerance"])
    previous_loss = float("inf")
    converged = False
    iterations = 0
    for iterations in range(1, int(optimizer["max_iterations"]) + 1):
        probabilities = _sigmoid(standardized @ coefficients)
        clipped = np.clip(probabilities, 1e-12, 1.0 - 1e-12)
        loss = float(
            -np.mean(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped))
            + 0.5 * l2 * np.dot(coefficients, coefficients)
        )
        gradient = standardized.T @ (probabilities - y) / len(y) + l2 * coefficients
        coefficients -= rate * gradient
        if abs(previous_loss - loss) <= tolerance:
            converged = True
            break
        previous_loss = loss
    raw = coefficients / scale
    final_probabilities = np.clip(
        _sigmoid(standardized @ coefficients), 1e-12, 1.0 - 1e-12
    )
    final_loss = float(
        -np.mean(
            y * np.log(final_probabilities)
            + (1.0 - y) * np.log(1.0 - final_probabilities)
        )
        + 0.5 * l2 * np.dot(coefficients, coefficients)
    )
    return raw, {
        "iterations": iterations,
        "converged": converged,
        "training_objective": round(final_loss, 10),
    }


def _kendall_score_vs_rank(scores: dict[str, float], ranks: dict[str, int]) -> float | None:
    ids = sorted(set(scores) & set(ranks))
    if len(ids) < 2:
        return None
    concordant = discordant = 0
    for left, right in itertools.combinations(ids, 2):
        score_delta = scores[left] - scores[right]
        # Lower coach rank is better; invert rank difference for same direction as score.
        rank_delta = ranks[right] - ranks[left]
        product = score_delta * rank_delta
        if product > 0:
            concordant += 1
        elif product < 0:
            discordant += 1
    denominator = concordant + discordant
    return (concordant - discordant) / denominator if denominator else None


def _fit_metrics(
    x: np.ndarray,
    y: np.ndarray,
    pairs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    coefficients: np.ndarray,
    feature_order: list[str],
) -> dict[str, Any]:
    probabilities = _sigmoid(x @ coefficients)
    non_tie = np.abs(y - 0.5) > 1e-12
    accuracy = (
        float(np.mean((probabilities[non_tie] >= 0.5) == (y[non_tie] == 1.0)))
        if np.any(non_tie) else None
    )
    clipped = np.clip(probabilities, 1e-12, 1.0 - 1e-12)
    log_loss = float(-np.mean(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))
    item_by_id = {item["sample_id"]: item for item in items}
    scores = {
        sample_id: float(
            np.dot([item["features"][name] for name in feature_order], coefficients)
        )
        for sample_id, item in item_by_id.items()
    }
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    split = pairs[0]["split"]
    for item in items:
        if item["split"] != split:
            continue
        for label in item["ranking_labels"]:
            grouped[(label["rank_group_id"], label["annotator_id"])][item["sample_id"]] = label["rank"]
    taus = []
    for ranks in grouped.values():
        tau = _kendall_score_vs_rank(scores, ranks)
        if tau is not None:
            taus.append(tau)
    return {
        "pair_count": len(pairs),
        "non_tie_pair_count": int(np.sum(non_tie)),
        "tie_pair_count": int(np.sum(~non_tie)),
        "pairwise_accuracy": round(accuracy, 10) if accuracy is not None else None,
        "pairwise_log_loss": round(log_loss, 10),
        "mean_kendall_tau": round(float(np.mean(taus)), 10) if taus else None,
        "evaluated_rank_lists": len(taus),
    }


def fit_ranking_candidate(
    dataset: dict[str, Any], protocol: dict[str, Any], *, fitted_at: str
) -> dict[str, Any]:
    _parse_time(fitted_at, "fitted_at")
    _protocol_gates(dataset, protocol)
    if _parse_time(fitted_at, "fitted_at") < _parse_time(
        protocol["source"]["registered_at"], "protocol.source.registered_at"
    ):
        raise RankingCalibrationError("ranking fit predates protocol registration")
    order = dataset["feature_order"]
    include_ties = protocol["backend"]["include_equal_rank_ties"]
    train_x, train_y, train_pairs = _matrix(dataset["pairs"], order, "train", include_ties)
    validation_x, validation_y, validation_pairs = _matrix(
        dataset["pairs"], order, "validation", include_ties
    )
    coefficients, optimizer_result = _fit_pairwise(
        train_x, train_y, protocol["backend"]["optimizer"]
    )
    digest = canonical_sha256(
        {
            "dataset": canonical_sha256(dataset),
            "protocol": canonical_sha256(protocol),
            "backend": BACKEND,
        }
    )[:16]
    version = f"ranking-candidate-{dataset['indicator_id']}-{digest}"
    test_only = dataset["artifact_scope"] == _TEST_DATASET_SCOPE
    candidate = {
        "schema_version": RANKING_CANDIDATE_SCHEMA_VERSION,
        "artifact_scope": _TEST_CANDIDATE_SCOPE if test_only else _REAL_CANDIDATE_SCOPE,
        "candidate_id": version,
        "candidate_version": version,
        "indicator_id": dataset["indicator_id"],
        "backend": BACKEND,
        "target_semantics": {
            "target": "relative_order_only",
            "absolute_grade_anchor_present": False,
            "A_E_grade_identified": False,
            "prediction_status": "candidate_relative_order_not_scored",
        },
        "feature_order": list(order),
        "unit_by_feature": dict(dataset["unit_by_feature"]),
        "feature_version_by_feature": dict(dataset["feature_version_by_feature"]),
        "coefficients": [float(value) for value in coefficients],
        "explainability": [
            {
                "feature_name": name,
                "coefficient": float(value),
                "unit": dataset["unit_by_feature"][name],
                "feature_version": dataset["feature_version_by_feature"][name],
                "interpretation": (
                    "positive left-minus-right contribution raises the modeled "
                    "probability that the left item is ranked better"
                ),
            }
            for name, value in zip(order, coefficients)
        ],
        "prepared_dataset": {
            "dataset_id": dataset["dataset_id"],
            "dataset_version": dataset["dataset_version"],
            "artifact_scope": dataset["artifact_scope"],
            "content_sha256": canonical_sha256(dataset),
            "source_samples_sha256": dataset["source"]["source_samples_sha256"],
        },
        "fit_protocol": {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "artifact_scope": protocol["artifact_scope"],
            "content_sha256": canonical_sha256(protocol),
            "source_sha256": protocol["source"]["source_sha256"],
        },
        "fit": {
            "method": "pairwise_bradley_terry_logistic_v1",
            "objective": "coach_relative_order_log_loss",
            "fitted_at": fitted_at,
            "optimizer": optimizer_result,
            "train_metrics": _fit_metrics(
                train_x,
                train_y,
                train_pairs,
                dataset["items"],
                coefficients,
                order,
            ),
            "validation_metrics": _fit_metrics(
                validation_x,
                validation_y,
                validation_pairs,
                dataset["items"],
                coefficients,
                order,
            ),
            "independent_test_metrics": None,
        },
        "independent_test": {
            "status": "sealed_not_evaluated",
            "seal_id": dataset["independent_test_seal"]["seal_id"],
            "record_count": dataset["independent_test_seal"]["record_count"],
            "groups": dataset["independent_test_seal"]["groups"],
            "sample_ids": dataset["independent_test_seal"]["sample_ids"],
            "content_sha256": dataset["independent_test_seal"]["content_sha256"],
            "canonicalization": CANONICALIZATION,
            "accessed_during_fit": False,
        },
        "safety": {
            "grade": None,
            "threshold_version": None,
            "A_E_output_allowed": False,
            "production_scoring_allowed": False,
            "F3_promoted": False,
            "F4_promoted": False,
            "reason": "absolute_grade_anchor_and_separate_A_E_calibration_required",
        },
    }
    validate_ranking_candidate(candidate)
    return candidate


def validate_ranking_candidate(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RankingCalibrationError("ranking candidate must be an object")
    if payload.get("schema_version") != RANKING_CANDIDATE_SCHEMA_VERSION:
        raise RankingCalibrationError("unsupported ranking candidate schema_version")
    scope = payload.get("artifact_scope")
    if scope not in {_REAL_CANDIDATE_SCOPE, _TEST_CANDIDATE_SCOPE}:
        raise RankingCalibrationError("ranking candidate scope is invalid")
    for field in ("candidate_id", "candidate_version", "indicator_id"):
        _require_string(payload, field, "ranking_candidate")
    if payload.get("backend") != BACKEND:
        raise RankingCalibrationError("ranking candidate backend is invalid")
    semantics = payload.get("target_semantics")
    if semantics != {
        "target": "relative_order_only",
        "absolute_grade_anchor_present": False,
        "A_E_grade_identified": False,
        "prediction_status": "candidate_relative_order_not_scored",
    }:
        raise RankingCalibrationError("ranking candidate target semantics are unsafe")
    order = _string_list(payload.get("feature_order"), "candidate.feature_order")
    units, versions = _exact_feature_maps(payload, order, "ranking_candidate")
    coefficients = payload.get("coefficients")
    if not isinstance(coefficients, list) or len(coefficients) != len(order):
        raise RankingCalibrationError("ranking coefficients must match feature_order")
    values = [_finite(value, "candidate.coefficients") for value in coefficients]
    explanation = payload.get("explainability")
    if not isinstance(explanation, list) or len(explanation) != len(order):
        raise RankingCalibrationError("ranking candidate explainability must cover features")
    for index, (item, name, value) in enumerate(zip(explanation, order, values)):
        if not isinstance(item, dict) or item.get("feature_name") != name:
            raise RankingCalibrationError("ranking explainability feature order mismatch")
        if _finite(item.get("coefficient"), f"explainability[{index}].coefficient") != value:
            raise RankingCalibrationError("ranking explainability coefficient mismatch")
        if item.get("unit") != units[name] or item.get("feature_version") != versions[name]:
            raise RankingCalibrationError("ranking explainability feature contract mismatch")
        _require_string(item, "interpretation", f"explainability[{index}]")
    dataset = payload.get("prepared_dataset")
    protocol = payload.get("fit_protocol")
    if not isinstance(dataset, dict) or not isinstance(protocol, dict):
        raise RankingCalibrationError("ranking candidate provenance is required")
    for field in ("dataset_id", "dataset_version"):
        _require_string(dataset, field, "candidate.prepared_dataset")
    for field in ("content_sha256", "source_samples_sha256"):
        _require_sha256(dataset.get(field), f"candidate.prepared_dataset.{field}")
    expected_dataset_scope = _REAL_DATASET_SCOPE if scope == _REAL_CANDIDATE_SCOPE else _TEST_DATASET_SCOPE
    if dataset.get("artifact_scope") != expected_dataset_scope:
        raise RankingCalibrationError("ranking candidate/dataset scope mismatch")
    for field in ("protocol_id", "protocol_version"):
        _require_string(protocol, field, "candidate.fit_protocol")
    for field in ("content_sha256", "source_sha256"):
        _require_sha256(protocol.get(field), f"candidate.fit_protocol.{field}")
    expected_protocol_scope = _REAL_PROTOCOL_SCOPE if scope == _REAL_CANDIDATE_SCOPE else _TEST_PROTOCOL_SCOPE
    if protocol.get("artifact_scope") != expected_protocol_scope:
        raise RankingCalibrationError("ranking candidate/protocol scope mismatch")
    fit = payload.get("fit")
    if not isinstance(fit, dict) or fit.get("method") != "pairwise_bradley_terry_logistic_v1":
        raise RankingCalibrationError("ranking candidate fit is invalid")
    if fit.get("objective") != "coach_relative_order_log_loss":
        raise RankingCalibrationError("ranking candidate objective is invalid")
    _parse_time(fit.get("fitted_at"), "candidate.fit.fitted_at")
    for field in ("train_metrics", "validation_metrics"):
        if not isinstance(fit.get(field), dict):
            raise RankingCalibrationError(f"candidate.fit.{field} is required")
    if fit.get("independent_test_metrics") is not None:
        raise RankingCalibrationError("ranking candidate cannot access independent-test metrics")
    independent = payload.get("independent_test")
    if not isinstance(independent, dict) or independent.get("status") != "sealed_not_evaluated":
        raise RankingCalibrationError("ranking candidate independent-test state is invalid")
    _require_string(independent, "seal_id", "candidate.independent_test")
    count = _non_negative_integer(independent.get("record_count"), "independent.record_count")
    _string_list(independent.get("groups"), "independent.groups")
    sample_ids = _string_list(independent.get("sample_ids"), "independent.sample_ids")
    if count != len(sample_ids) or count < 1:
        raise RankingCalibrationError("ranking candidate requires a non-empty sealed test")
    _require_sha256(independent.get("content_sha256"), "independent.content_sha256")
    if independent.get("canonicalization") != CANONICALIZATION or independent.get("accessed_during_fit") is not False:
        raise RankingCalibrationError("ranking candidate test seal is unsafe")
    if payload.get("safety") != {
        "grade": None,
        "threshold_version": None,
        "A_E_output_allowed": False,
        "production_scoring_allowed": False,
        "F3_promoted": False,
        "F4_promoted": False,
        "reason": "absolute_grade_anchor_and_separate_A_E_calibration_required",
    }:
        raise RankingCalibrationError("ranking candidate safety declaration is invalid")
    # A relative-order candidate must never smuggle in absolute grade boundaries.
    forbidden = {"thresholds", "cutpoints", "grade_scale", "grade_numeric_mapping"}
    if forbidden & set(payload):
        raise RankingCalibrationError("ranking candidate contains forbidden A-E parameters")


def rank_candidate_items(
    candidate: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    validate_ranking_candidate(candidate)
    if not isinstance(items, list) or not items:
        raise RankingCalibrationError("ranking prediction items must be non-empty")
    order = candidate["feature_order"]
    coefficients = candidate["coefficients"]
    results = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RankingCalibrationError(f"prediction items[{index}] must be an object")
        item_id = _require_string(item, "item_id", f"prediction.items[{index}]")
        if item_id in seen:
            raise RankingCalibrationError(f"duplicate prediction item_id: {item_id}")
        seen.add(item_id)
        features = item.get("features")
        if not isinstance(features, dict) or set(features) != set(order):
            raise RankingCalibrationError("prediction features must exactly cover feature_order")
        if item.get("unit_by_feature") != candidate["unit_by_feature"]:
            raise RankingCalibrationError("prediction feature units do not match candidate")
        if (
            item.get("feature_version_by_feature")
            != candidate["feature_version_by_feature"]
        ):
            raise RankingCalibrationError(
                "prediction feature versions do not match candidate"
            )
        contributions = {
            name: _finite(features[name], f"prediction.features.{name}") * coefficient
            for name, coefficient in zip(order, coefficients)
        }
        results.append(
            {
                "item_id": item_id,
                "relative_score": float(sum(contributions.values())),
                "feature_contributions": contributions,
                "grade": None,
                "confidence": None,
            }
        )
    results.sort(key=lambda item: (-item["relative_score"], item["item_id"]))
    previous_score: float | None = None
    previous_rank = 0
    for position, result in enumerate(results, start=1):
        if previous_score is None or not math.isclose(
            result["relative_score"], previous_score, rel_tol=0.0, abs_tol=1e-12
        ):
            previous_rank = position
        result["relative_rank"] = previous_rank
        previous_score = result["relative_score"]
    return {
        "status": "candidate_relative_order_not_scored",
        "candidate_version": candidate["candidate_version"],
        "indicator_id": candidate["indicator_id"],
        "relative_order": results,
        "grade": None,
        "threshold_version": None,
        "reason_codes": ["absolute_grade_anchor_missing", "A_E_calibration_required"],
    }


def validate_ranking_test_protocol(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != RANKING_TEST_PROTOCOL_SCHEMA_VERSION:
        raise RankingCalibrationError("unsupported ranking test protocol")
    scope = payload.get("artifact_scope")
    if scope not in {_REAL_TEST_PROTOCOL_SCOPE, _TEST_TEST_PROTOCOL_SCOPE}:
        raise RankingCalibrationError("ranking test protocol scope is invalid")
    for field in ("protocol_id", "protocol_version", "indicator_id"):
        _require_string(payload, field, "ranking_test_protocol")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise RankingCalibrationError("ranking test protocol source is required")
    expected = "preregistered_independent_ranking_test" if scope == _REAL_TEST_PROTOCOL_SCOPE else "synthetic_test_fixture"
    if source.get("kind") != expected:
        raise RankingCalibrationError(f"ranking test protocol source.kind must be {expected}")
    _require_sha256(source.get("source_sha256"), "test_protocol.source_sha256")
    _parse_time(source.get("registered_at"), "test_protocol.registered_at")
    binding = payload.get("candidate_binding")
    if not isinstance(binding, dict):
        raise RankingCalibrationError("ranking test candidate binding is required")
    for field in ("candidate_id", "candidate_version", "seal_id"):
        _require_string(binding, field, "test_protocol.candidate_binding")
    for field in ("candidate_sha256", "seal_content_sha256"):
        _require_sha256(binding.get(field), f"test_protocol.candidate_binding.{field}")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict):
        raise RankingCalibrationError("ranking test acceptance is required")
    _non_negative_integer(acceptance.get("min_evaluated_pairs"), "acceptance.min_evaluated_pairs")
    _non_negative_integer(acceptance.get("min_annotators"), "acceptance.min_annotators")
    accuracy = _finite(acceptance.get("min_pairwise_accuracy"), "acceptance.min_pairwise_accuracy")
    loss = _finite(acceptance.get("max_pairwise_log_loss"), "acceptance.max_pairwise_log_loss")
    tau = _finite(acceptance.get("min_mean_kendall_tau"), "acceptance.min_mean_kendall_tau")
    if not 0 <= accuracy <= 1 or loss < 0 or not -1 <= tau <= 1:
        raise RankingCalibrationError("ranking test acceptance values are outside metric domains")
    if payload.get("safety") != {
        "acceptance_applies_to": "relative_order_only",
        "A_E_grade_approval_possible": False,
        "production_scoring_approval_possible": False,
    }:
        raise RankingCalibrationError("ranking test protocol safety declaration is invalid")


def evaluate_ranking_independent_test(
    candidate: dict[str, Any], protocol: dict[str, Any], samples: list[dict[str, Any]], *, evaluated_at: str
) -> dict[str, Any]:
    validate_ranking_candidate(candidate)
    validate_ranking_test_protocol(protocol)
    _parse_time(evaluated_at, "evaluated_at")
    if _parse_time(evaluated_at, "evaluated_at") < _parse_time(protocol["source"]["registered_at"], "protocol.registered_at"):
        raise RankingCalibrationError("ranking independent test predates protocol registration")
    test_scope = protocol["artifact_scope"] == _TEST_TEST_PROTOCOL_SCOPE
    candidate_test_scope = candidate["artifact_scope"] == _TEST_CANDIDATE_SCOPE
    if test_scope != candidate_test_scope:
        raise RankingCalibrationError("ranking candidate/test protocol scope mismatch")
    if _parse_time(
        protocol["source"]["registered_at"], "test_protocol.source.registered_at"
    ) < _parse_time(candidate["fit"]["fitted_at"], "candidate.fit.fitted_at"):
        raise RankingCalibrationError(
            "ranking independent-test protocol registration predates its bound candidate"
        )
    binding = protocol["candidate_binding"]
    expected_binding = {
        "candidate_id": candidate["candidate_id"],
        "candidate_version": candidate["candidate_version"],
        "candidate_sha256": canonical_sha256(candidate),
        "seal_id": candidate["independent_test"]["seal_id"],
        "seal_content_sha256": candidate["independent_test"]["content_sha256"],
    }
    if binding != expected_binding:
        raise RankingCalibrationError("ranking test protocol is not bound to this candidate/seal")
    if protocol["indicator_id"] != candidate["indicator_id"]:
        raise RankingCalibrationError("ranking test protocol indicator mismatch")
    if not isinstance(samples, list):
        raise RankingCalibrationError("ranking independent-test samples must be an array")
    selected = sorted(
        (
            sample for sample in samples
            if isinstance(sample, dict) and sample.get("indicator_id") == candidate["indicator_id"]
        ), key=lambda item: item.get("sample_id", "")
    )
    if any(sample.get("split") != "independent_test" for sample in selected):
        raise RankingCalibrationError("ranking evaluator accepts independent_test samples only")
    seal = candidate["independent_test"]
    if canonical_sha256(selected) != seal["content_sha256"]:
        raise RankingCalibrationError("ranking independent-test content does not match sealed hash")
    if [sample.get("sample_id") for sample in selected] != seal["sample_ids"]:
        raise RankingCalibrationError("ranking independent-test sample IDs do not match seal")
    if len(selected) != seal["record_count"]:
        raise RankingCalibrationError("ranking independent-test sample count does not match seal")
    items = []
    for sample in selected:
        item, names = _source_item(sample, candidate["indicator_id"], candidate["feature_order"])
        if names != candidate["feature_order"]:
            raise RankingCalibrationError("ranking test feature order mismatch")
        if item["unit_by_feature"] != candidate["unit_by_feature"] or item["feature_version_by_feature"] != candidate["feature_version_by_feature"]:
            raise RankingCalibrationError("ranking test feature contract mismatch")
        items.append(item)
    pairs = _pair_records(items, candidate["feature_order"])
    x, y, evaluated_pairs = _matrix(pairs, candidate["feature_order"], "independent_test", True)
    metrics = _fit_metrics(
        x,
        y,
        evaluated_pairs,
        items,
        np.asarray(candidate["coefficients"], dtype=np.float64),
        candidate["feature_order"],
    )
    annotator_count = len({pair["annotator_id"] for pair in evaluated_pairs})
    acceptance = protocol["acceptance"]
    checks = {
        "min_evaluated_pairs": metrics["pair_count"] >= acceptance["min_evaluated_pairs"],
        "min_annotators": annotator_count >= acceptance["min_annotators"],
        "min_pairwise_accuracy": metrics["pairwise_accuracy"] is not None and metrics["pairwise_accuracy"] >= acceptance["min_pairwise_accuracy"],
        "max_pairwise_log_loss": metrics["pairwise_log_loss"] <= acceptance["max_pairwise_log_loss"],
        "min_mean_kendall_tau": metrics["mean_kendall_tau"] is not None and metrics["mean_kendall_tau"] >= acceptance["min_mean_kendall_tau"],
    }
    passed = all(checks.values())
    report_seed = {
        "candidate_sha256": canonical_sha256(candidate),
        "protocol_sha256": canonical_sha256(protocol),
        "seal_sha256": seal["content_sha256"],
        "metrics": metrics,
    }
    report_version = "ranking-independent-test-" + canonical_sha256(report_seed)[:16]
    report = {
        "schema_version": RANKING_TEST_REPORT_SCHEMA_VERSION,
        "artifact_scope": "synthetic_test_only_ranking_test_report" if test_scope else "ranking_independent_test_report",
        "report_id": report_version,
        "report_version": report_version,
        "indicator_id": candidate["indicator_id"],
        "status": "passed_relative_order_test" if passed else "failed_relative_order_test",
        "evaluated_at": evaluated_at,
        "target_semantics": {
            "evaluated_target": "relative_order_only",
            "absolute_grade_anchor_present": False,
            "A_E_grade_approved": False,
            "production_scoring_approved": False,
        },
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_version": candidate["candidate_version"],
            "content_sha256": canonical_sha256(candidate),
        },
        "protocol": {
            "protocol_id": protocol["protocol_id"],
            "protocol_version": protocol["protocol_version"],
            "content_sha256": canonical_sha256(protocol),
        },
        "independent_test_seal": {
            "seal_id": seal["seal_id"],
            "record_count": seal["record_count"],
            "content_sha256": seal["content_sha256"],
            "verified": True,
        },
        "metrics": {**metrics, "annotator_count": annotator_count},
        "acceptance": {"preregistered_values": dict(acceptance), "checks": checks, "passed": passed},
        "safety": {
            "grade": None,
            "threshold_version": None,
            "A_E_output_allowed": False,
            "promotion_to_A_E_asset_allowed": False,
            "reason": "relative_order_validation_does_not_identify_absolute_grade_boundaries",
        },
    }
    validate_ranking_test_report(report)
    return report


def validate_ranking_test_report(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != RANKING_TEST_REPORT_SCHEMA_VERSION:
        raise RankingCalibrationError("unsupported ranking test report")
    if payload.get("artifact_scope") not in {"ranking_independent_test_report", "synthetic_test_only_ranking_test_report"}:
        raise RankingCalibrationError("ranking test report scope is invalid")
    for field in ("report_id", "report_version", "indicator_id"):
        _require_string(payload, field, "ranking_test_report")
    if payload.get("status") not in {"passed_relative_order_test", "failed_relative_order_test"}:
        raise RankingCalibrationError("ranking test report status is invalid")
    _parse_time(payload.get("evaluated_at"), "report.evaluated_at")
    if payload.get("target_semantics") != {
        "evaluated_target": "relative_order_only",
        "absolute_grade_anchor_present": False,
        "A_E_grade_approved": False,
        "production_scoring_approved": False,
    }:
        raise RankingCalibrationError("ranking test report target semantics are unsafe")
    for section in ("candidate", "protocol", "independent_test_seal"):
        if not isinstance(payload.get(section), dict):
            raise RankingCalibrationError(f"ranking report {section} is required")
    for section in ("candidate", "protocol", "independent_test_seal"):
        _require_sha256(payload[section].get("content_sha256"), f"report.{section}.content_sha256")
    if payload["independent_test_seal"].get("verified") is not True:
        raise RankingCalibrationError("ranking report test seal must be verified")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict):
        raise RankingCalibrationError("ranking report acceptance is required")
    values = acceptance.get("preregistered_values")
    checks = acceptance.get("checks")
    metrics = payload.get("metrics")
    if not isinstance(values, dict) or not isinstance(checks, dict) or not isinstance(metrics, dict):
        raise RankingCalibrationError("ranking report acceptance inputs are invalid")
    required_values = {
        "min_evaluated_pairs",
        "min_annotators",
        "min_pairwise_accuracy",
        "max_pairwise_log_loss",
        "min_mean_kendall_tau",
    }
    required_metrics = {
        "pair_count",
        "annotator_count",
        "pairwise_accuracy",
        "pairwise_log_loss",
        "mean_kendall_tau",
    }
    if not required_values.issubset(values) or not required_metrics.issubset(metrics):
        raise RankingCalibrationError("ranking report acceptance fields are incomplete")
    for field in ("min_evaluated_pairs", "min_annotators"):
        _non_negative_integer(values[field], f"report.acceptance.{field}")
    for field in (
        "min_pairwise_accuracy",
        "max_pairwise_log_loss",
        "min_mean_kendall_tau",
    ):
        _finite(values[field], f"report.acceptance.{field}")
    for field in ("pair_count", "annotator_count"):
        _non_negative_integer(metrics[field], f"report.metrics.{field}")
    for field in ("pairwise_log_loss",):
        _finite(metrics[field], f"report.metrics.{field}")
    for field in ("pairwise_accuracy", "mean_kendall_tau"):
        if metrics[field] is not None:
            _finite(metrics[field], f"report.metrics.{field}")
    expected_checks = {
        "min_evaluated_pairs": metrics.get("pair_count") >= values.get("min_evaluated_pairs"),
        "min_annotators": metrics.get("annotator_count") >= values.get("min_annotators"),
        "min_pairwise_accuracy": metrics.get("pairwise_accuracy") is not None
        and metrics["pairwise_accuracy"] >= values.get("min_pairwise_accuracy"),
        "max_pairwise_log_loss": metrics.get("pairwise_log_loss")
        <= values.get("max_pairwise_log_loss"),
        "min_mean_kendall_tau": metrics.get("mean_kendall_tau") is not None
        and metrics["mean_kendall_tau"] >= values.get("min_mean_kendall_tau"),
    }
    if checks != expected_checks:
        raise RankingCalibrationError("ranking report acceptance checks do not recompute")
    if acceptance.get("passed") != all(checks.values()):
        raise RankingCalibrationError("ranking report acceptance does not recompute")
    expected_status = "passed_relative_order_test" if acceptance["passed"] else "failed_relative_order_test"
    if payload["status"] != expected_status:
        raise RankingCalibrationError("ranking report status/acceptance mismatch")
    if payload.get("safety") != {
        "grade": None,
        "threshold_version": None,
        "A_E_output_allowed": False,
        "promotion_to_A_E_asset_allowed": False,
        "reason": "relative_order_validation_does_not_identify_absolute_grade_boundaries",
    }:
        raise RankingCalibrationError("ranking report safety declaration is invalid")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RankingCalibrationError(f"invalid JSONL line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise RankingCalibrationError(f"JSONL line {line_number} must be an object")
            records.append(value)
    return records


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
