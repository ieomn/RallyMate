"""Bounded, source-bound ankle ordering candidates, independent of FS03 scoring.

One fixed image axis is estimated from the hip midpoint displacement of each
original adjacent frame pair. A sign exchange only brackets projected ordering;
it establishes neither a physical foot intersection nor a complete crossover.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import re
from typing import Any, Sequence

import numpy as np

from rallymate_features.coordinates import (
    COORDINATE_CONTRACT_VERSION, point_series, validated_camera_transform,
)
from rallymate_features.schemas import (
    MODEL_MEASUREMENT_PROVENANCE, PoseSequence, validated_source_time_binding,
)

VERSION = "projected-ankle-order-candidates-v1.0.1"
JOINTS = ("left_hip", "right_hip", "left_ankle", "right_ankle")
# Existing local feature gap policy; this is not evidence of missing frames.
MAX_LOCAL_SPAN_MS = 160
# Existing scoring-path point cutoff, not primary diagnostics (.20), the
# generic coordinate loader default (.25), or a Word-derived threshold.
CONFIDENCE_MIN = .35
CONFIRMED_ID_FLAGS = frozenset({
    "confirmed_id_switch", "confirmed_id_switch_detected",
    "confirmed_id_switch_present", "primary_player_id_switch_confirmed",
})
SOURCE_REFS = {
    "ordering": ["CARD-FS:word/document.xml:P0498", "CARD-FS:word/document.xml:P0506"],
    "formal_stage": ["CARD-FS:word/document.xml:P0418", "CARD-FS:word/document.xml:P0419"],
    "visibility": ["CARD-FS:word/document.xml:P0518"],
    "target_end": ["CARD-FS:word/document.xml:P0588"],
}


def _exact_ms(binding: dict) -> Fraction:
    return Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"])


def _rational(value: Fraction) -> dict:
    return {"numerator": value.numerator, "denominator": value.denominator, "approx_ms": float(value)}


def _opposite(a: float, b: float) -> bool:
    # Avoid a product that can overflow or underflow to zero.
    return (a < 0 < b) or (b < 0 < a)


def _same_sha(value, expected) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None and value.lower() == expected


def _bounded_strings(values, limit=32):
    return [value[:160] for value in values[:limit]]


def _parent_source_association(parent, bindings):
    """Check supplied proof before admitting even a weak parent association.

    Older RTM parents have only a source-track association. Keep their local
    diagnostics with that explicit limitation, but never let an existing,
    contradictory nested proof fall back to the weaker association.
    """
    expected = bindings[-1]
    by_frame = {row["frame_index"]: row for row in bindings}
    by_processed = {row["processed_index"]: row for row in bindings}
    mismatches = set()
    verified_endpoints = set()
    diagnostics = parent.get("track_diagnostics") or {}
    source_tracks = diagnostics.get("source_track_ids", [])
    weak_match = isinstance(source_tracks, list) and any(
        type(track) is int and track == expected["person_track_id"] for track in source_tracks)
    if "source_track_ids" in diagnostics and not weak_match:
        mismatches.add("track_diagnostics.source_track_ids_mismatch")

    def check_fields(value, field_map, reference, location):
        for key, target in field_map.items():
            if key not in value:
                continue
            actual = value[key]
            if target == "video_sha256":
                matches = _same_sha(actual, reference[target])
            else:
                matches = type(actual) is int and actual == reference[target]
            if not matches:
                mismatches.add(f"{location}.{key}_mismatch")

    identity = {key: key for key in ("video_sha256", "primary_player_id", "selection_epoch",
                                    "camera_reference_epoch", "time_base_num", "time_base_den")}
    identity["source_track_id"] = "person_track_id"
    check_fields(parent, identity, expected, "parent")
    check_fields(diagnostics, identity, expected, "track_diagnostics")
    nested_identity = {**identity, "person_track_id": "person_track_id"}
    endpoint_fields = {**nested_identity, "frame_index": "frame_index", "source_frame_index": "frame_index",
                       "processed_index": "processed_index", "pts": "pts",
                       "video_timestamp_ms": "video_timestamp_ms"}
    required = {"video_sha256", "frame_index", "processed_index", "pts", "time_base_num",
                "time_base_den", "person_track_id", "selection_epoch", "camera_reference_epoch"}

    def check_source_time(value, reference, location):
        # SAM source_bindings.timestamp_ms is the source clock, not video time.
        # The established source contract allows <= .51 ms export rounding.
        for key in ("timestamp_ms", "source_timestamp_ms"):
            if key in value and (type(value[key]) not in (int, float)
                    or not np.isfinite(value[key])
                    or abs(value[key] - float(_exact_ms(reference))) > .51):
                mismatches.add(f"{location}.{key}_mismatch")
        if "timestamp_source" in value and value["timestamp_source"] != "decoder_pts":
            mismatches.add(f"{location}.timestamp_source_mismatch")

    def check_bindings(rows, location, source_frames=None):
        if not isinstance(rows, list):
            mismatches.add(f"{location}.invalid_list")
            return
        if source_frames is not None and len(rows) != len(source_frames):
            mismatches.add(f"{location}.source_frames_length_mismatch")
        seen = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                mismatches.add(f"{location}.invalid_binding")
                continue
            check_fields(row, nested_identity, expected, location)
            if source_frames is not None and index < len(source_frames) and row.get("frame_index") != source_frames[index]:
                mismatches.add(f"{location}.source_frames_alignment_mismatch")
            frame, processed = row.get("frame_index"), row.get("processed_index")
            reference = by_frame.get(frame) if type(frame) is int else None
            if reference is None and type(processed) is int:
                reference = by_processed.get(processed)
            if reference is None:
                continue
            endpoint = reference["frame_index"]
            if endpoint in seen:
                mismatches.add(f"{location}.duplicate_endpoint_binding")
            seen.add(endpoint)
            check_fields(row, endpoint_fields, reference, location + ".endpoint")
            check_source_time(row, reference, location + ".endpoint")
            if required.issubset(row) and ("timestamp_ms" in row or "source_timestamp_ms" in row):
                verified_endpoints.add(endpoint)

    if "source_bindings" in parent:
        check_bindings(parent["source_bindings"], "parent.source_bindings")
    if "source_window" in parent:
        window = parent["source_window"]
        if not isinstance(window, dict):
            mismatches.add("source_window.invalid_object")
        else:
            check_fields(window, nested_identity, expected, "source_window")
            source_frames = window.get("source_frames")
            if source_frames is not None and (not isinstance(source_frames, list)
                    or any(type(frame) is not int for frame in source_frames)):
                mismatches.add("source_window.source_frames_invalid")
                source_frames = None
            if source_frames is not None:
                for key, target in (("video_timestamps_ms", "video_timestamp_ms"),
                                    ("source_timestamps_ms", "source_timestamp_ms")):
                    if key not in window:
                        continue
                    values = window[key]
                    if not isinstance(values, list) or len(values) != len(source_frames):
                        mismatches.add(f"source_window.{key}_length_mismatch")
                        continue
                    for frame, value in zip(source_frames, values):
                        if frame not in by_frame:
                            continue
                        if target == "source_timestamp_ms":
                            check_source_time({target: value}, by_frame[frame], "source_window")
                        elif type(value) is not int or value != by_frame[frame][target]:
                            mismatches.add(f"source_window.{key}_mismatch")
                if any(source_frames.count(frame) > 1 for frame in by_frame):
                    mismatches.add("source_window.duplicate_endpoint_frame")
            if "source_bindings" in window:
                check_bindings(window["source_bindings"], "source_window.source_bindings", source_frames)
    geometry = diagnostics.get("model_estimate_geometry")
    if isinstance(geometry, dict) and "frame_evidence" in geometry:
        evidence = geometry["frame_evidence"]
        location = "track_diagnostics.model_estimate_geometry.frame_evidence"
        if not isinstance(evidence, list):
            mismatches.add(location + ".invalid_list")
        else:
            diagnostic_bindings = []
            diagnostic_frames = []
            for row in evidence:
                if not isinstance(row, dict):
                    mismatches.add(location + ".invalid_row")
                    continue
                check_fields(row, nested_identity, expected, location)
                frame, processed = row.get("frame_index"), row.get("processed_index")
                reference = by_frame.get(frame) if type(frame) is int else None
                if reference is None and type(processed) is int:
                    reference = by_processed.get(processed)
                if reference is not None:
                    # Unlike the nested source_binding, this outer diagnostic
                    # row's timestamp_ms belongs to the video clock.
                    check_fields(row, {**endpoint_fields, "timestamp_ms": "video_timestamp_ms"},
                                 reference, location)
                if "source_binding" in row:
                    diagnostic_bindings.append(row["source_binding"])
                    diagnostic_frames.append(frame)
                ledger = row.get("request_ledger")
                if isinstance(ledger, dict):
                    check_fields(ledger, nested_identity, expected, location + ".request_ledger")
                    if reference is not None:
                        check_fields(ledger, endpoint_fields, reference, location + ".request_ledger")
                        check_source_time(ledger, reference, location + ".request_ledger")
            check_bindings(diagnostic_bindings, location + ".source_binding", diagnostic_frames)
    strong = not mismatches and verified_endpoints == set(by_frame)
    eligible = not mismatches and (strong or weak_match)
    reasons = sorted(mismatches)
    return {
        "status": ("mismatch" if mismatches else "pair_source_bindings_verified" if strong
                   else "weak_source_track_association" if weak_match else "unverified"),
        "eligible_for_gating": eligible, "pair_source_bindings_verified": strong,
        "mismatch_reasons": reasons[:16], "mismatch_reason_count": len(reasons),
        "mismatch_reasons_truncated": len(reasons) > 16,
    }


def _parent_evidence(parents, bindings, frames, is_model):
    """Preserve event context while applying diagnostics only to involved joints.

    Full diagnostics remain in the hashed events input. Every serialized list
    here has a fixed limit; hard-failure detection scans all matched parents.
    """
    binding = bindings[-1]
    # Existing primary-player diagnostics store processed indexes, even though
    # their historical field names say "frames". Never equate the two domains.
    processed_to_source = {item["processed_index"]: item["frame_index"] for item in bindings}
    flags, summaries, jumps, swaps = set(), [], {name: set() for name in JOINTS}, set()
    confirmed, bound_count, diagnostic_count = False, 0, 0
    association_counts = Counter()
    for parent in parents:
        diagnostics = parent.get("track_diagnostics") or {}
        association = _parent_source_association(parent, bindings)
        bound = association["eligible_for_gating"]
        association_counts[association["status"]] += 1
        parent_flags = sorted({value for value in parent.get("quality_flags", []) if isinstance(value, str)})
        flags.update(parent_flags)
        if bound:
            bound_count += 1
            confirmed |= bool(CONFIRMED_ID_FLAGS.intersection(parent_flags))
            by_joint = diagnostics.get("keypoint_jump_candidate_frames_by_joint", {})
            pairs = diagnostics.get("left_right_swap_candidate_joint_pairs", [])
            diagnostic_count += int("keypoint_jump_candidate_frames_by_joint" in diagnostics
                                    or "left_right_swap_candidate_joint_pairs" in diagnostics)
            for name in JOINTS:
                jumps[name].update(processed_to_source[frame] for frame in by_joint.get(name, [])
                                   if type(frame) is int and frame in processed_to_source)
            for item in pairs:
                left, right = item.get("left_joint"), item.get("right_joint")
                if left not in JOINTS and right not in JOINTS:
                    continue
                overlap = tuple(sorted({processed_to_source[frame] for frame in item.get("frames", [])
                                        if type(frame) is int and frame in processed_to_source}))
                if overlap:
                    swaps.add((str(left)[:80], str(right)[:80], overlap))
        if len(summaries) < 4:
            phases = parent.get("key_phases_ms") or {}
            slowdown = phases.get("first_step_slowdown_proxy_ms")
            slowdown = slowdown if type(slowdown) in (int, float) and np.isfinite(slowdown) else None
            summaries.append({
                "event_id": str(parent.get("event_id", ""))[:160],
                "event_code": str(parent.get("event_code", ""))[:32],
                "video_id": str(parent.get("video_id", ""))[:160],
                "person_track_id": parent.get("person_track_id"),
                "start_ms": parent["start_ms"], "end_ms": parent["end_ms"],
                "source_track_association": association["status"],
                "source_association": association,
                "quality_flags": _bounded_strings(parent_flags), "quality_flag_count": len(parent_flags),
                "quality_flags_truncated": len(parent_flags) > 32,
                "diagnostic_semantics": str(diagnostics.get("diagnostic_semantics", "not_provided"))[:800],
                "key_phases_ms": {"first_step_slowdown_proxy_ms": slowdown},
                "slowdown_proxy_relation_to_pair": ("after_pair" if slowdown is not None and slowdown > binding["video_timestamp_ms"]
                    else "not_after_pair_or_unavailable"),
                "slowdown_proxy_confirms_first_step_landing": False,
            })
    flags = sorted(flags)
    local_jumps = {name: sorted(values) for name, values in jumps.items() if values}
    local_swaps = [{"left_joint": left, "right_joint": right, "frames": list(overlap)}
                   for left, right, overlap in sorted(swaps)[:8]]
    return {
        "parent_event_count": len(parents), "source_track_matched_parent_count": bound_count,
        "parent_source_association_counts": dict(sorted(association_counts.items())),
        "parent_source_association_policy": "existing_source_conflicts_excluded;legacy_track_only_association_is_weak_not_fully_verified",
        "parent_quality_flags": _bounded_strings(flags), "parent_quality_flag_count": len(flags),
        "parent_quality_flags_truncated": len(flags) > 32,
        "parent_events": summaries, "parent_events_truncated": len(parents) > 4,
        "local_joint_diagnostics": {
            "source_frames": frames, "participating_joints": list(JOINTS),
            "input_frame_index_domain": "processed_index", "output_frame_index_domain": "source_frame_index",
            "processed_to_source": [{"processed_index": key, "source_frame_index": value} for key, value in processed_to_source.items()],
            "keypoint_jump_candidate_frames_by_joint": local_jumps,
            "left_right_swap_candidate_joint_pairs": local_swaps,
            "swap_evidence_truncated": len(swaps) > 8,
            "assessment_status": ("sam_confidence_diagnostics_not_assessed" if is_model else
                "source_track_matched_diagnostics_available" if diagnostic_count else "not_provided"),
            "empty_lists_prove_no_anomaly": False,
        },
        "candidate_quality_status": "suspect" if local_jumps or local_swaps else "unvalidated",
    }, confirmed


def _chord_relation(left: np.ndarray, right: np.ndarray) -> str:
    """Relation of endpoint chords, not continuous or simultaneous trajectories."""
    def orient(a, b, c):
        u, v = b - a, c - a
        return float(u[0] * v[1] - u[1] * v[0])

    a, b = left
    c, d = right
    if np.array_equal(a, b) or np.array_equal(c, d):
        return "degenerate_endpoint_chord"
    ab_c, ab_d, cd_a, cd_b = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if _opposite(ab_c, ab_d) and _opposite(cd_a, cd_b):
        return "proper_endpoint_chord_intersection"
    for value, p, q, r in ((ab_c, a, b, c), (ab_d, a, b, d), (cd_a, c, d, a), (cd_b, c, d, b)):
        if value == 0 and np.all(r >= np.minimum(p, q)) and np.all(r <= np.maximum(p, q)):
            return "touching_or_collinear_endpoint_chords"
    return "disjoint_endpoint_chords"


class _Context:
    def __init__(self, sequence, primary_timeline, events, video_id):
        self.sequence = sequence
        self.video_id = video_id
        self.events = events
        self.timeline: dict[int, dict | None] = {}
        self.duplicate_processed = set()
        processed_counts = Counter()
        for row in primary_timeline or ():
            if not isinstance(row, dict):
                continue
            frame = row.get("source_frame_index")
            if type(frame) is int:
                self.timeline[frame] = row if frame not in self.timeline else None
            processed = row.get("processed_index")
            if type(processed) is int:
                processed_counts[processed] += 1
        self.duplicate_processed = {key for key, count in processed_counts.items() if count > 1}
        self.frame_counts = Counter(int(frame) for frame in sequence.source_frames)
        self.points, self.masks = {}, {}
        for name in JOINTS:
            xy, mask = point_series(sequence, name, confidence_min=CONFIDENCE_MIN)
            if sequence.model_estimate_bindings is None and name in sequence.confidence:
                confidence = np.asarray(sequence.confidence[name])
                mask &= np.isfinite(confidence) & (confidence <= 1)
            self.points[name], self.masks[name] = xy, mask
        self.applied = set(sequence.coordinate_metadata.get("camera_compensation", {}).get("applied_source_frames", []))

    def parents(self, start, end):
        # The caller must supply video identity to authorize parent association.
        # Parent phase boundaries are never used to create the geometric pair.
        if self.video_id is None:
            return []
        return [event for event in self.events
                if event.get("video_id") == self.video_id
                and event.get("person_track_id") == self.sequence.primary_player_id
                and isinstance(event.get("start_ms"), (int, float))
                and isinstance(event.get("end_ms"), (int, float))
                and event["start_ms"] <= end and event["end_ms"] >= start]

    def pair(self, before: int) -> dict[str, Any]:
        sequence = self.sequence
        indexes = [before, before + 1]
        frames = [int(sequence.source_frames[i]) for i in indexes]
        result: dict[str, Any] = {"sequence_indexes": indexes, "source_frames": frames,
                                  "status": "excluded", "reason": None}

        def exclude(reason):
            result["reason"] = reason
            return result

        if any(self.frame_counts[frame] != 1 for frame in frames):
            return exclude("duplicate_source_frame")
        if frames[1] != frames[0] + 1:
            return exclude("nonadjacent_source_frames")
        bindings = [validated_source_time_binding(sequence.source_time_bindings.get(frame)) for frame in frames]
        if any(binding is None for binding in bindings):
            return exclude("source_time_binding_unavailable")
        a, b = bindings
        result["source_bindings"] = bindings
        for i, frame, binding in zip(indexes, frames, bindings):
            if (binding["frame_index"] != frame or binding["video_timestamp_ms"] != sequence.timestamp_ms[i]
                    or binding["primary_player_id"] != sequence.primary_player_id):
                return exclude("source_time_binding_mismatch")
        if a["video_sha256"] != b["video_sha256"]:
            return exclude("video_identity_changed")
        if any(a[key] != b[key] for key in ("primary_player_id", "person_track_id", "selection_epoch")):
            return exclude("subject_identity_changed")
        if a["camera_reference_epoch"] != b["camera_reference_epoch"]:
            return exclude("camera_reference_changed")
        if any(a[key] != b[key] for key in ("time_base_num", "time_base_den")):
            return exclude("source_timebase_changed")
        if b["processed_index"] != a["processed_index"] + 1:
            return exclude("nonadjacent_processed_indexes")
        times = [_exact_ms(binding) for binding in bindings]
        result["source_interval_ms"] = [_rational(time) for time in times]
        dt = times[1] - times[0]
        if dt <= 0:
            return exclude("source_pts_not_increasing")
        if dt > MAX_LOCAL_SPAN_MS:
            return exclude("temporal_span_exceeds_local_policy")
        video_dt = Fraction(str(float(sequence.timestamp_ms[indexes[1]]))) - Fraction(str(float(sequence.timestamp_ms[indexes[0]])))
        if video_dt <= 0 or abs(dt - video_dt) > Fraction(102, 100):
            return exclude("source_video_clock_mismatch")
        for binding in bindings:
            row = self.timeline.get(binding["frame_index"])
            if not row or binding["processed_index"] in self.duplicate_processed:
                return exclude("primary_timeline_missing_or_duplicated")
            if row.get("selection_status") != "selected" or row.get("identity_ambiguous") is not False:
                return exclude("primary_identity_unverified")
            fields = {"source_frame_index": "frame_index", "processed_index": "processed_index",
                      "timestamp_ms": "video_timestamp_ms", "primary_player_id": "primary_player_id",
                      "source_track_id": "person_track_id", "selection_epoch": "selection_epoch",
                      "camera_reference_epoch": "camera_reference_epoch"}
            if any(type(row.get(key)) is not int or row[key] != binding[target] for key, target in fields.items()):
                return exclude("primary_timeline_binding_mismatch")
            for key in ("pts", "time_base_num", "time_base_den", "source_timestamp_ms", "video_sha256"):
                if key in row and (not _same_sha(row[key], binding[key]) if key == "video_sha256"
                                   else isinstance(row[key], bool) or row[key] != binding[key]):
                    return exclude("primary_timeline_clock_mismatch")
        if sequence.model_estimate_bindings is not None:
            for index, binding in zip(indexes, bindings):
                model = sequence.model_estimate_bindings[index]
                if not model or not _same_sha(model.get("video_sha256"), binding["video_sha256"]) or any(model.get(key) != binding[key] for key in (
                        "frame_index", "processed_index", "pts", "time_base_num", "time_base_den",
                        "person_track_id", "selection_epoch", "camera_reference_epoch")) or model.get("timestamp_ms") != binding["source_timestamp_ms"]:
                    return exclude("model_request_source_binding_mismatch")
        metadata = sequence.coordinate_metadata
        if (metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
                or metadata.get("coordinate_system") != "camera_reference_xy_divided_by_source_long_edge"
                or metadata.get("axis_units") != "same_for_x_and_y"):
            return exclude("coordinate_contract_unverified")
        if (sequence.camera_motion_status is None or sequence.camera_reference_epochs is None
                or sequence.frame_transforms_to_reference is None):
            return exclude("camera_reference_unverified")
        for index, binding in zip(indexes, bindings):
            if (sequence.camera_motion_status[index] not in {"reference", "stationary", "compensated"}
                    or sequence.camera_reference_epochs[index] != binding["camera_reference_epoch"]
                    or binding["frame_index"] not in self.applied
                    or validated_camera_transform({"compensation_valid": True,
                        "matrix_to_reference": sequence.frame_transforms_to_reference[index]}) is None):
                return exclude("camera_reference_unverified")
        if sequence.frame_dimensions_px is None:
            return exclude("frame_dimensions_unavailable")
        dimensions = np.asarray(sequence.frame_dimensions_px[indexes], dtype=float)
        if not np.isfinite(dimensions).all() or np.any(dimensions <= 0) or not np.array_equal(dimensions[0], dimensions[1]):
            return exclude("frame_dimensions_invalid_or_changed")
        invalid = [name for name in JOINTS if not self.masks[name][indexes].all()]
        if invalid:
            result["unavailable_joints"] = invalid
            return exclude("model_geometry_unavailable" if sequence.model_estimate_bindings is not None else "joint_confidence_or_geometry_unavailable")
        parents = self.parents(a["video_timestamp_ms"], b["video_timestamp_ms"])
        parent_evidence, confirmed = _parent_evidence(parents, bindings, frames, sequence.model_estimate_bindings is not None)
        result.update(parent_evidence)
        if confirmed:
            return exclude("parent_confirmed_identity_switch")
        # Coordinates have already been camera-compensated. Only undo isotropic
        # long-edge normalization; do not apply the saved transforms again.
        long_edge = float(max(dimensions[0]))
        points = {name: self.points[name][indexes] * long_edge for name in JOINTS}
        if any(not np.isfinite(value).all() for value in points.values()):
            return exclude("nonfinite_reference_geometry")
        hip = (points["left_hip"] + points["right_hip"]) / 2
        displacement = hip[1] - hip[0]
        distance = float(np.hypot(*displacement))
        # Floating point resolution guard only, not an anatomical amplitude or
        # movement-reliability threshold. Even an available axis is unverified.
        numeric_floor = float(8 * np.finfo(float).eps * max(1., float(np.max(np.abs(hip)))))
        result["axis"] = {
            "source": "two_frame_hip_midpoint_displacement_in_camera_reference_image",
            "source_frames": frames, "source_pts": [a["pts"], b["pts"]],
            "time_base": [a["time_base_num"], a["time_base_den"]],
            "fixed_within_pair": True, "hip_displacement_px": displacement.tolist(),
            "hip_displacement_length_px": distance, "numeric_resolution_floor_px": numeric_floor,
            "available": False, "unit_vector": None, "movement_direction_reliability": "unverified",
            "physical_or_tactical_movement_axis_verified": False,
        }
        result["observations"] = {
            "coordinate_system": "camera_reference_pixels", "reference_epoch": a["camera_reference_epoch"],
            "joints": {name: value.tolist() for name, value in points.items()},
            "hip_midpoints": hip.tolist(),
            "left_minus_right_ankle_reference_x_px": (points["left_ankle"] - points["right_ankle"])[:, 0].tolist(),
            "hip_width_px": np.linalg.norm(points["left_hip"] - points["right_hip"], axis=1).tolist(),
            "ankle_separation_px": np.linalg.norm(points["left_ankle"] - points["right_ankle"], axis=1).tolist(),
        }
        result["quality"] = {
            "required_joints": list(JOINTS), "valid_endpoint_count": 2,
            "support_semantics": "model_geometry" if sequence.model_estimate_bindings is not None else "confidence_qualified_image_points",
            "rtm_confidence_min": None if sequence.model_estimate_bindings is not None else CONFIDENCE_MIN,
            "rtm_confidence_policy": "existing_scoring_point_cutoff_not_Word_threshold",
            "geometry_masks": {name: [bool(self.masks[name][i]) for i in indexes] for name in JOINTS},
            "confidence_available_masks": {name: [bool(np.isfinite(sequence.confidence[name][i])) for i in indexes] for name in JOINTS},
            "confidence": {name: [None if not np.isfinite(sequence.confidence[name][i]) else float(sequence.confidence[name][i]) for i in indexes] for name in JOINTS},
            "formal_window_ankle_visibility_fraction": None, "source_70_percent_visibility_verified": False,
            "formal_rule_observation_window": None, "formal_window_unavailable_reason": "first_step_landing_and_target_end_not_confirmed",
            "endpoint_support_numerator": 2, "endpoint_support_denominator": 2,
            "endpoint_support_certifies_formal_visibility": False,
            "side_view_or_severe_overlap_status": "unknown", "anatomical_left_right_assignment_verified": False,
            "parent_quality_assessment": "flags_preserved_not_independent_joint_validation",
        }
        if sequence.model_estimate_bindings is not None:
            result["quality"]["measurement_provenance"] = dict(MODEL_MEASUREMENT_PROVENANCE)
        result["normalization"] = {"input_contract": COORDINATE_CONTRACT_VERSION,
            "pixel_recovery_multiplier": long_edge, "equal_axis_units": True, "camera_transform_applied_again": False,
            "body_or_hip_width_normalization": "not_applied_ordering_is_scale_invariant",
            "stride_length_or_amplitude_measured": False, "metric_or_3d": False}
        if distance == 0:
            return exclude("zero_hip_displacement_no_axis")
        if not np.isfinite(distance) or distance <= numeric_floor:
            return exclude("numerically_degenerate_hip_displacement_no_axis")
        axis = displacement / distance
        result["axis"].update(available=True, unit_vector=axis.tolist())
        signed = (points["left_ankle"] - points["right_ankle"]) @ axis
        result["signed_ankle_order_px"] = signed.tolist()
        result["endpoint_chord_relation_2d"] = _chord_relation(points["left_ankle"], points["right_ankle"])
        result["continuous_trajectory_intersection_verified"] = False
        if np.any(signed == 0):
            return exclude("exact_zero_endpoint_no_strict_exchange")
        if not _opposite(*signed):
            result.update(status="no_exchange", reason="same_projected_order")
            return result
        result.update(status="candidate", reason="strict_projected_order_exchange", crossing_time_ms=None,
                      candidate_kind="image_plane_ankle_order_change", candidate_only=True, formal_event_promoted=False,
                      formal_step_verified=False, formal_start_ms=None, formal_end_ms=None,
                      source_clause_complete=False, scoring_allowed=False, technical_grade=None,
                      technical_score_0_to_100=None)
        result["risks"] = ["two_sample_axis_direction_reliability_unverified",
            "small_motion_and_local_pose_jitter_not_resolved", "side_view_and_overlap_not_resolved",
            "left_right_assignment_not_independently_verified", "ball_target_and_subsequent_support_not_observed",
            "first_step_landing_and_formal_phase_boundaries_not_established"]
        if result["observations"]["hip_width_px"][1] < result["observations"]["hip_width_px"][0]:
            result["risks"].append("observed_projected_hip_width_decrease_not_a_side_view_diagnosis")
        if sequence.model_estimate_bindings is not None:
            result["risks"].append("model_confidence_and_visibility_not_provided")
        if result["candidate_quality_status"] == "suspect":
            result["risks"].append("source_reported_jump_or_swap_on_participating_joint_at_pair_endpoint")
        return result


def inspect_foot_crossing_pair(sequence: PoseSequence, before_index: int, *,
                               primary_timeline: Sequence[dict] | None, events: Sequence[dict] = (),
                               video_id: str | None = None) -> dict:
    """Inspect one original two-frame bracket without changing any event."""
    if type(before_index) is not int or not 0 <= before_index < len(sequence.timestamp_ms) - 1:
        raise ValueError("before_index must identify a pair of adjacent sequence rows")
    return _Context(sequence, primary_timeline, events, video_id).pair(before_index)


def compute_foot_crossing_candidates(sequence: PoseSequence, *, primary_timeline: Sequence[dict] | None,
                                     events: Sequence[dict] = (), video_id: str | None = None,
                                     max_candidates: int = 64, max_exclusions: int = 16) -> dict:
    """Scan adjacent rows; bounded samples plus complete, disjoint outcome counts."""
    if type(max_candidates) is not int or not 0 <= max_candidates <= 128:
        raise ValueError("max_candidates must be an integer from 0 to 128")
    if type(max_exclusions) is not int or not 0 <= max_exclusions <= 32:
        raise ValueError("max_exclusions must be an integer from 0 to 32")
    context = _Context(sequence, primary_timeline, events, video_id)
    counts, candidate_quality_counts, candidates, exclusions = Counter(), Counter(), [], []
    for before in range(len(sequence.timestamp_ms) - 1):
        row = context.pair(before)
        counts[row["reason"]] += 1
        if row["status"] == "candidate":
            candidate_quality_counts[row["candidate_quality_status"]] += 1
        if row["status"] == "candidate" and len(candidates) < max_candidates:
            candidates.append(row)
        elif row["status"] == "excluded" and len(exclusions) < max_exclusions:
            exclusions.append({key: row[key] for key in ("sequence_indexes", "source_frames", "reason")})
    candidate_count = counts.get("strict_projected_order_exchange", 0)
    no_exchange = counts.get("same_projected_order", 0)
    pair_count = max(0, len(sequence.timestamp_ms) - 1)
    excluded_count = pair_count - candidate_count - no_exchange
    return {
        "version": VERSION, "source_refs": SOURCE_REFS,
        "semantics": "independent_projected_ankle_order_candidates_not_formal_FS03_measurement_or_grade",
        "scoring_allowed": False, "technical_grade": None, "formal_step_verified": False,
        "policy": {"axis": "fixed_within_each_original_two_frame_pair",
            "strict_sign_exchange_only": True, "zero_endpoints_bridged": False, "missing_frames_interpolated": False,
            "ordering_deadband_px": 0, "persistence_requirement": "none_raw_candidate_only",
            "joint_risk_policy": "retain_suspect_only_for_participating_joint_and_endpoint_overlap",
            "rtm_confidence_min": CONFIDENCE_MIN,
            "precise_crossing_time_interpolated": False, "minimum_motion_reliability_threshold": None,
            "numeric_axis_guard": "8_float64_eps_times_max_1_absolute_hip_coordinate",
            "max_local_span_ms": MAX_LOCAL_SPAN_MS, "span_limit_semantics": "existing_local_engineering_policy_not_missing_frame_detection",
            "frame_and_processed_adjacency_required": True, "pts_delta_need_not_be_constant": True,
            "screen_x_is_diagnostic_only": True, "parent_phases_define_formal_stage": False},
        "counts": {"frames": len(sequence.timestamp_ms), "adjacent_sequence_pairs": pair_count,
            "candidates": candidate_count, "no_exchange": no_exchange, "excluded": excluded_count,
            "candidate_quality": dict(sorted(candidate_quality_counts.items())),
            "reasons": dict(sorted(counts.items()))},
        "output_limits": {"candidate_limit": max_candidates, "exclusion_limit": max_exclusions,
            "retention": "first_in_source_sequence_order", "candidates_truncated": candidate_count > len(candidates),
            "exclusions_truncated": excluded_count > len(exclusions)},
        "candidates": candidates, "exclusion_examples": exclusions,
    }
