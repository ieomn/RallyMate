"""Replay saved measurements to verify source grouping and score preservation.

Requires before.json captured before the display-only migration. This does not
rerun inference, rewrite run artifacts, validate technique accuracy, or calibrate
new scores.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rallymate_scoring.training_dimensions import DIMENSION_NAMES, EXPECTED_CARD_COUNTS
from rallymate_service.footwork_review import load_footwork_review
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_service.user_demo import load_indicator_feature_records


def scored_criteria(report):
    return {(dimension["id"], row["id"], row["episode_id"], row["start_ms"], row["end_ms"]): row["score"]
            for dimension in report["dimensions"] for row in dimension["criteria"] if row["score"] is not None}


def main():
    output = Path("reports/training-dimensions-20261016")
    before_path = output / "before.json"
    before = json.loads(before_path.read_text(encoding="utf-8"))
    results = []
    for run_id, baseline in before.items():
        run = Path("service_data/runs", run_id)
        inputs = [run / "summary.json", run / "indicator-features.jsonl", run / "events.jsonl"]
        input_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        summary = json.loads(inputs[0].read_text(encoding="utf-8"))
        replay = Path("reports/model-comparison-20261015", run_id + "-after-source-measurements.json")
        if replay.exists():
            summary["action_recognition"] = json.loads(replay.read_text(encoding="utf-8"))
            input_hashes[str(replay)] = hashlib.sha256(replay.read_bytes()).hexdigest()
        records = load_indicator_feature_records(inputs[1])
        review = load_footwork_review(inputs[2], records,
            video_id=summary.get("minimum_scoring_loop", {}).get("video_id", run_id))
        after = build_process_evaluation(summary, records, footwork_review=review)
        old = {row["id"]: row for row in baseline["dimensions"]}
        current = {row["id"]: row for row in after["dimensions"]}
        old_scores = {key: value["score"] for key, value in old.items()}
        scores = {key: value["score"] for key, value in current.items()}
        counts = {key: current[key]["source_basis"]["card_count"] for key in DIMENSION_NAMES}
        ids = [card for key in DIMENSION_NAMES for card in current[key]["source_basis"]["card_ids"]]
        assert old_scores == scores
        assert baseline["overall_score"] == after["overall_score"] is None
        assert scored_criteria(baseline) == scored_criteria(after)
        assert counts == EXPECTED_CARD_COUNTS and len(ids) == len(set(ids)) == 298
        assert sum(len(current[key]["source_basis"]["source_blockers"]) for key in DIMENSION_NAMES) == 100
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in input_hashes.items())
        report_path = output / f"{run_id}-process-evaluation.json"
        report_path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = {"run_id": run_id, "scores_unchanged": True, "scored_criteria_unchanged": True,
                  "scores": scores, "source_card_counts": counts, "unique_source_cards": len(set(ids)),
                  "source_blocked_cards_preserved": 100, "input_artifacts_unchanged": True,
                  "movement_measured_criteria_before": old["movement"]["measured_criterion_count"],
                  "movement_measured_criteria_after": current["movement"]["measured_criterion_count"],
                  "movement_sample_count": current["movement"]["sample_count"],
                  "movement_status_reason": current["movement"]["status_reason"],
                  "preparation_status_reason": current["preparation"]["status_reason"],
                  "report_path": str(report_path), "report_bytes": report_path.stat().st_size,
                  "input_sha256": input_hashes}
        results.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "input_sha256"}, ensure_ascii=False))
    (output / "validation.json").write_text(json.dumps({
        "semantics": "display_grouping_and_score_preservation_not_technical_accuracy",
        "ground_truth_used": False, "new_scoring_formula_added": False,
        "baseline_sha256": hashlib.sha256(before_path.read_bytes()).hexdigest(),
        "runs": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
