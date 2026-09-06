from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .blocker_taxonomy import (
    SCORING_BLOCKER_TAXONOMY_VERSION,
    blocker_group_for_flag,
    taxonomy_document,
    truth_requirement_for_flag,
    typed_reason_for_flag,
)


BLOCKER_TAXONOMY_AUDIT_VERSION = "blocker-taxonomy-replay-audit-v1.0.0"
CONFIDENCE_REPLAY_TOLERANCE = 0.0000011


class BlockerTaxonomyAuditError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BlockerTaxonomyAuditError(f"expected JSON object: {path}")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise BlockerTaxonomyAuditError(
                f"expected JSON object at {path}:{line_number}"
            )
        output.append(row)
    return output


def _row_index(
    path: Path, key: Callable[[dict[str, Any]], tuple[str, ...] | str]
) -> dict[tuple[str, ...] | str, dict[str, Any]]:
    output: dict[tuple[str, ...] | str, dict[str, Any]] = {}
    for row in _jsonl(path):
        row_key = key(row)
        if row_key in output:
            raise BlockerTaxonomyAuditError(f"duplicate row key {row_key!r}: {path}")
        output[row_key] = row
    return output


def _without_confidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_confidence(item)
            for key, item in value.items()
            if key != "confidence"
        }
    if isinstance(value, list):
        return [_without_confidence(item) for item in value]
    return value


def _confidence_leaves(value: Any, prefix: str = "") -> dict[str, float | None]:
    output: dict[str, float | None] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if key == "confidence":
                if item is not None and not isinstance(item, (int, float)):
                    raise BlockerTaxonomyAuditError(
                        f"non-numeric confidence at {path}: {item!r}"
                    )
                output[path] = None if item is None else float(item)
            else:
                output.update(_confidence_leaves(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            output.update(_confidence_leaves(item, f"{prefix}[{index}]"))
    return output


def _compare_indexed_rows(
    baseline: dict[tuple[str, ...] | str, dict[str, Any]],
    replay: dict[tuple[str, ...] | str, dict[str, Any]],
) -> dict[str, Any]:
    keys_exact = set(baseline) == set(replay)
    nonconfidence_exact = keys_exact
    differing_confidence_leaf_count = 0
    max_abs_confidence_delta = 0.0
    confidence_structure_exact = keys_exact
    if keys_exact:
        for row_key in sorted(baseline, key=str):
            left = baseline[row_key]
            right = replay[row_key]
            if _without_confidence(left) != _without_confidence(right):
                nonconfidence_exact = False
            left_confidence = _confidence_leaves(left)
            right_confidence = _confidence_leaves(right)
            if set(left_confidence) != set(right_confidence):
                confidence_structure_exact = False
                continue
            for leaf_path, left_value in left_confidence.items():
                right_value = right_confidence[leaf_path]
                if left_value == right_value:
                    continue
                differing_confidence_leaf_count += 1
                if left_value is None or right_value is None:
                    confidence_structure_exact = False
                    continue
                max_abs_confidence_delta = max(
                    max_abs_confidence_delta, abs(left_value - right_value)
                )
    return {
        "row_count_baseline": len(baseline),
        "row_count_replay": len(replay),
        "row_keys_exact": keys_exact,
        "nonconfidence_payloads_exact": nonconfidence_exact,
        "confidence_structure_exact": confidence_structure_exact,
        "differing_confidence_leaf_count": differing_confidence_leaf_count,
        "max_abs_confidence_delta": round(max_abs_confidence_delta, 12),
        "within_confidence_replay_tolerance": (
            confidence_structure_exact
            and max_abs_confidence_delta <= CONFIDENCE_REPLAY_TOLERANCE
        ),
    }


def _source_binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def build_blocker_taxonomy_replay_audit(
    *,
    taxonomy_path: Path,
    readiness_audit_path: Path,
    baseline_runs_root: Path,
    replay_runs_root: Path,
    audit_id: str,
) -> dict[str, Any]:
    taxonomy = _json(taxonomy_path)
    if taxonomy != taxonomy_document():
        raise BlockerTaxonomyAuditError(
            "machine taxonomy does not match the canonical Python taxonomy"
        )
    readiness = _json(readiness_audit_path)
    video_ids = [str(value) for value in readiness["scope"]["video_ids"]]
    indicator_ids = [str(value) for value in readiness["scope"]["indicator_ids"]]

    comparisons: list[dict[str, Any]] = []
    total_confidence_differences = 0
    maximum_confidence_delta = 0.0
    for video_id in video_ids:
        baseline_dir = baseline_runs_root / video_id
        replay_dir = replay_runs_root / video_id
        file_specs: tuple[
            tuple[str, Callable[[dict[str, Any]], tuple[str, ...] | str]], ...
        ] = (
            ("events.jsonl", lambda row: str(row["event_id"])),
            (
                "features.jsonl",
                lambda row: (str(row["event_id"]), str(row["feature_name"])),
            ),
            (
                "indicator-features.jsonl",
                lambda row: (str(row["event_id"]), str(row["indicator_id"])),
            ),
            (
                "scores.jsonl",
                lambda row: (str(row["event_id"]), str(row["indicator_id"])),
            ),
        )
        artifacts: dict[str, Any] = {}
        for filename, key in file_specs:
            baseline_path = baseline_dir / filename
            replay_path = replay_dir / filename
            comparison = _compare_indexed_rows(
                _row_index(baseline_path, key), _row_index(replay_path, key)
            )
            comparison["baseline"] = _source_binding(baseline_path)
            comparison["replay"] = _source_binding(replay_path)
            artifacts[filename] = comparison
            total_confidence_differences += comparison[
                "differing_confidence_leaf_count"
            ]
            maximum_confidence_delta = max(
                maximum_confidence_delta,
                comparison["max_abs_confidence_delta"],
            )
        comparisons.append({"video_id": video_id, "artifacts": artifacts})

    active_by_flag = {
        str(item["reason_or_flag"]): int(item["indicator_instance_count"])
        for item in readiness["scoring_block_flags"]
    }
    recovery_by_flag = {
        str(item["flag"]): item
        for item in readiness["scoring_block_recovery_priority"]
    }
    all_flags = sorted(set(active_by_flag) | set(recovery_by_flag))
    priority: list[dict[str, Any]] = []
    group_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "flags": [],
            "active_flag_occurrence_count": 0,
            "recovery_indicator_occurrence_count": 0,
        }
    )
    for flag in all_flags:
        reason_code = typed_reason_for_flag(flag)
        if reason_code is None:
            raise BlockerTaxonomyAuditError(f"untyped active scoring flag: {flag}")
        recovery = recovery_by_flag.get(flag, {})
        group = blocker_group_for_flag(flag)
        row = {
            "flag": flag,
            "reason_code": reason_code,
            "truth_requirement": truth_requirement_for_flag(flag),
            "blocker_group": group,
            "active_indicator_occurrence_count": active_by_flag.get(flag, 0),
            "recovery_indicator_occurrence_count": int(
                recovery.get("indicator_instance_count", 0)
            ),
            "sole_block_indicator_occurrence_count": int(
                recovery.get(
                    "sole_block_to_calibration_required_indicator_instance_count", 0
                )
            ),
        }
        priority.append(row)
        group_totals[group]["flags"].append(flag)
        group_totals[group]["active_flag_occurrence_count"] += row[
            "active_indicator_occurrence_count"
        ]
        group_totals[group]["recovery_indicator_occurrence_count"] += row[
            "recovery_indicator_occurrence_count"
        ]
    priority.sort(
        key=lambda item: (
            -item["recovery_indicator_occurrence_count"], item["flag"]
        )
    )
    blocker_groups = [
        {
            "blocker_group": group,
            **values,
            "count_semantics": "overlapping_flag_occurrences_not_unique_indicator_instances",
        }
        for group, values in sorted(group_totals.items())
    ]

    all_artifacts_preserved = all(
        artifact["row_keys_exact"]
        and artifact["nonconfidence_payloads_exact"]
        and artifact["within_confidence_replay_tolerance"]
        for video in comparisons
        for artifact in video["artifacts"].values()
    )
    score_semantics_exact = all(
        video["artifacts"]["scores.jsonl"]["nonconfidence_payloads_exact"]
        for video in comparisons
    )
    if not all_artifacts_preserved or not score_semantics_exact:
        raise BlockerTaxonomyAuditError(
            "taxonomy replay changed event/feature/score behavior beyond the declared confidence tolerance"
        )

    return {
        "schema_version": "1.0.0",
        "audit_version": BLOCKER_TAXONOMY_AUDIT_VERSION,
        "audit_id": audit_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "taxonomy_centralized_behavior_preserved_no_gate_change",
        "source": {
            "taxonomy": {
                **_source_binding(taxonomy_path),
                "taxonomy_version": SCORING_BLOCKER_TAXONOMY_VERSION,
            },
            "readiness_audit": {
                **_source_binding(readiness_audit_path),
                "audit_id": readiness["audit_id"],
                "audit_version": readiness["audit_version"],
                "source_fingerprint_sha256": readiness[
                    "source_fingerprint_sha256"
                ],
            },
            "baseline_runs_root": str(baseline_runs_root.resolve()),
            "replay_runs_root": str(replay_runs_root.resolve()),
        },
        "scope": {
            "video_ids": video_ids,
            "indicator_ids": indicator_ids,
            "indicator_event_instance_count": int(
                readiness["counts"]["indicator_event_instances"]
            ),
            "registry_version": readiness["scope"]["registry_version"],
        },
        "replay_comparison": {
            "confidence_replay_tolerance": CONFIDENCE_REPLAY_TOLERANCE,
            "confidence_tolerance_semantics": "serialization_rounding_replay_tolerance_not_scoring_threshold",
            "differing_confidence_leaf_count": total_confidence_differences,
            "max_abs_confidence_delta": round(maximum_confidence_delta, 12),
            "all_row_keys_exact": True,
            "all_nonconfidence_payloads_exact": True,
            "score_semantics_exact": score_semantics_exact,
            "videos": comparisons,
        },
        "blocker_recovery_priority": priority,
        "blocker_groups": blocker_groups,
        "assertions": {
            "every_active_flag_has_one_typed_reason": True,
            "every_active_flag_has_one_truth_requirement": True,
            "jump_is_not_identity": (
                typed_reason_for_flag("keypoint_jump_candidates_present")
                != "event_identity_continuity_unverified"
            ),
            "left_right_swap_is_not_identity": (
                typed_reason_for_flag("left_right_swap_candidates_present")
                != "event_identity_continuity_unverified"
            ),
            "tactical_target_is_not_identity": (
                typed_reason_for_flag("tactical_target_direction_not_observed")
                != "event_identity_continuity_unverified"
            ),
            "replay_behavior_preserved": all_artifacts_preserved,
        },
        "safety": {
            "quality_gate_modified": False,
            "measurement_gate_modified": False,
            "scoring_gate_modified": False,
            "candidate_events_are_ground_truth": False,
            "event_accuracy_claim": False,
            "feature_accuracy_claim": False,
            "score_accuracy_claim": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
            "overlapping_occurrences_summed_as_unique_instances": False,
        },
    }


def validate_blocker_taxonomy_replay_audit(payload: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "audit_version",
        "audit_id",
        "generated_at",
        "status",
        "source",
        "scope",
        "replay_comparison",
        "blocker_recovery_priority",
        "blocker_groups",
        "assertions",
        "safety",
    }
    if set(payload) != required:
        raise BlockerTaxonomyAuditError(
            f"unexpected top-level fields: {sorted(set(payload) ^ required)}"
        )
    if payload["schema_version"] != "1.0.0":
        raise BlockerTaxonomyAuditError("unsupported schema_version")
    if payload["audit_version"] != BLOCKER_TAXONOMY_AUDIT_VERSION:
        raise BlockerTaxonomyAuditError("unsupported audit_version")
    if payload["status"] != "taxonomy_centralized_behavior_preserved_no_gate_change":
        raise BlockerTaxonomyAuditError("audit status is not behavior-preserved")
    assertions = payload["assertions"]
    if not assertions or not all(value is True for value in assertions.values()):
        raise BlockerTaxonomyAuditError("all audit assertions must pass")
    safety = payload["safety"]
    if any(value is not False for value in safety.values()):
        raise BlockerTaxonomyAuditError("all safety claims must remain false")
    comparison = payload["replay_comparison"]
    if not comparison["all_row_keys_exact"]:
        raise BlockerTaxonomyAuditError("replay row keys changed")
    if not comparison["all_nonconfidence_payloads_exact"]:
        raise BlockerTaxonomyAuditError("replay non-confidence payloads changed")
    if not comparison["score_semantics_exact"]:
        raise BlockerTaxonomyAuditError("score semantics changed")
    if comparison["max_abs_confidence_delta"] > comparison[
        "confidence_replay_tolerance"
    ]:
        raise BlockerTaxonomyAuditError("confidence replay tolerance exceeded")
    flags = [row["flag"] for row in payload["blocker_recovery_priority"]]
    if len(flags) != len(set(flags)) or not flags:
        raise BlockerTaxonomyAuditError("blocker flags must be unique and non-empty")


def write_blocker_taxonomy_replay_audit(
    payload: dict[str, Any], output_path: Path
) -> None:
    validate_blocker_taxonomy_replay_audit(copy.deepcopy(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
