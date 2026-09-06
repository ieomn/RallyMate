#!/usr/bin/env python3
"""Validate and serve an FS09 public blind-handoff bundle.

This file is deliberately Python-stdlib-only so that the exact script can be
copied into a public handoff and run there without installing RallyMate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import unquote_to_bytes, urlsplit


MANIFEST_NAME = "handoff-manifest.json"
ENTRYPOINT = "review.html"
LAUNCHER_NAME = "serve_fs09_phase_blind_handoff.py"
SCHEMA_VERSION = "1.1.0"
BUNDLE_VERSION = "fs09-phase-blind-handoff-v1.1.0"
BUNDLE_STATUS = "technical_handoff_verified_external_protocol_required"
MANIFEST_KEYS = {
    "schema_version",
    "bundle_version",
    "bundle_id",
    "generated_at",
    "status",
    "source_authority",
    "scope",
    "entrypoint",
    "server_launcher",
    "artifacts",
    "content_root_sha256",
    "safety",
}
ARTIFACT_KEYS = {"path", "bytes", "sha256"}
SOURCE_AUTHORITY_KEYS = {
    "kind",
    "manifest_sha256",
    "manifest_generated_at",
    "analysis_plan_sha256",
    "plan_version",
    "pack_version",
    "task_contract_sha256",
    "candidate_contract_sha256",
}
SCOPE_KEYS = {"task_ids", "media_paths"}
EXPECTED_TASK_IDS = [
    "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-001",
    "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-002",
]
EXPECTED_MEDIA_PATHS = [
    "media/task-001-browser.mp4",
    "media/task-002-browser.mp4",
]
EXPECTED_ARTIFACT_PATHS = [
    "OPERATOR_README.md",
    "analysis-plan.json",
    "fs09-phase-truth-workbench.css",
    "fs09-phase-truth-workbench.js",
    *EXPECTED_MEDIA_PATHS,
    ENTRYPOINT,
    LAUNCHER_NAME,
]
BOOTSTRAP_OPEN = '<script id="fs09-phase-truth-bootstrap" type="application/json">'
PLAN_KEYS = {
    "schema_version",
    "plan_version",
    "plan_id",
    "artifact_scope",
    "status",
    "frozen_at",
    "registration_claim",
    "study_binding",
    "population",
    "metric_plan",
    "interpretation_boundaries",
    "decision_authority",
}
STUDY_BINDING_KEYS = {
    "pack_version",
    "evaluator_version",
    "video_ids",
    "view_groups",
    "task_ids",
    "indicator_ids",
    "event_codes",
    "phase_keys",
    "required_features",
    "task_contract_sha256",
    "candidate_contract_sha256",
    "tasks_artifact_sha256",
    "sealed_candidates_artifact_sha256",
    "workbench_artifact_sha256",
    "blind_review_target_selection",
    "blind_handoff_version",
    "blind_handoff_artifact_sha256",
    "export_provenance_contract",
    "annotation_revision_contract",
    "source_artifact_sha256",
}
EXPECTED_PLAN_VERSION = "fs09-phase-analysis-plan-v1.3.0"
EXPECTED_PLAN_ID = "m88-m77-fs09-two-task-descriptive-v3"
EXPECTED_PLAN_FROZEN_AT = "2026-08-30T01:52:00Z"
EXPECTED_PACK_VERSION = "fs09-phase-truth-pack-v1.3.0"
EXPECTED_EVALUATOR_VERSION = "fs09-phase-truth-evaluation-v1.3.0"
EXPECTED_TASK_CONTRACT_SHA256 = (
    "4BFD62348E254A8EF7AFE9244FCC3E4349878F49761022C425149F357C2AE7B8"
)
EXPECTED_CANDIDATE_CONTRACT_SHA256 = (
    "9D933FE215D654F371973E692A98A82D4DE874360AAD1BEB07D2E0C86F643805"
)
EXPECTED_TASKS_ARTIFACT_SHA256 = (
    "E44A6B633E9744771D8DEA2E95CDEBE552F21D6B459BBC87C5BA51D293B9F8F2"
)
EXPECTED_SEALED_CANDIDATES_ARTIFACT_SHA256 = (
    "1944D8FF92629852851617688A34BB9C42F11469062044704A2571864437CF10"
)
EXPECTED_MEDIA_SHA256 = {
    EXPECTED_MEDIA_PATHS[0]: "FADDE9A2A9AD794B04C371D0293D871E516DBF0D11B286F5DFEE582E64F756EF",
    EXPECTED_MEDIA_PATHS[1]: "028F4DD40B203A866F75378A61A4562866C73F2E2B711428302685F11A7DB5FD",
}
EXPECTED_CSS_SHA256 = "0052DFD0D3474607C4BB4C20C862EF03667581D5AB0FF56F7E9E40F5B8A56E95"
EXPECTED_JS_SHA256 = "390AB69F44893AF9FB62EF03357A4C251984656B1D8E8EA110D5BA7446D17340"
EXPECTED_CANONICAL_REVIEW_SHA256 = (
    "0AE0C9F90D0D7DD2EFB12FE791B5D954D153853BA00B96E4E6A81090E6858DE5"
)
EXPECTED_PUBLIC_README_SHA256 = (
    "FD512EE3B5A10250F8204664C0CED6DFAB16ABA22E40B44DD916F7D6867CD07E"
)
EXPECTED_PHASE_KEYS = [
    "peak_speed_ms",
    "deceleration_peak_ms",
    "restabilization_onset_ms",
    "stable_control_onset_ms",
]
EXPECTED_REQUIRED_FEATURES = [
    "stability_duration_ms",
    "hip_center_speed_drop_body_s",
    "double_support_proxy_duration_ms",
    "torso_lean_variability_deg",
    "shoulder_hip_angular_velocity_change_deg_s",
    "hip_deceleration_to_double_support_proxy_ms",
]
EXPECTED_TASK_WINDOWS = [
    {
        "task_id": EXPECTED_TASK_IDS[0],
        "review_start_ms": 331000,
        "review_end_ms": 336000,
        "target_selection_anchor_ms": 333500,
    },
    {
        "task_id": EXPECTED_TASK_IDS[1],
        "review_start_ms": 449500,
        "review_end_ms": 454500,
        "target_selection_anchor_ms": 452000,
    },
]
EXPECTED_ANNOTATION_FIELDS = [
    "annotation_id",
    "task_id",
    "annotator_id",
    "handoff_bundle_id",
    "analysis_plan_sha256",
    "event_present",
    "event_start_ms",
    "event_end_ms",
    "event_reason",
    "peak_speed_status",
    "peak_speed_ms",
    "peak_speed_reason",
    "deceleration_peak_status",
    "deceleration_peak_ms",
    "deceleration_peak_reason",
    "restabilization_onset_status",
    "restabilization_onset_ms",
    "restabilization_onset_reason",
    "stable_control_onset_status",
    "stable_control_onset_ms",
    "stable_control_onset_reason",
    "confidence",
    "notes",
    "annotation_revision_sha256",
    "annotated_at",
    "exported_at",
]
EXPECTED_ADJUDICATION_FIELDS = [
    "adjudication_id",
    "task_id",
    "source_annotation_ids",
    "source_annotation_revision_sha256s",
    "reviewer_id",
    *EXPECTED_ANNOTATION_FIELDS[3:-3],
    "adjudicated_at",
    "exported_at",
    "status",
]
EXPECTED_SAFETY = {
    "relative_artifact_paths_only": True,
    "canonical_manifest_included": False,
    "tasks_file_included": False,
    "sealed_candidates_included": False,
    "compiled_truth_included": False,
    "absolute_paths_included": False,
    "candidate_boundaries_embedded_in_annotation_ui": False,
    "candidate_phases_embedded_in_annotation_ui": False,
    "coarse_target_selection_anchor_included": True,
    "target_selection_anchor_is_candidate_boundary_or_phase": False,
    "exact_candidate_boundaries_derivable_from_public_window": False,
    "exact_candidate_boundaries_or_phases_embedded_in_bundle": False,
    "isolated_public_bundle_only_required": True,
    "coarse_anchor_is_confidential": False,
    "candidate_contract_hash_is_confidential": False,
    "sealed_candidates_artifact_hash_is_confidential": False,
    "candidate_derived_hashes_are_confidential": False,
    "candidate_derived_hashes_are_content_commitments_only": True,
    "pose_overlay_embedded_in_annotation_ui": False,
    "directory_listing_enabled": False,
    "exact_server_allowlist": True,
    "source_pack_required_for_authority_replay": True,
    "hash_is_identity_or_signature": False,
    "annotation_execution_authorized": False,
    "external_protocol_receipt_verified": False,
    "production_enabled": False,
    "grade_generated": False,
    "threshold_generated": False,
    "maturity_promoted": False,
}
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
BUNDLE_ID_RE = re.compile(r"^m88-fs09-blind-[0-9A-F]{64}$")
RFC3339_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T"
    r"([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.\d+)?Z$"
)
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class BundleValidationError(ValueError):
    """The handoff directory is not the exact bundle described by its manifest."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures machine readable too."""

    def error(self, message: str) -> None:
        _emit_json({"ok": False, "error": message}, stream=sys.stderr)
        raise SystemExit(2)


def _emit_json(value: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
        file=stream,
        flush=True,
    )


def _reject_json_constant(value: str) -> None:
    raise BundleValidationError(f"JSON contains invalid constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json_object(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleValidationError(f"{name} is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except BundleValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"{name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"{name} must be a JSON object")
    return value


def _load_manifest(raw: bytes) -> dict[str, Any]:
    return _load_json_object(raw, name=MANIFEST_NAME)


def _require_nonempty_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BundleValidationError(f"{name} must be a non-empty trimmed string")
    return value


def _require_rfc3339_timestamp(value: Any, *, name: str) -> datetime:
    text = _require_nonempty_string(value, name=name)
    match = RFC3339_TIMESTAMP_RE.fullmatch(text)
    if match is None:
        raise BundleValidationError(
            f"{name} must be a canonical RFC3339 UTC timestamp ending in Z"
        )
    year, month, day, hour, minute, second = (
        int(part) for part in match.groups()[:6]
    )
    try:
        datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise BundleValidationError(f"{name} is not a real timestamp") from exc
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _require_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BundleValidationError(f"{name} must be an uppercase SHA-256 digest")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleValidationError("value is not canonical JSON") from exc


def _json_equal(left: Any, right: Any) -> bool:
    return _canonical_json_bytes(left) == _canonical_json_bytes(right)


def _require_exact_object(
    value: Any, expected_keys: set[str], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise BundleValidationError(f"{name} does not have the exact required keys")
    return value


def _expected_bootstrap_tasks() -> list[dict[str, Any]]:
    selection_rule = (
        "label_the_single_visually_observable_FS09_action_identified_by_the_"
        "pre_frozen_coarse_anchor;anchor_is_a_selection_cue_only_and_does_not_"
        "constrain_submitted_boundary_or_phase"
    )
    return [
        {
            "schema_version": "1.0.0",
            "task_id": window["task_id"],
            "video_id": "8d7754d0de6d315674013d5b69a0b6ba",
            "view_group": "fixed-camera-8d7754d0",
            "event_code": "FS09",
            "indicator_id": "FS09-M05",
            "review_start_ms": window["review_start_ms"],
            "review_end_ms": window["review_end_ms"],
            "source_time_offset_ms": window["review_start_ms"],
            "target_selection_anchor_ms": window["target_selection_anchor_ms"],
            "target_selection_rule": selection_rule,
            "review_clip_id": f"task-{index:03d}-browser.mp4",
            "required_phase_keys": EXPECTED_PHASE_KEYS,
            "annotation_semantics": (
                "blind_full_FS09_event_and_visible_phase_truth;"
                "stable_control_is_visual_control_not_ground_contact_or_force"
            ),
            "truth_status": "pending",
        }
        for index, window in enumerate(EXPECTED_TASK_WINDOWS, start=1)
    ]


def _validate_analysis_plan(
    plan_raw: bytes,
    *,
    source_authority: dict[str, Any],
    source_manifest_at: datetime,
    records_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    plan = _load_json_object(plan_raw, name="analysis-plan.json")
    _require_exact_object(plan, PLAN_KEYS, name="analysis plan")
    expected_header = {
        "schema_version": "1.0.0",
        "plan_version": EXPECTED_PLAN_VERSION,
        "plan_id": EXPECTED_PLAN_ID,
        "artifact_scope": "repository_bound_descriptive_analysis_plan",
        "status": "frozen_before_human_label_intake",
        "frozen_at": EXPECTED_PLAN_FROZEN_AT,
    }
    if any(plan.get(key) != value for key, value in expected_header.items()):
        raise BundleValidationError("analysis plan v1.3 header drifted")
    frozen_at = _require_rfc3339_timestamp(plan["frozen_at"], name="plan.frozen_at")
    if frozen_at >= source_manifest_at:
        raise BundleValidationError("analysis plan was not frozen before the source manifest")
    if (
        source_authority["plan_version"] != EXPECTED_PLAN_VERSION
        or source_authority["pack_version"] != EXPECTED_PACK_VERSION
    ):
        raise BundleValidationError("source authority plan/pack version drifted")

    expected_registration = {
        "kind": "repository_content_hash_binding",
        "external_registry_present": False,
        "human_registrant_asserted": False,
        "externally_registered_acceptance_protocol": False,
        "human_labels_present_at_freeze": False,
        "non_null_pilot_results_present_at_freeze": False,
    }
    if not _json_equal(plan["registration_claim"], expected_registration):
        raise BundleValidationError("analysis plan registration claim drifted")

    study = _require_exact_object(
        plan["study_binding"], STUDY_BINDING_KEYS, name="analysis plan study_binding"
    )
    expected_study_identity = {
        "pack_version": EXPECTED_PACK_VERSION,
        "evaluator_version": EXPECTED_EVALUATOR_VERSION,
        "video_ids": ["8d7754d0de6d315674013d5b69a0b6ba"],
        "view_groups": ["fixed-camera-8d7754d0"],
        "task_ids": EXPECTED_TASK_IDS,
        "indicator_ids": ["FS09-M05"],
        "event_codes": ["FS09"],
        "phase_keys": EXPECTED_PHASE_KEYS,
        "required_features": EXPECTED_REQUIRED_FEATURES,
        "blind_handoff_version": BUNDLE_VERSION,
    }
    if any(
        not _json_equal(study.get(key), value)
        for key, value in expected_study_identity.items()
    ):
        raise BundleValidationError("analysis plan study identity drifted")

    expected_contract_hashes = {
        "task_contract_sha256": EXPECTED_TASK_CONTRACT_SHA256,
        "candidate_contract_sha256": EXPECTED_CANDIDATE_CONTRACT_SHA256,
        "tasks_artifact_sha256": EXPECTED_TASKS_ARTIFACT_SHA256,
        "sealed_candidates_artifact_sha256": (
            EXPECTED_SEALED_CANDIDATES_ARTIFACT_SHA256
        ),
    }
    for key, expected in expected_contract_hashes.items():
        if _require_sha256(study.get(key), name=f"study_binding.{key}") != expected:
            raise BundleValidationError(f"analysis plan {key} drifted")
    if (
        study["task_contract_sha256"]
        != source_authority["task_contract_sha256"]
        or study["candidate_contract_sha256"]
        != source_authority["candidate_contract_sha256"]
    ):
        raise BundleValidationError("analysis plan/source contract authority drifted")

    expected_target_selection = {
        "kind": "pre_frozen_candidate_selected_coarse_anchor",
        "anchor_selects_action_only": True,
        "anchor_constrains_submitted_boundary_or_phase": False,
        "anchor_is_candidate_boundary_or_phase": False,
        "exact_candidate_boundaries_derivable_from_public_window": False,
        "candidate_selected_coarse_localization_disclosed": True,
        "tasks": EXPECTED_TASK_WINDOWS,
    }
    if not _json_equal(
        study["blind_review_target_selection"], expected_target_selection
    ):
        raise BundleValidationError("analysis plan target-selection contract drifted")

    workbench_hashes = _require_exact_object(
        study["workbench_artifact_sha256"],
        {
            "fs09-phase-truth-workbench.js",
            "fs09-phase-truth-workbench.css",
            "review.html",
            "OPERATOR_README.md",
        },
        name="analysis plan workbench hashes",
    )
    handoff_hashes = _require_exact_object(
        study["blind_handoff_artifact_sha256"],
        {"OPERATOR_README.md", LAUNCHER_NAME},
        name="analysis plan handoff hashes",
    )
    for group_name, hashes in (
        ("workbench", workbench_hashes),
        ("handoff", handoff_hashes),
    ):
        for key, value in hashes.items():
            _require_sha256(
                value, name=f"analysis plan {group_name} artifact {key}"
            )
    if workbench_hashes["fs09-phase-truth-workbench.css"] != EXPECTED_CSS_SHA256:
        raise BundleValidationError("analysis plan CSS authority drifted")
    if workbench_hashes["fs09-phase-truth-workbench.js"] != EXPECTED_JS_SHA256:
        raise BundleValidationError("analysis plan JS authority drifted")
    if workbench_hashes["review.html"] != EXPECTED_CANONICAL_REVIEW_SHA256:
        raise BundleValidationError("analysis plan canonical review authority drifted")
    if handoff_hashes["OPERATOR_README.md"] != EXPECTED_PUBLIC_README_SHA256:
        raise BundleValidationError("analysis plan public README authority drifted")
    for path in (
        "fs09-phase-truth-workbench.js",
        "fs09-phase-truth-workbench.css",
    ):
        if workbench_hashes[path] != records_by_path[path]["sha256"]:
            raise BundleValidationError(f"plan-bound workbench artifact drifted: {path}")
    for path in ("OPERATOR_README.md", LAUNCHER_NAME):
        if handoff_hashes[path] != records_by_path[path]["sha256"]:
            raise BundleValidationError(f"plan-bound handoff artifact drifted: {path}")

    source_hashes = _require_exact_object(
        study["source_artifact_sha256"],
        {
            "m74_report",
            "review_clip_manifest",
            "source_video",
            "task_001_review_clip",
            "task_002_review_clip",
        },
        name="analysis plan source hashes",
    )
    for key, value in source_hashes.items():
        _require_sha256(value, name=f"analysis plan source {key}")
    if (
        source_hashes["task_001_review_clip"] != EXPECTED_MEDIA_SHA256[EXPECTED_MEDIA_PATHS[0]]
        or source_hashes["task_002_review_clip"]
        != EXPECTED_MEDIA_SHA256[EXPECTED_MEDIA_PATHS[1]]
    ):
        raise BundleValidationError("analysis plan review-clip authority drifted")
    for path, expected in EXPECTED_MEDIA_SHA256.items():
        if records_by_path[path]["sha256"] != expected:
            raise BundleValidationError(f"public review clip drifted: {path}")

    expected_export_provenance = {
        "public_blind_handoff_required": True,
        "handoff_bundle_id_on_every_row": True,
        "analysis_plan_sha256_on_every_row": True,
        "browser_storage_scoped_by_bundle_and_plan": True,
        "decision_timestamp_persisted_at_valid_save": True,
        "decision_edit_updates_decision_timestamp": True,
        "repeat_export_preserves_decision_timestamp": True,
        "export_timestamp_recorded_separately": True,
        "mixed_handoff_exports_allowed": False,
        "private_authority_pack_annotation_allowed": False,
        "external_protocol_receipt_required_before_first_decision": True,
        "technical_handoff_allows_human_export": False,
        "adjudication_binds_exact_annotation_revisions": True,
    }
    if not _json_equal(
        study["export_provenance_contract"], expected_export_provenance
    ):
        raise BundleValidationError("analysis plan export provenance drifted")
    expected_revision_contract = {
        "algorithm": "sha256_of_canonical_annotation_row_without_revision_or_exported_at",
        "digest_encoding": "uppercase_hex",
        "repeat_export_same_decision_revision_stable": True,
        "changed_decision_or_decision_time_changes_revision": True,
        "adjudication_source_ids_sorted": True,
        "adjudication_source_revision_sha256s_same_order": True,
        "source_revision_change_requires_re_adjudication": True,
        "digest_is_signature_or_trusted_timestamp": False,
    }
    if not _json_equal(
        study["annotation_revision_contract"], expected_revision_contract
    ):
        raise BundleValidationError("analysis plan annotation revision contract drifted")

    expected_population = {
        "sampling_frame": "two_preselected_sealed_candidate_actions_with_coarse_selection_anchors",
        "selection_timing": "selected_before_human_phase_truth",
        "video_count": 1,
        "task_count": 2,
        "candidate_window_count": 2,
        "all_accepted_adjudicated_tasks_included": True,
        "full_timeline_reviewed": False,
        "event_precision_estimable": False,
        "event_recall_estimable": False,
        "event_f1_estimable": False,
        "missed_event_rate_estimable": False,
        "population_prevalence_estimable": False,
        "inferential_statistics_allowed": False,
        "generalization_allowed": False,
        "post_result_exclusion_allowed": False,
    }
    if not _json_equal(plan["population"], expected_population):
        raise BundleValidationError("analysis plan population contract drifted")

    expected_interpretation = {
        "permitted_conclusion": "descriptive_disagreement_for_the_two_bound_review_windows_only",
        "plan_is_external_preregistration": False,
        "supports_pass_fail_decision": False,
        "supports_model_selection": False,
        "supports_accuracy_claim": False,
        "supports_population_generalization": False,
        "candidate_outputs_are_truth": False,
        "feature_boundary_difference_is_pose_accuracy": False,
        "feature_boundary_difference_is_total_feature_error": False,
        "manual_stable_control_is_ground_contact_or_force_truth": False,
        "low_ankle_motion_proxy_is_visual_stable_control_truth": False,
        "low_ankle_motion_proxy_is_double_support_truth": False,
        "low_ankle_motion_proxy_is_foot_contact_truth": False,
        "low_ankle_motion_proxy_is_force_or_load_transfer_truth": False,
        "two_task_pilot_can_support_maturity_promotion": False,
        "private_authority_pack_may_be_served_or_distributed": False,
        "public_blind_handoff_required_for_human_annotation": True,
        "handoff_identity_required_on_every_human_export": True,
        "decision_time_is_export_time": False,
        "client_reported_times_are_trusted_timestamps": False,
        "exact_candidate_boundaries_derivable_from_public_window": False,
        "candidate_selected_coarse_localization_disclosed": True,
        "target_selection_anchor_constrains_human_boundaries": False,
        "technical_handoff_is_annotation_authorization": False,
        "external_protocol_receipt_required_before_first_human_decision": True,
    }
    if not _json_equal(plan["interpretation_boundaries"], expected_interpretation):
        raise BundleValidationError("analysis plan interpretation boundaries drifted")
    expected_authority = {
        "classification": "descriptive_diagnostic_only_no_pass_fail",
        "acceptance_thresholds": None,
        "scoring_thresholds": None,
        "external_acceptance_protocol_still_required": True,
        "annotation_execution_authorized": False,
        "runtime_event_change_allowed": False,
        "runtime_feature_change_allowed": False,
        "model_selection_allowed": False,
        "grade_generation_allowed": False,
        "threshold_generation_allowed": False,
        "f2_to_f3_allowed": False,
        "f3_to_f4_allowed": False,
        "production_release_allowed": False,
    }
    if not _json_equal(plan["decision_authority"], expected_authority):
        raise BundleValidationError("analysis plan decision authority drifted")
    expected_metric_plan = {
        "numeric_convention": {
            "error_sign": "candidate_minus_truth",
            "p95_method": "numpy_quantile_linear",
            "output_rounding_decimal_places": 8,
            "non_finite_values_are_numeric": False,
            "missing_numeric_value": None,
            "zero_fill_missing_allowed": False,
            "imputation_allowed": False,
        },
        "event_interval": {
            "eligibility": "adjudicated_event_present_true",
            "task_accounting": "all_two_accepted_adjudicated_tasks_retained_in_counts",
            "reported_metrics": [
                "segment_iou_mean",
                "boundary_start_mae_ms",
                "boundary_end_mae_ms",
                "boundary_start_bias_candidate_minus_truth_ms",
                "boundary_end_bias_candidate_minus_truth_ms",
            ],
            "event_present_false_handling": "retain_in_counts_exclude_from_interval_numerics",
            "truth_absent_count_semantics": "count_of_adjudicated_event_present_false_not_verified_detector_false_positives",
            "zero_eligible_count": 0,
            "zero_eligible_numeric_values": None,
            "unsupported_estimands": [
                "event_precision",
                "event_recall",
                "event_f1",
                "missed_event_rate",
                "population_prevalence",
            ],
        },
        "phase_timing": {
            "phase_keys": EXPECTED_PHASE_KEYS,
            "eligibility_all_of": [
                "manual_event_present",
                "truth_phase_status_observed",
                "candidate_phase_timestamp_present",
            ],
            "reported_aggregate_fields": [
                "count",
                "mae",
                "p95_absolute_error",
                "bias_candidate_minus_truth",
            ],
            "retained_non_numeric_detail_cases": [
                "truth_phase_not_observed",
                "truth_phase_unobservable",
                "candidate_phase_timestamp_missing",
            ],
            "excluded_from_numeric_aggregates": [
                "manual_event_absent",
                "truth_phase_not_observed",
                "truth_phase_unobservable",
                "candidate_phase_timestamp_missing",
            ],
            "zero_eligible_count": 0,
            "zero_eligible_numeric_values": None,
            "imputation_allowed": False,
        },
        "feature_boundary_conditioning": {
            "features": EXPECTED_REQUIRED_FEATURES,
            "detail_rows_per_manual_present_task": len(EXPECTED_REQUIRED_FEATURES),
            "candidate_condition": "candidate_event_and_phase_boundaries",
            "reference_condition": "manual_event_and_phase_boundaries",
            "pose_source": "same_model_pose_held_fixed",
            "numeric_eligibility_all_of": [
                "candidate_feature_valid",
                "manual_boundary_conditioned_feature_valid",
                "both_values_finite_numeric",
            ],
            "reported_aggregate_fields": [
                "count",
                "mae",
                "p95_absolute_error",
                "bias_candidate_minus_truth",
            ],
            "invalid_or_unavailable_handling": "retain_values_reasons_and_null_differences_exclude_from_numeric_aggregates",
            "manual_event_absent_handling": "retain_task_count_no_feature_comparison_rows",
            "zero_eligible_count": 0,
            "zero_eligible_numeric_values": None,
            "imputation_allowed": False,
        },
    }
    if not _json_equal(plan["metric_plan"], expected_metric_plan):
        raise BundleValidationError("analysis plan metric plan drifted")
    return plan


def _validate_public_review(
    raw: bytes,
    *,
    manifest: dict[str, Any],
    plan: dict[str, Any],
    records_by_path: dict[str, dict[str, Any]],
) -> None:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleValidationError("review.html is not UTF-8") from exc
    if html.count(BOOTSTRAP_OPEN) != 1:
        raise BundleValidationError("review.html bootstrap is missing or ambiguous")
    data_start = html.index(BOOTSTRAP_OPEN) + len(BOOTSTRAP_OPEN)
    data_end = html.find("</script>", data_start)
    if data_end < 0:
        raise BundleValidationError("review.html bootstrap is not closed")
    bootstrap = _load_json_object(
        html[data_start:data_end].replace("<\\/", "</").encode("utf-8"),
        name="review.html bootstrap",
    )
    expected_bootstrap_keys = {
        "schema_version",
        "pack_version",
        "handoff_bundle_id",
        "analysis_plan_sha256",
        "annotation_execution_authorized",
        "external_protocol_receipt_verified",
        "review_clips",
        "review_clip_fps",
        "workbench_asset_sha256",
        "tasks",
        "phase_keys",
        "annotation_fields",
        "adjudication_fields",
        "required_annotators",
    }
    _require_exact_object(
        bootstrap, expected_bootstrap_keys, name="review.html bootstrap"
    )
    expected_identity = {
        "schema_version": "1.0.0",
        "pack_version": EXPECTED_PACK_VERSION,
        "handoff_bundle_id": manifest["bundle_id"],
        "analysis_plan_sha256": manifest["source_authority"][
            "analysis_plan_sha256"
        ],
        "annotation_execution_authorized": False,
        "external_protocol_receipt_verified": False,
        "review_clips": dict(zip(EXPECTED_TASK_IDS, EXPECTED_MEDIA_PATHS, strict=True)),
        "review_clip_fps": {task_id: 24.0 for task_id in EXPECTED_TASK_IDS},
        "workbench_asset_sha256": {
            "js": records_by_path["fs09-phase-truth-workbench.js"]["sha256"],
            "css": records_by_path["fs09-phase-truth-workbench.css"]["sha256"],
        },
        "tasks": _expected_bootstrap_tasks(),
        "phase_keys": EXPECTED_PHASE_KEYS,
        "annotation_fields": EXPECTED_ANNOTATION_FIELDS,
        "adjudication_fields": EXPECTED_ADJUDICATION_FIELDS,
        "required_annotators": 2,
    }
    if not _json_equal(bootstrap, expected_identity):
        raise BundleValidationError("review.html bootstrap contract drifted")

    canonical_bootstrap = dict(bootstrap)
    canonical_bootstrap["handoff_bundle_id"] = None
    canonical_bootstrap["analysis_plan_sha256"] = None
    encoded = json.dumps(
        canonical_bootstrap, ensure_ascii=False, allow_nan=False
    ).replace("</", "<\\/")
    canonical_html = html[:data_start] + encoded + html[data_end:]
    identity = (
        '<p id="blind-handoff-identity"><strong>Portable blind handoff</strong> · '
        f'<code>{manifest["bundle_id"]}</code> · analysis plan SHA-256 '
        f'<code>{manifest["source_authority"]["analysis_plan_sha256"]}</code></p>'
    )
    if canonical_html.count(identity) != 1:
        raise BundleValidationError("review.html identity paragraph drifted")
    canonical_html = canonical_html.replace(identity, "", 1)
    expected_review_sha = plan["study_binding"]["workbench_artifact_sha256"][
        "review.html"
    ]
    canonical_review_sha = hashlib.sha256(canonical_html.encode("utf-8")).hexdigest().upper()
    if (
        expected_review_sha != EXPECTED_CANONICAL_REVIEW_SHA256
        or canonical_review_sha != EXPECTED_CANONICAL_REVIEW_SHA256
    ):
        raise BundleValidationError("review.html is not the reversible plan-bound workbench")


def _safe_bundle_path(value: Any, *, name: str) -> str:
    text = _require_nonempty_string(value, name=name)
    if "\\" in text or "\x00" in text:
        raise BundleValidationError(f"{name} contains a forbidden character")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    parts = text.split("/")
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise BundleValidationError(f"{name} must be a normalized relative path")
    if len(parts) == 1:
        pass
    elif len(parts) == 2 and parts[0] == "media":
        pass
    else:
        raise BundleValidationError(f"{name} must be directly under root or media/")
    if text == MANIFEST_NAME:
        raise BundleValidationError(f"{name} may not name {MANIFEST_NAME}")
    return text


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _snapshot_regular_file(path: Path, *, name: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BundleValidationError(f"missing file: {name}") from exc
    if _is_link_like(path) or not path.is_file():
        raise BundleValidationError(f"{name} must be a regular non-symlink file")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise BundleValidationError(f"could not read file: {name}") from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or len(raw) != after.st_size:
        raise BundleValidationError(f"file changed while being validated: {name}")
    return raw


def _actual_bundle_files(directory: Path) -> set[str]:
    files: set[str] = set()
    try:
        root_entries = list(os.scandir(directory))
    except OSError as exc:
        raise BundleValidationError("could not inspect bundle directory") from exc
    for entry in root_entries:
        entry_path = Path(entry.path)
        if entry.is_symlink() or _is_link_like(entry_path):
            raise BundleValidationError(f"bundle contains symlink: {entry.name}")
        if entry.is_dir(follow_symlinks=False):
            if entry.name != "media":
                raise BundleValidationError(
                    f"bundle contains forbidden directory: {entry.name}"
                )
            try:
                media_entries = list(os.scandir(entry.path))
            except OSError as exc:
                raise BundleValidationError("could not inspect media directory") from exc
            for media_entry in media_entries:
                relative = f"media/{media_entry.name}"
                if media_entry.is_symlink() or _is_link_like(Path(media_entry.path)):
                    raise BundleValidationError(
                        f"bundle contains symlink: {relative}"
                    )
                if not media_entry.is_file(follow_symlinks=False):
                    raise BundleValidationError(
                        f"media contains a non-file entry: {relative}"
                    )
                files.add(relative)
        elif entry.is_file(follow_symlinks=False):
            files.add(entry.name)
        else:
            raise BundleValidationError(
                f"bundle contains a non-file entry: {entry.name}"
            )
    return files


def _canonical_content_root(artifacts: list[dict[str, Any]]) -> str:
    ordered = sorted(artifacts, key=lambda record: record["path"])
    canonical = json.dumps(
        {"artifacts": ordered},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def _expected_bundle_id(
    source_authority: dict[str, Any], generated_at: str
) -> str:
    canonical = json.dumps(
        {
            "bundle_version": BUNDLE_VERSION,
            "generated_at": generated_at,
            "source_authority": source_authority,
            "task_ids": EXPECTED_TASK_IDS,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"m88-fs09-blind-{hashlib.sha256(canonical).hexdigest().upper()}"


def validate_and_snapshot(
    directory: Path,
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    requested_directory = directory.expanduser()
    if _is_link_like(requested_directory):
        raise BundleValidationError("bundle directory may not be a symlink or junction")
    try:
        resolved = requested_directory.resolve(strict=True)
    except OSError as exc:
        raise BundleValidationError("bundle directory does not exist") from exc
    if not resolved.is_dir():
        raise BundleValidationError("--directory must name a directory")

    manifest_path = resolved / MANIFEST_NAME
    manifest_raw = _snapshot_regular_file(manifest_path, name=MANIFEST_NAME)
    manifest = _load_manifest(manifest_raw)
    actual_keys = set(manifest)
    if actual_keys != MANIFEST_KEYS:
        missing = sorted(MANIFEST_KEYS - actual_keys)
        extra = sorted(actual_keys - MANIFEST_KEYS)
        raise BundleValidationError(
            f"handoff manifest keys mismatch (missing={missing}, extra={extra})"
        )

    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError(f"schema_version must be {SCHEMA_VERSION}")
    if manifest["bundle_version"] != BUNDLE_VERSION:
        raise BundleValidationError(f"bundle_version must be {BUNDLE_VERSION}")
    if manifest["status"] != BUNDLE_STATUS:
        raise BundleValidationError(f"status must be {BUNDLE_STATUS}")
    bundle_id = _require_nonempty_string(manifest["bundle_id"], name="bundle_id")
    if BUNDLE_ID_RE.fullmatch(bundle_id) is None:
        raise BundleValidationError("bundle_id has an invalid format")
    generated_at = _require_rfc3339_timestamp(
        manifest["generated_at"], name="generated_at"
    )
    source_authority = manifest["source_authority"]
    if not isinstance(source_authority, dict) or set(source_authority) != SOURCE_AUTHORITY_KEYS:
        raise BundleValidationError("source_authority has an invalid structure")
    if source_authority["kind"] != "private_canonical_pack_content_hash":
        raise BundleValidationError("source_authority.kind is invalid")
    for field in (
        "manifest_sha256",
        "analysis_plan_sha256",
        "task_contract_sha256",
        "candidate_contract_sha256",
    ):
        _require_sha256(source_authority[field], name=f"source_authority.{field}")
    source_manifest_at = _require_rfc3339_timestamp(
        source_authority["manifest_generated_at"],
        name="source_authority.manifest_generated_at",
    )
    if source_manifest_at > generated_at:
        raise BundleValidationError("generated_at predates the source manifest")
    for field in ("plan_version", "pack_version"):
        _require_nonempty_string(
            source_authority[field], name=f"source_authority.{field}"
        )

    scope = manifest["scope"]
    if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS:
        raise BundleValidationError("scope has an invalid structure")
    if scope["task_ids"] != EXPECTED_TASK_IDS:
        raise BundleValidationError("scope.task_ids does not match the frozen two-task pilot")
    if scope["media_paths"] != EXPECTED_MEDIA_PATHS:
        raise BundleValidationError("scope.media_paths does not match the frozen media set")
    if bundle_id != _expected_bundle_id(source_authority, manifest["generated_at"]):
        raise BundleValidationError("bundle_id does not match source_authority and scope")

    safety = manifest["safety"]
    if safety != EXPECTED_SAFETY:
        raise BundleValidationError("safety declarations do not match the safe public bundle")

    entrypoint = _safe_bundle_path(manifest["entrypoint"], name="entrypoint")
    launcher = _safe_bundle_path(manifest["server_launcher"], name="server_launcher")
    if entrypoint != ENTRYPOINT:
        raise BundleValidationError(f"entrypoint must be {ENTRYPOINT}")
    if launcher != LAUNCHER_NAME:
        raise BundleValidationError(f"server_launcher must be {LAUNCHER_NAME}")

    artifact_values = manifest["artifacts"]
    if not isinstance(artifact_values, list) or not artifact_values:
        raise BundleValidationError("artifacts must be a non-empty JSON array")
    artifacts: list[dict[str, Any]] = []
    artifact_paths: set[str] = set()
    for index, value in enumerate(artifact_values):
        if not isinstance(value, dict) or set(value) != ARTIFACT_KEYS:
            raise BundleValidationError(
                f"artifacts[{index}] must contain exactly path, bytes, sha256"
            )
        path = _safe_bundle_path(value["path"], name=f"artifacts[{index}].path")
        byte_count = value["bytes"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise BundleValidationError(
                f"artifacts[{index}].bytes must be a positive integer"
            )
        digest = _require_sha256(
            value["sha256"], name=f"artifacts[{index}].sha256"
        )
        if path in artifact_paths:
            raise BundleValidationError(f"duplicate artifact path: {path}")
        artifact_paths.add(path)
        artifacts.append({"path": path, "bytes": byte_count, "sha256": digest})

    if [record["path"] for record in artifacts] != EXPECTED_ARTIFACT_PATHS:
        raise BundleValidationError(
            "artifacts must be the exact sorted eight-file public allowlist"
        )
    records_by_path = {record["path"]: record for record in artifacts}
    if (
        records_by_path["analysis-plan.json"]["sha256"]
        != source_authority["analysis_plan_sha256"]
    ):
        raise BundleValidationError(
            "analysis-plan.json is not bound by source_authority"
        )

    for required_path, field in ((ENTRYPOINT, "entrypoint"), (LAUNCHER_NAME, "server_launcher")):
        if required_path not in artifact_paths:
            raise BundleValidationError(f"{field} is not listed in artifacts")

    declared_root = _require_sha256(
        manifest["content_root_sha256"], name="content_root_sha256"
    )
    actual_root = _canonical_content_root(artifacts)
    if declared_root != actual_root:
        raise BundleValidationError("content_root_sha256 does not match artifacts")

    expected_files = artifact_paths | {MANIFEST_NAME}
    actual_files = _actual_bundle_files(resolved)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise BundleValidationError(
            f"bundle file set mismatch (missing={missing}, extra={extra})"
        )

    snapshots: dict[str, bytes] = {MANIFEST_NAME: manifest_raw}
    for record in artifacts:
        path = record["path"]
        raw = _snapshot_regular_file(resolved / Path(*path.split("/")), name=path)
        if len(raw) != record["bytes"]:
            raise BundleValidationError(f"artifact byte count mismatch: {path}")
        digest = hashlib.sha256(raw).hexdigest().upper()
        if digest != record["sha256"]:
            raise BundleValidationError(f"artifact SHA-256 mismatch: {path}")
        snapshots[path] = raw

    plan = _validate_analysis_plan(
        snapshots["analysis-plan.json"],
        source_authority=source_authority,
        source_manifest_at=source_manifest_at,
        records_by_path=records_by_path,
    )
    _validate_public_review(
        snapshots[ENTRYPOINT],
        manifest=manifest,
        plan=plan,
        records_by_path=records_by_path,
    )

    # Keep only immutable byte snapshots after validation; request handling never
    # reopens the bundle and therefore cannot observe later disk changes.
    return manifest, MappingProxyType(snapshots)


def _request_bundle_path(raw_target: str, *, entrypoint: str) -> str | None:
    parts = urlsplit(raw_target)
    if parts.scheme or parts.netloc:
        return None
    try:
        decoded = unquote_to_bytes(parts.path).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if decoded == "/":
        return entrypoint
    if not decoded.startswith("/") or decoded.startswith("//"):
        return None
    relative = decoded[1:]
    try:
        return _safe_bundle_path(relative, name="request path")
    except BundleValidationError:
        return None


def _content_type(path: str) -> str:
    explicit = {
        ".css": "text/css; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".mp4": "video/mp4",
    }
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in explicit:
        return explicit[suffix]
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _parse_range(value: str, size: int) -> tuple[int, int]:
    match = RANGE_RE.fullmatch(value.strip())
    if match is None or size <= 0:
        raise ValueError("invalid or unsatisfiable byte range")
    start_text, end_text = match.groups()
    if not start_text:
        if not end_text:
            raise ValueError("empty byte range")
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix byte range")
        return max(0, size - suffix), size - 1
    start = int(start_text)
    if start >= size:
        raise ValueError("byte range starts beyond resource")
    end = size - 1 if not end_text else min(size - 1, int(end_text))
    if end < start:
        raise ValueError("byte range ends before it starts")
    return start, end


def make_request_handler(
    snapshots: Mapping[str, bytes], *, entrypoint: str
) -> type[BaseHTTPRequestHandler]:
    class BlindHandoffRequestHandler(BaseHTTPRequestHandler):
        server_version = "RallyMateBlindHandoff/1.0"
        sys_version = ""

        def _common_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")

        def _json_error(
            self,
            status: HTTPStatus,
            error: str,
            *,
            head_only: bool,
            content_range: str | None = None,
        ) -> None:
            body = json.dumps(
                {"error": error}, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if content_range is not None:
                self.send_header("Content-Range", content_range)
                self.send_header("Accept-Ranges", "bytes")
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve(self, *, head_only: bool) -> None:
            port = int(self.server.server_address[1])
            allowed_host = f"127.0.0.1:{port}"
            host_values = self.headers.get_all("Host", failobj=[])
            if host_values != [allowed_host]:
                self._json_error(
                    HTTPStatus.MISDIRECTED_REQUEST,
                    "host_not_allowed",
                    head_only=head_only,
                )
                return
            path = _request_bundle_path(self.path, entrypoint=entrypoint)
            if path is None or path not in snapshots:
                self._json_error(
                    HTTPStatus.NOT_FOUND, "not_found", head_only=head_only
                )
                return
            raw = snapshots[path]
            size = len(raw)
            range_header = self.headers.get("Range")
            if range_header is None:
                status = HTTPStatus.OK
                start, end = 0, size - 1
            else:
                try:
                    start, end = _parse_range(range_header, size)
                except (TypeError, ValueError, OverflowError):
                    self._json_error(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        "range_not_satisfiable",
                        head_only=head_only,
                        content_range=f"bytes */{size}",
                    )
                    return
                status = HTTPStatus.PARTIAL_CONTENT

            body = raw if status == HTTPStatus.OK else raw[start : end + 1]
            self.send_response(status)
            self.send_header("Content-Type", _content_type(path))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - HTTP method hook
            self._serve(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802 - HTTP method hook
            self._serve(head_only=True)

        def _method_not_allowed(self) -> None:
            port = int(self.server.server_address[1])
            if self.headers.get_all("Host", failobj=[]) != [f"127.0.0.1:{port}"]:
                self._json_error(
                    HTTPStatus.MISDIRECTED_REQUEST,
                    "host_not_allowed",
                    head_only=False,
                )
                return
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self._common_headers()
            self.end_headers()

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed
        do_TRACE = _method_not_allowed
        do_CONNECT = _method_not_allowed

    return BlindHandoffRequestHandler


class SnapshotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = JsonArgumentParser(
        description="Validate and serve an FS09 public blind-handoff bundle"
    )
    parser.add_argument("--directory", type=Path, default=Path("."))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.bind != "127.0.0.1":
        parser.error("--bind must be exactly 127.0.0.1 (loopback only)")
    return args


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest, snapshots = validate_and_snapshot(args.directory)
    directory = args.directory.expanduser().resolve(strict=True)
    validation_result = {
        "ok": True,
        "status": "valid",
        "bundle_status": manifest["status"],
        "manifest_sha256": hashlib.sha256(snapshots[MANIFEST_NAME]).hexdigest().upper(),
        "bundle_id": manifest["bundle_id"],
        "analysis_plan_sha256": manifest["source_authority"][
            "analysis_plan_sha256"
        ],
        "content_root_sha256": manifest["content_root_sha256"],
        "file_count": len(snapshots),
    }
    if args.validate_only:
        _emit_json(validation_result)
        return 0

    handler = make_request_handler(snapshots, entrypoint=ENTRYPOINT)
    server = SnapshotHTTPServer((args.bind, args.port), handler)
    actual_host, actual_port = server.server_address[:2]
    _emit_json(
        {
            **validation_result,
            "status": "serving",
            "url": f"http://{actual_host}:{actual_port}/",
        }
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except BundleValidationError as exc:
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2
    except OSError as exc:
        _emit_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
