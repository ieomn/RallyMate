"""CPU-only comparison of isolated SAM pipeline artifacts, without accuracy claims."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_sam3d_batches import BINDING_FIELDS, compare_snapshots
from rallymate_vision.pose.metadata import MHR70_NAMES, sha256_file


EVENT_FIELDS = ("event_id", "person_track_id", "event_code", "start_ms", "end_ms",
                "key_phases_ms", "quality_flags", "source_window", "geometry_mask")
SCORE_FIELDS = ("event_id", "person_track_id", "indicator_id", "status", "grade",
                "grade_rule", "reason_codes", "assessment_status", "quality_gate")
FRAME_FIELDS = ("frame", "detections", "subject_identity", "camera_motion")


def rows(path: Path, fields=None) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        result = []
        for line in stream:
            if line.strip():
                row = json.loads(line)
                result.append({key: row.get(key) for key in fields} if fields else row)
        return result


def recursive_changes(old, new) -> dict:
    numeric, other = [], []
    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                other.append({"path": path, "reason": "key_set_changed",
                              "removed": sorted(set(a) - set(b)), "added": sorted(set(b) - set(a))})
            for key in sorted(a.keys() & b.keys()):
                walk(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                other.append({"path": path, "reason": "list_length_changed", "old": len(a), "new": len(b)})
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, path + "/" + str(index))
        elif (isinstance(a, (int, float)) and isinstance(b, (int, float))
              and not isinstance(a, bool) and not isinstance(b, bool)):
            if a != b:
                numeric.append({"path": path, "old": a, "new": b, "abs_delta": abs(a - b)})
        elif a != b:
            other.append({"path": path, "old": a, "new": b})
    walk(old, new, "")
    return {"numeric_changes": len(numeric),
            "largest_numeric_changes": sorted(numeric, key=lambda value: -value["abs_delta"])[:20],
            "nonnumeric_changes": len(other), "nonnumeric_differences": other}


def _verified_artifact(root: Path, artifact: dict, key: str) -> Path:
    relative = Path(artifact[key])
    path = (root / relative).resolve()
    if relative.is_absolute() or root.resolve() not in path.parents:
        raise ValueError("Native mesh path escaped its trial output")
    digest = artifact["sha256" if key == "path" else "faces_sha256"]
    if sha256_file(path).lower() != digest.lower():
        raise ValueError("Native mesh checksum changed")
    return path


def native_snapshot(root: Path, row: dict, faces_cache: dict) -> dict:
    if row.get("status") != "ok":
        raise ValueError("Native inference did not produce complete geometry")
    artifact = row["mesh_artifact"]
    with np.load(_verified_artifact(root, artifact, "path"), allow_pickle=False) as mesh:
        vertices, camera, focal = (np.array(mesh[key], copy=True) for key in ("vertices", "camera", "focal"))
    cache_key = (str(root), artifact["faces_path"], artifact["faces_sha256"])
    if cache_key not in faces_cache:
        faces_cache[cache_key] = np.load(_verified_artifact(root, artifact, "faces_path"), allow_pickle=False)
    coordinates = {}
    for key, dimensions in (("keypoints_2d", 2), ("keypoints_3d", 3)):
        values = row[key]
        if not isinstance(values, dict) or set(values) != set(MHR70_NAMES):
            raise ValueError("Native output must retain every one of the 70 named joints")
        coordinates[key] = np.asarray([values[name] for name in MHR70_NAMES])
        if coordinates[key].shape != (70, dimensions):
            raise ValueError("Native joint dimensions differ from the MHR70 contract")
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not vertices.shape[0]:
        raise ValueError("Full mesh vertices are missing")
    faces = faces_cache[cache_key]
    if (artifact.get("vertex_count") != len(vertices) or artifact.get("face_count") != len(faces)
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer) or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Full mesh topology differs from its retained artifact contract")
    return {"binding": {key: row[key] for key in BINDING_FIELDS}, **coordinates,
            "vertices": vertices, "camera": camera, "focal": focal, "faces": faces}


def compare_native(reference_dir: Path, actual_dir: Path, reference: list, actual: list, guard=lambda: None) -> dict:
    """Read one pair of full meshes at a time; do not retain every mesh in RAM."""
    if not reference or not actual:
        raise ValueError("A pipeline comparison requires successful native subjects")
    per_subject, skipped, faces_cache = [], [], {}
    for index, (old, new) in enumerate(zip(reference, actual)):
        guard()
        a, b = native_snapshot(reference_dir, old, faces_cache), native_snapshot(actual_dir, new, faces_cache)
        if a["binding"] != b["binding"]:
            skipped.append({"index": index, "reason": "source_or_identity_binding_changed"})
            continue
        per_subject.extend(compare_snapshots([a], [b])["per_subject"])
    paired = min(len(reference), len(actual))
    for directory, remaining, reason in ((reference_dir, reference[paired:], "missing_candidate_subject"),
                                         (actual_dir, actual[paired:], "unexpected_candidate_subject")):
        for index, row in enumerate(remaining, start=paired):
            guard()
            native_snapshot(directory, row, faces_cache)
            skipped.append({"index": index, "reason": reason})
    fields = ("keypoints_2d", "keypoints_3d", "vertices", "camera", "focal", "faces")
    complete = len(reference) == len(actual) == len(per_subject)
    return {"subjects": len(actual), "reference_subjects": len(reference),
            "all_subjects_compared": complete, "skipped_subjects": skipped,
            "all_exact": complete and all(item["differences"][name].get("exact", False)
                                           for item in per_subject for name in fields),
            "max_abs_by_field": {name: max((item["differences"][name]["max_abs"] for item in per_subject), default=0.)
                                 if all(item["differences"][name]["shape_equal"] for item in per_subject) else None
                                 for name in fields},
            "per_subject": per_subject, "semantics": "numerical_equivalence_only_not_scoring_accuracy"}


def validate_source_bindings(native: list[dict], frames: list[dict], source: dict) -> None:
    expected = {item[0]["frame_index"]: item[0] for item in source["frames"]}
    if len(frames) != len(expected):
        raise ValueError("Pipeline frame count differs from the bound source prefix")
    for index, row in enumerate(frames):
        frame = row["frame"]
        binding = expected.get(frame["index"])
        if binding is None or frame["processed_index"] != index or frame["index"] != index:
            raise ValueError("Pipeline reordered the bound source frames")
        for key in ("pts", "time_base_num", "time_base_den"):
            if frame[key] != binding[key]:
                raise ValueError("Pipeline frame PTS differs from the existing native archive")
        if frame["video_sha256"].lower() != source["video_sha256"].lower():
            raise ValueError("Pipeline frame source hash differs")
    seen = set()
    previous_index = -1
    for row in native:
        binding = expected.get(row["frame_index"])
        identity = (row["frame_index"], row["person_track_id"])
        if identity in seen or binding is None or row["processed_index"] != row["frame_index"]:
            raise ValueError("Native output duplicated a subject or changed source frames")
        if row["frame_index"] < previous_index:
            raise ValueError("Native output reordered source frames")
        previous_index = row["frame_index"]
        seen.add(identity)
        if row["video_sha256"].lower() != source["video_sha256"].lower():
            raise ValueError("Native source hash differs")
        if any(row[key] != binding[key] for key in ("pts", "time_base_num", "time_base_den", "image_size")):
            raise ValueError("Native PTS or dimensions differ from the bound archive")


def timing_summary(trials: list[dict], comparisons: list[dict]) -> list[dict]:
    counts = {item["trial"]: item for item in comparisons}
    result = []
    for batch in sorted({trial["batch_frames"] for trial in trials}):
        group = [trial for trial in trials if trial["batch_frames"] == batch]
        wall = [trial["caller_wall_seconds"] for trial in group]
        pose = [trial["processing"]["stage_seconds"]["pose_seconds"] for trial in group]
        if any(value <= 0 for value in wall + pose):
            raise ValueError("Timing must be positive")
        result.append({"batch_frames": batch, "trials": len(group),
                       "median_pipeline_wall_seconds": statistics.median(wall),
                       "median_pose_seconds": statistics.median(pose),
                       "median_pipeline_frames_per_second": statistics.median(
                           counts[trial["trial"]]["source_frames"] / elapsed for trial, elapsed in zip(group, wall)),
                       "median_sam_subjects_per_second": statistics.median(
                           counts[trial["trial"]]["successful_subjects"] / elapsed for trial, elapsed in zip(group, pose))})
    return result


def validate_trial_order(probe: dict) -> int:
    # Earlier v2 probes used only candidate 4 and had no explicit candidate field.
    candidate = probe.get("candidate_batch_frames", 4)
    if type(candidate) is not int or candidate not in (4, 8):
        raise ValueError("Pipeline candidate batch frames must be 4 or 8")
    expected = [1, candidate, candidate, 1]
    actual = [trial["batch_frames"] for trial in probe["trials"]]
    if (any(type(value) is not int for value in actual) or actual != expected
            or probe.get("trial_order", expected) != expected):
        raise ValueError("A complete ABBA 1,N,N,1 sequence with the same declared candidate is required")
    return candidate


def compare_pipeline(root: Path, *, guard=lambda: None) -> dict:
    probe = json.loads((root / "probe.json").read_text(encoding="utf-8"))
    trials = probe["trials"]
    candidate = validate_trial_order(probe)
    reference_dir = Path(trials[0]["output_dir"])
    reference = rows(reference_dir / "sam3d_frames.jsonl")
    ref_frames = rows(reference_dir / "frames.jsonl", FRAME_FIELDS)
    ref_events, ref_scores = [rows(reference_dir / name) for name in ("events.jsonl", "scores.jsonl")]
    report = {"schema_version": "sam3d-pipeline-comparison-v1", "trials": [],
              "candidate_batch_frames": candidate,
              "scope": "all_70_joints_full_mesh_source_identity_event_and_score_decision_differences",
              "scoring_accuracy": {"validated": False, "reason": "No independent ground truth; calibration_required never establishes scoring accuracy"}}
    for trial in trials:
        guard()
        path = Path(trial["output_dir"])
        native = rows(path / "sam3d_frames.jsonl")
        frames = rows(path / "frames.jsonl", FRAME_FIELDS)
        events, scores = [rows(path / name) for name in ("events.jsonl", "scores.jsonl")]
        validate_source_bindings(native, frames, probe["source_binding"])
        select = lambda values, keys: [{key: row.get(key) for key in keys} for row in values]
        state = trial.get("scoring_state", {})
        report["trials"].append({"trial": trial["trial"], "batch_frames": trial["batch_frames"],
            "source_frames": len(frames), "successful_subjects": sum(row.get("status") == "ok" for row in native),
            "native_status_counts": dict(Counter(row.get("status", "missing") for row in native)),
            "binding_equal": select(reference, BINDING_FIELDS) == select(native, BINDING_FIELDS),
            "frame_routing_exact": {key: [row.get(key) for row in ref_frames] == [row.get(key) for row in frames] for key in FRAME_FIELDS},
            "geometry": compare_native(reference_dir, path, reference, native, guard),
            "event_count": len(events), "score_count": len(scores),
            "event_core_exact": select(ref_events, EVENT_FIELDS) == select(events, EVENT_FIELDS),
            "score_decision_exact": select(ref_scores, SCORE_FIELDS) == select(scores, SCORE_FIELDS),
            "score_evidence_exact": [row.get("evidence") for row in ref_scores] == [row.get("evidence") for row in scores],
            "event_all_fields": recursive_changes(ref_events, events),
            "score_all_fields": recursive_changes(ref_scores, scores),
            "feature_differences": recursive_changes([row.get("feature") for row in ref_scores], [row.get("feature") for row in scores]),
            "scoring_state": state, "score_status_counts": dict(Counter(row.get("status", "missing") for row in scores)),
            "scoring_accuracy_validated": False})
    report["timing"] = timing_summary(trials, report["trials"])
    report["routing_and_decisions_equal"] = all(
        item["binding_equal"] and all(item["frame_routing_exact"].values()) and item["event_core_exact"]
        and item["score_decision_exact"] for item in report["trials"])
    report["status"] = "comparison_complete_not_scoring_acceptance"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    args = parser.parse_args()
    comparison = compare_pipeline(args.input_root.resolve())
    (args.input_root / "comparison.json").write_text(json.dumps(comparison, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(comparison["timing"], indent=2))
