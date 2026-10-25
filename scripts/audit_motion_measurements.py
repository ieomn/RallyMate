"""Replay stored pose evidence and count source-aligned motion measurements.

This measures observability, never scoring accuracy. It does not rerun GPU pose
models or alter original run artifacts. Optional baselines must be actual saved
recognition outputs, rather than synthetic zero-filled measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from rallymate_scoring.stroke_candidates import recognize_strokes_from_artifacts
from rallymate_service.process_evaluation import build_process_evaluation


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def summarize(analysis: dict) -> dict:
    episodes = [episode for name, family in analysis["motion_analysis"]["families"].items()
                if name != "return" for episode in family["episodes"]]
    # A return is contextual relabeling of a baseline episode, not a second
    # physical observation. Exclude aliases from measurement denominators.
    by_measurement, by_phase = {}, {}
    statuses = Counter()
    for episode in episodes:
        for phase in episode.get("process_motion", {}).get("source_observations", {}).get("phases", []):
            phase_counts = by_phase.setdefault(phase["phase"], Counter())
            for name, value in phase["measurements"].items():
                status = value["status"]
                statuses[status] += 1
                phase_counts[status] += 1
                by_measurement.setdefault(name, Counter())[status] += 1
    return {"candidate_count": analysis.get("candidate_count"), "unique_episode_count": len(episodes),
            "processed_frame_count": analysis.get("diagnostics", {}).get("frame_count"),
            "measurement_status_counts": dict(statuses), "by_measurement": by_measurement,
            "by_phase": by_phase, "source_measurement_rows": sum(statuses.values()),
            "count_semantics": "per_episode_per_phase_per_measurement_not_unique_Word_cards"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for run in args.run_dir:
        inputs = {name: digest(run / name) for name in ("frames.jsonl", "primary-player.jsonl")}
        started = time.perf_counter()
        result = recognize_strokes_from_artifacts(run / "frames.jsonl", run / "primary-player.jsonl")
        elapsed = time.perf_counter() - started
        detail = args.output.parent / f"{run.name}-after-source-measurements.json"
        detail.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        row = {"run_id": run.name, "input_sha256": inputs, "replay_seconds": round(elapsed, 3),
               "analysis_version": result["motion_analysis"]["analysis_version"],
               "after": summarize(result), "detail_file": detail.name}
        process = build_process_evaluation({"action_recognition": result})
        row["process_reference"] = {"semantics": process["semantics"], "is_calibrated": process["is_calibrated"],
                                    "dimensions": [{key: value[key] for key in ("id", "score", "status", "sample_count", "scored_sample_count")}
                                                   for value in process["dimensions"]],
                                    "scope": "motion_only_replay_no_external_target_or_confirmed_contact"}
        baseline = args.baseline_dir / f"{run.name}-before-source-measurements.json" if args.baseline_dir else None
        if baseline is not None and baseline.is_file():
            row["before"] = summarize(json.loads(baseline.read_text(encoding="utf-8")))
            row["baseline_sha256"] = digest(baseline)
            row["baseline_semantics"] = "saved_replay_before_source_observations_was_connected_not_pre_task_release"
        # Confirm the replay did not alter frozen evidence inputs.
        row["original_inputs_unchanged"] = inputs == {name: digest(run / name) for name in inputs}
        rows.append(row)
        print(json.dumps({"run": run.name, "episodes": row["after"]["unique_episode_count"],
                          "measurement_status_counts": row["after"]["measurement_status_counts"]}, ensure_ascii=False))
    report = {"schema_version": "1.0.0", "generated_at": datetime.now(timezone.utc).isoformat(),
              "semantics": "motion_measurement_observability_replay_not_accuracy", "ground_truth_used": False,
              "source_code_sha256": {name: digest(Path(__file__).resolve().parents[1] / "src" / "rallymate_scoring" / name)
                                     for name in ("process_motion.py", "stroke_analysis.py", "indicator_observations.py")},
              "runs": rows, "limitations_zh": [
                  "本报告复用保留的逐帧姿态和主球员时间线，不等于本次重新推理视频。",
                  "计数单位是动作片段×阶段×测量项，不是独立Word指标数量或评分准确率。",
                  "局部值只描述可见连续子窗口；未确认触球、正式GS阶段或教练等级。"]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
