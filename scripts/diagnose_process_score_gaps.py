"""Read existing runs and separate missing observations from absent score rules."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import statistics

from rallymate_service.process_evaluation import build_process_evaluation, _episodes
from rallymate_service.footwork_review import load_footwork_review
from rallymate_service.user_demo import load_indicator_feature_records


def stats(values):
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": round(min(values), 4), "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4), "max": round(max(values), 4)}


def main():
    replay_dir = Path("reports/model-comparison-20261015")
    paths = {p.name.removesuffix("-after-source-measurements.json"): p for p in replay_dir.glob("*-after-source-measurements.json")}
    paths["d73032eb-0c6e-4087-9001-310758fdcb5c"] = None
    results = []
    for run_id, replay_path in paths.items():
        run_dir = Path("service_data/runs", run_id)
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        if replay_path:
            summary["action_recognition"] = json.loads(replay_path.read_text(encoding="utf-8"))
        records = load_indicator_feature_records(run_dir / "indicator-features.jsonl")
        video_id = summary.get("minimum_scoring_loop", {}).get("video_id", run_id)
        review = load_footwork_review(run_dir / "events.jsonl", records, video_id=video_id)
        report = build_process_evaluation(summary, records, footwork_review=review)
        episodes = _episodes(summary)
        measures = Counter()
        proposed_progression, support_by_phase = [], {p: [] for p in ("preparation", "acceleration", "follow_through")}
        for episode in episodes:
            process = episode.get("process_motion", {})
            for phase in process.get("phases", []):
                measures[f"{phase['phase']}:{phase['status']}"] += 1
                if phase.get("phase") == "preparation" and phase.get("status") == "measured":
                    wrist = phase.get("wrist", {})
                    path, net = wrist.get("path_length_torso"), wrist.get("net_displacement_torso")
                    if isinstance(path, (float, int)) and path >= .03 and isinstance(net, (float, int)):
                        proposed_progression.append(min(1., net/path)*100)
            for phase in process.get("source_observations", {}).get("phases", []):
                metric = phase.get("measurements", {}).get("hip_inside_ankle_support_fraction", {})
                if metric.get("status") == "measured" and isinstance(metric.get("mean"), (float, int)):
                    support_by_phase[phase["phase"]].append(metric["mean"]*100)
        movements = [r for r in records if r.get("indicator_id") == "FS02-M02"]
        result = {"run_id": run_id, "episode_count": len(episodes), "phase_statuses": dict(measures),
            "dimensions": [{k: row.get(k) for k in ("id", "score", "scoring_rule_count", "sample_count", "scored_sample_count", "measured_criterion_count")}
                           for row in report["dimensions"]],
            "fs02_m02_count": len(movements),
            "fs02_m02_scoring_context_statuses": dict(Counter(r.get("scoring_context", {}).get("status", "missing") for r in movements)),
            "fs02_m02_reason_codes": dict(Counter(reason for r in movements for reason in r.get("reason_codes", []))),
            "candidate_preparation_net_path_ratio_percent": stats(proposed_progression),
            "candidate_support_inside_percent_by_phase": {k: stats(v) for k,v in support_by_phase.items()},
            "candidate_formula_semantics": "diagnostic_raw_geometry_only_not_production_score_not_quality_validation"}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
    output = Path("reports/process-score-gap-diagnosis-20261016.json")
    output.write_text(json.dumps({"status": "diagnosis_only", "production_formulas_modified": False,
                                 "ground_truth_used": False, "runs": results}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
