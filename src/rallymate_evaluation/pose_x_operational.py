from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "rtmpose-x-operational-extension-2026-09-04.1"
PER_VIDEO_REPORT_VERSION = "rtmpose-x-operational-extension-v1.0.0"
SUMMARY_REPORT_VERSION = "multivideo-rtmpose-x-operational-extension-v1.0.0"
VIDEO_IDS = (
    "3ae77ee3271d67de171585a5c39ddd69",
    "850cb0006b406c7176eeda8d711cd065",
    "8d7754d0de6d315674013d5b69a0b6ba",
)


class PoseXOperationalError(ValueError):
    """Raised when the frozen X operational experiment contract drifts."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _inside(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise PoseXOperationalError(f"{label} must be a non-empty relative path")
    value = Path(relative)
    if value.is_absolute():
        raise PoseXOperationalError(f"{label} must be relative to the workspace")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise PoseXOperationalError(f"{label} escapes the workspace")
    return resolved


def _bound_file(
    root: Path,
    relative: Any,
    expected_sha256: Any,
    label: str,
    *,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    path = _inside(root, relative, label)
    if not path.is_file():
        raise PoseXOperationalError(f"{label} does not exist: {path}")
    observed = sha256_file(path)
    if observed != str(expected_sha256 or "").upper():
        raise PoseXOperationalError(f"{label} SHA-256 drifted")
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise PoseXOperationalError(f"{label} byte size drifted")
    return {
        "path": str(path),
        "relative_path": Path(relative).as_posix(),
        "sha256": observed,
        "bytes": size,
    }


def source_binding(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PoseXOperationalError(f"source file is missing: {resolved}")
    return {
        "path": str(resolved),
        "sha256": sha256_file(resolved),
        "bytes": resolved.stat().st_size,
    }


def verify_source_binding(binding: Any, label: str) -> Path:
    if not isinstance(binding, dict):
        raise PoseXOperationalError(f"{label} binding is missing")
    path = Path(str(binding.get("path", "")))
    if not path.is_file():
        raise PoseXOperationalError(f"{label} bound file is missing")
    if sha256_file(path) != str(binding.get("sha256", "")).upper():
        raise PoseXOperationalError(f"{label} source binding failed")
    if "bytes" in binding and path.stat().st_size != int(binding["bytes"]):
        raise PoseXOperationalError(f"{label} byte size binding failed")
    return path


def _json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseXOperationalError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PoseXOperationalError(f"{label} must be a JSON object")
    return value


def _jsonl(path: str | Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise PoseXOperationalError(
                        f"{label} line {line_number} must be an object"
                    )
                rows.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise PoseXOperationalError(f"{label} is not valid JSONL") from exc
    if not rows:
        raise PoseXOperationalError(f"{label} is empty")
    return rows


def _require_false(value: Any, fields: Iterable[str], label: str) -> None:
    if not isinstance(value, dict):
        raise PoseXOperationalError(f"{label} must be an object")
    drifted = [field for field in fields if value.get(field) is not False]
    if drifted:
        raise PoseXOperationalError(f"{label} unsafe fields: {', '.join(drifted)}")


def _summary_versions(label: str) -> str:
    return {
        "M68": "multivideo-required-joint-superset-router-v1.0.0",
        "M70": "multivideo-pose-profile-context-ablation-v1.0.0",
        "M71": "multivideo-larger-pose-model-extension-v1.0.0",
    }[label]


def load_x_operational_protocol(
    workspace: str | Path,
    protocol_path: str | Path = "models/rtmpose/m97-x-operational-protocol.json",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    path = _inside(root, str(protocol_path), "M97 protocol")
    protocol = _json(path, "M97 protocol")
    if protocol.get("schema_version") != "1.0.0" or protocol.get("protocol_version") != PROTOCOL_VERSION:
        raise PoseXOperationalError("unsupported M97 protocol identity")
    _require_false(
        protocol.get("safety"),
        (
            "changes_deployment_registry",
            "changes_production_default",
            "opens_or_hashes_sealed_holdout",
            "uses_ground_truth",
            "claims_accuracy",
            "promotes_candidate",
            "promotes_F3_or_F4",
            "changes_feature_gate",
            "changes_measurement_gate",
            "changes_event_boundaries",
            "uses_feature_values_for_routing",
            "uses_event_outcomes_for_routing",
            "uses_grades_or_thresholds_for_routing",
            "generates_grades_or_thresholds",
        ),
        "M97 safety",
    )

    production = protocol.get("production_default_binding")
    if not isinstance(production, dict):
        raise PoseXOperationalError("production default binding is missing")
    deployment = _bound_file(
        root,
        production.get("registry_relative_path"),
        production.get("registry_sha256"),
        "production deployment registry",
    )
    deployment_json = _json(deployment["path"], "production deployment registry")
    if deployment_json.get("default_preset") != production.get("preset_id"):
        raise PoseXOperationalError("production default preset drifted")

    candidate = protocol.get("candidate")
    if not isinstance(candidate, dict):
        raise PoseXOperationalError("X candidate binding is missing")
    if (
        candidate.get("candidate_id") != "rtmpose-x-halpe26-384x288-m95-shadow"
        or candidate.get("input_size_hw") != [384, 288]
        or candidate.get("native_keypoint_format") != "halpe26"
        or candidate.get("runtime") != "pytorch"
        or candidate.get("profile") != "analysis"
        or candidate.get("flip_test") is not True
    ):
        raise PoseXOperationalError("X candidate execution identity drifted")
    candidate_registry = _bound_file(
        root,
        candidate.get("source_registry_relative_path"),
        candidate.get("source_registry_sha256"),
        "M95 X candidate registry",
    )
    candidate_registry_json = _json(candidate_registry["path"], "M95 X candidate registry")
    matches = [
        item
        for item in candidate_registry_json.get("candidates", [])
        if item.get("candidate_id") == candidate["candidate_id"]
    ]
    if len(matches) != 1:
        raise PoseXOperationalError("M95 registry does not contain exactly one X candidate")
    registered = matches[0]
    if any(
        registered.get(source_field) != candidate.get(protocol_field)
        for source_field, protocol_field in (
            ("model_relative_path", "model_relative_path"),
            ("checkpoint_sha256", "checkpoint_sha256"),
            ("checkpoint_bytes", "checkpoint_bytes"),
            ("config_runtime_relative_path", "config_runtime_relative_path"),
            ("config_sha256", "config_sha256"),
            ("input_size_hw", "input_size_hw"),
            ("native_keypoint_format", "native_keypoint_format"),
        )
    ):
        raise PoseXOperationalError("M97 candidate does not match the bound M95 registry")
    checkpoint = _bound_file(
        root,
        candidate.get("model_relative_path"),
        candidate.get("checkpoint_sha256"),
        "X checkpoint",
        expected_bytes=int(candidate.get("checkpoint_bytes", -1)),
    )
    config = _bound_file(
        root,
        candidate.get("config_runtime_relative_path"),
        candidate.get("config_sha256"),
        "X config",
    )

    historical = protocol.get("historical_summary_bindings")
    if not isinstance(historical, dict) or tuple(historical) != ("M68", "M70", "M71"):
        raise PoseXOperationalError("historical summaries must be ordered M68, M70, M71")
    historical_reports: dict[str, dict[str, Any]] = {}
    historical_bindings: dict[str, dict[str, Any]] = {}
    for label, binding in historical.items():
        if not isinstance(binding, dict):
            raise PoseXOperationalError(f"{label} summary binding is missing")
        bound = _bound_file(
            root,
            binding.get("relative_path"),
            binding.get("sha256"),
            f"{label} summary",
        )
        report = _json(bound["path"], f"{label} summary")
        if report.get("report_version") != _summary_versions(label):
            raise PoseXOperationalError(f"{label} summary version drifted")
        historical_reports[label] = report
        historical_bindings[label] = bound

    m68_report = historical_reports["M68"]
    m70_report = historical_reports["M70"]
    m71_report = historical_reports["M71"]
    if (
        m68_report.get("scope", {}).get("video_ids")
        != m70_report.get("scope", {}).get("video_ids")
        or m70_report.get("scope", {}).get("video_ids")
        != m71_report.get("scope", {}).get("video_ids")
        or int(m68_report.get("scope", {}).get("indicator_instances", -1))
        != int(m70_report.get("scope", {}).get("indicator_instances", -2))
        or int(m70_report.get("scope", {}).get("indicator_instances", -1))
        != int(m71_report.get("scope", {}).get("indicator_instances", -2))
    ):
        raise PoseXOperationalError("M68/M70/M71 historical scopes differ")
    m68_projection = m68_report.get("routed_projection", {})
    m70_baseline = m70_report.get("baseline", {})
    for m68_field, m70_field in (
        ("feature_vector_complete", "feature_vector_complete"),
        ("feature_vector_incomplete", "feature_vector_incomplete"),
        ("operational_measured", "operational_measured"),
        ("operational_unavailable", "operational_unavailable"),
        ("measurement_hard_fail", "measurement_hard_fail"),
        ("non_hard_fail_feature_incomplete", "non_hard_fail_feature_incomplete"),
    ):
        if int(m68_projection.get(m68_field, -1)) != int(
            m70_baseline.get(m70_field, -2)
        ):
            raise PoseXOperationalError("M70 baseline no longer equals M68 routed projection")
    if m71_report.get("baseline_m68") != m70_baseline:
        raise PoseXOperationalError("M71 M68 baseline differs from the bound M70 baseline")
    if m71_report.get("baseline_m70") != m70_report.get("strategies", {}).get(
        "m69_anchored_extension"
    ):
        raise PoseXOperationalError("M71 M70 baseline differs from the bound M70 strategy")

    scope = protocol.get("development_scope")
    if not isinstance(scope, dict):
        raise PoseXOperationalError("development scope is missing")
    if (
        int(scope.get("video_count", -1)) != 3
        or int(scope.get("target_frame_count", -1)) != 258
        or scope.get("selection_policy")
        != "candidate_required_joint_valid_set_strictly_contains_M70_baseline"
    ):
        raise PoseXOperationalError("development scope identity drifted")
    holdout = scope.get("sealed_holdout")
    if not isinstance(holdout, dict) or holdout.get("use_during_development") is not False:
        raise PoseXOperationalError("sealed holdout guard is invalid")
    holdout_id = str(holdout.get("video_id", ""))
    holdout_relative = str(holdout.get("video_relative_path", ""))
    holdout_sha256 = str(holdout.get("video_sha256", "")).upper()
    if not holdout_id or not holdout_relative or len(holdout_sha256) != 64:
        raise PoseXOperationalError("sealed holdout identity is incomplete")
    _inside(root, holdout_relative, "sealed holdout declared path")

    videos = scope.get("videos")
    if not isinstance(videos, list) or len(videos) != 3:
        raise PoseXOperationalError("exactly three development videos are required")
    verified_videos = []
    target_total = 0
    for expected_id, item in zip(VIDEO_IDS, videos):
        if not isinstance(item, dict) or item.get("video_id") != expected_id:
            raise PoseXOperationalError("development video order or ID drifted")
        video_relative = str(item.get("video_relative_path", ""))
        video_sha256 = str(item.get("video_sha256", "")).upper()
        if (
            expected_id == holdout_id
            or Path(video_relative).as_posix() == Path(holdout_relative).as_posix()
            or video_sha256 == holdout_sha256
        ):
            raise PoseXOperationalError("development scope aliases the sealed holdout")
        target_count = int(item.get("target_frame_count", -1))
        if target_count < 1:
            raise PoseXOperationalError("target frame count must be positive")
        target_total += target_count
        verified_videos.append(
            {
                "video_id": expected_id,
                "target_frame_count": target_count,
                "video": _bound_file(
                    root,
                    video_relative,
                    video_sha256,
                    f"development video {expected_id}",
                ),
                "m70_ablation_report": _bound_file(
                    root,
                    item.get("m70_ablation_report_relative_path"),
                    item.get("m70_ablation_report_sha256"),
                    f"M70 ablation report {expected_id}",
                ),
                "m70_anchored_report": _bound_file(
                    root,
                    item.get("m70_anchored_report_relative_path"),
                    item.get("m70_anchored_report_sha256"),
                    f"M70 anchored report {expected_id}",
                ),
                "m71_l_report": _bound_file(
                    root,
                    item.get("m71_l_report_relative_path"),
                    item.get("m71_l_report_sha256"),
                    f"M71 L report {expected_id}",
                ),
            }
        )
    if target_total != 258:
        raise PoseXOperationalError("per-video target counts do not sum to 258")
    if historical_reports["M70"].get("scope", {}).get("target_event_frame_count") != 258:
        raise PoseXOperationalError("M70 summary target scope drifted")
    if historical_reports["M71"].get("scope", {}).get("target_frame_count") != 258:
        raise PoseXOperationalError("M71 summary target scope drifted")
    if tuple(historical_reports["M71"].get("scope", {}).get("video_ids", [])) != tuple(sorted(VIDEO_IDS)):
        raise PoseXOperationalError("M71 summary video scope drifted")

    return {
        "root": str(root),
        "protocol": {
            "path": str(path),
            "relative_path": Path(protocol_path).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "version": protocol["protocol_version"],
        },
        "production_default": {
            "preset_id": production["preset_id"],
            "registry": deployment,
            "unchanged": True,
        },
        "candidate": {
            **candidate,
            "registry": candidate_registry,
            "checkpoint": checkpoint,
            "config": config,
        },
        "historical_summaries": historical_bindings,
        "historical_reports": historical_reports,
        "scope": scope,
        "holdout_guard": {
            "video_id": holdout_id,
            "declared_relative_path": Path(holdout_relative).as_posix(),
            "declared_sha256": holdout_sha256,
            "file_opened_or_hashed": False,
        },
        "videos": verified_videos,
    }


def comparable_frame_identity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "processed_index": int(item["processed_index"]),
        "source_frame_index": int(item["source_frame_index"]),
        "timestamp_ms": int(item["timestamp_ms"]),
        "track_id": item.get("track_id"),
        "roi_margin": float(item["roi_margin"]),
        "min_roi_size_px": int(item["min_roi_size_px"]),
    }


def assert_m71_comparable_scope(
    ablation: dict[str, Any],
    m71: dict[str, Any],
    *,
    expected_video_id: str,
    expected_target_count: int,
) -> list[dict[str, Any]]:
    if ablation.get("video_id") != expected_video_id or m71.get("video_id") != expected_video_id:
        raise PoseXOperationalError("M70/M71 report video identity drifted")
    ablation_rows = ablation.get("inference", {}).get("frame_results")
    m71_rows = m71.get("inference", {}).get("frame_results")
    if not isinstance(ablation_rows, list) or not isinstance(m71_rows, list):
        raise PoseXOperationalError("M70/M71 frame scopes are missing")
    if len(ablation_rows) != expected_target_count or len(m71_rows) != expected_target_count:
        raise PoseXOperationalError("M70/M71 target frame count drifted")
    ablation_identity = [comparable_frame_identity(item) for item in ablation_rows]
    m71_identity = [comparable_frame_identity(item) for item in m71_rows]
    if ablation_identity != m71_identity:
        raise PoseXOperationalError("M70 and M71 did not retain identical frame/ROI context")
    indexes = [item["processed_index"] for item in ablation_identity]
    sources = [item["source_frame_index"] for item in ablation_identity]
    if len(indexes) != len(set(indexes)) or len(sources) != len(set(sources)):
        raise PoseXOperationalError("target scope repeats a processed or source frame")
    allowed_contexts = {(0.15, 32), (0.30, 32), (0.15, 8)}
    if {
        (item["roi_margin"], item["min_roi_size_px"])
        for item in ablation_identity
    } - allowed_contexts:
        raise PoseXOperationalError("target scope contains an unregistered ROI context")
    return ablation_identity


def identity_set(impact: dict[str, Any], field: str) -> set[tuple[str, str]]:
    return {
        (str(item["event_id"]), str(item["indicator_id"]))
        for item in impact.get(field, [])
    }


def _identity_rows(values: set[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"event_id": event_id, "indicator_id": indicator_id}
        for event_id, indicator_id in sorted(values)
    ]


def compare_recovery_sets(
    candidate: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
    candidate_recovered = identity_set(candidate, "recovered_indicator_instances")
    reference_recovered = identity_set(reference, "recovered_indicator_instances")
    candidate_regressed = identity_set(candidate, "regressed_indicator_instances")
    reference_regressed = identity_set(reference, "regressed_indicator_instances")
    candidate_only = candidate_recovered - reference_recovered
    lost_reference = reference_recovered - candidate_recovered
    return {
        "reference_recovery_count": len(reference_recovered),
        "candidate_recovery_count": len(candidate_recovered),
        "shared_recovery_count": len(candidate_recovered & reference_recovered),
        "additional_recovery_count": len(candidate_only),
        "additional_recoveries": _identity_rows(candidate_only),
        "lost_reference_recovery_count": len(lost_reference),
        "lost_reference_recoveries": _identity_rows(lost_reference),
        "reference_regression_count": len(reference_regressed),
        "candidate_regression_count": len(candidate_regressed),
        "additional_candidate_regression_count": len(candidate_regressed - reference_regressed),
        "additional_candidate_regressions": _identity_rows(
            candidate_regressed - reference_regressed
        ),
    }


def _validate_transition_impact(impact: Any, label: str) -> None:
    if not isinstance(impact, dict):
        raise PoseXOperationalError(f"{label} must be an object")
    recovered = identity_set(impact, "recovered_indicator_instances")
    regressed = identity_set(impact, "regressed_indicator_instances")
    recovered_rows = impact.get("recovered_indicator_instances")
    regressed_rows = impact.get("regressed_indicator_instances")
    if not isinstance(recovered_rows, list) or len(recovered_rows) != len(recovered):
        raise PoseXOperationalError(f"{label} recovered identities are invalid or duplicated")
    if not isinstance(regressed_rows, list) or len(regressed_rows) != len(regressed):
        raise PoseXOperationalError(f"{label} regressed identities are invalid or duplicated")
    if int(impact.get("recovered_indicator_instance_count", -1)) != len(recovered):
        raise PoseXOperationalError(f"{label} recovered count is inconsistent")
    if int(impact.get("regressed_indicator_instance_count", -1)) != len(regressed):
        raise PoseXOperationalError(f"{label} regressed count is inconsistent")
    if recovered & regressed:
        raise PoseXOperationalError(f"{label} marks one identity as recovered and regressed")


def _validate_reference_comparison(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise PoseXOperationalError(f"{label} must be an object")
    reference = int(value.get("reference_recovery_count", -1))
    candidate = int(value.get("candidate_recovery_count", -1))
    shared = int(value.get("shared_recovery_count", -1))
    additional = int(value.get("additional_recovery_count", -1))
    lost = int(value.get("lost_reference_recovery_count", -1))
    reference_regressions = int(value.get("reference_regression_count", -1))
    candidate_regressions = int(value.get("candidate_regression_count", -1))
    additional_regressions = int(value.get("additional_candidate_regression_count", -1))
    if min(
        reference,
        candidate,
        shared,
        additional,
        lost,
        reference_regressions,
        candidate_regressions,
        additional_regressions,
    ) < 0:
        raise PoseXOperationalError(f"{label} contains a negative count")
    if shared + additional != candidate or shared + lost != reference:
        raise PoseXOperationalError(f"{label} recovery set arithmetic is inconsistent")
    for count, field in (
        (additional, "additional_recoveries"),
        (lost, "lost_reference_recoveries"),
        (additional_regressions, "additional_candidate_regressions"),
    ):
        rows = value.get(field)
        if not isinstance(rows, list) or len(rows) != count:
            raise PoseXOperationalError(f"{label}.{field} count is inconsistent")
        identities = {
            (str(item["event_id"]), str(item["indicator_id"])) for item in rows
        }
        if len(identities) != len(rows):
            raise PoseXOperationalError(f"{label}.{field} contains duplicate identities")
    if additional_regressions > candidate_regressions:
        raise PoseXOperationalError(f"{label} regression set arithmetic is inconsistent")


def expected_per_video_status(report: dict[str, Any]) -> str:
    comparison = report.get("comparison_to_m68", {})
    m70 = report.get("comparison_to_m70", {})
    m71 = report.get("comparison_to_m71", {})
    no_m68_regression = all(
        int(comparison.get(kind, {}).get("regressed_indicator_instance_count", -1)) == 0
        for kind in ("feature_vector", "operational_measurement")
    )
    preserves_m70 = all(
        int(m70.get(kind, {}).get("lost_reference_recovery_count", -1)) == 0
        for kind in ("feature_vector", "operational_measurement")
    )
    preserves_m71 = all(
        int(m71.get(kind, {}).get("lost_reference_recovery_count", -1)) == 0
        for kind in ("feature_vector", "operational_measurement")
    )
    if no_m68_regression and preserves_m70 and preserves_m71:
        return "experimental_x_extension_preserves_m71_recovery_requires_truth"
    if no_m68_regression and preserves_m70:
        return "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery"
    return "experimental_x_extension_rejected"


def validate_per_video_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != PER_VIDEO_REPORT_VERSION:
        raise PoseXOperationalError("unsupported X operational per-video report")
    if report.get("video_id") not in VIDEO_IDS:
        raise PoseXOperationalError("X operational report video is outside development scope")
    inference = report.get("inference", {})
    target = int(inference.get("target_frame_count", -1))
    if target < 1 or int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != target:
        raise PoseXOperationalError("X inference accounting is inconsistent")
    if int(inference.get("observation_row_count", -1)) != target:
        raise PoseXOperationalError("X observation accounting is inconsistent")
    reason_counts = inference.get("reason_counts")
    if not isinstance(reason_counts, dict) or sum(int(value) for value in reason_counts.values()) != target:
        raise PoseXOperationalError("X inference reason accounting is inconsistent")
    latency = inference.get("latency_ms")
    if not isinstance(latency, dict):
        raise PoseXOperationalError("X latency diagnostics are missing")
    latency_count = int(latency.get("count", -1))
    if latency_count < 1 or latency_count > target:
        raise PoseXOperationalError("X latency sample count is inconsistent")
    if any(
        not math.isfinite(float(latency.get(field, math.nan)))
        or float(latency.get(field, -1)) < 0
        for field in ("mean", "p50", "p95", "min", "max")
    ):
        raise PoseXOperationalError("X latency diagnostics are invalid")
    comparability = report.get("comparability", {})
    if any(
        comparability.get(field) is not True
        for field in (
            "same_processed_and_source_frames_as_M71",
            "same_timestamps_tracks_and_ROI_contexts_as_M71",
            "same_analysis_profile_and_flip_test_as_M71",
            "same_M70_router_baseline_as_M71",
            "same_M68_fixed_boundary_comparison_baseline_as_M71",
        )
    ):
        raise PoseXOperationalError("X/L comparability proof is incomplete")
    audit = report.get("router_audit", {})
    if int(audit.get("selected_frame_count", -1)) + int(audit.get("rejected_frame_count", -1)) != target:
        raise PoseXOperationalError("required-joint router accounting is inconsistent")
    _require_false(
        audit,
        (
            "selection_uses_feature_values",
            "selection_uses_event_outcomes",
            "selection_uses_grades_or_thresholds",
            "required_joint_validity_regression_allowed",
        ),
        "required-joint router",
    )
    comparison = report.get("comparison_to_m68", {})
    reference_comparisons = {
        "M70": report.get("comparison_to_m70", {}),
        "M71": report.get("comparison_to_m71", {}),
    }
    for kind in ("feature_vector", "operational_measurement"):
        impact = comparison.get(kind)
        _validate_transition_impact(impact, f"comparison_to_m68.{kind}")
        for label, reference in reference_comparisons.items():
            value = reference.get(kind)
            _validate_reference_comparison(value, f"comparison_to_{label}.{kind}")
            if (
                int(value["candidate_recovery_count"])
                != int(impact["recovered_indicator_instance_count"])
                or int(value["candidate_regression_count"])
                != int(impact["regressed_indicator_instance_count"])
            ):
                raise PoseXOperationalError(
                    f"comparison_to_{label}.{kind} candidate counts drifted"
                )
    expected = expected_per_video_status(report)
    if report.get("status") != expected:
        raise PoseXOperationalError("X operational status hides a recovery regression")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get("candidate_promoted") is not False:
        raise PoseXOperationalError("X experiment changed production state")
    if decision.get("operational_gain_is_observability_not_accuracy") is not True:
        raise PoseXOperationalError("X operational semantics are missing")
    _require_false(
        report.get("safety"),
        (
            "accuracy_claim",
            "ground_truth_provided",
            "sealed_holdout_opened_or_hashed",
            "production_enabled",
            "automatic_profile_fallback_enabled",
            "feature_gate_modified",
            "measurement_gate_modified",
            "event_boundaries_modified",
            "routing_uses_feature_values",
            "routing_uses_event_outcomes",
            "routing_uses_grades_or_thresholds",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
        ),
        "X operational safety",
    )


def _sum(reports: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for report in reports:
        value: Any = report
        for key in keys:
            value = value[key]
        total += int(value)
    return total


def _aggregate_reference_comparison(
    reports: list[dict[str, Any]], reference_key: str, kind: str
) -> dict[str, Any]:
    fields = (
        "reference_recovery_count",
        "candidate_recovery_count",
        "shared_recovery_count",
        "additional_recovery_count",
        "lost_reference_recovery_count",
        "reference_regression_count",
        "candidate_regression_count",
        "additional_candidate_regression_count",
    )
    result = {
        field: _sum(reports, reference_key, kind, field) for field in fields
    }
    for list_field in (
        "additional_recoveries",
        "lost_reference_recoveries",
        "additional_candidate_regressions",
    ):
        result[list_field] = [
            {
                "video_id": report["video_id"],
                **item,
            }
            for report in reports
            for item in report[reference_key][kind][list_field]
        ]
    return result


def build_multivideo_summary(
    *,
    verified_protocol: dict[str, Any],
    report_paths: list[str | Path],
) -> dict[str, Any]:
    if len(report_paths) != 3:
        raise PoseXOperationalError("M97 summary requires exactly three per-video reports")
    by_id: dict[str, tuple[dict[str, Any], Path]] = {}
    for report_path in report_paths:
        path = Path(report_path).resolve()
        report = _json(path, "M97 per-video report")
        validate_per_video_report(report)
        video_id = str(report["video_id"])
        if video_id in by_id:
            raise PoseXOperationalError("duplicate M97 per-video report")
        for name, binding in report.get("sources", {}).items():
            verify_source_binding(binding, f"{video_id}.sources.{name}")
        for name, binding in report.get("artifacts", {}).items():
            verify_source_binding(binding, f"{video_id}.artifacts.{name}")
        expected = next(item for item in verified_protocol["videos"] if item["video_id"] == video_id)
        if report["sources"].get("video") != expected["video"]:
            raise PoseXOperationalError(f"{video_id} video binding drifted from protocol")
        if report["sources"].get("m70_ablation_report") != expected["m70_ablation_report"]:
            raise PoseXOperationalError(f"{video_id} M70 ablation binding drifted")
        if report["sources"].get("m70_anchored_report") != expected["m70_anchored_report"]:
            raise PoseXOperationalError(f"{video_id} M70 anchored binding drifted")
        if report["sources"].get("m71_l_report") != expected["m71_l_report"]:
            raise PoseXOperationalError(f"{video_id} M71 L binding drifted")
        by_id[video_id] = (report, path)
    if tuple(by_id) != VIDEO_IDS:
        # Callers may pass any order; normalize after verifying exact membership.
        if set(by_id) != set(VIDEO_IDS):
            raise PoseXOperationalError("M97 reports do not cover the exact three-video scope")
    reports = [by_id[video_id][0] for video_id in VIDEO_IDS]

    latency_values: list[float] = []
    latency_by_video: list[dict[str, Any]] = []
    for item in reports:
        observations = _jsonl(
            item["artifacts"]["x_pose_observations"]["path"],
            f'{item["video_id"]} X observations',
        )
        if len(observations) != int(item["inference"]["target_frame_count"]):
            raise PoseXOperationalError("X observation artifact count drifted")
        values = [
            float(row["latency_ms"])
            for row in observations
            if row.get("latency_ms") is not None
        ]
        observed_distribution = latency_distribution(values)
        if observed_distribution != item["inference"]["latency_ms"]:
            raise PoseXOperationalError("X latency artifact differs from its report")
        latency_values.extend(values)
        latency_by_video.append(
            {
                "video_id": item["video_id"],
                **observed_distribution,
            }
        )

    historical = verified_protocol["historical_reports"]
    m68 = historical["M70"]["baseline"]
    m70 = historical["M70"]["strategies"]["m69_anchored_extension"]
    m71 = historical["M71"]["m71_projection"]
    scope_total = int(historical["M70"]["scope"]["indicator_instances"])
    feature_recovered = _sum(
        reports, "comparison_to_m68", "feature_vector", "recovered_indicator_instance_count"
    )
    feature_regressed = _sum(
        reports, "comparison_to_m68", "feature_vector", "regressed_indicator_instance_count"
    )
    operational_recovered = _sum(
        reports,
        "comparison_to_m68",
        "operational_measurement",
        "recovered_indicator_instance_count",
    )
    operational_regressed = _sum(
        reports,
        "comparison_to_m68",
        "operational_measurement",
        "regressed_indicator_instance_count",
    )
    comparison_m70 = {
        kind: _aggregate_reference_comparison(reports, "comparison_to_m70", kind)
        for kind in ("feature_vector", "operational_measurement")
    }
    comparison_m71 = {
        kind: _aggregate_reference_comparison(reports, "comparison_to_m71", kind)
        for kind in ("feature_vector", "operational_measurement")
    }
    expected_reference_counts = {
        "M70": {
            "feature_vector": (
                int(m70["feature_recovered"]),
                int(m70["feature_regressed"]),
            ),
            "operational_measurement": (
                int(m70["operational_recovered"]),
                int(m70["operational_regressed"]),
            ),
        },
        "M71": {
            "feature_vector": (
                int(m71["feature_recovered_from_m68"]),
                int(m71["feature_regressed_from_m68"]),
            ),
            "operational_measurement": (
                int(m71["operational_recovered_from_m68"]),
                int(m71["operational_regressed_from_m68"]),
            ),
        },
    }
    for label, comparison in (("M70", comparison_m70), ("M71", comparison_m71)):
        for kind in ("feature_vector", "operational_measurement"):
            expected_recovery, expected_regression = expected_reference_counts[label][kind]
            if (
                comparison[kind]["reference_recovery_count"] != expected_recovery
                or comparison[kind]["reference_regression_count"] != expected_regression
            ):
                raise PoseXOperationalError(
                    f"M97 per-video recovery sets do not reconstruct {label} summary"
                )
    projection = {
        "selected_frame_count": _sum(reports, "router_audit", "selected_frame_count"),
        "feature_recovered_from_m68": feature_recovered,
        "feature_regressed_from_m68": feature_regressed,
        "feature_vector_complete": int(m68["feature_vector_complete"]) + feature_recovered - feature_regressed,
        "feature_vector_incomplete": int(m68["feature_vector_incomplete"]) - feature_recovered + feature_regressed,
        "operational_recovered_from_m68": operational_recovered,
        "operational_regressed_from_m68": operational_regressed,
        "operational_measured": int(m68["operational_measured"]) + operational_recovered - operational_regressed,
        "operational_unavailable": int(m68["operational_unavailable"]) - operational_recovered + operational_regressed,
        "measurement_hard_fail": int(m68["measurement_hard_fail"]),
        "non_hard_fail_operational_residual": int(m68["non_hard_fail_feature_incomplete"]) - operational_recovered + operational_regressed,
    }
    no_m68_regression = feature_regressed == 0 and operational_regressed == 0
    preserves_m70 = all(
        comparison_m70[kind]["lost_reference_recovery_count"] == 0
        for kind in comparison_m70
    )
    preserves_m71 = all(
        comparison_m71[kind]["lost_reference_recovery_count"] == 0
        for kind in comparison_m71
    )
    if no_m68_regression and preserves_m70 and preserves_m71:
        status = "experimental_x_extension_preserves_m71_recovery_requires_truth"
    elif no_m68_regression and preserves_m70:
        status = "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery"
    else:
        status = "experimental_x_extension_rejected"
    report = {
        "schema_version": "1.0.0",
        "report_version": SUMMARY_REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scope": {
            "video_count": 3,
            "video_ids": list(VIDEO_IDS),
            "indicator_count": int(historical["M70"]["scope"]["indicator_count"]),
            "indicator_instances": scope_total,
            "target_frame_count": _sum(reports, "inference", "target_frame_count"),
        },
        "sources": {
            "protocol": verified_protocol["protocol"],
            "historical_summaries": verified_protocol["historical_summaries"],
            "per_video_reports": [source_binding(by_id[video_id][1]) for video_id in VIDEO_IDS],
        },
        "candidate": {
            "candidate_id": verified_protocol["candidate"]["candidate_id"],
            "model_sha256": verified_protocol["candidate"]["checkpoint"]["sha256"],
            "config_sha256": verified_protocol["candidate"]["config"]["sha256"],
            "input_size_hw": [384, 288],
            "native_keypoint_format": "halpe26",
            "profile": "analysis",
            "flip_test": True,
            "selection_policy": "required_joint_validity_strict_superset_on_M70_residual_frames",
        },
        "comparability": {
            "same_258_frames_as_M71_L": True,
            "same_per_frame_ROI_context_as_M71_L": True,
            "same_analysis_profile_and_flip_test_as_M71_L": True,
            "same_M70_router_baseline_as_M71_L": True,
            "same_M68_fixed_boundary_baseline_as_M71_L": True,
        },
        "inference": {
            "pose_output_produced": _sum(reports, "inference", "pose_output_produced"),
            "pose_output_missing": _sum(reports, "inference", "pose_output_missing"),
            "latency_ms": latency_distribution(latency_values),
            "latency_ms_by_video": latency_by_video,
            "latency_semantics": (
                "GPU-synchronized PoseEstimator call per target ROI; excludes video "
                "decode, source binding, routing, and fixed-boundary evaluation"
            ),
            "direct_M_or_L_latency_comparator_in_this_run": False,
        },
        "baseline_M68": m68,
        "baseline_M70": m70,
        "baseline_M71": m71,
        "X_projection": projection,
        "comparison_to_M70": comparison_m70,
        "comparison_to_M71": comparison_m71,
        "by_video": [
            {
                "video_id": item["video_id"],
                "target_frames": item["inference"]["target_frame_count"],
                "pose_output_produced": item["inference"]["pose_output_produced"],
                "selected_frames": item["router_audit"]["selected_frame_count"],
                "feature_recovered_from_M68": item["comparison_to_m68"]["feature_vector"]["recovered_indicator_instance_count"],
                "feature_regressed_from_M68": item["comparison_to_m68"]["feature_vector"]["regressed_indicator_instance_count"],
                "operational_recovered_from_M68": item["comparison_to_m68"]["operational_measurement"]["recovered_indicator_instance_count"],
                "operational_regressed_from_M68": item["comparison_to_m68"]["operational_measurement"]["regressed_indicator_instance_count"],
                "additional_operational_recovery_over_M70": item["comparison_to_m70"]["operational_measurement"]["additional_recovery_count"],
                "lost_M70_operational_recovery_count": item["comparison_to_m70"]["operational_measurement"]["lost_reference_recovery_count"],
                "additional_operational_recovery_over_M71": item["comparison_to_m71"]["operational_measurement"]["additional_recovery_count"],
                "lost_M71_operational_recovery_count": item["comparison_to_m71"]["operational_measurement"]["lost_reference_recovery_count"],
            }
            for item in reports
        ],
        "decision": {
            "production_default_changed": False,
            "candidate_promoted": False,
            "M68_regression_free": no_m68_regression,
            "M70_recovered_sets_preserved": preserves_m70,
            "M71_recovered_sets_preserved": preserves_m71,
            "operational_gain_is_observability_not_accuracy": True,
            "next_required_evidence": (
                "human corrected Halpe26 keypoints, per-view feature error, and a "
                "preregistered independent-video release validation"
            ),
        },
        "field_semantics": {
            "feature_complete": "fixed_candidate_event_boundaries_and_unchanged_feature_functions",
            "operational_measured": "feature_complete_and_original_source_quality_gate_preserved",
            "recovery": "M68_unavailable_or_incomplete_instance_becomes_available_or_complete",
            "regression": "M68_available_or_complete_instance_becomes_unavailable_or_incomplete",
            "comparison_to_M70_or_M71": "set_difference_between_recoveries_measured_against_the_same_M68_baseline",
        },
        "safety": {
            "accuracy_claim": False,
            "ground_truth_provided": False,
            "sealed_holdout_opened_or_hashed": False,
            "production_enabled": False,
            "automatic_profile_fallback_enabled": False,
            "feature_gate_modified": False,
            "measurement_gate_modified": False,
            "event_boundaries_modified": False,
            "routing_uses_feature_values": False,
            "routing_uses_event_outcomes": False,
            "routing_uses_grades_or_thresholds": False,
            "grades_generated": False,
            "thresholds_generated": False,
            "maturity_promoted": False,
            "current_three_video_result_is_not_independent_validation": True,
        },
        "not_completed": [
            "human_keypoint_accuracy_comparison",
            "independent_video_release_validation",
            "candidate_promotion",
            "production_default_change",
        ],
    }
    validate_multivideo_summary(report)
    return report


def validate_multivideo_summary(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != SUMMARY_REPORT_VERSION:
        raise PoseXOperationalError("unsupported M97 multivideo report")
    if report.get("status") not in {
        "experimental_x_extension_preserves_m71_recovery_requires_truth",
        "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery",
        "experimental_x_extension_rejected",
    }:
        raise PoseXOperationalError("unsafe M97 summary status")
    scope = report.get("scope", {})
    if (
        int(scope.get("video_count", -1)) != 3
        or tuple(scope.get("video_ids", [])) != VIDEO_IDS
        or int(scope.get("target_frame_count", -1)) != 258
    ):
        raise PoseXOperationalError("M97 summary did not retain the 258-frame scope")
    total = int(scope.get("indicator_instances", -1))
    inference = report.get("inference", {})
    if int(inference.get("pose_output_produced", -1)) + int(
        inference.get("pose_output_missing", -1)
    ) != int(scope.get("target_frame_count", -2)):
        raise PoseXOperationalError("M97 inference accounting is inconsistent")
    latency = inference.get("latency_ms")
    if not isinstance(latency, dict) or not 1 <= int(latency.get("count", -1)) <= int(
        scope.get("target_frame_count", -2)
    ):
        raise PoseXOperationalError("M97 latency accounting is inconsistent")
    if any(
        not math.isfinite(float(latency.get(field, math.nan)))
        or float(latency.get(field, -1)) < 0
        for field in ("mean", "p50", "p95", "min", "max")
    ):
        raise PoseXOperationalError("M97 latency diagnostics are invalid")
    if inference.get("direct_M_or_L_latency_comparator_in_this_run") is not False:
        raise PoseXOperationalError("M97 speed scope is overstated")
    projection = report.get("X_projection", {})
    if int(projection.get("feature_vector_complete", -1)) + int(
        projection.get("feature_vector_incomplete", -1)
    ) != total:
        raise PoseXOperationalError("M97 feature projection is inconsistent")
    if int(projection.get("operational_measured", -1)) + int(
        projection.get("operational_unavailable", -1)
    ) != total:
        raise PoseXOperationalError("M97 operational projection is inconsistent")
    comparisons: dict[str, dict[str, Any]] = {}
    for label in ("M70", "M71"):
        comparison = report.get(f"comparison_to_{label}")
        if not isinstance(comparison, dict):
            raise PoseXOperationalError(f"M97 comparison to {label} is missing")
        comparisons[label] = comparison
        for kind in ("feature_vector", "operational_measurement"):
            _validate_reference_comparison(
                comparison.get(kind), f"comparison_to_{label}.{kind}"
            )
    if any(
        int(comparisons[label][kind]["candidate_recovery_count"])
        != int(
            projection[
                "feature_recovered_from_m68"
                if kind == "feature_vector"
                else "operational_recovered_from_m68"
            ]
        )
        or int(comparisons[label][kind]["candidate_regression_count"])
        != int(
            projection[
                "feature_regressed_from_m68"
                if kind == "feature_vector"
                else "operational_regressed_from_m68"
            ]
        )
        for label in ("M70", "M71")
        for kind in ("feature_vector", "operational_measurement")
    ):
        raise PoseXOperationalError("M97 comparison candidate counts differ from projection")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get("candidate_promoted") is not False:
        raise PoseXOperationalError("M97 summary changed production")
    no_m68 = bool(decision.get("M68_regression_free"))
    preserves_m70 = bool(decision.get("M70_recovered_sets_preserved"))
    preserves_m71 = bool(decision.get("M71_recovered_sets_preserved"))
    actual_no_m68 = all(
        int(projection.get(field, -1)) == 0
        for field in ("feature_regressed_from_m68", "operational_regressed_from_m68")
    )
    actual_preserves_m70 = all(
        int(comparisons["M70"][kind]["lost_reference_recovery_count"]) == 0
        for kind in ("feature_vector", "operational_measurement")
    )
    actual_preserves_m71 = all(
        int(comparisons["M71"][kind]["lost_reference_recovery_count"]) == 0
        for kind in ("feature_vector", "operational_measurement")
    )
    if (no_m68, preserves_m70, preserves_m71) != (
        actual_no_m68,
        actual_preserves_m70,
        actual_preserves_m71,
    ):
        raise PoseXOperationalError("M97 decision booleans differ from measured sets")
    expected = (
        "experimental_x_extension_preserves_m71_recovery_requires_truth"
        if no_m68 and preserves_m70 and preserves_m71
        else (
            "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery"
            if no_m68 and preserves_m70
            else "experimental_x_extension_rejected"
        )
    )
    if report.get("status") != expected:
        raise PoseXOperationalError("M97 summary status hides a regression")
    if decision.get("operational_gain_is_observability_not_accuracy") is not True:
        raise PoseXOperationalError("M97 operational semantics are missing")
    _require_false(
        report.get("safety"),
        (
            "accuracy_claim",
            "ground_truth_provided",
            "sealed_holdout_opened_or_hashed",
            "production_enabled",
            "automatic_profile_fallback_enabled",
            "feature_gate_modified",
            "measurement_gate_modified",
            "event_boundaries_modified",
            "routing_uses_feature_values",
            "routing_uses_event_outcomes",
            "routing_uses_grades_or_thresholds",
            "grades_generated",
            "thresholds_generated",
            "maturity_promoted",
        ),
        "M97 summary safety",
    )
    if report.get("safety", {}).get("current_three_video_result_is_not_independent_validation") is not True:
        raise PoseXOperationalError("M97 independent-validation limitation is missing")


def latency_distribution(values: list[float]) -> dict[str, Any]:
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise PoseXOperationalError("latency values must be finite and non-negative")
    ordered = sorted(values)

    def percentile(percent: float) -> float:
        position = (len(ordered) - 1) * percent / 100.0
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return round(ordered[lower], 6)
        weight = position - lower
        return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)

    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 6),
        "p50": percentile(50),
        "p95": percentile(95),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }
