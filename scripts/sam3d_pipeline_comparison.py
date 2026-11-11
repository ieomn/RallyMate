"""CPU-only comparison of isolated SAM pipeline artifacts, without accuracy claims."""
from __future__ import annotations

import argparse
from collections import Counter
from itertools import zip_longest
import heapq
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
MAX_JSONL_ROW_BYTES = 64 * 1024 * 1024
MAX_DIFFERENCE_EXAMPLES = 100
MAX_SUBJECT_EXAMPLES = 256
MAX_FACES_CACHE_BYTES = 32 * 1024 * 1024
MAX_FACES_CACHE_ENTRIES = 8
_MISSING = object()


def rows(path: Path, fields=None, guard=lambda: None):
    """Read every row, retaining only the current bounded JSON object."""
    with path.open("rb") as stream:
        while True:
            guard()
            line = stream.readline(MAX_JSONL_ROW_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_JSONL_ROW_BYTES:
                raise ValueError("Comparison JSONL row exceeds the explicit 64 MiB limit")
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Comparison JSONL row must be an object")
                yield {key: row.get(key) for key in fields} if fields else row


class Changes:
    """Exact full-walk counts with bounded diagnostic examples, never sampling."""
    def __init__(self, guard=lambda: None):
        self.numeric_count = self.other_count = self.visited = 0
        self.numeric, self.other = [], []
        self.values_truncated = False
        self.guard = guard

    def preview(self, value):
        if isinstance(value, (dict, list)):
            self.values_truncated = True
            return {"value_type": type(value).__name__, "length": len(value), "value_truncated": True}
        if isinstance(value, str) and len(value) > 1024:
            self.values_truncated = True
            return {"prefix": value[:1024], "length": len(value), "value_truncated": True}
        return value

    def difference(self, value):
        self.other_count += 1
        if len(self.other) < MAX_DIFFERENCE_EXAMPLES:
            self.other.append(value)

    def walk(self, a, b, path=""):
        self.visited += 1
        if self.visited % 4096 == 0:
            self.guard()
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                removed, added = sorted(set(a) - set(b)), sorted(set(b) - set(a))
                self.values_truncated |= len(removed) > 20 or len(added) > 20
                self.difference({"path": path, "reason": "key_set_changed",
                    "removed": removed[:20], "added": added[:20], "removed_count": len(removed),
                    "added_count": len(added), "keys_truncated": len(removed) > 20 or len(added) > 20})
            for key in sorted(a.keys() & b.keys()):
                self.walk(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                self.difference({"path": path, "reason": "list_length_changed", "old": len(a), "new": len(b)})
            for index, (x, y) in enumerate(zip(a, b)):
                self.walk(x, y, path + "/" + str(index))
        elif (isinstance(a, (int, float)) and isinstance(b, (int, float))
              and not isinstance(a, bool) and not isinstance(b, bool)):
            if a != b:
                self.numeric_count += 1
                delta = abs(a - b)
                value = {"path": path, "old": a, "new": b, "abs_delta": delta}
                candidate = (delta, -self.numeric_count, value)
                if len(self.numeric) < 20:
                    heapq.heappush(self.numeric, candidate)
                elif candidate[:2] > self.numeric[0][:2]:
                    heapq.heapreplace(self.numeric, candidate)
        elif a != b:
            self.difference({"path": path, "old": self.preview(a), "new": self.preview(b)})

    def result(self):
        return {"numeric_changes": self.numeric_count,
            "largest_numeric_changes": [row[2] for row in sorted(self.numeric, key=lambda row: (-row[0], -row[1]))],
            "nonnumeric_changes": self.other_count, "nonnumeric_differences": self.other,
            "examples_truncated": self.numeric_count > 20 or self.other_count > MAX_DIFFERENCE_EXAMPLES or self.values_truncated,
            "diagnostic_values_truncated": self.values_truncated,
            "nonnumeric_examples_truncated": self.other_count > MAX_DIFFERENCE_EXAMPLES,
            "comparison_scope": "all_values_exact_counts_bounded_examples"}


def recursive_changes(old, new) -> dict:
    changes = Changes()
    changes.walk(old, new)
    return changes.result()


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
        loaded = np.load(_verified_artifact(root, artifact, "faces_path"), allow_pickle=False)
        while faces_cache and (len(faces_cache) >= MAX_FACES_CACHE_ENTRIES or
                sum(value.nbytes for value in faces_cache.values()) + loaded.nbytes > MAX_FACES_CACHE_BYTES):
            faces_cache.pop(next(iter(faces_cache)))
        if loaded.nbytes <= MAX_FACES_CACHE_BYTES:
            faces_cache[cache_key] = loaded
        faces = loaded
    else:
        faces = faces_cache[cache_key]
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
    if (artifact.get("vertex_count") != len(vertices) or artifact.get("face_count") != len(faces)
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer) or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Full mesh topology differs from its retained artifact contract")
    return {"binding": {key: row[key] for key in BINDING_FIELDS}, **coordinates,
            "vertices": vertices, "camera": camera, "focal": focal, "faces": faces}


def compare_native(reference_dir: Path, actual_dir: Path, reference, actual, guard=lambda: None) -> dict:
    """Read one pair of full meshes at a time; do not retain every mesh in RAM."""
    reference = (row for row in reference if row.get("status") == "ok")
    actual = (row for row in actual if row.get("status") == "ok")
    per_subject, skipped, faces_cache = [], [], {}
    fields = ("keypoints_2d", "keypoints_3d", "vertices", "camera", "focal", "faces")
    reference_count = actual_count = compared_count = skipped_count = 0
    maxima = dict.fromkeys(fields, 0.)
    shape_equal = dict.fromkeys(fields, True)
    exact = True
    for index, (old, new) in enumerate(zip_longest(reference, actual, fillvalue=_MISSING)):
        guard()
        reference_count += old is not _MISSING
        actual_count += new is not _MISSING
        if old is _MISSING or new is _MISSING:
            directory, row, reason = ((actual_dir, new, "unexpected_candidate_subject") if old is _MISSING
                                     else (reference_dir, old, "missing_candidate_subject"))
            native_snapshot(directory, row, faces_cache)
            skipped_count += 1
            if len(skipped) < MAX_SUBJECT_EXAMPLES:
                skipped.append({"index": index, "reason": reason})
            continue
        a, b = native_snapshot(reference_dir, old, faces_cache), native_snapshot(actual_dir, new, faces_cache)
        if a["binding"] != b["binding"]:
            skipped_count += 1
            if len(skipped) < MAX_SUBJECT_EXAMPLES:
                skipped.append({"index": index, "reason": "source_or_identity_binding_changed"})
            del a, b
            continue
        result = compare_snapshots([a], [b])["per_subject"][0]
        del a, b
        compared_count += 1
        for name in fields:
            difference = result["differences"][name]
            shape_equal[name] &= difference["shape_equal"]
            exact &= difference.get("exact", False)
            maxima[name] = max(maxima[name], difference.get("max_abs", 0.))
        if len(per_subject) < MAX_SUBJECT_EXAMPLES:
            per_subject.append(result)
    complete = reference_count == actual_count == compared_count
    return {"subjects": actual_count, "reference_subjects": reference_count,
            "all_subjects_compared": complete, "skipped_subjects": skipped,
            "compared_subjects": compared_count, "skipped_subject_count": skipped_count,
            "examples_truncated": compared_count > len(per_subject) or skipped_count > len(skipped),
            "per_subject_examples_truncated": compared_count > len(per_subject),
            "skipped_examples_truncated": skipped_count > len(skipped),
            "all_exact": complete and exact,
            "max_abs_by_field": {name: maxima[name] if shape_equal[name] else None for name in fields},
            "per_subject": per_subject, "semantics": "numerical_equivalence_only_not_scoring_accuracy"}


def validate_source_bindings(native, frames, source: dict) -> None:
    expected = {item[0]["frame_index"]: item[0] for item in source["frames"]}
    start = source.get("start_frame", 0)
    frame_count = 0
    for index, row in enumerate(frames):
        frame_count += 1
        frame = row["frame"]
        binding = expected.get(frame["index"])
        if binding is None or frame["processed_index"] != index or frame["index"] != start + index:
            raise ValueError("Pipeline reordered the bound source frames")
        for key in ("pts", "time_base_num", "time_base_den"):
            if frame[key] != binding[key]:
                raise ValueError("Pipeline frame PTS differs from the existing native archive")
        if frame["video_sha256"].lower() != source["video_sha256"].lower():
            raise ValueError("Pipeline frame source hash differs")
    if frame_count != len(expected):
        raise ValueError("Pipeline frame count differs from the bound source prefix")
    seen = set()
    native_frames = set()
    previous_index = -1
    for row in native:
        binding = expected.get(row["frame_index"])
        identity = (row["frame_index"], row["person_track_id"])
        if identity in seen or binding is None or row["processed_index"] != row["frame_index"] - start:
            raise ValueError("Native output duplicated a subject or changed source frames")
        if row["frame_index"] < previous_index:
            raise ValueError("Native output reordered source frames")
        previous_index = row["frame_index"]
        seen.add(identity)
        native_frames.add(row["frame_index"])
        if row["video_sha256"].lower() != source["video_sha256"].lower():
            raise ValueError("Native source hash differs")
        if any(row[key] != binding[key] for key in ("pts", "time_base_num", "time_base_den", "image_size")):
            raise ValueError("Native PTS or dimensions differ from the bound archive")
    if native_frames != set(expected):
        raise ValueError("Native output omitted a bound source frame, including empty frames")


def timing_summary(trials: list[dict], comparisons: list[dict]) -> list[dict]:
    counts = {item["trial"]: item for item in comparisons}
    result = []
    key = lambda item: (item["batch_frames"], item.get("pipeline_overlap", 0), item.get("archive_pending_frames", 2))
    for batch, overlap, pending in sorted({key(trial) for trial in trials}):
        group = [trial for trial in trials if key(trial) == (batch, overlap, pending)]
        wall = [trial["caller_wall_seconds"] for trial in group]
        pose = [trial["processing"]["stage_seconds"]["pose_seconds"] for trial in group]
        if any(value <= 0 for value in wall + pose):
            raise ValueError("Timing must be positive")
        result.append({"batch_frames": batch, "trials": len(group),
                       "pipeline_overlap": overlap, "archive_pending_frames": pending,
                       "median_pipeline_wall_seconds": statistics.median(wall),
                       "median_pose_seconds": statistics.median(pose),
                       "median_pipeline_frames_per_second": statistics.median(
                           counts[trial["trial"]]["source_frames"] / elapsed for trial, elapsed in zip(group, wall)),
                       "median_sam_subjects_per_second": statistics.median(
                           counts[trial["trial"]]["successful_subjects"] / elapsed for trial, elapsed in zip(group, pose))})
        if all("sam_request_wall_seconds" in trial["processing"].get("pipeline_overlap", {}) for trial in group):
            result[-1]["median_sam_request_wall_seconds"] = statistics.median(
                trial["processing"]["pipeline_overlap"]["sam_request_wall_seconds"] for trial in group)
        if all("foreground_wait_seconds" in trial["processing"].get("mesh_archive", {}) for trial in group):
            result[-1]["median_archive_foreground_wait_seconds"] = statistics.median(
                trial["processing"]["mesh_archive"]["foreground_wait_seconds"] for trial in group)
    return result


def validate_trial_order(probe: dict) -> int:
    # Earlier v2 probes used only candidate 4 and had no explicit candidate field.
    candidate = probe.get("candidate_batch_frames", 4)
    if type(candidate) is not int or candidate not in (4, 8):
        raise ValueError("Pipeline candidate batch frames must be 4 or 8")
    default = [1, candidate, candidate, 1]
    expected = probe.get("trial_order", default)
    actual = [trial["batch_frames"] for trial in probe["trials"]]
    if probe.get("schedule_abba"):
        from scripts.benchmark_sam3d_pipeline import scheduling_trials
        pending = probe.get("experiment_configuration", {}).get("archive_pending_frames", 2)
        configurations = scheduling_trials(probe["schedule_abba"], candidate, pending)
        observed = [{key: trial.get(key) for key in configurations[0]} for trial in probe["trials"]]
        if (actual != [candidate] * 4 or expected != actual
                or probe.get("trial_configurations") != configurations or observed != configurations):
            raise ValueError("A complete fixed-batch scheduling ABBA matching the declared candidate is required")
        return candidate
    allowed = ([1, candidate], [candidate, 1], default, [candidate, 1, 1, candidate])
    if (not isinstance(expected, list) or expected not in allowed
            or any(type(value) is not int for value in actual + expected) or actual != expected):
        raise ValueError("A complete declared AB, BA, ABBA or BAAB sequence with the same candidate is required")
    return candidate


def compare_rows(reference, actual, *, projections, differences=None, guard=lambda: None):
    """Compare aligned streams to exhaustion, including every extra/missing row."""
    exact = dict.fromkeys(projections, True)
    changes = {name: Changes(guard) for name in (differences or {})}
    reference_count = actual_count = 0
    statuses = Counter()
    for index, (old, new) in enumerate(zip_longest(reference, actual, fillvalue=_MISSING)):
        guard()
        reference_count += old is not _MISSING
        actual_count += new is not _MISSING
        if new is not _MISSING:
            statuses[new.get("status", "missing")] += 1
        if old is _MISSING or new is _MISSING:
            exact = dict.fromkeys(projections, False)
            continue
        for name, select in projections.items():
            # Do not short-circuit reading or deep-diff accounting after a mismatch.
            exact[name] &= select(old) == select(new)
        for name, select in (differences or {}).items():
            changes[name].walk(select(old), select(new), "/" + str(index))
    if reference_count != actual_count:
        for change in changes.values():
            change.difference({"path": "", "reason": "list_length_changed", "old": reference_count, "new": actual_count})
    return {"reference_count": reference_count, "actual_count": actual_count,
            "exact": exact, "differences": {name: change.result() for name, change in changes.items()},
            "status_counts": dict(statuses)}


def compare_pipeline(root: Path, *, guard=lambda: None) -> dict:
    probe = json.loads((root / "probe.json").read_text(encoding="utf-8"))
    trials = probe["trials"]
    candidate = validate_trial_order(probe)
    reference_dir = Path(trials[0]["output_dir"])
    report = {"schema_version": "sam3d-pipeline-comparison-v1", "trials": [],
              "candidate_batch_frames": candidate,
              "trial_order": [trial["batch_frames"] for trial in trials],
              "schedule_abba": probe.get("schedule_abba"),
              "sampling_design": "paired_single_trials_not_repeatability" if len(trials) == 2 else "balanced_repeated_trials",
              "scope": "all_70_joints_full_mesh_source_identity_event_and_score_decision_differences",
              "memory_policy": {"jsonl": "streamed_pairwise_all_rows_no_sampling", "max_jsonl_row_bytes": MAX_JSONL_ROW_BYTES,
                  "mesh": "one_full_geometry_pair", "faces_cache_bytes": MAX_FACES_CACHE_BYTES,
                  "faces_cache_entries": MAX_FACES_CACHE_ENTRIES,
                  "subject_examples_per_trial": MAX_SUBJECT_EXAMPLES, "nonnumeric_examples_per_comparison": MAX_DIFFERENCE_EXAMPLES,
                  "retained_source_binding": "probe_source_frame_metadata_not_full_native_geometry",
                  "difference_counts_and_maxima": "exact_over_all_compared_values"},
              "scoring_accuracy": {"validated": False, "reason": "No independent ground truth; calibration_required never establishes scoring accuracy"}}
    for trial in trials:
        guard()
        path = Path(trial["output_dir"])
        read = lambda directory, name, fields=None: rows(directory / name, fields, guard)
        validate_source_bindings(read(path, "sam3d_frames.jsonl"), read(path, "frames.jsonl", FRAME_FIELDS), probe["source_binding"])
        project = lambda keys: lambda row: {key: row.get(key) for key in keys}
        native = compare_rows(read(reference_dir, "sam3d_frames.jsonl"), read(path, "sam3d_frames.jsonl"), guard=guard,
            projections={"status": project(("frame_index", "person_track_id", "status", "reason")), "binding": project(BINDING_FIELDS)})
        frames = compare_rows(read(reference_dir, "frames.jsonl", FRAME_FIELDS), read(path, "frames.jsonl", FRAME_FIELDS), guard=guard,
            projections={key: (lambda row, key=key: row.get(key)) for key in FRAME_FIELDS})
        events = compare_rows(read(reference_dir, "events.jsonl"), read(path, "events.jsonl"), guard=guard,
            projections={"core": project(EVENT_FIELDS)}, differences={"all": lambda row: row})
        scores = compare_rows(read(reference_dir, "scores.jsonl"), read(path, "scores.jsonl"), guard=guard,
            projections={"decision": project(SCORE_FIELDS), "evidence": lambda row: row.get("evidence")},
            differences={"all": lambda row: row, "feature": lambda row: row.get("feature")})
        state = trial.get("scoring_state", {})
        report["trials"].append({"trial": trial["trial"], "batch_frames": trial["batch_frames"],
            "pipeline_overlap": trial.get("pipeline_overlap", 0),
            "archive_pending_frames": trial.get("archive_pending_frames", 2),
            "source_frames": frames["actual_count"], "successful_subjects": native["status_counts"].get("ok", 0),
            "native_status_counts": native["status_counts"],
            "native_status_exact": native["exact"]["status"],
            "binding_equal": native["exact"]["binding"],
            "frame_routing_exact": frames["exact"],
            "geometry": compare_native(reference_dir, path, read(reference_dir, "sam3d_frames.jsonl"), read(path, "sam3d_frames.jsonl"), guard),
            "event_count": events["actual_count"], "score_count": scores["actual_count"],
            "event_core_exact": events["exact"]["core"],
            "score_decision_exact": scores["exact"]["decision"],
            "score_evidence_exact": scores["exact"]["evidence"],
            "event_all_fields": events["differences"]["all"],
            "score_all_fields": scores["differences"]["all"],
            "feature_differences": scores["differences"]["feature"],
            "scoring_state": state, "score_status_counts": scores["status_counts"],
            "scoring_accuracy_validated": False})
    report["timing"] = timing_summary(trials, report["trials"])
    report["routing_and_decisions_equal"] = all(
        item["binding_equal"] and item["native_status_exact"] and all(item["frame_routing_exact"].values()) and item["event_core_exact"]
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
