"""Read a bounded, operator-published candidate snapshot without inference.

Publication attests the large input hashes. GET checks their stat for drift;
it does not claim to revalidate their contents. No scoring modules are imported.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from fractions import Fraction

INDEX_VERSION = "foot-crossing-candidates-index-v1"
MANIFEST_VERSION = "foot-crossing-candidates-manifest-v1"
RESPONSE_VERSION = "foot-crossing-candidates-response-v1"
ARTIFACT_VERSION = "foot-crossing-candidates-artifact-v1.0.1"
ALGORITHM_VERSION = "projected-ankle-order-candidates-v1.0.1"
ARTIFACT_NAME = "foot-crossing-candidates.json"
LIMITS = dict(index_bytes=4096, manifest_bytes=65536, artifact_bytes=4194304,
              response_bytes=1048576, summary_bytes=67108864, candidates=128,
              exclusions=32, string_chars=800, json_depth=24)
INPUT_NAMES = {"frames": "frames.jsonl", "events": "events.jsonl", "primary": "primary-player.jsonl"}
CORE_CODE = frozenset(("src/rallymate_features/foot_crossing.py", "src/rallymate_scoring/source_assessment.py",
                       "src/rallymate_features/coordinates.py", "src/rallymate_features/schemas.py",
                       "src/rallymate_features/validity.py"))
SUPPORTED_CORE_SHA256 = {
    "src/rallymate_features/foot_crossing.py": "41f150d0141e9ca30dab9fee35e2a38b8d7453e1b8455ef11ccb0c6feee75934",
    "src/rallymate_scoring/source_assessment.py": "81ceaf31c8d76332c17b42ed279f1019267870ec8975c3cffaf83856b2732ad6",
}
SUPPORTED_CORE_BYTES = {"src/rallymate_features/foot_crossing.py": 37126,
                        "src/rallymate_scoring/source_assessment.py": 9111}
# Keep exact historical identities. The additional source-assessment identity
# adds contextual hip measurements; its crossing writer, binding helpers and
# artifact constants are unchanged. Each digest remains paired with its size.
SUPPORTED_CORE_IDENTITIES = {
    name: frozenset(((digest, SUPPORTED_CORE_BYTES[name]),))
    for name, digest in SUPPORTED_CORE_SHA256.items()
}
SUPPORTED_CORE_IDENTITIES["src/rallymate_scoring/source_assessment.py"] |= frozenset((
    ("3e47e38d803e041679a45088f61d74ca31ac2ba56b499cfe925b8665e6848ee4", 19962),
))
JOINTS = frozenset(("left_hip", "right_hip", "left_ankle", "right_ankle"))
PARENT_POLICY = "existing_source_conflicts_excluded;legacy_track_only_association_is_weak_not_fully_verified"
SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class CandidateArtifactError(ValueError):
    def __init__(self, reason_code: str, status: str = "invalid_artifact"):
        super().__init__(reason_code)
        self.reason_code, self.status = reason_code, status


def _require(condition, reason="invalid_structure", status="invalid_artifact"):
    if not condition:
        raise CandidateArtifactError(reason, status)


def _sha(value):
    _require(isinstance(value, str) and SHA.fullmatch(value), "invalid_sha256")
    return value.lower()


def _int(value, minimum=0, maximum=2**53 - 1):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2**53 - 1


def _tree(value, *, strings=800, node_limit=120000):
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(depth <= LIMITS["json_depth"] and nodes <= node_limit, "json_complexity_limit", "limit_exceeded")
        if isinstance(item, dict):
            _require(all(isinstance(key, str) and len(key) <= 160 for key in item), "invalid_object_key")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _require(len(item) <= strings, "string_limit", "limit_exceeded")
        elif type(item) is int:
            # Filesystem nanosecond stamps/inodes are never exposed as JS
            # numbers. Endpoint timing integers get the stricter _int check.
            _require(abs(item) <= 2**128, "unsafe_integer")
        elif item is not None and type(item) is not bool:
            _require(_number(item), "nonfinite_or_unsafe_number")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _json(raw, *, strings=800, node_limit=120000):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CandidateArtifactError("nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, CandidateArtifactError):
            raise
        raise CandidateArtifactError("invalid_json") from None
    _tree(value, strings=strings, node_limit=node_limit)
    _require(isinstance(value, dict), "json_object_required")
    return value


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_stat(path: Path):
    info = Path(path).lstat()
    _require(stat.S_ISREG(info.st_mode), "input_not_regular", "binding_conflict")
    return dict(device=info.st_dev, inode=info.st_ino, size=info.st_size,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe_path(root: Path, *parts):
    """No caller-controlled paths and no symlink directories or files."""
    path = root
    for part in parts:
        _require(isinstance(part, str) and part not in ("", ".", "..") and "/" not in part and "\\" not in part,
                 "unsafe_path", "binding_conflict")
        path = path / part
        _require(not path.is_symlink(), "symlink_rejected", "binding_conflict")
    _require(path.resolve().is_relative_to(root.resolve()), "path_escape", "binding_conflict")
    return path


def _read(path, limit):
    _require(not any(part.is_symlink() for part in (path, *path.parents)), "symlink_rejected", "binding_conflict")
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), "not_regular_file", "binding_conflict")
    _require(0 < before.st_size <= limit, "file_size_limit", "limit_exceeded")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        # On Windows some Python versions expose birth time from path stat
        # but change time from fd stat. Compare like with like for ctime.
        _require(_signature(opened)[:4] == _signature(before)[:4], "file_changed", "binding_conflict")
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    _require(len(raw) <= limit, "file_size_limit", "limit_exceeded")
    _require(_signature(opened) == _signature(after) and _signature(before) == _signature(path.lstat()) and len(raw) == before.st_size,
             "file_changed", "binding_conflict")
    _require(not any(part.is_symlink() for part in (path, *path.parents)), "symlink_rejected", "binding_conflict")
    return raw


def source_binding():
    # Avoid importing rallymate_scoring.__init__, which imports scoring engines.
    path = Path(__file__).resolve().parents[1] / "rallymate_scoring/data/technical_review_rules.json"
    catalog = _json(_read(path, LIMITS["summary_bytes"]), strings=LIMITS["summary_bytes"])
    document = next((item for item in catalog["source_documents"] if item.get("id") == "CARD-FS"), None)
    _require(document is not None, "source_catalog_unavailable", "binding_conflict")
    return {"reference_version": catalog["source_reference_version"],
            "reference_sha256": catalog["source_reference_sha256"], "document": document}


def input_bundle_sha(video_id, video_sha256, inputs):
    return canonical_sha({"video_id": video_id, "video_sha256": video_sha256,
                          "inputs": {key: {"bytes": inputs[key]["bytes"], "sha256": inputs[key]["sha256"]}
                                     for key in INPUT_NAMES}})


def _job_identity(job):
    _require(isinstance(job, dict) and isinstance(job.get("id"), str) and JOB_ID.fullmatch(job["id"]), "invalid_job", "binding_conflict")
    _require(job.get("status") == "succeeded", "job_not_succeeded", "binding_conflict")
    summary = job.get("summary")
    _require(isinstance(summary, dict) and summary.get("job_id") == job["id"], "job_summary_identity_mismatch", "binding_conflict")
    loop = summary.get("minimum_scoring_loop", {})
    _require(isinstance(loop, dict), "invalid_loop_identity", "binding_conflict")
    provenance, recovery = loop.get("provenance", {}), summary.get("recovery", {})
    _require(isinstance(provenance, dict) and isinstance(recovery, dict), "invalid_source_identity", "binding_conflict")
    source_ids = [value for value in (summary.get("source_failed_job_id"), recovery.get("source_failed_job_id"),
                                     provenance.get("pipeline_job_id")) if value is not None]
    source_id = source_ids[0] if source_ids else job["id"]
    _require(isinstance(source_id, str) and JOB_ID.fullmatch(source_id) and all(value == source_id for value in source_ids),
             "source_job_identity_mismatch", "binding_conflict")
    _require(isinstance(loop.get("video_id"), str) and JOB_ID.fullmatch(loop["video_id"]), "video_identity_missing", "binding_conflict")
    return summary, loop, provenance, recovery, source_id


def _hash_matches(value, expected, reason):
    _require(_sha(value) == _sha(expected), reason, "binding_conflict")


def _bindings(row, video_sha):
    frames, bindings, interval = row.get("source_frames"), row.get("source_bindings"), row.get("source_interval_ms")
    _require(isinstance(frames, list) and len(frames) == 2 and all(_int(i) for i in frames) and frames[1] == frames[0] + 1, "nonadjacent_source_frames")
    _require(isinstance(bindings, list) and len(bindings) == 2 and isinstance(interval, list) and len(interval) == 2, "invalid_source_pair")
    exact = []
    for frame, binding, rational in zip(frames, bindings, interval):
        _require(isinstance(binding, dict) and isinstance(rational, dict), "invalid_source_binding")
        for key in ("frame_index", "processed_index", "video_timestamp_ms", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch"):
            _require(_int(binding.get(key)), "invalid_source_integer")
        _require(_int(binding.get("pts"), -(2**53 - 1)) and _number(binding.get("source_timestamp_ms")), "invalid_source_time")
        _require(binding["frame_index"] == frame and binding.get("timestamp_source") == "decoder_pts", "source_frame_binding_mismatch")
        for key in ("time_base_num", "time_base_den"):
            _require(_int(binding.get(key), 1), "invalid_timebase")
        _hash_matches(binding.get("video_sha256"), video_sha, "candidate_video_mismatch")
        stamp = Fraction(binding["pts"] * binding["time_base_num"] * 1000, binding["time_base_den"])
        _require(_int(rational.get("numerator"), -(2**53 - 1)) and _int(rational.get("denominator"), 1)
                 and Fraction(rational["numerator"], rational["denominator"]) == stamp
                 and _number(rational.get("approx_ms")) and abs(rational["approx_ms"] - float(stamp)) < 1e-6
                 and abs(binding["source_timestamp_ms"] - float(stamp)) <= .51, "source_pts_mismatch")
        exact.append(stamp)
    a, b = bindings
    _require(b["processed_index"] == a["processed_index"] + 1 and 0 < exact[1] - exact[0] <= 160, "source_pair_discontinuity")
    _require(b["video_timestamp_ms"] > a["video_timestamp_ms"] and abs(float(exact[1] - exact[0]) - (b["video_timestamp_ms"] - a["video_timestamp_ms"])) <= 1.02, "video_time_discontinuity")
    _require(all(a[key] == b[key] for key in ("primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch", "time_base_num", "time_base_den")), "subject_or_camera_discontinuity")
    fields = ("frame_index", "processed_index", "video_sha256", "pts", "time_base_num", "time_base_den", "source_timestamp_ms", "video_timestamp_ms", "timestamp_source", "primary_player_id", "person_track_id", "selection_epoch", "camera_reference_epoch")
    return [{key: binding[key] for key in fields} for binding in bindings]


def _candidate(row, video_sha):
    _require(isinstance(row, dict) and row.get("status") == "candidate" and row.get("reason") == "strict_projected_order_exchange", "invalid_candidate")
    _require(row.get("scoring_allowed") is False and row.get("formal_step_verified") is False and row.get("candidate_only") is True
             and row.get("formal_event_promoted") is False and row.get("technical_grade") is None
             and row.get("technical_score_0_to_100") is None and row.get("crossing_time_ms") is None, "formal_claim_rejected")
    associations = row.get("parent_source_association_counts")
    _require(isinstance(associations, dict) and set(associations) <= {"mismatch", "pair_source_bindings_verified", "weak_source_track_association", "unverified"}
             and all(_int(value) for value in associations.values()) and _int(row.get("parent_event_count"))
             and sum(associations.values()) == row["parent_event_count"]
             and _int(row.get("source_track_matched_parent_count"))
             and row["source_track_matched_parent_count"] == associations.get("pair_source_bindings_verified", 0) + associations.get("weak_source_track_association", 0)
             and row.get("parent_source_association_policy") == PARENT_POLICY, "invalid_parent_association_proof")
    bindings = _bindings(row, video_sha)
    signed = row.get("signed_ankle_order_px")
    _require(isinstance(signed, list) and len(signed) == 2 and all(_number(value) for value in signed)
             and (signed[0] < 0 < signed[1] or signed[1] < 0 < signed[0]), "invalid_signed_order")
    axis = row.get("axis", {})
    _require(isinstance(axis, dict) and axis.get("source") == "two_frame_hip_midpoint_displacement_in_camera_reference_image"
             and axis.get("fixed_within_pair") is True and axis.get("available") is True
             and axis.get("movement_direction_reliability") == "unverified" and axis.get("physical_or_tactical_movement_axis_verified") is False
             and axis.get("source_frames") == row["source_frames"]
             and axis.get("source_pts") == [item["pts"] for item in bindings]
             and axis.get("time_base") == [bindings[0]["time_base_num"], bindings[0]["time_base_den"]], "invalid_axis_binding")
    for key in ("unit_vector", "hip_displacement_px"):
        _require(isinstance(axis.get(key), list) and len(axis[key]) == 2 and all(_number(value) for value in axis[key]), "invalid_axis_vector")
    _require(abs(math.hypot(*axis["unit_vector"]) - 1) <= 1e-6 and _number(axis.get("hip_displacement_length_px"))
             and axis["hip_displacement_length_px"] > 0 and _number(axis.get("numeric_resolution_floor_px"))
             and 0 <= axis["numeric_resolution_floor_px"] < axis["hip_displacement_length_px"], "invalid_axis_length")
    length = axis["hip_displacement_length_px"]
    _require(abs(math.hypot(*axis["hip_displacement_px"]) - length) <= 1e-6 * max(1, length)
             and all(abs(unit - displacement / length) <= 1e-6
                     for unit, displacement in zip(axis["unit_vector"], axis["hip_displacement_px"])), "inconsistent_axis_geometry")
    quality = row.get("quality", {})
    _require(isinstance(quality, dict) and quality.get("support_semantics") in ("model_geometry", "confidence_qualified_image_points"), "invalid_quality")
    model = quality["support_semantics"] == "model_geometry"
    _require(quality.get("rtm_confidence_min") is None if model else quality.get("rtm_confidence_min") == .35, "invalid_confidence_policy")
    _require(all(isinstance(quality.get(key), dict) for key in ("geometry_masks", "confidence", "confidence_available_masks")), "invalid_endpoint_support")
    for joint in JOINTS:
        masks = quality.get("geometry_masks", {}).get(joint)
        scores = quality.get("confidence", {}).get(joint)
        confidence_masks = quality.get("confidence_available_masks", {}).get(joint)
        _require(masks == [True, True] and all(type(value) is bool for value in masks)
                 and isinstance(scores, list) and len(scores) == 2
                 and confidence_masks == ([False, False] if model else [True, True])
                 and all(type(value) is bool for value in confidence_masks), "invalid_endpoint_support")
        _require(all(value is None for value in scores) if model else all(_number(value) and .35 <= value <= 1 for value in scores), "invalid_endpoint_confidence")
    diagnostics = row.get("local_joint_diagnostics", {})
    _require(isinstance(diagnostics, dict) and diagnostics.get("source_frames") == row["source_frames"]
             and diagnostics.get("input_frame_index_domain") == "processed_index" and diagnostics.get("output_frame_index_domain") == "source_frame_index"
             and diagnostics.get("empty_lists_prove_no_anomaly") is False, "invalid_diagnostics")
    _require(diagnostics.get("assessment_status") in (("sam_confidence_diagnostics_not_assessed",) if model else ("source_track_matched_diagnostics_available", "not_provided")), "invalid_diagnostic_semantics")
    _require(diagnostics.get("participating_joints") == ["left_hip", "right_hip", "left_ankle", "right_ankle"]
             and diagnostics.get("processed_to_source") == [{"processed_index": item["processed_index"], "source_frame_index": item["frame_index"]} for item in bindings]
             and type(diagnostics.get("swap_evidence_truncated")) is bool, "invalid_diagnostic_mapping")
    jumps = diagnostics.get("keypoint_jump_candidate_frames_by_joint", {})
    swaps = diagnostics.get("left_right_swap_candidate_joint_pairs", [])
    _require(isinstance(jumps, dict) and set(jumps) <= JOINTS
             and all(isinstance(items, list) and 1 <= len(items) <= 2
                     and all(_int(i) and i in row["source_frames"] for i in items)
                     and len(set(items)) == len(items) for items in jumps.values()), "invalid_jump_scope")
    _require(isinstance(swaps, list) and len(swaps) <= 8, "invalid_swap_scope")
    for item in swaps:
        _require(isinstance(item, dict) and item.get("left_joint") in JOINTS and item.get("right_joint") in JOINTS
                 and isinstance(item.get("frames"), list) and 1 <= len(item["frames"]) <= 2
                 and all(_int(i) and i in row["source_frames"] for i in item["frames"])
                 and len(set(item["frames"])) == len(item["frames"]), "invalid_swap_scope")
    _require(row.get("candidate_quality_status") == ("suspect" if jumps or swaps else "unvalidated"), "invalid_candidate_quality")
    result = {key: row[key] for key in ("source_frames", "signed_ankle_order_px", "candidate_quality_status", "parent_event_count", "parent_source_association_counts", "parent_source_association_policy")}
    result["source_interval_ms"] = [{key: stamp[key] for key in ("numerator", "denominator", "approx_ms")} for stamp in row["source_interval_ms"]]
    result.update(source_bindings=bindings, processed_indexes=[item["processed_index"] for item in bindings],
                  axis={key: axis[key] for key in ("source", "source_frames", "source_pts", "time_base", "fixed_within_pair", "hip_displacement_px", "hip_displacement_length_px", "numeric_resolution_floor_px", "available", "unit_vector", "movement_direction_reliability", "physical_or_tactical_movement_axis_verified")},
                  local_joint_diagnostics={key: diagnostics[key] for key in ("source_frames", "participating_joints", "processed_to_source", "input_frame_index_domain", "output_frame_index_domain", "assessment_status", "empty_lists_prove_no_anomaly", "keypoint_jump_candidate_frames_by_joint", "swap_evidence_truncated")},
                  quality={"support_semantics": quality["support_semantics"], "rtm_confidence_min": quality["rtm_confidence_min"]},
                  candidate_only=True, scoring_allowed=False, formal_step_verified=False, technical_grade=None)
    result["local_joint_diagnostics"]["left_right_swap_candidate_joint_pairs"] = [{key: item[key] for key in ("left_joint", "right_joint", "frames")} for item in swaps]
    return result


def validate_publication(manifest, artifact, job):
    """Pure bounded snapshot validation; returns a selected-field projection.

    Large source files are never opened. The publisher must independently hash
    and guard them before calling this helper and publishing the index.
    """
    _tree(manifest)
    _tree(artifact)
    _require(isinstance(manifest, dict) and isinstance(artifact, dict), "object_required")
    summary, loop, provenance, recovery, source_id = _job_identity(job)
    _require(manifest.get("schema_version") == MANIFEST_VERSION, "manifest_version", "unsupported_version")
    _require(manifest.get("display_job_id") == job["id"] and manifest.get("source_job_id") == source_id
             and manifest.get("video_id") == loop["video_id"], "manifest_job_mismatch", "binding_conflict")
    _hash_matches(manifest.get("job_summary_sha256"), canonical_sha(summary), "job_summary_changed")
    _sha(manifest.get("source_summary_sha256"))
    _require(manifest.get("input_binding_mode") == "verified_publication_snapshot", "invalid_binding_mode")
    _hash_matches(manifest.get("video_sha256"), provenance.get("video_sha256"), "video_provenance_mismatch")
    inputs = manifest.get("inputs", {})
    _require(isinstance(inputs, dict) and set(inputs) == set(INPUT_NAMES), "invalid_inputs")
    for key, name in INPUT_NAMES.items():
        item = inputs[key]
        _require(isinstance(item, dict) and item.get("name") == name and _int(item.get("bytes")), "invalid_input")
        _sha(item.get("sha256"))
        stamp = item.get("stat", {})
        _require(isinstance(stamp, dict) and set(stamp) == {"device", "inode", "size", "mtime_ns", "ctime_ns"}
                 and all(type(value) is int and value >= 0 for value in stamp.values()) and stamp["size"] == item["bytes"], "invalid_input_stat")
    _hash_matches(inputs["frames"]["sha256"], provenance.get("frames_sha256"), "frames_provenance_mismatch")
    _require(isinstance(loop.get("artifact_sha256"), dict) and isinstance(recovery.get("source_artifact_sha256", {}), dict), "invalid_provenance_hashes", "binding_conflict")
    pinned_events = loop["artifact_sha256"].get("events_jsonl")
    _hash_matches(inputs["events"]["sha256"], pinned_events, "events_provenance_mismatch")
    for key, name in INPUT_NAMES.items():
        pin = recovery.get("source_artifact_sha256", {}).get(name)
        if pin is not None:
            _hash_matches(inputs[key]["sha256"], pin, "recovery_input_mismatch")
    _hash_matches(manifest.get("input_bundle_sha256"), input_bundle_sha(manifest["video_id"], manifest["video_sha256"], inputs), "input_bundle_mismatch")
    producer = manifest.get("producer", {})
    _require(isinstance(producer, dict) and producer.get("algorithm_version") == ALGORITHM_VERSION, "producer_version", "unsupported_version")
    code = producer.get("code")
    _require(isinstance(code, list) and 5 <= len(code) <= 32, "invalid_producer_code")
    names = set()
    for item in code:
        _require(isinstance(item, dict) and isinstance(item.get("name"), str), "invalid_code_entry")
        name = item["name"]
        _require(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts and "\\" not in name
                 and ":" not in name and name not in names and _int(item.get("bytes"), 1), "invalid_code_entry")
        _sha(item.get("sha256"))
        if name in SUPPORTED_CORE_IDENTITIES:
            _require((_sha(item["sha256"]), item["bytes"]) in SUPPORTED_CORE_IDENTITIES[name],
                     "unsupported_algorithm_code", "unsupported_version")
        names.add(name)
    _require(CORE_CODE <= names, "missing_producer_code")
    current_binding = source_binding()
    _require(manifest.get("source_binding") == current_binding == artifact.get("source_binding"), "source_catalog_mismatch", "binding_conflict")
    reference = manifest.get("artifact", {})
    _require(isinstance(reference, dict) and reference.get("name") == ARTIFACT_NAME and _int(reference.get("bytes"), 1, LIMITS["artifact_bytes"]), "invalid_artifact_reference")
    _sha(reference.get("sha256"))
    _require(reference.get("artifact_version") == artifact.get("artifact_version") == ARTIFACT_VERSION
             and reference.get("algorithm_version") == artifact.get("version") == ALGORITHM_VERSION, "artifact_version", "unsupported_version")
    _require(artifact.get("video_id") == manifest["video_id"], "artifact_video_id_mismatch", "binding_conflict")
    for field, expected in (("video_sha256", manifest["video_sha256"]), ("frames_sha256", inputs["frames"]["sha256"]),
                            ("events_sha256", inputs["events"]["sha256"]), ("primary_timeline_sha256", inputs["primary"]["sha256"])):
        _hash_matches(artifact.get(field), expected, "artifact_input_mismatch")
    _require(artifact.get("scoring_allowed") is False and artifact.get("formal_step_verified") is False
             and artifact.get("technical_grade") is None and artifact.get("semantics") == "independent_projected_ankle_order_candidates_not_formal_FS03_measurement_or_grade", "formal_claim_rejected")
    counts, limits, rows = artifact.get("counts", {}), artifact.get("output_limits", {}), artifact.get("candidates")
    _require(isinstance(counts, dict) and all(_int(counts.get(key)) for key in ("frames", "adjacent_sequence_pairs", "candidates", "no_exchange", "excluded")), "invalid_counts")
    _require(counts["adjacent_sequence_pairs"] == max(0, counts["frames"] - 1) == counts["candidates"] + counts["no_exchange"] + counts["excluded"], "inconsistent_counts")
    for key in ("candidate_quality", "reasons"):
        _require(isinstance(counts.get(key), dict) and len(counts[key]) <= 64
                 and all(re.fullmatch(r"[a-z][a-z0-9_]{0,79}", name) for name in counts[key])
                 and all(_int(value) for value in counts[key].values()), "invalid_count_breakdown")
    _require(set(counts["candidate_quality"]) <= {"suspect", "unvalidated"}, "invalid_quality_counts")
    _require(sum(counts["candidate_quality"].values()) == counts["candidates"] and sum(counts["reasons"].values()) == counts["adjacent_sequence_pairs"], "inconsistent_counts")
    _require(counts["reasons"].get("strict_projected_order_exchange", 0) == counts["candidates"]
             and counts["reasons"].get("same_projected_order", 0) == counts["no_exchange"], "inconsistent_reason_counts")
    _require(isinstance(rows, list) and len(rows) <= LIMITS["candidates"] and isinstance(limits, dict)
             and _int(limits.get("candidate_limit"), 0, LIMITS["candidates"])
             and _int(limits.get("exclusion_limit"), 0, LIMITS["exclusions"]), "candidate_limit", "limit_exceeded")
    exclusions = artifact.get("exclusion_examples")
    _require(isinstance(exclusions, list) and len(exclusions) == min(counts["excluded"], limits["exclusion_limit"])
             and len(rows) == min(counts["candidates"], limits["candidate_limit"])
             and limits.get("retention") == "first_in_source_sequence_order"
             and limits.get("candidates_truncated") is (counts["candidates"] > len(rows))
             and limits.get("exclusions_truncated") is (counts["excluded"] > len(exclusions)), "inconsistent_limits")
    projected = [_candidate(row, manifest["video_sha256"]) for row in rows]
    _require(all(sum(row["candidate_quality_status"] == quality for row in projected)
                 <= counts["candidate_quality"].get(quality, 0) for quality in ("unvalidated", "suspect")), "inconsistent_quality_counts")
    starts = [row["source_frames"][0] for row in projected]
    _require(all(a < b for a, b in zip(starts, starts[1:])), "candidate_order_or_duplicate")
    identity = {key: manifest[key] for key in ("input_bundle_sha256", "source_job_id", "video_id", "video_sha256", "job_summary_sha256", "source_binding", "input_binding_mode")}
    identity["producer"] = {"algorithm_version": ALGORITHM_VERSION, "code": [{key: item[key] for key in ("name", "bytes", "sha256")} for item in code]}
    identity.update(artifact_sha256=reference["sha256"], artifact_version=ARTIFACT_VERSION, algorithm_version=ALGORITHM_VERSION,
                    inputs={key: {field: inputs[key][field] for field in ("bytes", "sha256")} for key in INPUT_NAMES})
    response = dict(schema_version=RESPONSE_VERSION, job_id=job["id"], status="available",
                    reason_code="available_zero_candidates" if counts["candidates"] == 0 else "available_candidates",
                    identity=identity, counts={key: counts[key] for key in ("frames", "adjacent_sequence_pairs", "candidates", "no_exchange", "excluded", "candidate_quality", "reasons")},
                    output_limits={**{key: limits[key] for key in ("candidate_limit", "exclusion_limit", "retention", "candidates_truncated", "exclusions_truncated")}, "stored_candidate_count": len(rows), "total_candidate_count": counts["candidates"]},
                    candidates=projected, scoring_allowed=False, formal_step_verified=False, technical_grade=None)
    _response_size(response)
    return response


def _response_size(response):
    _require(len(json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) <= LIMITS["response_bytes"], "response_size_limit", "limit_exceeded")


def load_foot_crossing_candidates(data_root: Path, job: dict, expected_artifact_sha256: str | None = None) -> dict:
    result = dict(schema_version=RESPONSE_VERSION, job_id=job.get("id"), status="artifact_unavailable", reason_code="artifact_missing",
                  identity=None, counts=None, output_limits=None, candidates=[], scoring_allowed=False, formal_step_verified=False, technical_grade=None)
    try:
        _require(isinstance(job.get("id"), str) and JOB_ID.fullmatch(job["id"]), "invalid_job", "binding_conflict")
        _require(job.get("status") == "succeeded", "job_not_succeeded", "binding_conflict")
        if expected_artifact_sha256 is not None:
            _sha(expected_artifact_sha256)
        root = Path(data_root).resolve()
        index_path = _safe_path(root, "derived-foot-crossing", job["id"], "index.json")
        if not index_path.exists():
            result.update(status="not_computed", reason_code="publication_not_found")
            return result
        _job_identity(job)
        index_raw = _read(index_path, LIMITS["index_bytes"])
        index = _json(index_raw)
        _require(set(index) == {"schema_version", "job_id", "manifest_sha256"} and index.get("schema_version") == INDEX_VERSION and index.get("job_id") == job["id"], "invalid_index")
        manifest_sha = _sha(index.get("manifest_sha256"))
        _require(index["manifest_sha256"] == manifest_sha, "noncanonical_manifest_sha")
        directory = _safe_path(root, "derived-foot-crossing", job["id"], manifest_sha)
        manifest_raw = _read(_safe_path(directory, "manifest.json"), LIMITS["manifest_bytes"])
        _hash_matches(hashlib.sha256(manifest_raw).hexdigest(), manifest_sha, "manifest_hash_mismatch")
        manifest = _json(manifest_raw)
        reference = manifest.get("artifact", {})
        _require(isinstance(reference, dict), "invalid_artifact_reference")
        if expected_artifact_sha256 is not None:
            _hash_matches(reference.get("sha256"), expected_artifact_sha256, "expected_artifact_mismatch")
        artifact_raw = _read(_safe_path(directory, ARTIFACT_NAME), LIMITS["artifact_bytes"])
        _require(len(artifact_raw) == reference.get("bytes"), "artifact_size_mismatch", "binding_conflict")
        _hash_matches(hashlib.sha256(artifact_raw).hexdigest(), reference.get("sha256"), "artifact_hash_mismatch")
        artifact = _json(artifact_raw)
        result = validate_publication(manifest, artifact, job)
        output = _safe_path(root, "runs", job["id"])
        _require(Path(job["output_dir"]).resolve() == output.resolve(), "job_output_directory_mismatch", "binding_conflict")
        summary_raw = _read(_safe_path(output, "summary.json"), LIMITS["summary_bytes"])
        _hash_matches(hashlib.sha256(summary_raw).hexdigest(), manifest["source_summary_sha256"], "source_summary_changed")
        # Full-video summaries can exceed 16 MiB (a 217-second local job is
        # ~24 MiB / 539k nodes). Keep an independent byte/node budget; this
        # summary is never returned by the 1 MiB candidate endpoint.
        saved_summary = _json(summary_raw, strings=LIMITS["summary_bytes"], node_limit=1000000)
        _hash_matches(canonical_sha(saved_summary), manifest["job_summary_sha256"], "summary_database_mismatch")
        for key, name in INPUT_NAMES.items():
            _require(file_stat(_safe_path(output, name)) == manifest["inputs"][key]["stat"], "input_stat_changed", "binding_conflict")
        _require(_read(index_path, LIMITS["index_bytes"]) == index_raw
                 and _read(_safe_path(directory, "manifest.json"), LIMITS["manifest_bytes"]) == manifest_raw
                 and _read(_safe_path(directory, ARTIFACT_NAME), LIMITS["artifact_bytes"]) == artifact_raw,
                 "publication_changed", "binding_conflict")
        _require(_read(_safe_path(output, "summary.json"), LIMITS["summary_bytes"]) == summary_raw,
                 "source_summary_changed", "binding_conflict")
        for key, name in INPUT_NAMES.items():
            _require(file_stat(_safe_path(output, name)) == manifest["inputs"][key]["stat"], "input_stat_changed", "binding_conflict")
        result["identity"]["manifest_sha256"] = manifest_sha
        _response_size(result)
        return result
    except CandidateArtifactError as error:
        status, reason = error.status, error.reason_code
    except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError):
        status, reason = "artifact_unavailable", "artifact_read_or_structure_error"
    return dict(schema_version=RESPONSE_VERSION, job_id=job.get("id"), status=status, reason_code=reason,
                identity=None, counts=None, output_limits=None, candidates=[], scoring_allowed=False, formal_step_verified=False, technical_grade=None)
