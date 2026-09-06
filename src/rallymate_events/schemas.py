from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EVENT_CODES = {"FS01", "FS02", "FS09"}
EVENT_LOCAL_COVERAGE_DETECTOR_VERSIONS = frozenset(
    {
        "pose-motion-bout-v0.3.1",
        "pose-motion-bout-v0.4.0",
        "pose-motion-bout-v0.4.1",
    }
)
EVENT_MOTION_REFERENCE_DETECTOR_VERSIONS = frozenset(
    {"pose-motion-bout-v0.4.0", "pose-motion-bout-v0.4.1"}
)
EVENT_LOCAL_COVERAGE_SCOPE = "emitted_event_interval_inclusive"
EVENT_LOCAL_COVERAGE_SEMANTICS = (
    "event_detection_signal_quality_only_not_A_to_E_threshold"
)


def _validate_event_local_kinematic_coverage(record: dict[str, Any]) -> None:
    """Validate the v0.3.1 event-local detector coverage invariant.

    Older detector and manual records remain readable.  Starting with v0.3.1,
    however, every emitted event must carry coverage calculated on its own
    inclusive interval, and the low-coverage quality flag must agree with that
    machine-readable result.
    """

    provenance = record["provenance"]
    if provenance.get("detector_version") not in EVENT_LOCAL_COVERAGE_DETECTOR_VERSIONS:
        return
    coverage = provenance.get("event_kinematic_coverage")
    if not isinstance(coverage, dict):
        raise ValueError(
            "event detector v0.3.1+ requires event_kinematic_coverage provenance"
        )
    required = {
        "scope",
        "start_index",
        "end_index",
        "sample_count",
        "valid_sample_count",
        "coverage_fraction",
        "minimum_required_fraction",
        "status",
        "semantics",
    }
    missing = required - set(coverage)
    if missing:
        raise ValueError(
            f"event_kinematic_coverage missing fields: {sorted(missing)}"
        )
    if coverage["scope"] != EVENT_LOCAL_COVERAGE_SCOPE:
        raise ValueError("event_kinematic_coverage scope is invalid")
    if coverage["semantics"] != EVENT_LOCAL_COVERAGE_SEMANTICS:
        raise ValueError("event_kinematic_coverage semantics are invalid")

    integer_fields = (
        "start_index",
        "end_index",
        "sample_count",
        "valid_sample_count",
    )
    if any(
        isinstance(coverage[name], bool) or not isinstance(coverage[name], int)
        for name in integer_fields
    ):
        raise ValueError("event_kinematic_coverage counts and indices must be integers")
    start_index = coverage["start_index"]
    end_index = coverage["end_index"]
    sample_count = coverage["sample_count"]
    valid_sample_count = coverage["valid_sample_count"]
    if start_index < 0 or end_index < start_index:
        raise ValueError("event_kinematic_coverage indices are invalid")
    if sample_count != end_index - start_index + 1:
        raise ValueError("event_kinematic_coverage sample_count is inconsistent")
    if not 0 <= valid_sample_count <= sample_count:
        raise ValueError("event_kinematic_coverage valid_sample_count is invalid")

    fraction = coverage["coverage_fraction"]
    minimum = coverage["minimum_required_fraction"]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (fraction, minimum)
    ):
        raise ValueError("event_kinematic_coverage fractions must be numeric")
    expected_fraction = round(valid_sample_count / sample_count, 8)
    if abs(float(fraction) - expected_fraction) > 1e-9:
        raise ValueError("event_kinematic_coverage coverage_fraction is inconsistent")
    if not 0 < float(minimum) <= 1:
        raise ValueError("event_kinematic_coverage minimum_required_fraction is invalid")
    expected_status = "sufficient" if fraction >= minimum else "low"
    if coverage["status"] != expected_status:
        raise ValueError("event_kinematic_coverage status is inconsistent")
    has_low_flag = "pose_kinematic_coverage_low" in record["quality_flags"]
    if has_low_flag != (expected_status == "low"):
        raise ValueError(
            "pose_kinematic_coverage_low flag must match event-local coverage status"
        )


def _validate_event_motion_reference(record: dict[str, Any]) -> None:
    provenance = record["provenance"]
    if provenance.get("detector_version") not in EVENT_MOTION_REFERENCE_DETECTOR_VERSIONS:
        return
    reference = provenance.get("event_motion_reference")
    if not isinstance(reference, dict):
        raise ValueError("pose-motion-bout-v0.4+ requires event_motion_reference provenance")
    required = {
        "mode",
        "scope",
        "start_index",
        "end_index",
        "sample_count",
        "body_center_sample_count",
        "hip_center_fallback_sample_count",
        "missing_sample_count",
        "alignment",
        "semantics",
    }
    missing = required - set(reference)
    if missing:
        raise ValueError(f"event_motion_reference missing fields: {sorted(missing)}")
    if reference["scope"] != EVENT_LOCAL_COVERAGE_SCOPE:
        raise ValueError("event_motion_reference scope is invalid")
    if reference["semantics"] != (
        "event_candidate_motion_reference_only_not_biomechanical_center_of_mass_"
        "not_keypoint_imputation_and_not_A_to_E_scoring"
    ):
        raise ValueError("event_motion_reference semantics are invalid")
    count_fields = (
        "start_index",
        "end_index",
        "sample_count",
        "body_center_sample_count",
        "hip_center_fallback_sample_count",
        "missing_sample_count",
    )
    if any(
        isinstance(reference[name], bool) or not isinstance(reference[name], int)
        for name in count_fields
    ):
        raise ValueError("event_motion_reference counts and indices must be integers")
    start = reference["start_index"]
    end = reference["end_index"]
    sample_count = reference["sample_count"]
    counts = (
        reference["body_center_sample_count"],
        reference["hip_center_fallback_sample_count"],
        reference["missing_sample_count"],
    )
    if start < 0 or end < start or sample_count != end - start + 1:
        raise ValueError("event_motion_reference interval is inconsistent")
    if any(value < 0 for value in counts) or sum(counts) != sample_count:
        raise ValueError("event_motion_reference evidence counts are inconsistent")
    alignment = reference["alignment"]
    if not isinstance(alignment, dict):
        raise ValueError("event_motion_reference alignment must be an object")
    for name in ("method", "status", "direct_overlap_sample_count", "offset_xy"):
        if name not in alignment:
            raise ValueError(f"event_motion_reference alignment missing {name}")
    if alignment["method"] != "robust_median_body_center_minus_hip_center_xy":
        raise ValueError("event_motion_reference alignment method is invalid")
    overlap_count = alignment["direct_overlap_sample_count"]
    if isinstance(overlap_count, bool) or not isinstance(overlap_count, int) or overlap_count < 0:
        raise ValueError("event_motion_reference overlap count is invalid")
    offset = alignment["offset_xy"]
    if offset is not None and (
        not isinstance(offset, list)
        or len(offset) != 2
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in offset)
    ):
        raise ValueError("event_motion_reference offset_xy is invalid")


def validate_event_record(record: dict[str, Any], *, annotation: bool = False) -> None:
    required = {
        "schema_version",
        "event_id",
        "person_track_id",
        "event_code",
        "start_ms",
        "end_ms",
        "key_phases_ms",
        "confidence",
        "boundary_uncertainty_ms",
        "quality_flags",
        "provenance",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"event record missing fields: {sorted(missing)}")
    if record["schema_version"] != "1.0.0":
        raise ValueError("unsupported event schema_version")
    if not isinstance(record["event_id"], str) or not record["event_id"]:
        raise ValueError("event_id must be a non-empty string")
    if not isinstance(record["person_track_id"], int) or record["person_track_id"] < 1:
        raise ValueError("person_track_id must be a positive integer")
    if record["event_code"] not in EVENT_CODES:
        raise ValueError("event_code must be FS01, FS02 or FS09")
    if not isinstance(record["start_ms"], int) or not isinstance(record["end_ms"], int):
        raise ValueError("event boundaries must be integer milliseconds")
    if record["start_ms"] < 0 or record["end_ms"] <= record["start_ms"]:
        raise ValueError("event boundaries are invalid")
    if not isinstance(record["key_phases_ms"], dict):
        raise ValueError("key_phases_ms must be an object")
    if any(
        value is not None
        and (not isinstance(value, int) or not record["start_ms"] <= value <= record["end_ms"])
        for value in record["key_phases_ms"].values()
    ):
        raise ValueError("key phase timestamps must be null or inside the event")
    confidence = record["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("event confidence must be in 0..1")
    uncertainty = record["boundary_uncertainty_ms"]
    if not isinstance(uncertainty, int) or uncertainty < 0:
        raise ValueError("boundary_uncertainty_ms must be non-negative")
    if not isinstance(record["quality_flags"], list):
        raise ValueError("quality_flags must be a list")
    if not isinstance(record["provenance"], dict):
        raise ValueError("provenance must be an object")
    _validate_event_local_kinematic_coverage(record)
    _validate_event_motion_reference(record)
    if annotation:
        if record.get("annotation_source") != "manual":
            raise ValueError("ground-truth event annotation_source must be manual")
        if not isinstance(record.get("annotator_id"), str) or not record["annotator_id"]:
            raise ValueError("ground-truth event requires annotator_id")
        if not isinstance(record.get("video_id"), str) or not record["video_id"]:
            raise ValueError("ground-truth event requires video_id")
        if not isinstance(record.get("view_group"), str) or not record["view_group"]:
            raise ValueError("ground-truth event requires view_group")
        if "adjudication_status" in record and record["adjudication_status"] != "accepted":
            raise ValueError("compiled event truth must be adjudication_status=accepted")
        if "adjudication_status" in record and (
            not isinstance(record.get("reviewer_id"), str) or not record["reviewer_id"]
        ):
            raise ValueError("accepted event truth requires reviewer_id")


def load_event_annotations(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid event annotation line {line_number}: {exc}") from exc
            validate_event_record(record, annotation=True)
            records.append(record)
    return records
