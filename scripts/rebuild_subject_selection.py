"""Rebuild current assessment identity from exact video pixels and saved poses.

Writes only the supported content-bound selection sidecar; original frames,
native joints and formal primary-player artifacts are never rewritten. This is
an algorithmic replay, not a manual identity label or a pose interpolation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import zip_longest
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from rallymate_tracking.subject_lock import APPEARANCE_REVISION, SubjectLock
from rallymate_tracking.primary_player import _frame_candidates
from validate_full_test_scoring import sha256, write_json


def reselected_row(original, record, identity):
    """Keep native identity epochs; source pixels cannot manufacture a pose."""
    row = dict(original)
    candidates = _frame_candidates(record, unique_source=True)
    matches = [candidate for candidate in candidates if candidate["source_track_id"] == identity.get("source_track_id")]
    selected = matches[0] if len(matches) == 1 and identity.get("status") in {"locked", "reidentified"} else None
    binding = selected.get("model_source_binding") if selected else None
    if selected and binding is None:
        identity = SubjectLock.unknown("reselected_target_native_pose_unavailable")
        selected = None
    row.update(subject_identity=identity, source_track_id=selected["source_track_id"] if selected else None,
               selection_status="selected" if selected else "missing", selection_reason_code=identity.get("reason_code"),
               bbox_px=selected["bbox_px"] if selected else None, pose_present=bool(selected and selected["pose"] is not None),
               selection_epoch=binding["selection_epoch"] if selected else None,
               camera_reference_epoch=binding["camera_reference_epoch"] if selected else None,
               selection_best_score=None, selection_second_best_score=None, selection_score_margin=None,
               selection_prior_score=None, selection_competing_source_track_id=None,
               identity_ambiguous=False if selected else None,
               selection_identity_guard=APPEARANCE_REVISION,
               keypoint_valid_fraction=selected["pose_valid_fraction"] if selected else None,
               pose_selection_fraction=selected["pose_selection_fraction"] if selected else None,
               model_geometry_coverage=selected["model_geometry_coverage"] if selected else None)
    return row


def rebuild(run_dir: Path, video: Path, *, output: Path | None = None):
    import cv2
    run_dir, video = run_dir.resolve(), video.resolve()
    source_sha = sha256(video)
    inputs = {name: sha256(run_dir / name) for name in ("frames.jsonl", "sam3d_frames.jsonl", "primary-player.jsonl")}
    output = output or run_dir / "tennis-subject-selection.json"
    if output.is_file():
        prior = json.loads(output.read_text(encoding="utf-8"))
        if prior.get("selection_authority") != "source_pixel_algorithm_replay":
            raise ValueError("An existing manually reviewed selection is preserved; choose a different output")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError("Source video cannot be decoded")
    lock, timeline, tracks, reasons = SubjectLock(), [], Counter(), Counter()
    changed, recovered, started = 0, 0, time.monotonic()
    try:
        with (run_dir / "frames.jsonl").open(encoding="utf-8") as frames, (run_dir / "primary-player.jsonl").open(encoding="utf-8") as primary:
            for expected, (raw, selection) in enumerate(zip_longest(frames, primary)):
                if raw is None or selection is None:
                    raise ValueError("Projected frame and primary timeline lengths differ")
                record, original = json.loads(raw), json.loads(selection)
                f = record["frame"]
                if f["index"] != expected or original["source_frame_index"] != expected or str(f.get("video_sha256", "")).lower() != source_sha:
                    raise ValueError("Replay requires a complete exact-SHA source frame stream")
                ok, image = cap.read()
                if not ok or image.shape[:2] != (f["height"], f["width"]):
                    raise ValueError(f"Original source frame could not be decoded: {expected}")
                identity = lock.update(record.get("detections", []), timestamp_ms=f["timestamp_ms"], width=f["width"], height=f["height"],
                                       image=image, camera_epoch=record.get("camera_motion", {}).get("reference_epoch"),
                                       camera_motion=record.get("camera_motion"))
                row = reselected_row(original, record, identity)
                changed += row["source_track_id"] != original["source_track_id"]
                recovered += row["source_track_id"] is not None and row["source_track_id"] != original["source_track_id"]
                if row["source_track_id"] is not None:
                    tracks[row["source_track_id"]] += 1
                else:
                    reasons[row["selection_reason_code"]] += 1
                timeline.append(row)
        if len(timeline) != int(cap.get(cv2.CAP_PROP_FRAME_COUNT)):
            raise ValueError("Replay does not cover the full original source")
    finally:
        cap.release()
    if inputs != {name: sha256(run_dir / name) for name in inputs}:
        raise ValueError("Inference artifacts changed during identity replay")
    document = {"version": "tennis-subject-selection-v1", "source_video_sha256": source_sha,
                "source_artifact_sha256": {key: inputs[key] for key in ("sam3d_frames.jsonl", "frames.jsonl")},
                "original_primary_sha256": inputs["primary-player.jsonl"],
                "selection_authority": "source_pixel_algorithm_replay", "appearance_revision": APPEARANCE_REVISION,
                "created_utc": datetime.now(timezone.utc).isoformat(), "timeline": timeline,
                "diagnostics": {"frame_count": len(timeline), "changed_selection_frames": changed,
                                "newly_selected_native_frames": recovered, "selected_track_frame_counts": dict(tracks),
                                "missing_identity_reasons": dict(reasons), "elapsed_seconds": round(time.monotonic() - started, 3)}}
    write_json(output, document)
    return {"sidecar": str(output), "source_video_sha256": source_sha, "appearance_revision": APPEARANCE_REVISION, **document["diagnostics"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.run_dir, args.video, output=args.output), indent=2))


if __name__ == "__main__":
    main()
