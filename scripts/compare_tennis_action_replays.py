"""Account for automatic windows against independently reviewed short intervals.

Window localization, phase coverage, anchor error and technique are reported
separately. A window covering a visual anchor is not automatically a correct
phase segmentation or confirmed contact. Matching is one to one. Reviews never
modify predictions or contribute to their score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(predictions, review):
    intervals = review["reviewed_intervals"]
    predictions = [p for p in predictions if any(a <= p["peak_ms"] <= b for a, b in intervals)]
    truth = review["positive_actions"]
    tolerance = review["anchor_uncertainty_ms"]
    edges = {}
    for i, action in enumerate(truth):
        edges[i] = sorted([j for j, prediction in enumerate(predictions)
            if prediction["person_track_id"] == action["person_track_id"]
            and prediction["start_ms"] - tolerance <= action["anchor_ms"] <= prediction["end_ms"] + tolerance],
            key=lambda j: abs(predictions[j]["peak_ms"] - action["anchor_ms"]))
    matched = {}

    def assign(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or assign(matched[j], seen):
                matched[j] = i
                return True
        return False

    for i in sorted(edges, key=lambda i: len(edges[i])):
        assign(i, set())
    matches = []
    for j, i in sorted(matched.items(), key=lambda pair: pair[1]):
        action, prediction = truth[i], predictions[j]
        phases = action.get("phase_evidence_ms", {})
        phase_coverage = {phase: prediction["start_ms"] <= timestamp <= prediction["end_ms"]
                          for phase, timestamp in phases.items()}
        error = prediction["peak_ms"] - action["anchor_ms"]
        matches.append({"manual_action_id": action["manual_action_id"],
            "prediction_id": prediction.get("episode_id", prediction.get("candidate_id")),
            "window_ms": [prediction["start_ms"], prediction["peak_ms"], prediction["end_ms"]],
            "anchor_error_ms": round(error, 3), "anchor_within_declared_uncertainty": abs(error) <= tolerance,
            "reviewed_phase_observations_covered": phase_coverage,
            "all_reviewed_phase_observations_covered": all(phase_coverage.values()) if len(phase_coverage) == 3 else None,
            "observed_technique": action["technique"], "predicted_family": prediction.get("family"),
            "predicted_profile": prediction.get("motion_pattern", {}).get("profile_id"),
            "localized_only_not_confirmed_contact": True})
    unmatched = [p for j, p in enumerate(predictions) if j not in matched]
    return {"reference_count": len(truth), "prediction_count_in_reviewed_intervals": len(predictions),
            "localized_count": len(matches), "anchor_within_uncertainty_count": sum(m["anchor_within_declared_uncertainty"] for m in matches),
            "all_three_phase_observations_covered_count": sum(m["all_reviewed_phase_observations_covered"] is True for m in matches),
            "matches": matches, "unlocalized_actions": [a["manual_action_id"] for i, a in enumerate(truth) if i not in matched.values()],
            "unmatched_predictions": [{key: p.get(key) for key in ("candidate_id", "episode_id", "person_track_id", "start_ms", "peak_ms", "end_ms", "family")} for p in unmatched],
            "negative_interval_predictions": [{"kind": negative["kind"], "window": [negative["start_ms"], negative["end_ms"]],
                "prediction_ids": [p.get("episode_id", p.get("candidate_id")) for p in unmatched
                                   if negative["start_ms"] <= p["peak_ms"] <= negative["end_ms"]]}
                for negative in review.get("non_action_or_partial_intervals", [])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite comparison")
    reference, evaluation = read(args.reference), read(args.evaluation)
    if reference.get("version") != "tennis-source-manual-action-set-v1" or evaluation.get("code_changed_during_execution") is not False:
        raise ValueError("Expected reviewed source windows and unchanged replay code")
    output = {"version": "tennis-action-window-comparison-v1", "reference_sha256": sha(args.reference),
              "evaluation_sha256": sha(args.evaluation), "runner_sha256": sha(Path(__file__)),
              "manual_references_used_in_predictions": False, "expert_ground_truth": False,
              "semantics": "one_to_one_visual_anchor_localization_and_separate_phase_support_not_contact_accuracy",
              "cases": []}
    for sample in evaluation["samples"]:
        choices = [r for r in reference["sets"] if r["video_sha256"].lower() == sample["source_video_sha256"].lower()]
        if len(choices) > 1:
            raise ValueError("Ambiguous reference source")
        if not choices:
            continue
        name = sample["sample_id"]
        recognition_path = args.evaluation.parent / f"{name}-recognition.json"
        assessment_path = args.evaluation.parent / f"{name}-assessment.json"
        if sha(recognition_path) != sample["recognition_sha256"] or sha(assessment_path) != sample["assessment_sha256"]:
            raise ValueError("Prediction changed after replay")
        recognition, assessment = read(recognition_path), read(assessment_path)
        output["cases"].append({"sample_id": name, "source_video_sha256": sample["source_video_sha256"],
            "raw_candidates": compare(recognition["candidates"], choices[0]),
            "scored_episodes": compare(assessment["episodes"], choices[0])})
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
