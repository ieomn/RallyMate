"""Replay the complete Word-card response from already inferred local videos.

No model inference and no human technical-grade ground truth are involved.
The report measures observable evidence and rule coverage, never accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_service.source_assessment import load_source_aligned_assessment
from rallymate_service.user_demo import load_indicator_feature_records


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", type=Path, default=Path("reports/model-comparison-20261015"))
    parser.add_argument("--runs-root", type=Path, default=Path("service_data/runs"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/word-indicator-assessment-20261015"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for replay_path in sorted(args.replay_dir.glob("*-after-source-measurements.json")):
        run_id = replay_path.name.removesuffix("-after-source-measurements.json")
        run_dir = args.runs_root / run_id
        summary = read(run_dir / "summary.json")
        summary["job_id"] = run_id
        loop = summary.get("minimum_scoring_loop", {})
        provenance = loop.get("provenance", {})
        feature_path = run_dir / "indicator-features.jsonl"
        records = load_indicator_feature_records(feature_path) if feature_path.exists() and feature_path.stat().st_size else []
        source = load_source_aligned_assessment(run_dir, video_id=loop.get("video_id") or run_id,
            duration_ms=summary.get("input", {}).get("video", {}).get("duration_ms"),
            video_sha256=provenance.get("video_sha256"), frames_sha256=provenance.get("frames_sha256"))
        before_path = args.replay_dir / f"{run_id}-before-source-measurements.json"
        before = None
        if before_path.exists():
            summary["action_recognition"] = read(before_path)
            before = build_indicator_assessment(summary, records, source_assessment=source)["counts"]
        summary["action_recognition"] = read(replay_path)
        after = build_indicator_assessment(summary, records, source_assessment=source)
        write(args.output_dir / f"{run_id}-indicator-assessment.json", after)
        report = {"run_id": run_id, "source_replay_sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
                  "source_aligned_status": source["status"], "source_aligned_reason_zh": source["reason_zh"],
                  "before": before, "after": after["counts"],
                  "observation_summaries_scope": "returned_display_observations_only",
                  "observation_statuses": dict(Counter(o["status"] for o in after["observations"])),
                  "observation_scopes": dict(Counter(o["scope"] for o in after["observations"])),
                  "full_response_filename": f"{run_id}-indicator-assessment.json"}
        reports.append(report)
        print(json.dumps({"run_id": run_id, "counts": after["counts"]}, ensure_ascii=False))
    write(args.output_dir / "summary.json", {
        "version": "word-indicator-assessment-replay-v1", "ground_truth_used": False,
        "semantics": "observable_measurements_and_source_rule_states_not_technical_accuracy",
        "code_sha256": {name: hashlib.sha256(Path("src/rallymate_scoring", name).read_bytes()).hexdigest()
                        for name in ("indicator_assessment.py", "indicator_observations.py")},
        "runs": reports,
        "limitations_zh": ["相同视频的已推理帧用于新旧测量重放，没有重新用模型打分。",
                           "没有人工评分真值，不能将更多可测项或更少空值解释为更高技术评分准确率。",
                           "source_aligned_measured、legacy_measured和contextual_measured分别统计，彼此可能重叠，不相加成已通过的卡片数。"]})


if __name__ == "__main__":
    main()
