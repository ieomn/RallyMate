"""CPU-only, fixed-event diagnostic for the opt-in continuous derivative.

Run with ``python -m rallymate_evaluation.kinematic_continuity --run-dir ...
--output ...``. Source artifacts and the production scoring path are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_features.coordinates import point_series, pose_sequence_from_records
from rallymate_features.kinematics import (
    CONTIGUOUS_DERIVATIVE_VERSION,
    contiguous_irregular_derivative,
    irregular_derivative,
)
from rallymate_features.schemas import PoseSequence
from rallymate_features.smoothing import smooth_series
from rallymate_features.temporal import MAX_CONTIGUOUS_GAP_MS


REPORT_VERSION = "fixed-event-kinematic-continuity-diagnostic-v1.0.0"
JOINTS = ("left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle")


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _peak(velocity: np.ndarray) -> float | None:
    speeds = np.linalg.norm(velocity, axis=1)
    finite = speeds[np.isfinite(speeds)]
    return float(finite.max()) if finite.size else None


def _compare(times: np.ndarray, frames: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    baseline = irregular_derivative(times, values)
    candidate = contiguous_irregular_derivative(times, values)
    changed = np.flatnonzero(~np.isclose(baseline, candidate, rtol=1e-12, atol=1e-12, equal_nan=True).all(axis=1))
    finite = np.flatnonzero(np.isfinite(values).all(axis=1))
    evidence = []
    for index in changed:
        position = int(np.searchsorted(finite, index))
        left = int(finite[max(0, position - 1)])
        right = int(finite[min(len(finite) - 1, position + 1)])
        evidence.append({
            "source_frame": int(frames[index]), "timestamp_ms": int(times[index]),
            "baseline_velocity_xy": [_finite(v) for v in baseline[index]],
            "candidate_velocity_xy": [_finite(v) for v in candidate[index]],
            "baseline_stencil_source_frames": [int(frames[left]), int(frames[right])],
            "baseline_stencil_timestamp_ms": [int(times[left]), int(times[right])],
            "baseline_stencil_crosses_missing_samples": right - left > (int(right != index) + int(left != index)),
            "baseline_stencil_crosses_long_interval": bool(np.any(np.diff(times[left:right + 1]) > MAX_CONTIGUOUS_GAP_MS)),
        })
    return {
        "changed_sample_count": len(evidence),
        "baseline_finite_sample_count": int(np.isfinite(baseline).all(axis=1).sum()),
        "candidate_finite_sample_count": int(np.isfinite(candidate).all(axis=1).sum()),
        "baseline_peak_image_speed": _peak(baseline),
        "candidate_peak_image_speed": _peak(candidate),
        "changed_samples": evidence,
    }


def evaluate_kinematic_continuity(sequence: PoseSequence, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the same joint series inside the same saved event boundaries.

    Each event is sliced before smoothing or differentiation so neither branch
    borrows samples from outside that event. These image speeds are diagnostic
    values, not the production's body-normalized feature implementations.
    """
    rows = []
    for event in events:
        start, end = event.get("start_ms"), event.get("end_ms")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not np.isfinite([start, end]).all() or end <= start):
            raise ValueError("fixed events require finite increasing boundaries")
        if event.get("person_track_id") != sequence.primary_player_id:
            raise ValueError("fixed events must belong to the input primary player")
        indexes = np.flatnonzero((sequence.timestamp_ms >= start) & (sequence.timestamp_ms <= end))
        times, frames = sequence.timestamp_ms[indexes], sequence.source_frames[indexes]
        comparisons = {}
        for joint in JOINTS:
            raw, _ = point_series(sequence, joint)
            raw = raw[indexes]
            # The second branch isolates derivative behavior on the EXISTING
            # smoother's output; smoothed values are not extra observations.
            modes = {"raw_observations": raw, "existing_smoother": smooth_series(times, raw) if len(times) else raw}
            for mode, values in modes.items():
                comparisons[f"{joint}/{mode}"] = _compare(times, frames, values)
        rows.append({
            "event_id": event.get("event_id"), "event_code": event.get("event_code"),
            "start_ms": start, "end_ms": end, "key_phases_ms": event.get("key_phases_ms"),
            "observed_sample_count": len(indexes), "comparisons": comparisons,
        })
    return {
        "schema_version": REPORT_VERSION,
        "scope": "fixed_saved_event_lower_limb_image_derivative_diagnostic_only",
        "baseline": "legacy_finite_neighbor_secant_unchanged",
        "candidate_algorithm_version": CONTIGUOUS_DERIVATIVE_VERSION,
        "candidate_active_in_production": False,
        "max_contiguous_gap_ms": MAX_CONTIGUOUS_GAP_MS,
        "gap_limit_basis": "existing_continuous_duration_engineering_bound_not_technical_grade_threshold",
        "speed_unit": "source_frame_long_edge/s",
        "smoothing": {"max_gap_ms": 160, "radius_ms": 100, "semantics": "existing_interpolated_smoothed_series_not_raw_observation"},
        "event_boundaries_changed": False,
        "event_count": len(rows),
        "changed_event_count": sum(any(row["changed_sample_count"] for row in event["comparisons"].values()) for event in rows),
        "changed_joint_mode_sample_count": sum(row["changed_sample_count"] for event in rows for row in event["comparisons"].values()),
        "accuracy": None, "technical_score": None,
        "limitation": "differences_are_not_accuracy_evidence; saved_events_and_poses_are_not_truth; overlapping_events_repeat_samples; promotion_requires_new_versioned_registry_and_validation",
        "events": rows,
    }


def evaluate_kinematic_continuity_files(run_dir: Path, output: Path) -> dict[str, Any]:
    output = Path(output)
    if output.exists():
        raise FileExistsError("refusing to overwrite an existing diagnostic or source artifact")
    paths = {name: Path(run_dir) / name for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl")}
    contents = {name: path.read_bytes() for name, path in paths.items()}
    sources = {name: {"path": str(paths[name].resolve()), "sha256": hashlib.sha256(data).hexdigest()} for name, data in contents.items()}
    records = {name: [json.loads(line) for line in data.splitlines() if line.strip()] for name, data in contents.items()}
    sequence = pose_sequence_from_records(records["frames.jsonl"], records["primary-player.jsonl"])
    report = evaluate_kinematic_continuity(sequence, records["events.jsonl"])
    for name, path in paths.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sources[name]["sha256"]:
            raise ValueError("source artifacts changed during the diagnostic")
    report["sources"] = sources
    report["source_bytes_unchanged"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against a concurrent writer.
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_kinematic_continuity_files(args.run_dir, args.output)
    print(json.dumps({key: report[key] for key in ("event_count", "changed_event_count", "changed_joint_mode_sample_count", "candidate_active_in_production", "source_bytes_unchanged")}))


if __name__ == "__main__":
    main()
