"""Replay a frozen whole-clip crop-detector experiment, without rerunning YOLO.

The supplemental detections are observations, not a score or confirmed stroke.
Original inputs remain immutable. Run from the intended committed source tree.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rallymate_scoring.stroke_candidates import detect_stroke_candidates
from rallymate_service.gs_card_execution import _decode_video_timeline
from replay_card_video_validation import match_action_windows

VERSION = "primary-person-square-crop-feasibility-v1"


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def merge_crop_observations(records, primary, supplemental, timeline):
    """Verify full source order and crop coordinates before appending copies."""
    if not records or len(records) != len(primary) or len(records) != len(supplemental):
        raise ValueError("crop_source_inventory_mismatch")
    if any(not _integer(row.get("frame_index")) for row in timeline):
        raise ValueError("crop_decoder_frame_invalid")
    decoded = {row["frame_index"]: row for row in timeline}
    if len(decoded) != len(timeline):
        raise ValueError("crop_decoder_duplicate_frame")
    augmented = deepcopy(records)
    previous_frame = previous_time = previous_processed = None
    for original, output, selected, crop in zip(records, augmented, primary, supplemental):
        f = original["frame"]
        index, processed = f.get("index"), f.get("processed_index")
        width, height = f.get("width"), f.get("height")
        if (not _integer(index) or not _integer(processed) or not _integer(width) or width <= 0
                or not _integer(height) or height <= 0 or f.get("timestamp_source") != "decoder_pts"
                or index not in decoded or previous_frame is not None and index != previous_frame + 1
                or previous_processed is not None and processed != previous_processed + 1):
            raise ValueError("crop_original_frame_invalid")
        d = decoded[index]
        if (not _integer(d.get("pts")) or not _integer(d.get("time_base_num")) or d["time_base_num"] <= 0
                or not _integer(d.get("time_base_den")) or d["time_base_den"] <= 0):
            raise ValueError("crop_decoder_clock_invalid")
        actual = d["pts"] * d["time_base_num"] * 1000 / d["time_base_den"]
        if (any(not _integer(obj.get(key)) for obj, keys in (
                    (selected, ("source_frame_index", "processed_index", "source_track_id")),
                    (crop, ("source_frame_index", "processed_index", "source_track_id", "pts", "time_base_num", "time_base_den")))
                for key in keys)
                or not _finite(selected.get("timestamp_ms")) or not _finite(crop.get("source_timestamp_ms"))
                or not _finite(f.get("timestamp_ms")) or abs(actual - f["timestamp_ms"]) > .51
                or previous_time is not None and actual <= previous_time
                or selected.get("source_frame_index") != index or selected.get("processed_index") != processed
                or selected.get("timestamp_ms") != f["timestamp_ms"]
                or crop.get("source_frame_index") != index or crop.get("processed_index") != processed
                or crop.get("source_timestamp_ms") != actual
                or any(crop.get(key) != d[key] for key in ("pts", "time_base_num", "time_base_den"))):
            raise ValueError("crop_source_clock_mismatch")
        previous_frame, previous_time, previous_processed = index, actual, processed
        if (selected.get("selection_status") != "selected" or selected.get("identity_ambiguous") is True
                or not _integer(selected.get("source_track_id"))
                or crop.get("source_track_id") != selected["source_track_id"]):
            raise ValueError("crop_primary_binding_invalid")
        box = selected.get("bbox_px")
        if (not isinstance(box, list) or len(box) != 4 or not all(_finite(x) for x in box)
                or min(box[2] - box[0], box[3] - box[1]) < 20
                or crop.get("original_primary_bbox_px") != box or crop.get("status") != "inferred"):
            raise ValueError("crop_primary_box_invalid")
        size = max(box[2] - box[0], box[3] - box[1])
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        expected = [max(0, math.floor(cx - size)), max(0, math.floor(cy - size)),
                    min(width, math.ceil(cx + size)), min(height, math.ceil(cy + size))]
        if crop.get("crop_bbox_px") != expected or expected[0] >= expected[2] or expected[1] >= expected[3]:
            raise ValueError("crop_extent_mismatch")
        detections = crop.get("detections")
        if not isinstance(detections, list):
            raise ValueError("crop_detection_inventory_invalid")
        for detection in detections:
            local, full = detection.get("crop_local_bbox_px"), detection.get("bbox_px")
            if (detection.get("source") != VERSION or type(detection.get("class_id")) is not int
                    or detection["class_id"] != 38 or detection.get("class_name") != "racket"
                    or not _finite(detection.get("confidence")) or not .25 <= detection["confidence"] <= 1
                    or detection.get("crop_bbox_px") != expected
                    or not isinstance(local, list) or len(local) != 4 or not all(_finite(x) for x in local)
                    or not isinstance(full, list) or len(full) != 4 or not all(_finite(x) for x in full)
                    or full != [local[0] + expected[0], local[1] + expected[1], local[2] + expected[0], local[3] + expected[1]]
                    or not 0 <= local[0] < local[2] <= expected[2] - expected[0]
                    or not 0 <= local[1] < local[3] <= expected[3] - expected[1]):
                raise ValueError("crop_detection_mapping_invalid")
        output["detections"].extend(deepcopy(detections))
    return augmented


def replay(evidence, artifact_root):
    plan = evidence["plan"]
    if (evidence.get("version") != VERSION or plan.get("version") != VERSION
            or plan.get("imgsz") != 640 or plan.get("conf") != .25 or plan.get("iou") != .7
            or plan.get("classes") != [38] or plan.get("device") != "cpu"):
        raise ValueError("crop_experiment_policy_mismatch")
    root = Path(artifact_root).resolve()
    case = evidence["source_case"]
    required = {case["local_video"], "models/yolo26n.pt", "tests/fixtures/card-video-cases-heldout-20261022.json",
                case["run_dir"] + "/frames.jsonl", case["run_dir"] + "/primary-player.jsonl"}
    normalized_names = [name.replace("\\", "/") for name in plan["source_inputs"]]
    if len(normalized_names) != len(set(normalized_names)) or set(normalized_names) != required:
        raise ValueError("crop_required_input_digests_missing")
    for name, digest in plan["source_inputs"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or _sha(path) != digest:
            raise ValueError("crop_original_input_digest_mismatch")
    manifest = json.loads((root / "tests/fixtures/card-video-cases-heldout-20261022.json").read_text(encoding="utf-8"))
    source_case = next((row for row in manifest["cases"] if row["id"] == case["id"]), None)
    if not source_case or any(case.get(key) != source_case.get(key) for key in case):
        raise ValueError("crop_case_manifest_mismatch")
    run, video = root / case["run_dir"], root / case["local_video"]
    summary_path = run / "summary.json"
    if _sha(summary_path) != evidence["replay_summary_sha256"]:
        raise ValueError("crop_replay_summary_mismatch")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    def rows(name): return [json.loads(line) for line in (run / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    original, primary = rows("frames.jsonl"), rows("primary-player.jsonl")
    if (not summary.get("job_id") or any(row.get("job_id") != summary["job_id"] for row in original)
            or any("job_id" in row and row["job_id"] != summary["job_id"] for row in primary)):
        raise ValueError("crop_source_job_mismatch")
    timeline = _decode_video_timeline(video, case["video_sha256"])
    augmented = merge_crop_observations(original, primary, evidence["supplemental_observations"], timeline)
    result = {"version": VERSION, "scope": "saved_crop_inference_replay_not_new_inference",
              "source_frame_count": len(original), "supplemental_racket_boxes": sum(len(x["detections"]) for x in evidence["supplemental_observations"]),
              "original_grade": None, "score": None, "source_case_id": case["id"]}
    for name, records in (("baseline", original), ("crop", augmented)):
        recognition = detect_stroke_candidates(records, primary_timeline=primary)
        candidates = recognition["candidates"] + recognition.get("review_candidates", [])
        result[name] = {"primary_candidates": recognition["candidates"], "secondary_candidates": recognition.get("review_candidates", []),
                        "matching": match_action_windows(candidates, case["annotations"])}
    if (_sha(summary_path) != evidence["replay_summary_sha256"]
            or any(_sha(root / name) != digest for name, digest in plan["source_inputs"].items())):
        raise ValueError("crop_original_input_changed")
    result["original_inputs_unchanged"] = True
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "tests/fixtures/racket-crop-development-atp-20261024.json")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output already exists; preserve earlier evidence.")
    report = replay(json.loads(args.evidence.read_text(encoding="utf-8")), args.artifact_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "crop_matches": report["crop"]["matching"]["matched_count"]}))
