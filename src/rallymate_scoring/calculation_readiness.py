from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from rallymate_scoring.feasibility import measurement_feature_names


REPORT_VERSION = "indicator-calculation-readiness-v1.0.0"
REQUIRED_SOURCE_KEYS = frozenset(
    {"feasibility_registry", "events", "indicator_features", "scores"}
)


class CalculationReadinessError(ValueError):
    pass


def _remediation(reason: str, layer: str) -> str:
    if reason == "required_event_candidate_missing":
        return (
            "record the complete movement with lead-in and post-event context, then add "
            "accepted manual event truth if the rule candidate remains missing"
        )
    if reason in {
        "valid_fraction_below_quality_gate",
        "primary_pose_coverage_low",
        "pose_kinematic_coverage_low",
    }:
        return (
            "keep the complete primary player in frame at usable scale and reduce occlusion; "
            "do not fill missing joints with zero"
        )
    if reason in {"primary_track_coverage_low", "confirmed_id_switch_present"}:
        return (
            "record one clearly separated primary player or provide accepted identity continuity truth"
        )
    if reason.startswith("required_phase_missing:"):
        return (
            "record enough pre/post-event context and provide an accepted manual phase boundary"
        )
    if reason == "source_track_switch_candidates_present":
        return "review primary-player identity continuity before formal scoring"
    if reason == "keypoint_jump_candidates_present":
        return "complete joint-scoped keypoint jump truth review before formal scoring"
    if reason == "left_right_swap_candidates_present":
        return "complete left/right keypoint assignment truth review before formal scoring"
    if reason == "tactical_target_direction_not_observed":
        return "provide accepted target-direction semantic truth before formal scoring"
    if reason == "primary_identity_ambiguous":
        return "provide accepted primary-player identity truth before formal scoring"
    if reason.startswith("phase_proxy_"):
        return "review the corresponding manual event phase before formal scoring"
    return (
        "inspect the traceable event/feature evidence and collect the missing observation"
        if layer == "measurement"
        else "collect the typed human truth required by this scoring-only blocker"
    )


def build_indicator_calculation_readiness(
    *,
    registry: dict[str, Any],
    events: list[dict[str, Any]],
    indicator_records: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    video_id: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, Any]:
    indicators = registry.get("indicators", [])
    registry_by_id = {str(item["indicator_id"]): item for item in indicators}
    if not registry_by_id or len(registry_by_id) != len(indicators):
        raise CalculationReadinessError("registry indicator set is empty or duplicated")
    registry_ids = sorted(registry_by_id)
    event_by_id = {str(item["event_id"]): item for item in events}
    if len(event_by_id) != len(events):
        raise CalculationReadinessError("event IDs are duplicated")
    score_by_key = {
        (str(item["event_id"]), str(item["indicator_id"])): item for item in scores
    }
    if len(score_by_key) != len(scores):
        raise CalculationReadinessError("score event/indicator keys are duplicated")

    records_by_indicator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in indicator_records:
        indicator_id = str(record.get("indicator_id"))
        if indicator_id not in registry_by_id:
            raise CalculationReadinessError(f"indicator record is outside registry: {indicator_id}")
        event_id = str(record.get("event_id"))
        event = event_by_id.get(event_id)
        if event is None:
            raise CalculationReadinessError(f"indicator record references unknown event: {event_id}")
        if record.get("video_id") != video_id or event.get("video_id") != video_id:
            raise CalculationReadinessError("video identity differs across calculation artifacts")
        expected_features = measurement_feature_names(registry_by_id[indicator_id])
        actual_features = [str(item["feature_name"]) for item in record.get("features", [])]
        if actual_features != expected_features:
            raise CalculationReadinessError(
                f"required feature order differs for {event_id}/{indicator_id}"
            )
        score = score_by_key.get((event_id, indicator_id))
        if score is None or score.get("status") != record.get("scoring_status"):
            raise CalculationReadinessError(
                f"score status differs for {event_id}/{indicator_id}"
            )
        records_by_indicator[indicator_id].append(record)
    if len(indicator_records) != len(scores):
        raise CalculationReadinessError("indicator and score record counts differ")

    measurement_issue_index: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"indicator_ids": set(), "event_ids": set()}
    )
    scoring_issue_index: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"indicator_ids": set(), "event_ids": set()}
    )
    per_indicator = []
    measured_total = unavailable_total = indicators_with_candidate = 0
    indicators_with_measurement = 0
    for indicator_id in registry_ids:
        registry_item = registry_by_id[indicator_id]
        rows = sorted(
            records_by_indicator[indicator_id],
            key=lambda item: (int(event_by_id[str(item["event_id"])]["start_ms"]), str(item["event_id"])),
        )
        measured = [item for item in rows if item.get("feature_status") == "measured"]
        unavailable = [item for item in rows if item.get("feature_status") == "unavailable"]
        if len(measured) + len(unavailable) != len(rows):
            raise CalculationReadinessError("unexpected feature_status")
        indicators_with_candidate += bool(rows)
        indicators_with_measurement += bool(measured)
        measured_total += len(measured)
        unavailable_total += len(unavailable)
        feature_failure_reasons: Counter[str] = Counter()
        measurement_block_flags: Counter[str] = Counter()
        scoring_block_flags: Counter[str] = Counter()
        score_status_counts: Counter[str] = Counter()
        if not rows:
            feature_failure_reasons["required_event_candidate_missing"] += 1
            measurement_issue_index["required_event_candidate_missing"]["indicator_ids"].add(
                indicator_id
            )
        for row in rows:
            event_id = str(row["event_id"])
            score_status_counts[str(row["scoring_status"])] += 1
            gate = row.get("quality_gate", {})
            for flag in gate.get("hard_fail_flags", []):
                measurement_block_flags[str(flag)] += 1
                measurement_issue_index[str(flag)]["indicator_ids"].add(indicator_id)
                measurement_issue_index[str(flag)]["event_ids"].add(event_id)
            for flag in gate.get("scoring_block_flags", []):
                scoring_block_flags[str(flag)] += 1
                scoring_issue_index[str(flag)]["indicator_ids"].add(indicator_id)
                scoring_issue_index[str(flag)]["event_ids"].add(event_id)
            for feature in row.get("features", []):
                if feature.get("valid") is not True:
                    reason = str(feature.get("reason") or "required_feature_invalid")
                    feature_failure_reasons[reason] += 1
                    measurement_issue_index[reason]["indicator_ids"].add(indicator_id)
                    measurement_issue_index[reason]["event_ids"].add(event_id)
        evidence_examples = []
        for row in measured[:3]:
            event = event_by_id[str(row["event_id"])]
            source_frames = sorted(
                {
                    int(frame)
                    for feature in row.get("features", [])
                    for frame in feature.get("source_frames", [])
                }
            )
            if not source_frames:
                raise CalculationReadinessError("measured record has no source frames")
            evidence_examples.append(
                {
                    "event_id": str(row["event_id"]),
                    "event_code": str(row["event_code"]),
                    "start_ms": int(event["start_ms"]),
                    "end_ms": int(event["end_ms"]),
                    "person_track_id": row["person_track_id"],
                    "source_frames": source_frames,
                }
            )
        calculation_status = (
            "measured_on_at_least_one_candidate"
            if measured
            else "candidate_instances_present_but_none_measured"
            if rows
            else "required_event_candidate_missing"
        )
        per_indicator.append(
            {
                "indicator_id": indicator_id,
                "feasibility_level": str(registry_item["feasibility_level"]),
                "required_events": list(registry_item["required_events"]),
                "required_features": measurement_feature_names(registry_item),
                "required_scoring_features": list(registry_item["required_features"]),
                "event_instance_count": len(rows),
                "measured_instance_count": len(measured),
                "unavailable_instance_count": len(unavailable),
                "calculation_status": calculation_status,
                "score_status_counts": dict(sorted(score_status_counts.items())),
                "feature_failure_reason_counts": dict(
                    sorted(feature_failure_reasons.items())
                ),
                "measurement_block_flag_counts": dict(
                    sorted(measurement_block_flags.items())
                ),
                "scoring_block_flag_counts": dict(sorted(scoring_block_flags.items())),
                "measured_evidence_examples": evidence_examples,
            }
        )

    def issue_rows(
        index: dict[str, dict[str, set[str]]], layer: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "layer": layer,
                "reason": reason,
                "affected_indicator_ids": sorted(values["indicator_ids"]),
                "affected_event_ids": sorted(values["event_ids"]),
                "recommended_action": _remediation(reason, layer),
                "automatic_quality_gate_relaxation_allowed": False,
            }
            for reason, values in sorted(index.items())
        ]

    status = (
        "no_candidate_events"
        if not events
        else "all_registry_indicators_have_measured_candidate"
        if indicators_with_measurement == len(registry_ids)
        else "some_registry_indicators_have_no_measured_candidate"
    )
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "sources": sources,
        "scope": {
            "video_id": video_id,
            "feasibility_registry_version": registry.get("registry_version"),
            "indicator_ids": registry_ids,
            "event_codes": sorted(
                {
                    str(required).split(".", 1)[0]
                    for item in indicators
                    for required in item.get("required_events", [])
                }
            ),
            "candidate_events_are_ground_truth": False,
        },
        "summary": {
            "registry_indicator_count": len(registry_ids),
            "candidate_event_count": len(events),
            "indicator_event_instance_count": len(indicator_records),
            "measured_indicator_event_instance_count": measured_total,
            "unavailable_indicator_event_instance_count": unavailable_total,
            "indicator_with_candidate_count": indicators_with_candidate,
            "indicator_with_measured_candidate_count": indicators_with_measurement,
            "indicator_without_measured_candidate_count": len(registry_ids)
            - indicators_with_measurement,
            "all_registry_indicators_effectively_calculated_on_this_video": (
                indicators_with_measurement == len(registry_ids)
            ),
            "formal_scoring_ready": False,
        },
        "per_indicator": per_indicator,
        "measurement_remediation": issue_rows(measurement_issue_index, "measurement"),
        "formal_scoring_truth_requirements": issue_rows(scoring_issue_index, "scoring"),
        "interpretation": {
            "effectively_calculated_means": "at least one real candidate event in this video has every registry-required feature valid with non-empty source-frame evidence",
            "does_not_mean": "event accuracy, feature truth accuracy, grade accuracy, or F4 readiness",
            "formal_scoring_blockers_are_separate": "a complete F2 feature vector may remain unavailable for A-E because identity, event, keypoint, tactical context, calibration, or independent-test evidence is missing",
        },
        "safety": {
            "accuracy_claim": False,
            "event_ground_truth_provided": False,
            "keypoint_ground_truth_provided": False,
            "quality_gate_modified": False,
            "missing_value_zero_filled": False,
            "grade_generated_by_report": False,
            "threshold_generated_by_report": False,
            "maturity_promoted": False,
        },
    }
    validate_indicator_calculation_readiness(report)
    return report


def validate_indicator_calculation_readiness(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get(
        "report_version"
    ) != REPORT_VERSION:
        raise CalculationReadinessError("unsupported calculation readiness report")
    sources = report.get("sources", {})
    if not isinstance(sources, dict) or not REQUIRED_SOURCE_KEYS.issubset(sources):
        raise CalculationReadinessError("calculation readiness source lineage is incomplete")
    for source_name, source in sources.items():
        if not isinstance(source_name, str) or not source_name or not isinstance(source, dict):
            raise CalculationReadinessError("calculation readiness source lineage is malformed")
        path = source.get("path")
        sha256 = source.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(char not in "0123456789ABCDEF" for char in sha256)
        ):
            raise CalculationReadinessError("calculation readiness source lineage is malformed")
    scope = report.get("scope", {})
    if (
        not isinstance(scope, dict)
        or not isinstance(scope.get("video_id"), str)
        or not scope.get("video_id")
        or not isinstance(scope.get("feasibility_registry_version"), str)
        or not scope.get("feasibility_registry_version")
        or scope.get("candidate_events_are_ground_truth") is not False
    ):
        raise CalculationReadinessError("calculation readiness scope is malformed")
    rows = report.get("per_indicator", [])
    scope_ids = sorted(str(value) for value in scope.get("indicator_ids", []))
    row_ids = sorted(str(item.get("indicator_id")) for item in rows)
    if not scope_ids or len(scope_ids) != len(set(scope_ids)) or row_ids != scope_ids:
        raise CalculationReadinessError("calculation readiness indicator membership differs")
    summary = report.get("summary", {})
    measured = sum(int(item.get("measured_instance_count", -1)) for item in rows)
    unavailable = sum(int(item.get("unavailable_instance_count", -1)) for item in rows)
    total = sum(int(item.get("event_instance_count", -1)) for item in rows)
    with_candidate = sum(int(item.get("event_instance_count", 0)) > 0 for item in rows)
    with_measured = sum(int(item.get("measured_instance_count", 0)) > 0 for item in rows)
    if min(measured, unavailable, total) < 0 or measured + unavailable != total:
        raise CalculationReadinessError("calculation readiness instance accounting differs")
    if (
        int(summary.get("registry_indicator_count", -1)) != len(rows)
        or int(summary.get("indicator_event_instance_count", -1)) != total
        or int(summary.get("measured_indicator_event_instance_count", -1)) != measured
        or int(summary.get("unavailable_indicator_event_instance_count", -1)) != unavailable
        or int(summary.get("indicator_with_candidate_count", -1)) != with_candidate
        or int(summary.get("indicator_with_measured_candidate_count", -1)) != with_measured
        or int(summary.get("indicator_without_measured_candidate_count", -1))
        != len(rows) - with_measured
    ):
        raise CalculationReadinessError("calculation readiness summary accounting differs")
    all_measured = with_measured == len(rows)
    if summary.get("all_registry_indicators_effectively_calculated_on_this_video") is not all_measured:
        raise CalculationReadinessError("calculation readiness completeness claim differs")
    expected_status = (
        "no_candidate_events"
        if int(summary.get("candidate_event_count", -1)) == 0
        else "all_registry_indicators_have_measured_candidate"
        if all_measured
        else "some_registry_indicators_have_no_measured_candidate"
    )
    if report.get("status") != expected_status:
        raise CalculationReadinessError("calculation readiness status differs")
    for item in rows:
        measured_count = int(item.get("measured_instance_count", 0))
        unavailable_count = int(item.get("unavailable_instance_count", 0))
        instances = int(item.get("event_instance_count", 0))
        if (
            measured_count < 0
            or unavailable_count < 0
            or instances < 0
            or measured_count + unavailable_count != instances
            or not item.get("required_events")
            or not item.get("required_features")
        ):
            raise CalculationReadinessError("per-indicator instance accounting differs")
        score_status_total = sum(
            int(count) for count in item.get("score_status_counts", {}).values()
        )
        if score_status_total != instances:
            raise CalculationReadinessError("per-indicator score accounting differs")
        expected = (
            "measured_on_at_least_one_candidate"
            if measured_count
            else "candidate_instances_present_but_none_measured"
            if instances
            else "required_event_candidate_missing"
        )
        if item.get("calculation_status") != expected:
            raise CalculationReadinessError("per-indicator calculation status differs")
        if measured_count and not item.get("measured_evidence_examples"):
            raise CalculationReadinessError("measured indicator lacks evidence example")
        for evidence in item.get("measured_evidence_examples", []):
            frames = evidence.get("source_frames", [])
            if (
                not frames
                or frames != sorted(set(frames))
                or int(evidence.get("start_ms", -1)) < 0
                or int(evidence.get("end_ms", -1))
                < int(evidence.get("start_ms", -1))
            ):
                raise CalculationReadinessError("measured indicator evidence is malformed")
    if summary.get("formal_scoring_ready") is not False:
        raise CalculationReadinessError("F2 calculation report must not claim formal scoring")
    for item in [
        *report.get("measurement_remediation", []),
        *report.get("formal_scoring_truth_requirements", []),
    ]:
        if item.get("automatic_quality_gate_relaxation_allowed") is not False:
            raise CalculationReadinessError("calculation report weakens a quality gate")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "event_ground_truth_provided",
        "keypoint_ground_truth_provided",
        "quality_gate_modified",
        "missing_value_zero_filled",
        "grade_generated_by_report",
        "threshold_generated_by_report",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise CalculationReadinessError(f"unsafe calculation readiness claim: {field}")
