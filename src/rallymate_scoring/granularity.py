from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable


CURRENT_COCO_JOINTS = {
    "J004",
    "J008",
    "J009",
    "J006",
    "J007",
    "J033",
    "J034",
    "J101",
    "J121",
    "J103",
    "J123",
    "J071",
    "J072",
    "J141",
    "J161",
    "J143",
    "J163",
}
DEPENDENCIES = ("pose", "ball", "racket", "court", "tracking")
READY_THRESHOLD = 0.75
PARTIAL_THRESHOLD = 0.40
FEASIBILITY_LEVELS = ("F0", "F1", "F2", "F3", "F4")


def _registry() -> dict[str, Any]:
    resource = files("rallymate_scoring").joinpath("data/metric_cards.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _registry_indicator_metadata(value: Any) -> tuple[dict[str, str], dict[str, list[str]]]:
    levels: dict[str, str] = {}
    blockers: dict[str, list[str]] = {}
    if not isinstance(value, list):
        return levels, blockers
    for item in value:
        if not isinstance(item, dict):
            continue
        indicator_id = item.get("indicator_id")
        level = item.get("feasibility_level")
        if isinstance(indicator_id, str) and level in FEASIBILITY_LEVELS:
            levels[indicator_id] = str(level)
            blockers[indicator_id] = [
                str(blocker)
                for blocker in item.get("current_blockers", [])
                if isinstance(blocker, str)
            ]
    return levels, blockers


def _minimum_loop_context(summary: dict[str, Any]) -> dict[str, Any]:
    """Resolve the runtime scoring-loop set without a compiled indicator list.

    ``indicator_feature_validity`` is the authoritative runtime membership list.
    Registry metadata refines maturity and blockers when it is embedded or its
    provenance path remains readable.  Older six-indicator summaries contain
    neither, so feature-validity membership safely falls back to F2: an entry in
    that table means the feature layer was actually invoked, not that F3/F4 was
    established.
    """

    loop = _mapping(summary.get("minimum_scoring_loop"))
    raw_validity = _mapping(loop.get("indicator_feature_validity"))
    validity = {
        str(indicator_id): _mapping(value)
        for indicator_id, value in raw_validity.items()
        if isinstance(indicator_id, str)
    }
    indicator_ids = sorted(validity)
    model_versions = _mapping(loop.get("model_versions"))
    provenance = _mapping(loop.get("provenance"))
    scope = _mapping(loop.get("scope"))

    registry_container = _mapping(loop.get("registry"))
    source_registry = _mapping(loop.get("source_registry"))
    registry_version = (
        registry_container.get("registry_version")
        or source_registry.get("registry_version")
        or loop.get("registry_version")
        or model_versions.get("feasibility_registry")
    )
    registry_path_value = (
        provenance.get("feasibility_registry_path")
        or registry_container.get("path")
        or source_registry.get("path")
        or loop.get("feasibility_registry_path")
    )
    registry_sha256 = (
        provenance.get("feasibility_registry_sha256")
        or registry_container.get("sha256")
        or source_registry.get("sha256")
    )

    levels: dict[str, str] = {}
    level_sources: dict[str, str] = {}
    registry_blockers: dict[str, list[str]] = {}

    for field_name in ("indicator_feasibility_levels", "feasibility_levels"):
        for indicator_id, level in _mapping(loop.get(field_name)).items():
            if isinstance(indicator_id, str) and level in FEASIBILITY_LEVELS:
                levels[indicator_id] = str(level)
                level_sources[indicator_id] = f"summary.minimum_scoring_loop.{field_name}"

    for container_name, container in (
        ("registry", registry_container),
        ("source_registry", source_registry),
    ):
        embedded_levels, embedded_blockers = _registry_indicator_metadata(
            container.get("indicators")
        )
        for indicator_id, level in embedded_levels.items():
            levels.setdefault(indicator_id, level)
            level_sources.setdefault(
                indicator_id,
                f"summary.minimum_scoring_loop.{container_name}.indicators",
            )
        registry_blockers.update(embedded_blockers)

    registry_read_status = "not_provided"
    if registry_path_value:
        registry_path = Path(str(registry_path_value)).expanduser()
        if registry_path.is_file():
            try:
                registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
                registry_version = registry_version or registry_data.get(
                    "registry_version"
                )
                file_levels, file_blockers = _registry_indicator_metadata(
                    registry_data.get("indicators")
                )
                for indicator_id, level in file_levels.items():
                    levels.setdefault(indicator_id, level)
                    level_sources.setdefault(
                        indicator_id, "provenance.feasibility_registry_path"
                    )
                registry_blockers.update(file_blockers)
                registry_read_status = "loaded"
            except (OSError, ValueError, TypeError):
                registry_read_status = "invalid_or_unreadable"
        else:
            registry_read_status = "not_found"

    for indicator_id, value in validity.items():
        inline_level = value.get("feasibility_level")
        if inline_level in FEASIBILITY_LEVELS:
            levels[indicator_id] = str(inline_level)
            level_sources[indicator_id] = (
                "summary.minimum_scoring_loop.indicator_feature_validity"
            )
        elif indicator_id not in levels:
            levels[indicator_id] = "F2"
            level_sources[indicator_id] = "feature_validity_membership_fallback"

    events = [str(item) for item in scope.get("events", []) if isinstance(item, str)]
    if not events:
        events = sorted({item.split("-", 1)[0] for item in indicator_ids if "-" in item})

    indicator_score_status_counts = {
        str(indicator_id): _mapping(value)
        for indicator_id, value in _mapping(
            loop.get("indicator_score_status_counts")
        ).items()
        if isinstance(indicator_id, str)
    }
    indicator_grade_counts = {
        str(indicator_id): _mapping(value)
        for indicator_id, value in _mapping(loop.get("indicator_grade_counts")).items()
        if isinstance(indicator_id, str)
    }
    for indicator_id, value in validity.items():
        nested_score_counts = _mapping(value.get("score_status_counts"))
        nested_grade_counts = _mapping(value.get("grade_counts"))
        if nested_score_counts:
            indicator_score_status_counts[indicator_id] = nested_score_counts
        if nested_grade_counts:
            indicator_grade_counts[indicator_id] = nested_grade_counts

    return {
        "validity": validity,
        "indicator_ids": indicator_ids,
        "feasibility_levels": levels,
        "feasibility_level_sources": level_sources,
        "registry_blockers": registry_blockers,
        "score_status_counts": _mapping(loop.get("score_status_counts")),
        "grade_counts": _mapping(loop.get("grade_counts")),
        "indicator_score_status_counts": indicator_score_status_counts,
        "indicator_grade_counts": indicator_grade_counts,
        "events": events,
        "registry_version": str(registry_version) if registry_version else None,
        "registry_path": str(registry_path_value) if registry_path_value else None,
        "registry_sha256": str(registry_sha256) if registry_sha256 else None,
        "registry_read_status": registry_read_status,
        "declared_indicator_count": scope.get("indicator_count"),
        "source": {
            "video_id": loop.get("video_id"),
            "loop_version": loop.get("loop_version"),
            "event_codes": events,
            "pose_backend": model_versions.get("pose_backend"),
            "pose_profile": model_versions.get("pose_profile"),
            "pose_model_sha256": model_versions.get("pose_model_sha256"),
            "primary_player_version": model_versions.get("primary_player"),
            "event_version": model_versions.get("event"),
            "feature_version": model_versions.get("feature"),
        },
    }


def _max_missing_run(frame_indexes: set[int], frame_count: int) -> int:
    longest = 0
    current = 0
    for index in range(frame_count):
        if index in frame_indexes:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _longest_track_fraction(
    tracks: dict[int, set[int]], frame_count: int
) -> float:
    if not tracks or frame_count <= 0:
        return 0.0
    return max(len(indexes) for indexes in tracks.values()) / frame_count


def inspect_frame_observations(frames_path: str | Path) -> dict[str, Any]:
    """Measure continuity on the actual output instead of trusting frame coverage."""

    path = Path(frames_path)
    class_frames: dict[str, set[int]] = defaultdict(set)
    detection_tracks: dict[str, dict[int, set[int]]] = {
        name: defaultdict(set) for name in ("player", "ball", "racket")
    }
    pose_tracks: dict[int, set[int]] = defaultdict(set)
    pose_joint_valid: dict[int, Counter[str]] = defaultdict(Counter)
    frame_count = 0

    with path.open("r", encoding="utf-8") as handle:
        for processed_index, line in enumerate(handle):
            record = json.loads(line)
            frame_count += 1
            for detection in record.get("detections", []):
                class_name = detection.get("class_name")
                track_id = detection.get("track_id")
                if class_name in detection_tracks and isinstance(track_id, int):
                    class_frames[class_name].add(processed_index)
                    detection_tracks[class_name][track_id].add(processed_index)
            for pose in record.get("poses", []):
                track_id = pose.get("person_track_id")
                if not isinstance(track_id, int):
                    continue
                pose_tracks[track_id].add(processed_index)
                for point in pose.get("keypoints", []):
                    joint_id = point.get("downstream_joint_id")
                    if (
                        isinstance(joint_id, str)
                        and float(point.get("confidence", 0.0)) >= 0.25
                    ):
                        pose_joint_valid[track_id][joint_id] += 1

    primary_pose_track = (
        max(pose_tracks, key=lambda track_id: len(pose_tracks[track_id]))
        if pose_tracks
        else None
    )
    primary_pose_frames = (
        len(pose_tracks[primary_pose_track]) if primary_pose_track is not None else 0
    )
    joint_valid_fraction: dict[str, float] = {}
    if primary_pose_track is not None and primary_pose_frames:
        joint_valid_fraction = {
            joint_id: round(
                pose_joint_valid[primary_pose_track][joint_id] / primary_pose_frames,
                4,
            )
            for joint_id in sorted(CURRENT_COCO_JOINTS)
        }

    observations: dict[str, Any] = {
        "frame_count": frame_count,
        "primary_pose_track_id": primary_pose_track,
        "primary_pose_track_fraction": round(
            primary_pose_frames / max(frame_count, 1), 4
        ),
        "pose_track_count": len(pose_tracks),
        "primary_pose_joint_valid_fraction": joint_valid_fraction,
    }
    for class_name in ("player", "ball", "racket"):
        observations[f"{class_name}_frame_fraction"] = round(
            len(class_frames[class_name]) / max(frame_count, 1), 4
        )
        observations[f"{class_name}_track_count"] = len(
            detection_tracks[class_name]
        )
        observations[f"{class_name}_longest_track_fraction"] = round(
            _longest_track_fraction(
                detection_tracks[class_name], max(frame_count, 1)
            ),
            4,
        )
        observations[f"{class_name}_max_missing_run_frames"] = _max_missing_run(
            class_frames[class_name], frame_count
        )
    return observations


def _is_optional(card: dict[str, Any], dependency: str) -> bool:
    required = card.get("requiredPoints", "")
    if dependency == "ball":
        return bool(
            re.search(r"可选[^；。]*BALL|可选对手与球轨迹", required, re.I)
            and not re.search(r"必须[^；。]*BALL", required, re.I)
        )
    if dependency == "court":
        return bool(re.search(r"场地(?:信息|标定|单应性)?(?:可选|建议)|单应性建议", required))
    return False


def _required_joints(card: dict[str, Any]) -> list[str]:
    return sorted(set(re.findall(r"J\d{3}", card.get("requiredPoints", ""))))


def _dependency_coverage(
    dependency: str,
    summary: dict[str, Any],
    observations: dict[str, Any],
) -> float:
    coverage = summary.get("coverage", {})
    if dependency == "pose":
        return float(
            observations.get(
                "primary_pose_track_fraction",
                coverage.get("pose_frame_fraction", 0.0),
            )
        )
    if dependency == "ball":
        return min(
            float(coverage.get("ball_frame_fraction", 0.0)),
            float(observations.get("ball_longest_track_fraction", 0.0)),
        )
    if dependency == "racket":
        return min(
            float(coverage.get("racket_frame_fraction", 0.0)),
            float(observations.get("racket_longest_track_fraction", 0.0)),
        )
    if dependency == "court":
        return float(coverage.get("court_calibrated_fraction", 0.0))
    if dependency == "tracking":
        return float(observations.get("primary_pose_track_fraction", 0.0))
    return 0.0


def _evidence_status(value: float) -> str:
    if value >= READY_THRESHOLD:
        return "ready"
    if value >= PARTIAL_THRESHOLD:
        return "partial"
    return "blocked"


def _model_granularity(
    card: dict[str, Any], required_joints: Iterable[str]
) -> tuple[str, list[str]]:
    dependencies = set(card.get("dependencies", []))
    blockers: list[str] = []
    missing_joints = sorted(set(required_joints) - CURRENT_COCO_JOINTS)
    if missing_joints:
        blockers.append("pose_joint_not_in_coco17")
    if "racket" in dependencies:
        blockers.append("racket_keypoints_missing")
    if "ball" in dependencies:
        blockers.append("dedicated_ball_trajectory_missing")
    if "tracking" in dependencies:
        blockers.append("production_temporal_tracker_missing")
    if "court" in dependencies:
        blockers.append("metric_court_calibration_required")

    if missing_joints or "racket_keypoints_missing" in blockers:
        return "unsupported", blockers
    if any(
        blocker in blockers
        for blocker in (
            "dedicated_ball_trajectory_missing",
            "production_temporal_tracker_missing",
            "metric_court_calibration_required",
        )
    ):
        return "partial", blockers
    return "supported", blockers


def _indicator_result(
    card: dict[str, Any],
    summary: dict[str, Any],
    observations: dict[str, Any],
    loop_context: dict[str, Any],
) -> dict[str, Any]:
    dependencies = list(card.get("dependencies", []))
    joints = _required_joints(card)
    optional = [dep for dep in dependencies if _is_optional(card, dep)]
    mandatory = [dep for dep in dependencies if dep not in optional]
    dependency_coverage = {
        dep: round(_dependency_coverage(dep, summary, observations), 4)
        for dep in dependencies
    }
    if "pose" in dependency_coverage and joints:
        joint_valid = observations.get("primary_pose_joint_valid_fraction", {})
        required_joint_coverage = min(
            (float(joint_valid.get(joint_id, 0.0)) for joint_id in joints),
            default=0.0,
        )
        dependency_coverage["pose"] = round(
            min(dependency_coverage["pose"], required_joint_coverage), 4
        )
    mandatory_evidence = min(
        (dependency_coverage[dep] for dep in mandatory), default=1.0
    )
    optional_evidence = (
        sum(dependency_coverage[dep] for dep in optional) / len(optional)
        if optional
        else mandatory_evidence
    )
    evidence = mandatory_evidence * 0.85 + optional_evidence * 0.15
    granularity, structural_blockers = _model_granularity(card, joints)
    evidence_status = _evidence_status(evidence)
    blockers = list(structural_blockers)
    if evidence_status != "ready":
        blockers.append("observed_evidence_below_threshold")
    loop_validity = loop_context["validity"].get(card["id"])
    if loop_validity is not None:
        valid_count = int(loop_validity.get("valid", 0))
        total_count = int(loop_validity.get("total", 0))
        measurement_status = "measured" if valid_count > 0 else "unavailable"
        feasibility_level = loop_context["feasibility_levels"].get(
            card["id"], "F2"
        )
        reported_status_counts = _mapping(
            loop_context["indicator_score_status_counts"].get(card["id"])
        )
        reported_grade_counts = _mapping(
            loop_context["indicator_grade_counts"].get(card["id"])
        )
        reported_scored = int(reported_status_counts.get("scored", 0))
        if valid_count <= 0:
            scoring_status = "unavailable"
        elif reported_scored > 0 and feasibility_level == "F4":
            scoring_status = "scored"
        elif int(reported_status_counts.get("calibration_required", 0)) > 0:
            scoring_status = "calibration_required"
        elif reported_scored > 0:
            # A summary cannot elevate an indicator below F4 into formal scoring.
            scoring_status = "calibration_required"
            blockers.append("reported_scored_status_rejected_without_F4")
        elif reported_status_counts:
            scoring_status = "unavailable"
        else:
            # Backward-compatible summaries did not include per-indicator score counts.
            scoring_status = "calibration_required"
        grade_counts = (
            {
                str(grade): int(count)
                for grade, count in reported_grade_counts.items()
                if str(grade) in {"A", "B", "C", "D", "E"} and int(count) > 0
            }
            if scoring_status == "scored"
            else {}
        )
        blockers.extend(loop_context["registry_blockers"].get(card["id"], []))
        if feasibility_level != "F4":
            blockers.extend(
                [
                    "event_ground_truth_required",
                    "feature_error_not_evaluated",
                    "coach_calibration_missing",
                    "independent_test_missing",
                ]
            )
        elif scoring_status != "scored":
            blockers.append("trusted_calibration_not_loaded_or_no_scored_event")
    else:
        valid_count = 0
        total_count = 0
        measurement_status = "not_in_minimum_scoring_loop"
        scoring_status = "unavailable"
        feasibility_level = None
        grade_counts = {}
        blockers.extend(
            [
                "event_segmentation_not_implemented",
                "metric_feature_calibration_not_implemented",
            ]
        )
    blockers = list(dict.fromkeys(blockers))
    return {
        "indicator_id": card["id"],
        "indicator_name": card.get("name"),
        "domain": card.get("domain"),
        "event_code": card.get("eventCode"),
        "stage_code": card.get("stageCode"),
        "source_status": card.get("sourceStatus"),
        "dependencies": dependencies,
        "mandatory_dependencies": mandatory,
        "optional_dependencies": optional,
        "required_joint_ids": joints,
        "coco17_joint_mapping_complete": set(joints).issubset(CURRENT_COCO_JOINTS),
        "dependency_coverage": dependency_coverage,
        "observed_evidence": round(evidence, 4),
        "observed_evidence_status": evidence_status,
        "model_granularity": granularity,
        "score_ready": scoring_status == "scored" and feasibility_level == "F4",
        "measurement_status": measurement_status,
        "scoring_status": scoring_status,
        "feasibility_level": feasibility_level,
        "feasibility_level_source": loop_context[
            "feasibility_level_sources"
        ].get(card["id"]),
        "measured_event_instances": valid_count,
        "candidate_event_instances": total_count,
        "scored_event_instances": (
            int(
                _mapping(
                    loop_context["indicator_score_status_counts"].get(card["id"])
                ).get("scored", 0)
            )
            if scoring_status == "scored"
            else 0
        ),
        "grade_counts": grade_counts,
        "blockers": blockers,
        "calculation_contract": card.get("calculation"),
        "required_points_contract": card.get("requiredPoints"),
    }


def _capability_matrix(
    cards: list[dict[str, Any]], loop_context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    def affected(dependency: str) -> int:
        return sum(dependency in card.get("dependencies", []) for card in cards)

    pose_only = sum(
        set(card.get("dependencies", [])) == {"pose"} for card in cards
    )
    loop_context = loop_context or {}
    loop_indicator_count = len(loop_context.get("indicator_ids", []))
    event_label = "/".join(loop_context.get("events", [])) or "runtime-configured"
    registry_version = loop_context.get("registry_version") or "not_recorded"
    has_scored_events = any(
        int(_mapping(counts).get("scored", 0)) > 0
        for counts in loop_context.get("indicator_score_status_counts", {}).values()
    )
    return [
        {
            "capability": "body_pose",
            "current_output": "COCO-17 mapped to RallyMate J IDs",
            "granularity": "structurally_supported_accuracy_unvalidated",
            "affected_indicators": affected("pose"),
            "pose_only_indicators": pose_only,
            "next_action": "retain_pretrained_topology_and_finetune_on_tennis_pose",
        },
        {
            "capability": "ball",
            "current_output": "generic sports-ball boxes + simple track_id",
            "granularity": "insufficient_for_contact_and_trajectory_metrics",
            "affected_indicators": affected("ball"),
            "next_action": "train_tennis_ball_detector_and_temporal_trajectory_tracker",
        },
        {
            "capability": "racket",
            "current_output": "generic tennis-racket bounding box",
            "granularity": "insufficient_for_racket_face_head_handle_and_sweet_spot",
            "affected_indicators": affected("racket"),
            "next_action": "train_separate_racket_keypoint_model",
        },
        {
            "capability": "court",
            "current_output": "region hint or manual four-point homography",
            "granularity": "usable_only_when_metric_calibration_is_valid",
            "affected_indicators": affected("court"),
            "next_action": "version_fixed_camera_metric_calibration",
        },
        {
            "capability": "events_and_features",
            "current_output": (
                f"{event_label} candidate events + {loop_indicator_count} configured "
                f"indicator feature sets (registry {registry_version})"
                if loop_indicator_count
                else "runtime scoring-loop membership is not present in this static audit"
            ),
            "granularity": (
                "versioned_event_grades_no_aggregate_score"
                if has_scored_events
                else "configured_features_not_coach_calibrated"
            ),
            "affected_indicators": loop_indicator_count,
            "next_action": (
                "monitor_calibration_drift_and_revalidate_independent_test_protocol"
                if has_scored_events
                else "collect_manual_event_keypoint_and_multi_coach_truth_then_review_F3"
            ),
        },
    ]


def static_model_capability() -> dict[str, Any]:
    registry = _registry()
    cards = registry["cards"]
    granularity_counts = Counter()
    joint_cards = 0
    mapped_joint_cards = 0
    for card in cards:
        joints = _required_joints(card)
        if "pose" in card.get("dependencies", []):
            joint_cards += 1
            if set(joints).issubset(CURRENT_COCO_JOINTS):
                mapped_joint_cards += 1
        granularity_counts[_model_granularity(card, joints)[0]] += 1
    return {
        "schema_version": "1.0.0",
        "registry_version": registry.get("registryVersion"),
        "indicator_count": len(cards),
        "domains": dict(Counter(card["domain"] for card in cards)),
        "model_granularity": dict(granularity_counts),
        "pose_assessment": {
            "pose_dependent_indicators": joint_cards,
            "explicit_joint_mapping_complete_indicators": mapped_joint_cards,
            "recommendation": "retain_COCO17_topology_and_tennis_finetune_first",
            "reason": (
                "评分卡显式要求的 J 关节均可由当前 COCO-17 映射；当前主要问题是"
                "网球动作域精度、主球员时序稳定性和真值验证，而不是立即替换拓扑。"
            ),
        },
        "capability_matrix": _capability_matrix(cards),
    }


def analyze_scoring_readiness(
    summary: dict[str, Any], frames_path: str | Path
) -> dict[str, Any]:
    registry = _registry()
    cards = registry["cards"]
    observations = inspect_frame_observations(frames_path)
    loop_context = _minimum_loop_context(summary)
    indicators = [
        _indicator_result(card, summary, observations, loop_context) for card in cards
    ]
    evidence_counts = Counter(
        result["observed_evidence_status"] for result in indicators
    )
    granularity_counts = Counter(
        result["model_granularity"] for result in indicators
    )
    blocker_counts = Counter(
        blocker for result in indicators for blocker in set(result["blockers"])
    )
    static = static_model_capability()
    measurement_counts = Counter(
        result["measurement_status"] for result in indicators
    )
    scoring_status_counts = Counter(result["scoring_status"] for result in indicators)
    minimum_loop_results = [
        result
        for result in indicators
        if result["indicator_id"] in loop_context["indicator_ids"]
        and result["feasibility_level"] is not None
    ]
    known_indicator_ids = {result["indicator_id"] for result in indicators}
    unknown_loop_indicator_ids = sorted(
        set(loop_context["indicator_ids"]) - known_indicator_ids
    )
    loop_indicator_count = len(loop_context["indicator_ids"])
    outside_loop_count = max(len(indicators) - len(minimum_loop_results), 0)
    maturity_counts = Counter(
        result["feasibility_level"] for result in minimum_loop_results
    )
    measured_indicator_count = sum(
        result["measurement_status"] == "measured"
        for result in minimum_loop_results
    )
    scored_indicator_count = sum(
        result["scoring_status"] == "scored" for result in minimum_loop_results
    )
    score_ready_count = sum(bool(result["score_ready"]) for result in indicators)
    loop_grade_counts: Counter[str] = Counter()
    for result in minimum_loop_results:
        loop_grade_counts.update(result.get("grade_counts", {}))
    formal_grade_status = (
        "scored"
        if scored_indicator_count > 0
        else "calibration_required"
        if measured_indicator_count > 0
        else "unavailable"
    )
    unique_levels = sorted(maturity_counts, key=FEASIBILITY_LEVELS.index)
    return {
        "schema_version": "1.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_id": summary.get("job_id"),
        "registry_version": registry.get("registryVersion"),
        "summary": {
            "indicator_count": len(indicators),
            "domains": dict(Counter(result["domain"] for result in indicators)),
            "observed_evidence": dict(evidence_counts),
            "model_granularity": dict(granularity_counts),
            "score_ready": score_ready_count,
            "score_blocked": len(indicators) - score_ready_count,
            "measurement_status": dict(measurement_counts),
            "scoring_status": dict(scoring_status_counts),
            "minimum_scoring_loop": {
                "indicator_count": loop_indicator_count,
                "recognized_indicator_count": len(minimum_loop_results),
                "not_in_loop_indicator_count": outside_loop_count,
                "indicator_ids": loop_context["indicator_ids"],
                "unknown_indicator_ids": unknown_loop_indicator_ids,
                "measured_indicator_count": measured_indicator_count,
                "scored_indicator_count": scored_indicator_count,
                "calibration_required_indicator_count": sum(
                    result["scoring_status"] == "calibration_required"
                    for result in minimum_loop_results
                ),
                "unavailable_indicator_count": sum(
                    result["scoring_status"] == "unavailable"
                    for result in minimum_loop_results
                ),
                "feasibility_level": (
                    unique_levels[0] if len(unique_levels) == 1 else None
                ),
                "feasibility_levels": dict(maturity_counts),
                "indicator_feasibility_levels": {
                    result["indicator_id"]: result["feasibility_level"]
                    for result in minimum_loop_results
                },
                "feasibility_level_sources": {
                    indicator_id: source
                    for indicator_id, source in loop_context[
                        "feasibility_level_sources"
                    ].items()
                    if indicator_id in loop_context["indicator_ids"]
                },
                "formal_grade_status": formal_grade_status,
                "grade_counts": dict(loop_grade_counts),
                "aggregate_grade_status": "not_designed",
                "registry": {
                    "version": loop_context["registry_version"],
                    "path": loop_context["registry_path"],
                    "sha256": loop_context["registry_sha256"],
                    "read_status": loop_context["registry_read_status"],
                    "declared_indicator_count": loop_context[
                        "declared_indicator_count"
                    ],
                },
                "source": loop_context["source"],
            },
            "primary_blockers": dict(blocker_counts.most_common()),
            "decision": (
                f"{loop_indicator_count} Pose-only indicators are registered in the runtime "
                f"loop; {measured_indicator_count} produced at least one valid feature set and "
                f"{scored_indicator_count} produced ledger-authorized event-level A-E grades. "
                "No aggregate score or grade is defined; "
                f"{outside_loop_count} cards are outside this loop."
                if scored_indicator_count > 0
                else f"{loop_indicator_count} Pose-only indicators are registered in the runtime "
                f"loop and {measured_indicator_count} produced at least one valid feature set. "
                "Formal A-E remains calibration_required for measured indicators until manual "
                "truth, coach calibration and independent testing exist; "
                f"{outside_loop_count} cards are outside this loop."
                if loop_indicator_count
                else "No runtime scoring-loop membership was found. All indicators remain "
                "unavailable; missing evaluation must not be represented as a zero score."
            ),
        },
        "pose_assessment": static["pose_assessment"],
        "capability_matrix": _capability_matrix(cards, loop_context),
        "observed_model_evidence": observations,
        "indicator_results": indicators,
    }
