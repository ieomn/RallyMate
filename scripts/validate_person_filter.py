"""Replay recorded person selection/pose checks without running any model.

This is a regression audit of existing observations, not a pose-accuracy score.
The known background region is specific to the inspected regression video.
Original observations, mesh geometry, and service artifacts are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

from rallymate_vision.sam3d_quality import Sam3DPoseQualityGate
from rallymate_tracking.secondary_subjects import SecondarySubjectSelector


REGRESSION_VIDEO_SHA = "71d3f59b7a8b966ef7645cac412cb03679376f70a2f080d270391a32697d7fa9"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_observations(run):
    observations = {}
    with (run / "frames.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            observations[row["frame"]["index"]] = {
                "frame": row["frame"],
                "primary_track_id": row.get("subject_identity", {}).get("source_track_id"),
                "players": [player for player in row["detections"] if player.get("class_name") == "player"],
                "camera_epoch": row.get("camera_motion", {}).get("reference_epoch", 0),
            }
    return observations


def audit_group(record, observation):
    if record.get("person_track_id") == observation["primary_track_id"]:
        return "primary"
    x1, y1, x2, y2 = record["bbox_px"]
    if (record.get("video_sha256", "").lower() == REGRESSION_VIDEO_SHA
            and 596 <= (x1 + x2) / 2 <= 614 and y2 - y1 < 40):
        return "known_central_background_region"
    return "other_secondary_not_fully_annotated"


def replay_selection(observations, source_video=None):
    selector = SecondarySubjectSelector(max_secondaries=1)
    capture, decoded_index = None, -1
    if source_video:
        import cv2
        capture = cv2.VideoCapture(str(source_video))
        if not capture.isOpened():
            raise ValueError("cannot open original source video")
    selected, receipts = {}, {}
    try:
        for frame_index, observation in observations.items():
            image = None
            if capture is not None:
                while decoded_index < frame_index:
                    ok, image = capture.read()
                    decoded_index += 1
                    if not ok:
                        raise ValueError("source video ended before recorded observations")
            frame = observation["frame"]
            players, receipt = selector.update(observation["players"],
                primary_track_id=observation["primary_track_id"],
                timestamp_ms=frame["timestamp_ms"], width=frame["width"], height=frame["height"],
                image=image, camera_epoch=observation["camera_epoch"])
            selected[frame_index] = {player["track_id"] for player in players}
            receipts[frame_index] = receipt
    finally:
        if capture is not None:
            capture.release()
    return selected, receipts


def validate(run: Path, *, fixture: Path | None = None, source_video: Path | None = None,
             select_without_images=False):
    started = time.perf_counter()
    names = ("frames.jsonl", "sam3d_frames.jsonl", "sam3d-mesh-frames.jsonl")
    input_hashes = {name: sha256(run / name) for name in names}
    quality_path = Path(__file__).parents[1] / "src/rallymate_vision/sam3d_quality.py"
    selector_path = Path(__file__).parents[1] / "src/rallymate_tracking/secondary_subjects.py"
    quality_hash, selector_hash = sha256(quality_path), sha256(selector_path)
    observations = read_observations(run)
    labels = {}
    if fixture:
        for item in json.loads(fixture.read_text(encoding="utf-8"))["labels"]:
            labels[item["frame_index"], item["track_id"]] = item
    selected, selection_receipts = ({}, {})
    if source_video or select_without_images:
        selected, selection_receipts = replay_selection(observations, source_video)
    gate, selected_gate = Sam3DPoseQualityGate(), Sam3DPoseQualityGate()
    counts, reasons = defaultdict(Counter), defaultdict(Counter)
    combined_counts, selection_reasons = defaultdict(Counter), Counter()
    rejected, labelled = [], []
    keep, native_keys = [], set()
    source_sha = None
    with (run / "sam3d_frames.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("status") != "ok":
                continue
            if source_sha is None:
                source_sha = record["video_sha256"]
            if source_sha != record["video_sha256"]:
                raise ValueError("native records contain multiple source videos")
            observation = observations[record["frame_index"]]
            group = audit_group(record, observation)
            receipt = gate.evaluate(record)
            key = (record["frame_index"], record["person_track_id"])
            native_keys.add(key)
            counts[group]["total"] += 1
            counts[group]["kept" if receipt["accepted"] else "rejected"] += 1
            if not receipt["accepted"]:
                reasons[group].update(receipt["reasons"])
                rejected.append({"frame_index": record["frame_index"],
                    "person_track_id": record["person_track_id"], "selection_epoch": record["selection_epoch"],
                    "bbox_px": record["bbox_px"], "group": group, "quality": receipt})
            selected_by_policy, combined_receipt = None, None
            if selected:
                selected_by_policy = record["person_track_id"] in selected[record["frame_index"]]
                if selected_by_policy:
                    combined_receipt = selected_gate.evaluate(record)
                allowed = selected_by_policy and combined_receipt["accepted"]
                combined_counts[group]["total"] += 1
                combined_counts[group]["kept" if allowed else "rejected"] += 1
                combined_counts[group]["selection_rejected" if not selected_by_policy else
                                       "quality_kept" if allowed else "quality_rejected"] += 1
                if allowed:
                    keep.append({name: record[name] for name in (
                        "frame_index", "person_track_id", "selection_epoch", "pts")})
            label = labels.get(key)
            if label:
                labelled.append({**label, "quality": receipt, "selected": selected_by_policy,
                                 "combined_quality": combined_receipt})
    if source_video and sha256(source_video) != source_sha.lower():
        raise ValueError("selection source-video hash does not match native records")
    if input_hashes != {name: sha256(run / name) for name in names}:
        raise ValueError("recorded inputs changed during validation")
    if (quality_hash, selector_hash) != (sha256(quality_path), sha256(selector_path)):
        raise ValueError("policy source changed during validation; rerun against one code revision")
    for frame_receipt in selection_receipts.values():
        selection_reasons.update(item["reason"] for item in frame_receipt["decisions"])
    report = {
        "version": "sam3d-person-filter-validation-v1",
        "source_video_sha256": source_sha,
        "inputs": input_hashes,
        "quality_code_sha256": quality_hash,
        "selection_code_sha256": selector_hash,
        "frame_count": len(observations),
        "quality_counts": dict(counts), "quality_reasons": dict(reasons),
        "labelled_samples": labelled, "quality_rejected": rejected,
        "combined_counts": dict(combined_counts),
        "selection_reasons": dict(selection_reasons),
        "selection_uses_source_images": bool(source_video),
        "selected_without_existing_native_output": sum(
            (frame_index, track) not in native_keys for frame_index, tracks in selected.items() for track in tracks),
        "limitations": [
            "Replays existing model observations; it does not measure model accuracy or run new inference.",
            "Central background region is a source-specific audit label checked in video samples, not a production rule.",
            "Other secondary observations contain genuine distant players, spectators, and other false detections; they are not all true positives.",
            "Frame-specific subject labels were visually checked; native keypoints are estimates, not ground truth.",
            "A newly selected candidate without existing native output cannot be rendered without new inference; this audit never invents that output.",
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    decisions = None
    if selected:
        decisions = {"version": "sam3d-replay-selection-v1", "source_video_sha256": source_sha,
            "inputs": report["inputs"], "keep": keep,
            "policy": {"selection_code_sha256": report["selection_code_sha256"],
                       "quality_code_sha256": report["quality_code_sha256"],
                       "uses_source_images": bool(source_video)},
            "metrics": {"combined_counts": report["combined_counts"],
                        "selected_without_existing_native_output": report["selected_without_existing_native_output"]}}
    return report, decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--source-video", type=Path)
    parser.add_argument("--select-without-images", action="store_true")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    for destination in (args.output, args.decisions):
        if destination is not None and destination.resolve().is_relative_to(run):
            parser.error("validation outputs must be outside the original run directory")
    if args.decisions is not None and args.decisions.resolve() == args.output.resolve():
        parser.error("validation report and replay decisions need distinct paths")
    report, decisions = validate(run, fixture=args.fixture,
        source_video=args.source_video, select_without_images=args.select_without_images)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if args.decisions:
        if decisions is None:
            raise ValueError("decisions require selection replay")
        args.decisions.parent.mkdir(parents=True, exist_ok=True)
        args.decisions.write_text(json.dumps(decisions, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "quality_counts": report["quality_counts"],
                      "combined_counts": report["combined_counts"],
                      "selected_without_native": report["selected_without_existing_native_output"]}))


if __name__ == "__main__":
    main()
