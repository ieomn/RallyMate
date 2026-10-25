"""Offline independent acceptance of saved native SAM records; never runs a model.

The public native contract uses a joint-name mapping, not a numeric array.
This audit deliberately validates its values, exact names, dimensions, source
PTS, and confidence semantics without using the production geometry validator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics

from rallymate_vision.pose.metadata import MHR70_NAMES


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def native_geometry_errors(record: dict) -> list[str]:
    errors = []
    for field, dimensions in (("keypoints_2d", 2), ("keypoints_3d", 3)):
        points = record.get(field)
        if not isinstance(points, dict):
            errors.append(f"{field}:expected_joint_name_mapping")
            continue
        if set(points) != set(MHR70_NAMES):
            errors.append(f"{field}:joint_names_mismatch")
        for name, point in points.items():
            if not isinstance(point, list) or len(point) != dimensions:
                errors.append(f"{field}:{name}:wrong_dimensions")
            elif not all(finite(value) for value in point):
                errors.append(f"{field}:{name}:nonfinite_or_non_numeric")
    return errors


def stats(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "sum": sum(values), "mean": statistics.fmean(values),
            "median": statistics.median(values), "p95_nearest_rank": values[math.ceil(.95 * len(values)) - 1],
            "min": values[0], "max": values[-1]}


def audit(run_dir: Path, video: Path, expected_frames: int, previous: Path | None = None) -> dict:
    import av

    native_path = run_dir / "sam3d_frames.jsonl"
    rows = [json.loads(line) for line in native_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    evaluation = json.loads((run_dir / "sam3d_evaluation.json").read_text(encoding="utf-8"))
    video_sha = digest(video)
    with av.open(str(video)) as container:
        decoded = [{"frame_index": index, "pts": frame.pts,
                    "time_base_num": frame.time_base.numerator,
                    "time_base_den": frame.time_base.denominator,
                    "image_size": [frame.width, frame.height]}
                   for index, frame in enumerate(container.decode(video=0))]
    failures = []
    for index, row in enumerate(rows):
        errors = native_geometry_errors(row)
        if index < len(decoded):
            source = decoded[index]
            for key in ("frame_index", "pts", "time_base_num", "time_base_den", "image_size"):
                if row.get(key) != source[key]:
                    errors.append(f"source:{key}:mismatch")
            expected_ms = round(Fraction(source["pts"] * source["time_base_num"] * 1000,
                                         source["time_base_den"]))
            if row.get("timestamp_ms") != expected_ms:
                errors.append("source:timestamp_ms:mismatch")
        if row.get("video_sha256", "").lower() != video_sha:
            errors.append("source:video_sha256:mismatch")
        if row.get("processed_index") != index:
            errors.append("source:processed_index:mismatch")
        if row.get("status") != "ok":
            errors.append("inference:not_ok")
        if "confidence" not in row or row["confidence"] is not None or row.get("confidence_semantics") != "not_provided_by_sam3d":
            errors.append("semantics:confidence")
        if row.get("units_3d") != "model_units" or row.get("camera_intrinsics") != "official_default_fov_uncalibrated":
            errors.append("semantics:metric_calibration")
        if errors:
            failures.append({"record": index, "errors": errors})
    checks = {"record_count_matches_expected": len(rows) == expected_frames,
              "independent_decoded_count_matches_expected": len(decoded) == expected_frames,
              "all_native_geometry_source_and_semantics_valid": not failures,
              "summary_job_identity_matches_directory": summary.get("job_id") == run_dir.name,
              "evaluation_counts_match": all(evaluation["counts"].get(key) == expected_frames for key in
                                             ("input_records", "status_ok", "valid_binding", "frames_with_all_core_2d", "frames_with_all_core_3d"))}
    model = summary["models"]["pose_backend"]
    result = {"schema": "independent-local-sam-native-acceptance-v2",
              "at_utc": datetime.now(timezone.utc).isoformat(), "job_id": run_dir.name,
              "checks": checks, "native_acceptance_passed": all(checks.values()),
              "record_count": len(rows), "status_counts": dict(Counter(row.get("status") for row in rows)),
              "geometry": {"contract": "named_MHR70_dictionary", "joints_per_record": 70,
                           "coordinate_count_2d": len(rows) * 70 * 2, "coordinate_count_3d": len(rows) * 70 * 3},
              "failures": failures, "video": {"path": str(video.resolve()), "sha256": video_sha},
              "runtime": {"all_inference_ms": stats([r["inference_ms"] for r in rows]),
                          "steady_inference_ms": stats([r["inference_ms"] for r in rows if not r.get("first_model_call")]),
                          "peak_memory_bytes": stats([r["peak_memory_bytes"] for r in rows]),
                          "model_load_seconds": model.get("load_seconds"), "pipeline": summary.get("runtime")},
              "evidence": {name: {"bytes": (run_dir / name).stat().st_size, "sha256": digest(run_dir / name)}
                           for name in ("sam3d_frames.jsonl", "summary.json", "sam3d_evaluation.json")},
              "limits": ["Saved 30-frame inference acceptance is not accuracy truth or long-video stability.",
                         "Unknown joint confidence and uncalibrated monocular units remain unknown.",
                         "This audit does not authorize formal scores or assert current service health."]}
    if previous:
        result["previous_failure_preserved"] = {"path": str(previous.resolve()), "sha256": digest(previous),
                                                 "correction": "Native coordinates are named dictionaries; iterating keys as coordinate arrays falsely rejected finite geometry."}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--expected-frames", type=int, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run_dir, args.video, args.expected_frames, args.previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Evidence is append-only: the operator must choose a fresh output path.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"output": str(args.output), "passed": result["native_acceptance_passed"],
                      "failures": result["failures"], "runtime": result["runtime"]}, ensure_ascii=False))
    return 0 if result["native_acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
