"""Current application scoring policy for freshly computed card measurements.

The application accepts advisory observations automatically. This policy does
not supply absent measurements or claim the source rubric's qualitative grade.
Callers must finish source, window and geometry checks before promoting a result.
"""
from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
import math

SCORING_POLICY_VERSION = "automatic-formal-card-scoring-v1.0.0"
FORMAL_SCORE_SEMANTICS = "application_condition_completion_score"
GRADE_POINTS = {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20}
ADVISORY_QUALITY_FLAGS = frozenset({
    "primary_track_coverage_low", "primary_pose_coverage_low", "pose_kinematic_coverage_low",
    "keypoint_jump_candidates_present", "left_right_swap_candidates_present",
    "primary_identity_ambiguous", "source_track_switch_candidates_present",
})


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def apply_scoring_policy(result: dict) -> dict:
    """Finalize an internal result in place; serialized grades are not inputs.

    Evaluators establish every condition from observations first. Review-only
    outputs without those observations remain unavailable. A second call after
    an adapter rejects a source binding also removes any previously set grade.
    """
    result["scoring_policy_version"] = SCORING_POLICY_VERSION
    result["original_grade"] = None
    result["source_grade_supported"] = False
    if result.get("status") in {"proxy_scored", "scored"}:
        conditions = result.get("conditions")
        valid = (isinstance(conditions, list) and bool(conditions)
                 and all(isinstance(c, Mapping) and type(c.get("passed")) is bool
                         and _finite(c.get("value")) for c in conditions))
        if valid:
            fraction = sum(c["passed"] for c in conditions) / len(conditions)
            grade = ("A" if fraction == 1 else "B" if fraction >= .75 else
                     "C" if fraction >= .5 else "D" if fraction > 0 else "E")
            supplied_grade = result.get("grade") if result["status"] == "scored" else result.get("proxy_grade")
            valid = (supplied_grade == grade and _finite(result.get("score"))
                     and result["score"] == GRADE_POINTS[grade]
                     and not result.get("reason_codes"))
        if valid:
            result.update(status="scored", grade=grade, proxy_grade=grade,
                          score=GRADE_POINTS[grade], condition_completion_fraction=fraction,
                          semantics=FORMAL_SCORE_SEMANTICS)
            return result
        result["reason_codes"] = sorted(set(result.get("reason_codes", [])
                                             + ["formal_score_condition_evidence_invalid"]))
    result.update(status="unavailable", grade=None, proxy_grade=None, score=None)
    result.pop("condition_completion_fraction", None)
    if isinstance(result.get("conditions"), list):
        for condition in result["conditions"]:
            if isinstance(condition, dict):
                condition["passed"] = None
    return result


def apply_report_scoring_policy(report: dict) -> dict:
    """Finalize a freshly computed adapter report, including its empty cards.

    Historical serialized reports are never passed through this function. The
    public reader keeps their original policy and does not promote stored grades.
    """
    from .card_evaluation import summarize_card_scores

    cards = report["cards"]
    instances = []
    for card in cards:
        actual = card["instances"]
        for instance in actual:
            apply_scoring_policy(instance)
        instances.extend(actual)
        if actual:
            card["run_status"] = "scored" if any(row["status"] == "scored" for row in actual) else "unavailable"
        elif card.get("run_status") == "review_required":
            card["run_status"] = "unavailable"
        card["instance_count"] = len(actual)
        card["instance_status_counts"] = dict(Counter(row["status"] for row in actual))
    ids = [row["indicator_id"] for row in cards]

    def summary_for(scope):
        scope_set = set(scope)
        summary = summarize_card_scores([row for row in instances if row["indicator_id"] in scope_set], scope)
        if "formal" not in summary["channels"]:
            summary["channels"]["formal"] = {"score": None, "scored_cards": 0,
                "applicable_cards": len(scope), "coverage": 0.0 if scope else None,
                "scored_event_instances": 0, "per_card": {}}
        formal = summary["channels"]["formal"]
        summary.update(scoring_policy_version=SCORING_POLICY_VERSION,
                       formal_scored_cards=formal["scored_cards"],
                       formal_scored_instances=formal["scored_event_instances"],
                       overall_score=formal["score"])
        return summary

    report["summary"] = summary_for(ids)
    if "observed_type_summary" in report:
        observed = set(report.get("input", {}).get("observed_event_codes", []))
        observed.update(row.get("event_code") for row in cards if row["instances"])
        report["observed_type_summary"] = summary_for([row["indicator_id"] for row in cards if row["event_code"] in observed])
    report.update(scoring_policy_version=SCORING_POLICY_VERSION,
                  score_semantics=FORMAL_SCORE_SEMANTICS,
                  overall_score=report["summary"]["overall_score"])
    coverage = report.get("coverage")
    if isinstance(coverage, dict):
        coverage["computed_card_instance_count"] = len(instances)
        coverage["run_card_status_counts"] = dict(Counter(row["run_status"] for row in cards))
        if "instance_status_counts" in coverage:
            coverage["instance_status_counts"] = dict(Counter(row["status"] for row in instances))
        coverage["formal_scored_card_count"] = report["summary"]["formal_scored_cards"]
        coverage["formal_scored_instance_count"] = report["summary"]["formal_scored_instances"]
    return report
