"""Compare freshly rebuilt runs on identical measured conditions.

Caller must reconstruct each report with build_card_execution_report first.
Athlete names and skill labels are deliberately not scoring inputs. This is a
descriptive comparison, not a validated estimate of overall playing strength.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from .card_scoring_policy import SUPPORTED_SCORING_POLICY_VERSIONS, formal_score_matches


def _cards(report: dict) -> dict:
    output = {}
    for card in report.get("cards", []):
        identifier = card.get("indicator_id")
        if not isinstance(identifier, str) or not identifier:
            continue
        # Repeated cards and conflicting copies cannot improve coverage.
        if identifier in output:
            output[identifier] = None
            continue
        instances, conflicts = {}, set()
        for row in card.get("instances", []):
            if (row.get("status") != "scored"
                    or row.get("scoring_policy_version") != report.get("scoring_policy_version")
                    or not formal_score_matches(row)
                    or not isinstance(row.get("event_id"), str) or not row["event_id"]):
                continue
            key = (row.get("video_id"), row["event_id"], row.get("person_track_id"))
            conditions = []
            for condition in row["conditions"]:
                threshold = condition.get("threshold")
                conditions.append({name: condition.get(name) for name in ("metric", "operator", "unit")}
                                  | {"threshold": threshold})
            try:
                signature = json.dumps({"conditions": sorted(conditions, key=lambda item: str(item["metric"])),
                    "rule_version": card.get("rule_version"), "measurement_version": row.get("measurement_version"),
                    "temporal_evidence_version": (row.get("temporal_evidence") or {}).get("version")},
                    sort_keys=True, ensure_ascii=False, allow_nan=False)
                fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                continue
            sample = (float(row["score"]), signature, fingerprint)
            if key in instances and instances[key] != sample:
                conflicts.add(key)
            instances[key] = sample
        samples = [value for key, value in instances.items() if key not in conflicts]
        if samples:
            output[identifier] = {"score": statistics.fmean(value[0] for value in samples),
                "event_count": len(samples), "condition_signatures": {value[1] for value in samples},
                "event_code": card.get("event_code")}
        else:
            output[identifier] = None
    return {key: value for key, value in output.items() if value is not None}


def compare_card_reports(left: dict, right: dict) -> dict:
    """Use shared card IDs and exact condition definitions, never unmatched totals."""
    left_policy, right_policy = left.get("scoring_policy_version"), right.get("scoring_policy_version")
    a, b = _cards(left), _cards(right)
    policy_matches = left_policy == right_policy and left_policy in SUPPORTED_SCORING_POLICY_VERSIONS
    matched, excluded = [], []
    for identifier in sorted(a.keys() & b.keys()):
        first, second = a[identifier], b[identifier]
        reason = ("scoring_policy_mismatch" if not policy_matches
                  else "action_type_mismatch" if not first["event_code"] or first["event_code"] != second["event_code"]
                  else "condition_reference_mismatch" if len(first["condition_signatures"]) != 1
                  or first["condition_signatures"] != second["condition_signatures"] else None)
        if reason:
            excluded.append({"indicator_id": identifier, "reason": reason})
            continue
        matched.append({"indicator_id": identifier, "event_code": first["event_code"],
            "left_score": first["score"], "right_score": second["score"],
            "difference_left_minus_right": first["score"] - second["score"],
            "left_event_count": first["event_count"], "right_event_count": second["event_count"]})
    left_score = statistics.fmean(item["left_score"] for item in matched) if matched else None
    right_score = statistics.fmean(item["right_score"] for item in matched) if matched else None
    action_groups = defaultdict(list)
    for item in matched:
        action_groups[item["event_code"]].append(item)
    return {"version": "matched-condition-comparison-v1.0.0",
        "status": "matched_conditions" if matched else "no_comparable_conditions",
        "semantics": "descriptive_condition_comparison_not_player_skill_ranking",
        "scoring_policy_version": left_policy if policy_matches else None,
        "left_measured_card_count": len(a), "right_measured_card_count": len(b),
        "left_unmatched_card_ids": sorted(a.keys() - b.keys()),
        "right_unmatched_card_ids": sorted(b.keys() - a.keys()),
        "matched_card_count": len(matched), "matched_cards": matched, "excluded_shared_cards": excluded,
        "left_matched_score": left_score, "right_matched_score": right_score,
        "difference_left_minus_right": left_score - right_score if matched else None,
        "by_action": [{"event_code": code, "matched_card_count": len(rows),
            "left_score": statistics.fmean(row["left_score"] for row in rows),
            "right_score": statistics.fmean(row["right_score"] for row in rows)}
            for code, rows in sorted(action_groups.items())],
        "aggregation": "equal_shared_card_weight_after_independent_event_mean",
        "statistical_significance": "not_established",
        "capture_conditions_verified": False}
