"""Prepare offline, action-level subject audit inputs without running inference.

This is an audit helper, not a subject classifier. Human labels stay pending until
the source frames are reviewed. Existing frozen reports and live runs are read only.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite audit evidence: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_for(root, row):
    candidates = sorted((root / "service_data/uploads").glob(row["job_id"] + ".*"))
    videos = [p for p in candidates if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".ogv"}]
    if len(videos) != 1:
        raise ValueError(f"Expected one submitted source for {row['sample_id']}, found {videos}")
    return videos[0]


def timeline_runs(rows):
    runs = []
    for r in rows:
        track = r.get("source_track_id")
        if not runs or runs[-1]["source_track_id"] != track:
            runs.append({"source_track_id": track, "start_ms": r["timestamp_ms"],
                         "end_ms": r["timestamp_ms"], "frames": 1})
        else:
            runs[-1]["end_ms"] = r["timestamp_ms"]
            runs[-1]["frames"] += 1
    return runs


def nearest(rows, timestamps, t):
    i = bisect_left(timestamps, t)
    return min(rows[max(0, i - 1):min(len(rows), i + 1)], key=lambda r: abs(r["timestamp_ms"] - t))


def sheet(source, timeline, window, destination, full_frame=False):
    import cv2
    import numpy as np
    lo, peak, hi = (window[k] for k in ("start_ms", "peak_ms", "end_ms"))
    times = [lo, lo + (peak - lo) / 2, max(lo, peak - 150), peak,
             min(hi, peak + 150), peak + (hi - peak) / 2, max(lo, hi - 80), max(lo, hi - 1)]
    stamps = [r["timestamp_ms"] for r in timeline]
    cap = cv2.VideoCapture(str(source))
    cells, evidence = [], []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t))
        ok, frame = cap.read()
        if not ok:
            continue
        row = nearest(timeline, stamps, t)
        box = row.get("bbox_px")
        if box:
            x1, y1, x2, y2 = map(float, box)
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            if not full_frame:
                w, h = x2 - x1, y2 - y1
                left, top = max(0, int(x1 - .85 * w)), max(0, int(y1 - .25 * h))
                right, bottom = min(frame.shape[1], int(x2 + .85 * w)), min(frame.shape[0], int(y2 + .2 * h))
                if right > left and bottom > top:
                    frame = frame[top:bottom, left:right]
        scale = min(400 / frame.shape[1], 270 / frame.shape[0])
        frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)))
        cell = np.zeros((300, 400, 3), dtype=np.uint8)
        cell[:frame.shape[0], :frame.shape[1]] = frame
        cv2.putText(cell, f'{t/1000:.3f}s old track {row.get("source_track_id")}', (5, 291), 0, .55, (255, 255, 255), 1)
        cells.append(cell)
        evidence.append({"requested_timestamp_ms": round(t, 3), "nearest_primary_timestamp_ms": row["timestamp_ms"],
                         "old_source_track_id": row.get("source_track_id")})
    cap.release()
    if not cells:
        return []
    while len(cells) % 4:
        cells.append(np.zeros_like(cells[0]))
    cv2.imwrite(str(destination), np.vstack([np.hstack(cells[i:i+4]) for i in range(0, len(cells), 4)]))
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("docs/evidence/tennis-score-20261026-manifest.json"))
    parser.add_argument("--round2", type=Path, default=Path(".codex_tmp/tennis-score-round2-20261026"))
    parser.add_argument("--output", type=Path, default=Path(".codex_tmp/tennis-score-round3-20261027"))
    parser.add_argument("--sheets", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    args.output.mkdir(parents=True, exist_ok=True)
    sheets_dir = args.output / "action-sheets"
    if args.sheets:
        sheets_dir.mkdir(exist_ok=True)
    old_manifest = read(args.manifest)
    evaluation = read(args.round2 / "after-final/evaluation.json")
    evaluated = {r["sample_id"]: r for r in evaluation["samples"]}
    samples, windows, candidates, inventory = [], [], [], []
    for original in old_manifest["samples"]:
        row = dict(original)
        row["round2_split"] = row["split"]
        row["round2_evaluation_validity"] = row.pop("evaluation_validity", "valid")
        row["round2_include_in_distribution"] = row.pop("include_in_distribution", True)
        row["split"] = "robustness" if row["split"] == "robustness" else "regression"
        row["audit_state"] = "pending_action_level_review"
        row["comparison_policy"] = "preserve_all_raw_outputs; decide subject attribution per action, never by whole-clip exclusion alone"
        source = source_for(root, row)
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        actual = digest.hexdigest()
        expected = row["source_video_sha256"].lower()
        if actual != expected:
            raise ValueError(f"Submitted source SHA changed: {row['sample_id']}")
        frozen = args.round2 / "frozen-runs" / row["job_id"]
        timeline = [json.loads(line) for line in (frozen / "primary-player.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assessment = read(args.round2 / "after-final" / f"{row['sample_id']}-assessment.json")
        samples.append(row)
        inventory.append({"sample_id": row["sample_id"], "job_id": row["job_id"], "source_relative_path": source.relative_to(root).as_posix(),
                          "source_sha256": actual, "sha_matches_round2": True,
                          "frozen_input_relative_path": frozen.as_posix(), "native_pose_exists": (frozen / "sam3d_frames.jsonl").is_file(),
                          "primary_track_runs": timeline_runs(timeline)})
        for index, episode in enumerate(assessment["episodes"]):
            w = {"window_id": f"{row['sample_id']}:{episode['episode_id']}", "sample_id": row["sample_id"],
                 "job_id": row["job_id"], "source_group": row["source_group"], "evaluation_label": row["evaluation_label"],
                 "round2_episode_id": episode["episode_id"], "start_ms": episode["start_ms"], "peak_ms": episode["peak_ms"], "end_ms": episode["end_ms"],
                 "old_source_track_id": episode["source_track_id"], "old_active_arm": episode["active_arm"], "old_family": episode["family"],
                 "old_quality_reason_codes": episode.get("quality_reason_codes", []),
                 "human_subject_id": None, "subject_role": None, "window_validity": "pending_visual_review",
                 "stroke_label": "unreviewed", "stroke_confidence": "unreviewed", "uncertainty": [],
                 "evidence_policy": "visual source frames; no professional label inference and no score-based selection"}
            if args.sheets:
                target = sheets_dir / f"{row['sample_id']}-e{index+1:02d}.jpg"
                w["evidence_frames"] = sheet(source, timeline, w, target)
                w["contact_sheet"] = target.relative_to(args.output).as_posix()
            windows.append(w)
        e = evaluated[row["sample_id"]]
        candidates.append({"sample_id": row["sample_id"], "job_id": row["job_id"], "round2_evaluation_validity": row["round2_evaluation_validity"],
                           "old_candidate_accounting": e["candidate_accounting"], "old_quality_reason_counts": e["quality_reason_counts"],
                           "old_raw_score": e["score"], "old_scored_actions": e["episodes"],
                           "error_scope_note": "Manual clip attribution and automatic candidate rejection are independent observations."})
    write_new(args.output / "manifest.json", {"schema_version": "tennis-round3-regression-manifest-v1", "not_blinded_holdout": True,
              "prior_results_seen": True, "evaluation_unit": "subject_identity_action_window", "no_new_downloads": True,
              "source_round2_manifest_sha256": old_manifest["source_frozen_manifest_sha256"], "samples": samples})
    write_new(args.output / "source-inventory.json", {"sources": inventory})
    write_new(args.output / "subject_windows.json", {"schema_version": "tennis-subject-window-audit-v1", "reviewer": "assistant_visual_audit_not_coach_ground_truth",
              "window_validity_values": ["pending_visual_review", "correct_target", "wrong_target", "ambiguous", "source_clipped"],
              "stroke_label_values": ["baseline_forehand", "baseline_two_hand_backhand", "baseline_one_hand_backhand", "backhand_slice", "forehand_slice", "serve", "unclassified"],
              "windows": windows})
    write_new(args.output / "old-candidate-accounting.json", {"samples": candidates})
    print(json.dumps({"samples": len(samples), "sources_available": len(inventory), "old_scored_windows": len(windows), "gpu_requested": False}))


if __name__ == "__main__":
    main()
