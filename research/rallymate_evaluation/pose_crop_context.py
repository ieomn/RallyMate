from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


CONTEXT_REPLAY_VERSION = "routed-pose-source-replay-v1.0.0"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _selected_pose(row: Mapping[str, Any], track_id: int | None) -> Any:
    if not isinstance(track_id, int):
        return None
    matches = [
        pose
        for pose in row.get("poses", [])
        if isinstance(pose, Mapping) and pose.get("person_track_id") == track_id
    ]
    if len(matches) > 1:
        raise ValueError("frame contains duplicate selected-Track Pose records")
    return matches[0] if matches else None


def classify_routed_pose_sources(
    *,
    baseline_rows: list[dict[str, Any]],
    routed_rows: list[dict[str, Any]],
    primary_timeline: list[dict[str, Any]],
    experiment_rows_by_name: Mapping[str, list[dict[str, Any]]],
    target_processed_indexes: Iterable[int],
) -> tuple[dict[int, str], dict[str, Any]]:
    """Replay which crop policy supplied the selected Pose on each frame.

    Classification uses exact selected-Pose equality plus the already audited
    non-Pose invariants. It does not inspect feature values or event outcomes.
    """

    if not baseline_rows or not experiment_rows_by_name:
        raise ValueError("baseline and experiment rows must be non-empty")
    names = tuple(sorted(experiment_rows_by_name))
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("experiment names must be non-empty strings")
    baseline = {int(row["frame"]["processed_index"]): row for row in baseline_rows}
    routed = {int(row["frame"]["processed_index"]): row for row in routed_rows}
    timeline = {int(row["processed_index"]): row for row in primary_timeline}
    experiments = {
        name: {int(row["frame"]["processed_index"]): row for row in rows}
        for name, rows in experiment_rows_by_name.items()
    }
    if (
        len(baseline) != len(baseline_rows)
        or len(routed) != len(routed_rows)
        or len(timeline) != len(primary_timeline)
        or set(baseline) != set(routed)
        or set(baseline) != set(timeline)
        or any(
            len(rows) != len(experiment_rows_by_name[name])
            or set(rows) != set(baseline)
            for name, rows in experiments.items()
        )
    ):
        raise ValueError("Pose source replay inputs must align one-to-one")
    targets = {int(value) for value in target_processed_indexes}
    if not targets or not targets <= set(baseline):
        raise ValueError("target indexes must be a non-empty frame subset")

    sources: dict[int, str] = {}
    counts: Counter[str] = Counter()
    for index in sorted(targets):
        base = baseline[index]
        route = routed[index]
        base_non_pose = {key: value for key, value in base.items() if key != "poses"}
        route_non_pose = {key: value for key, value in route.items() if key != "poses"}
        if _canonical(base_non_pose) != _canonical(route_non_pose):
            raise ValueError("routed frame changed non-Pose data")
        track_id = timeline[index].get("source_track_id")
        base_pose = _selected_pose(base, track_id)
        routed_pose = _selected_pose(route, track_id)
        base_value = _canonical(base_pose)
        routed_value = _canonical(routed_pose)
        if routed_value == base_value:
            source = "baseline"
        else:
            matches = [
                name
                for name in names
                if _canonical(_selected_pose(experiments[name][index], track_id))
                == routed_value
                and _canonical(_selected_pose(experiments[name][index], track_id))
                != base_value
            ]
            if len(matches) != 1:
                raise ValueError(
                    "changed routed Pose does not resolve to exactly one experiment"
                )
            source = matches[0]
        sources[index] = source
        counts[source] += 1

    return sources, {
        "replay_version": CONTEXT_REPLAY_VERSION,
        "target_frame_count": len(targets),
        "source_frame_count_by_policy": dict(sorted(counts.items())),
        "classification_uses_feature_values": False,
        "classification_uses_event_outcomes": False,
        "classification_uses_grades_or_thresholds": False,
        "changed_pose_requires_unique_exact_source_match": True,
    }


__all__ = ["CONTEXT_REPLAY_VERSION", "classify_routed_pose_sources"]
