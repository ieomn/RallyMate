from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import cv2

from rallymate_evaluation.event_bounded_pose_gaps import (
    validate_event_bounded_pose_gap_report_sources,
)
from rallymate_evaluation.small_roi_keypoint_truth import (
    _canonical_sha256,
    _read_csv,
    _read_jsonl,
    _source,
    _write_csv,
    _write_json,
    _write_jsonl,
    sha256_file,
)
from rallymate_features import (
    EventInterval,
    clear_feature_cache,
    compute_event_features,
    pose_sequence_from_records,
)


PACK_VERSION = "fs09-phase-truth-pack-v1.3.0"
EVALUATOR_VERSION = "fs09-phase-truth-evaluation-v1.3.0"
ANALYSIS_PLAN_VERSION = "fs09-phase-analysis-plan-v1.3.0"
# Updated only while the canonical pack still contains zero human annotations.
# The exact timestamp and raw plan SHA are filled after the v1.3 evidence graph is built.
ANALYSIS_PLAN_FROZEN_AT = "2026-08-30T01:52:00Z"
ANALYSIS_PLAN_SHA256 = "5B33C036C2A7674A4414975D4457D87BE801CC9E382D636861E71ED377D340A9"
REVIEW_CLIP_RENDERER_VERSION = "m77-fs09-blind-review-clips-v1.1.0"
INTAKE_VERSION = "fs09-phase-truth-intake-v1.3.0"
REQUIRED_ANNOTATORS = 2
TASK_COUNT = 2
TASK_WINDOW_CONTRACT = (
    {
        "task_suffix": "fs09-phase-001",
        "review_start_ms": 331000,
        "review_end_ms": 336000,
        "target_selection_anchor_ms": 333500,
    },
    {
        "task_suffix": "fs09-phase-002",
        "review_start_ms": 449500,
        "review_end_ms": 454500,
        "target_selection_anchor_ms": 452000,
    },
)
PHASE_KEYS = (
    "peak_speed_ms",
    "deceleration_peak_ms",
    "restabilization_onset_ms",
    "stable_control_onset_ms",
)
PHASE_PREFIXES = (
    "peak_speed",
    "deceleration_peak",
    "restabilization_onset",
    "stable_control_onset",
)
PHASE_STATUS = {"observed", "not_observed", "unobservable"}
TARGET_FEATURE = "hip_deceleration_to_double_support_proxy_ms"
REQUIRED_FEATURES = (
    "stability_duration_ms",
    "hip_center_speed_drop_body_s",
    "double_support_proxy_duration_ms",
    "torso_lean_variability_deg",
    "shoulder_hip_angular_velocity_change_deg_s",
    TARGET_FEATURE,
)

ANNOTATION_FIELDS = (
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
)
ADJUDICATION_FIELDS = (
    "adjudication_id",
    "task_id",
    "source_annotation_ids",
    "source_annotation_revision_sha256s",
    "reviewer_id",
    *ANNOTATION_FIELDS[3:-3],
    "adjudicated_at",
    "exported_at",
    "status",
)


class FS09PhaseTruthError(ValueError):
    pass


_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise FS09PhaseTruthError(f"{name} must be true or false")
    return normalized == "true"


def _optional_int(value: str, *, name: str) -> int | None:
    if not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise FS09PhaseTruthError(f"{name} must be an integer timestamp_ms") from exc
    return parsed


def _parse_confidence(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise FS09PhaseTruthError("confidence must be a finite number") from exc
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise FS09PhaseTruthError("confidence must be between 0 and 1")
    return parsed


def _csv_snapshot(
    path: str | Path,
    fields: tuple[str, ...],
) -> tuple[list[dict[str, str]], bytes, str]:
    source = Path(path).resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest().upper()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FS09PhaseTruthError(f"{source.name} must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != list(fields):
        raise FS09PhaseTruthError(
            f"{source.name} headers must exactly match {list(fields)}"
        )
    rows: list[dict[str, str]] = []
    for line, row in enumerate(reader, start=2):
        if None in row:
            raise FS09PhaseTruthError(f"{source.name} row {line} has extra columns")
        rows.append({key: value or "" for key, value in row.items()})
    return rows, raw, digest


def _json_snapshot(path: str | Path) -> tuple[Any, bytes, str]:
    source = Path(path).resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest().upper()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError(f"{source.name} must be UTF-8 JSON") from exc
    return value, raw, digest


def _jsonl_snapshot(path: str | Path) -> tuple[list[dict[str, Any]], bytes, str]:
    source = Path(path).resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest().upper()
    try:
        lines = raw.decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError(f"{source.name} must be UTF-8 JSONL") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise FS09PhaseTruthError(f"{source.name} must contain JSON objects")
    return rows, raw, digest


def _canonical_jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _path_contains(left, right) or _path_contains(right, left)


def _session_relative_path(value: str, *, expected: str, name: str) -> str:
    if not isinstance(value, str) or value != expected or "\\" in value:
        raise FS09PhaseTruthError(f"{name} must use exact session-relative path {expected}")
    parts = value.split("/")
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).is_absolute()
        or PurePosixPath(value).as_posix() != value
    ):
        raise FS09PhaseTruthError(f"{name} is not a canonical session-relative path")
    return value


def _session_path(pack: Path, value: str, *, expected: str, name: str) -> Path:
    relative = _session_relative_path(value, expected=expected, name=name)
    path = (pack / Path(*PurePosixPath(relative).parts)).resolve()
    if not _path_contains(pack, path):
        raise FS09PhaseTruthError(f"{name} escaped the immutable session")
    return path


def _session_source(pack: Path, path: Path, *, expected: str) -> dict[str, str]:
    resolved_pack = pack.resolve()
    resolved_path = path.resolve()
    actual = resolved_path.relative_to(resolved_pack).as_posix()
    _session_relative_path(actual, expected=expected, name="session source")
    return {"path": actual, "sha256": sha256_file(resolved_path)}


def _require_exact_keys(value: Any, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise FS09PhaseTruthError(f"{name} fields drifted")
    return value


def _strict_json_equal(left: Any, right: Any) -> bool:
    try:
        options = {
            "ensure_ascii": False,
            "sort_keys": True,
            "separators": (",", ":"),
            "allow_nan": False,
        }
        return json.dumps(left, **options) == json.dumps(right, **options)
    except (TypeError, ValueError):
        return False


def _annotation_revision_sha256(row: dict[str, str]) -> str:
    payload = {
        name: row.get(name, "")
        for name in ANNOTATION_FIELDS
        if name not in {"annotation_revision_sha256", "exported_at"}
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _require_sha256(value: Any, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789ABCDEF" for character in value)
    ):
        raise FS09PhaseTruthError(f"{name} must be uppercase SHA-256")
    return value


def _identity_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def _require_safe_identity(value: str, *, name: str) -> str:
    identity = value.strip()
    if not identity or any(character in identity for character in ";\r\n\x00"):
        raise FS09PhaseTruthError(f"{name} is empty or contains a reserved delimiter")
    return identity


def _require_timestamp(value: str, *, name: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise FS09PhaseTruthError(f"{name} must be canonical RFC3339 UTC date-time")
    text = value
    if not _RFC3339_UTC_RE.fullmatch(text):
        raise FS09PhaseTruthError(f"{name} must be canonical RFC3339 UTC date-time")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FS09PhaseTruthError(f"{name} must be RFC3339 date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise FS09PhaseTruthError(f"{name} must use UTC Z")
    return text


def _timestamp_value(value: str, *, name: str) -> datetime:
    return datetime.fromisoformat(
        _require_timestamp(value, name=name).replace("Z", "+00:00")
    )


def _validate_handoff_export_row(
    row: dict[str, str],
    *,
    handoff_bundle_id: str,
    analysis_plan_sha256: str,
    handoff_generated_at: datetime,
    decision_time_field: str,
    intake_generated_at: datetime,
) -> tuple[datetime, datetime]:
    if row.get("handoff_bundle_id", "") != handoff_bundle_id:
        raise FS09PhaseTruthError("M77 export handoff bundle identity mismatch")
    if row.get("analysis_plan_sha256", "") != analysis_plan_sha256:
        raise FS09PhaseTruthError("M77 export analysis-plan identity mismatch")
    decision_time = _timestamp_value(
        row.get(decision_time_field, ""), name=decision_time_field
    )
    exported_at = _timestamp_value(row.get("exported_at", ""), name="exported_at")
    if not handoff_generated_at < decision_time <= exported_at <= intake_generated_at:
        raise FS09PhaseTruthError(
            "M77 decision/export chronology is invalid for the verified blind handoff"
        )
    return decision_time, exported_at


def validate_fs09_phase_analysis_plan(plan: dict[str, Any]) -> None:
    """Validate the frozen descriptive plan without claiming external registration."""
    _require_exact_keys(
        plan,
        {
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
        },
        name="M77 analysis plan",
    )
    expected_header = {
        "schema_version": "1.0.0",
        "plan_version": ANALYSIS_PLAN_VERSION,
        "plan_id": "m88-m77-fs09-two-task-descriptive-v3",
        "artifact_scope": "repository_bound_descriptive_analysis_plan",
        "status": "frozen_before_human_label_intake",
        "frozen_at": ANALYSIS_PLAN_FROZEN_AT,
    }
    if any(plan.get(name) != value for name, value in expected_header.items()):
        raise FS09PhaseTruthError("unsupported M77 analysis plan contract")
    _require_timestamp(str(plan.get("frozen_at", "")), name="analysis plan frozen_at")

    registration = {
        "kind": "repository_content_hash_binding",
        "external_registry_present": False,
        "human_registrant_asserted": False,
        "externally_registered_acceptance_protocol": False,
        "human_labels_present_at_freeze": False,
        "non_null_pilot_results_present_at_freeze": False,
    }
    if not _strict_json_equal(plan.get("registration_claim"), registration):
        raise FS09PhaseTruthError("M77 analysis plan registration claim drifted")

    study = _require_exact_keys(
        plan.get("study_binding"),
        {
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
        },
        name="M77 analysis plan study binding",
    )
    expected_study_identity = {
        "pack_version": PACK_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "video_ids": ["8d7754d0de6d315674013d5b69a0b6ba"],
        "view_groups": ["fixed-camera-8d7754d0"],
        "task_ids": [
            "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-001",
            "m77:8d7754d0de6d315674013d5b69a0b6ba:fs09-phase-002",
        ],
        "indicator_ids": ["FS09-M05"],
        "event_codes": ["FS09"],
        "phase_keys": list(PHASE_KEYS),
        "required_features": list(REQUIRED_FEATURES),
        "blind_handoff_version": "fs09-phase-blind-handoff-v1.1.0",
    }
    if any(
        not _strict_json_equal(study.get(name), value)
        for name, value in expected_study_identity.items()
    ):
        raise FS09PhaseTruthError("M77 analysis plan study identity drifted")
    expected_study_hashes = {
        "task_contract_sha256": "4BFD62348E254A8EF7AFE9244FCC3E4349878F49761022C425149F357C2AE7B8",
        "candidate_contract_sha256": "9D933FE215D654F371973E692A98A82D4DE874360AAD1BEB07D2E0C86F643805",
        "tasks_artifact_sha256": "E44A6B633E9744771D8DEA2E95CDEBE552F21D6B459BBC87C5BA51D293B9F8F2",
        "sealed_candidates_artifact_sha256": "1944D8FF92629852851617688A34BB9C42F11469062044704A2571864437CF10",
    }
    for name, expected_sha in expected_study_hashes.items():
        actual_sha = _require_sha256(study.get(name), name=f"M77 analysis plan {name}")
        if actual_sha != expected_sha:
            raise FS09PhaseTruthError(f"M77 analysis plan {name} drifted")
    workbench_hashes = _require_exact_keys(
        study.get("workbench_artifact_sha256"),
        {
            "fs09-phase-truth-workbench.js",
            "fs09-phase-truth-workbench.css",
            "review.html",
            "OPERATOR_README.md",
        },
        name="M77 analysis plan workbench artifact SHA",
    )
    expected_workbench_hashes = {
        "fs09-phase-truth-workbench.js": "390AB69F44893AF9FB62EF03357A4C251984656B1D8E8EA110D5BA7446D17340",
        "fs09-phase-truth-workbench.css": "0052DFD0D3474607C4BB4C20C862EF03667581D5AB0FF56F7E9E40F5B8A56E95",
        "review.html": "0AE0C9F90D0D7DD2EFB12FE791B5D954D153853BA00B96E4E6A81090E6858DE5",
        "OPERATOR_README.md": "EC4342AFAE19DAA33E936E1BCAD595D45FC5E6330935E3A1D73800EF2EF968D9",
    }
    for name, expected_sha in expected_workbench_hashes.items():
        actual_sha = _require_sha256(
            workbench_hashes.get(name), name=f"M77 analysis plan workbench {name} SHA"
        )
        if actual_sha != expected_sha:
            raise FS09PhaseTruthError(
                f"M77 analysis plan workbench {name} drifted"
            )
    handoff_hashes = _require_exact_keys(
        study.get("blind_handoff_artifact_sha256"),
        {"OPERATOR_README.md", "serve_fs09_phase_blind_handoff.py"},
        name="M77 analysis plan blind handoff artifact SHA",
    )
    expected_handoff_hashes = {
        "OPERATOR_README.md": "FD512EE3B5A10250F8204664C0CED6DFAB16ABA22E40B44DD916F7D6867CD07E",
        "serve_fs09_phase_blind_handoff.py": "3DCAFCA0B67DD3ED661D37216526ADB56922E34E763BEA2722678E71E0A00217",
    }
    for name, expected_sha in expected_handoff_hashes.items():
        if _require_sha256(
            handoff_hashes.get(name),
            name=f"M77 analysis plan blind handoff {name} SHA",
        ) != expected_sha:
            raise FS09PhaseTruthError(
                f"M77 analysis plan blind handoff {name} drifted"
            )
    expected_target_selection = {
        "kind": "pre_frozen_candidate_selected_coarse_anchor",
        "anchor_selects_action_only": True,
        "anchor_constrains_submitted_boundary_or_phase": False,
        "anchor_is_candidate_boundary_or_phase": False,
        "exact_candidate_boundaries_derivable_from_public_window": False,
        "candidate_selected_coarse_localization_disclosed": True,
        "tasks": [
            {
                "task_id": f"m77:8d7754d0de6d315674013d5b69a0b6ba:{window['task_suffix']}",
                "review_start_ms": window["review_start_ms"],
                "review_end_ms": window["review_end_ms"],
                "target_selection_anchor_ms": window["target_selection_anchor_ms"],
            }
            for window in TASK_WINDOW_CONTRACT
        ],
    }
    if not _strict_json_equal(
        study.get("blind_review_target_selection"), expected_target_selection
    ):
        raise FS09PhaseTruthError("M77 analysis plan target-selection contract drifted")
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
    if not _strict_json_equal(
        study.get("export_provenance_contract"), expected_export_provenance
    ):
        raise FS09PhaseTruthError("M77 analysis plan export provenance drifted")
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
    if not _strict_json_equal(
        study.get("annotation_revision_contract"), expected_revision_contract
    ):
        raise FS09PhaseTruthError("M77 annotation revision contract drifted")
    source_hashes = _require_exact_keys(
        study.get("source_artifact_sha256"),
        {
            "m74_report",
            "review_clip_manifest",
            "source_video",
            "task_001_review_clip",
            "task_002_review_clip",
        },
        name="M77 analysis plan source artifact SHA",
    )
    expected_source_hashes = {
        "m74_report": "C53D66B5D916E56F3E956FE498E58A99701B5D420663FBD5316A9EEB9F8F1575",
        "review_clip_manifest": "C53A7E62830EDB200F8E415B31A58BC394E17EEEA4FE8D02ABC31A243B2F4AF8",
        "source_video": "FEDA989F394818B27AE38E5BA12DF4690E6139DF53DB9CD87E6C6278B3C2E7E0",
        "task_001_review_clip": "FADDE9A2A9AD794B04C371D0293D871E516DBF0D11B286F5DFEE582E64F756EF",
        "task_002_review_clip": "028F4DD40B203A866F75378A61A4562866C73F2E2B711428302685F11A7DB5FD",
    }
    for name, expected_sha in expected_source_hashes.items():
        actual_sha = _require_sha256(
            source_hashes.get(name), name=f"M77 analysis plan source {name} SHA"
        )
        if actual_sha != expected_sha:
            raise FS09PhaseTruthError(f"M77 analysis plan source {name} drifted")

    expected_population = {
        "sampling_frame": "two_preselected_sealed_candidate_actions_with_coarse_selection_anchors",
        "selection_timing": "selected_before_human_phase_truth",
        "video_count": 1,
        "task_count": TASK_COUNT,
        "candidate_window_count": TASK_COUNT,
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
    if not _strict_json_equal(plan.get("population"), expected_population):
        raise FS09PhaseTruthError("M77 analysis plan population rules drifted")

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
            "phase_keys": list(PHASE_KEYS),
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
            "features": list(REQUIRED_FEATURES),
            "detail_rows_per_manual_present_task": len(REQUIRED_FEATURES),
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
    if not _strict_json_equal(plan.get("metric_plan"), expected_metric_plan):
        raise FS09PhaseTruthError("M77 analysis plan metric rules drifted")

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
    if not _strict_json_equal(
        plan.get("interpretation_boundaries"), expected_interpretation
    ):
        raise FS09PhaseTruthError("M77 analysis plan interpretation drifted")

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
    if not _strict_json_equal(plan.get("decision_authority"), expected_authority):
        raise FS09PhaseTruthError("M77 analysis plan decision authority drifted")


def _validate_fs09_phase_analysis_plan_binding(
    plan: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    tasks_artifact_sha256: str,
    sealed_candidates_artifact_sha256: str,
    m74_report_sha256: str,
    review_clip_manifest_sha256: str,
    source_video_sha256: str,
    review_clip_sha256_by_task: dict[str, str],
    workbench_artifact_sha256: dict[str, str],
) -> None:
    validate_fs09_phase_analysis_plan(plan)
    task_ids = [str(row.get("task_id", "")) for row in tasks]
    if len(task_ids) != TASK_COUNT or len(set(task_ids)) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 analysis plan task membership is incomplete")
    expected_clip_hashes = {
        "task_001_review_clip": review_clip_sha256_by_task.get(task_ids[0]),
        "task_002_review_clip": review_clip_sha256_by_task.get(task_ids[1]),
    }
    workbench_hashes = _require_exact_keys(
        workbench_artifact_sha256,
        {
            "fs09-phase-truth-workbench.js",
            "fs09-phase-truth-workbench.css",
            "review.html",
            "OPERATOR_README.md",
        },
        name="M77 workbench artifact SHA input",
    )
    expected_study = {
        "pack_version": PACK_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "video_ids": sorted({str(row.get("video_id", "")) for row in tasks}),
        "view_groups": sorted({str(row.get("view_group", "")) for row in tasks}),
        "task_ids": task_ids,
        "indicator_ids": sorted({str(row.get("indicator_id", "")) for row in tasks}),
        "event_codes": sorted({str(row.get("event_code", "")) for row in tasks}),
        "phase_keys": list(PHASE_KEYS),
        "required_features": list(REQUIRED_FEATURES),
        "task_contract_sha256": _canonical_sha256(tasks),
        "candidate_contract_sha256": _canonical_sha256(candidates),
        "tasks_artifact_sha256": _require_sha256(
            tasks_artifact_sha256, name="M77 tasks artifact SHA"
        ),
        "sealed_candidates_artifact_sha256": _require_sha256(
            sealed_candidates_artifact_sha256,
            name="M77 sealed candidates artifact SHA",
        ),
        "workbench_artifact_sha256": {
            name: _require_sha256(value, name=f"M77 {name} SHA")
            for name, value in workbench_hashes.items()
        },
        "blind_review_target_selection": plan["study_binding"][
            "blind_review_target_selection"
        ],
        "blind_handoff_version": plan["study_binding"]["blind_handoff_version"],
        "blind_handoff_artifact_sha256": plan["study_binding"][
            "blind_handoff_artifact_sha256"
        ],
        "export_provenance_contract": plan["study_binding"][
            "export_provenance_contract"
        ],
        "annotation_revision_contract": plan["study_binding"][
            "annotation_revision_contract"
        ],
        "source_artifact_sha256": {
            "m74_report": _require_sha256(
                m74_report_sha256, name="M77 M74 report SHA"
            ),
            "review_clip_manifest": _require_sha256(
                review_clip_manifest_sha256, name="M77 review clip manifest SHA"
            ),
            "source_video": _require_sha256(
                source_video_sha256, name="M77 source video SHA"
            ),
            **{
                name: _require_sha256(value, name=f"M77 {name} SHA")
                for name, value in expected_clip_hashes.items()
            },
        },
    }
    if not _strict_json_equal(plan.get("study_binding"), expected_study):
        raise FS09PhaseTruthError("M77 analysis plan evidence binding drifted")


def _phase_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    validate_event_bounded_pose_gap_report_sources(report)
    items = [
        item
        for item in report["items"]
        if item["video_id"] == "8d7754d0de6d315674013d5b69a0b6ba"
        and item["event_code"] == "FS09"
        and item["indicator_id"] == "FS09-M05"
        and item["original_classification"]
        == "required_pose_phase_proxy_not_observed"
        and not item["gap_audit"]["interpolated_points"]
        and item["invalid_features_before"] == [TARGET_FEATURE]
        and tuple(item["required_features"]) == REQUIRED_FEATURES
    ]
    items.sort(key=lambda row: (int(row["start_ms"]), str(row["event_id"])))
    if len(items) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 FS09 phase-only residual scope drifted")
    return items


def _candidate_event(event_path: Path, event_id: str) -> dict[str, Any]:
    matches = [row for row in _read_jsonl(event_path) if row.get("event_id") == event_id]
    if len(matches) != 1:
        raise FS09PhaseTruthError(f"M77 candidate event is not unique: {event_id}")
    event = matches[0]
    phases = event.get("key_phases_ms")
    if not isinstance(phases, dict) or set(phases) != set(PHASE_KEYS):
        raise FS09PhaseTruthError(f"M77 candidate phase contract drifted: {event_id}")
    return event


def _snapshot(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_name": result["feature_name"],
        "feature_version": result["feature_version"],
        "value": result["value"],
        "unit": result["unit"],
        "confidence": result["confidence"],
        "valid": bool(result["valid"]),
        "reason": result["reason"],
        "source_frames": result["source_frames"],
    }


def _sealed_candidate(
    *, task_id: str, item: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    before = item["feature_transitions"][0]["before"]
    return {
        "schema_version": "1.0.0",
        "candidate_id": f"candidate-{task_id}",
        "task_id": task_id,
        "video_id": item["video_id"],
        "event_id": item["event_id"],
        "event_code": "FS09",
        "person_track_id": int(event["person_track_id"]),
        "start_ms": int(event["start_ms"]),
        "end_ms": int(event["end_ms"]),
        "key_phases_ms": {name: event["key_phases_ms"][name] for name in PHASE_KEYS},
        "boundary_uncertainty_ms": int(event["boundary_uncertainty_ms"]),
        "quality_flags": list(event["quality_flags"]),
        "target_feature_before": {
            name: before[name]
            for name in (
                "feature_name",
                "feature_version",
                "valid",
                "reason",
                "value",
                "unit",
                "confidence",
                "source_frames",
            )
        },
        "candidate_status": "sealed_pose_rule_candidate_not_truth",
    }


def _expected_contract(
    report: dict[str, Any], source: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = _phase_items(report)
    event_path = Path(source["events"]["path"]).resolve()
    tasks: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        event = _candidate_event(event_path, str(item["event_id"]))
        task_id = f"m77:{item['video_id']}:fs09-phase-{index:03d}"
        window = TASK_WINDOW_CONTRACT[index - 1]
        if not task_id.endswith(str(window["task_suffix"])):
            raise FS09PhaseTruthError("M77 frozen review-window task order drifted")
        review_start = int(window["review_start_ms"])
        review_end = int(window["review_end_ms"])
        anchor = int(window["target_selection_anchor_ms"])
        candidate_phases = {
            int(value) for value in event.get("key_phases_ms", {}).values()
        }
        if (
            not review_start < anchor < review_end
            or not int(event["start_ms"]) <= anchor <= int(event["end_ms"])
            or anchor in {
                int(event["start_ms"]),
                int(event["end_ms"]),
                *candidate_phases,
            }
        ):
            raise FS09PhaseTruthError(
                "M77 neutral target-selection anchor no longer identifies the sealed action"
            )
        tasks.append(
            {
                "schema_version": "1.0.0",
                "task_id": task_id,
                "video_id": item["video_id"],
                "view_group": f"fixed-camera-{str(item['video_id'])[:8]}",
                "event_code": "FS09",
                "indicator_id": "FS09-M05",
                "review_start_ms": review_start,
                "review_end_ms": review_end,
                "source_time_offset_ms": review_start,
                "target_selection_anchor_ms": anchor,
                "target_selection_rule": (
                    "label_the_single_visually_observable_FS09_action_identified_by_the_"
                    "pre_frozen_coarse_anchor;anchor_is_a_selection_cue_only_and_does_not_"
                    "constrain_submitted_boundary_or_phase"
                ),
                "review_clip_id": f"task-{index:03d}-browser.mp4",
                "required_phase_keys": list(PHASE_KEYS),
                "annotation_semantics": (
                    "blind_full_FS09_event_and_visible_phase_truth;"
                    "stable_control_is_visual_control_not_ground_contact_or_force"
                ),
                "truth_status": "pending",
            }
        )
        candidates.append(_sealed_candidate(task_id=task_id, item=item, event=event))
    return tasks, candidates


def validate_fs09_phase_review_clip_manifest(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("renderer_version") != REVIEW_CLIP_RENDERER_VERSION
        or manifest.get("status") != "passed"
    ):
        raise FS09PhaseTruthError("unsupported M77 review clip manifest")
    sources = manifest.get("sources", {})
    if set(sources) != {"m74_report", "video", "ffmpeg", "ffprobe"}:
        raise FS09PhaseTruthError("M77 review clip sources are incomplete")
    expected_window_contract = {
        "kind": "pre_frozen_candidate_selected_coarse_anchor",
        "anchor_selects_action_only": True,
        "anchor_constrains_submitted_boundary_or_phase": False,
        "anchor_is_candidate_boundary_or_phase": False,
        "exact_candidate_boundaries_derivable_from_window": False,
        "candidate_selected_coarse_localization_disclosed": True,
        "tasks": [
            {
                "task_id": f"m77:8d7754d0de6d315674013d5b69a0b6ba:{window['task_suffix']}",
                "review_start_ms": window["review_start_ms"],
                "review_end_ms": window["review_end_ms"],
                "target_selection_anchor_ms": window["target_selection_anchor_ms"],
            }
            for window in TASK_WINDOW_CONTRACT
        ],
    }
    if not _strict_json_equal(
        manifest.get("window_contract"), expected_window_contract
    ):
        raise FS09PhaseTruthError("M77 review clip window contract drifted")
    clips = manifest.get("clips")
    if not isinstance(clips, list) or len(clips) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 review clip task count drifted")
    if len({str(item.get("task_id")) for item in clips}) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 review clip task ids are not unique")
    for index, item in enumerate(clips):
        _require_exact_keys(
            item,
            {
                "task_id",
                "source_start_ms",
                "source_end_ms",
                "source_time_offset_ms",
                "target_selection_anchor_ms",
                "requested_duration_ms",
                "clip",
                "probe",
                "metadata_audit",
            },
            name="M77 review clip",
        )
        expected_window = expected_window_contract["tasks"][index]
        if (
            item.get("task_id") != expected_window["task_id"]
            or item.get("source_start_ms") != expected_window["review_start_ms"]
            or item.get("source_end_ms") != expected_window["review_end_ms"]
            or item.get("target_selection_anchor_ms")
            != expected_window["target_selection_anchor_ms"]
        ):
            raise FS09PhaseTruthError("M77 review clip window/anchor drifted")
        start = item.get("source_start_ms")
        end = item.get("source_end_ms")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or item.get("source_time_offset_ms") != start
            or item.get("requested_duration_ms") != end - start
        ):
            raise FS09PhaseTruthError("M77 review clip timestamp mapping is invalid")
        probe = item.get("probe", {})
        samples = probe.get("decode_samples")
        if (
            str(probe.get("codec_fourcc", "")).lower() != "h264"
            or not isinstance(probe.get("frame_count"), int)
            or probe["frame_count"] <= 0
            or not isinstance(probe.get("fps"), (int, float))
            or probe["fps"] <= 0
            or not isinstance(samples, list)
            or len(samples) != 3
            or any(sample.get("decoded") is not True for sample in samples)
        ):
            raise FS09PhaseTruthError("M77 review clip decode QA failed")
        metadata_audit = item.get("metadata_audit")
        if not _strict_json_equal(
            metadata_audit,
            {
                "ffprobe_sha256": sources["ffprobe"]["sha256"],
                "forbidden_tag_keys_present": [],
                "source_path_or_filename_present": False,
                "passed": True,
            },
        ):
            raise FS09PhaseTruthError("M77 review clip metadata audit failed")
    safety = manifest.get("safety", {})
    for name in (
        "pose_overlay_rendered",
        "candidate_boundary_overlay_rendered",
        "candidate_phase_overlay_rendered",
        "audio_included",
        "source_metadata_or_filename_retained",
        "target_selection_anchor_is_boundary_or_phase",
        "accuracy_claim",
        "production_enabled",
    ):
        if safety.get(name) is not False:
            raise FS09PhaseTruthError(f"unsafe M77 review clip field: {name}")


def probe_fs09_phase_review_clip(path: str | Path) -> dict[str, Any]:
    clip_path = Path(path).resolve()
    capture = cv2.VideoCapture(str(clip_path))
    if not capture.isOpened():
        raise FS09PhaseTruthError(f"cannot open M77 review clip: {clip_path}")
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    codec_int = int(capture.get(cv2.CAP_PROP_FOURCC))
    codec = "".join(chr((codec_int >> (8 * index)) & 0xFF) for index in range(4)).strip("\x00")
    decoded: list[dict[str, Any]] = []
    for frame_index in sorted({0, max(0, frames // 2), max(0, frames - 1)}):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        decoded.append({"frame_index": frame_index, "decoded": bool(ok and frame is not None)})
    capture.release()
    if frames <= 0 or fps <= 0 or width <= 0 or height <= 0 or not all(item["decoded"] for item in decoded):
        raise FS09PhaseTruthError(f"M77 review clip failed decode QA: {clip_path}")
    return {
        "codec_fourcc": codec,
        "frame_count": frames,
        "fps": round(fps, 8),
        "duration_ms": round(frames / fps * 1000),
        "width": width,
        "height": height,
        "decode_samples": decoded,
    }


def validate_fs09_phase_truth_manifest(manifest: dict[str, Any]) -> None:
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "pack_version",
            "generated_at",
            "status",
            "sources",
            "scope",
            "workbench",
            "task_contract_sha256",
            "candidate_contract_sha256",
            "artifacts",
            "safety",
        },
        name="M77 truth pack",
    )
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("pack_version") != PACK_VERSION
        or manifest.get("status") != "annotation_required"
    ):
        raise FS09PhaseTruthError("unsupported M77 truth pack contract")
    _require_timestamp(str(manifest.get("generated_at", "")), name="generated_at")
    scope = _require_exact_keys(
        manifest.get("scope"),
        {
            "video_count",
            "video_ids",
            "view_groups",
            "task_count",
            "indicator_ids",
            "event_codes",
            "phase_keys",
            "required_independent_annotators",
            "required_independent_reviewer",
        },
        name="M77 truth scope",
    )
    expected_scope = {
        "video_count": 1,
        "video_ids": ["8d7754d0de6d315674013d5b69a0b6ba"],
        "view_groups": ["fixed-camera-8d7754d0"],
        "task_count": TASK_COUNT,
        "indicator_ids": ["FS09-M05"],
        "event_codes": ["FS09"],
        "phase_keys": list(PHASE_KEYS),
        "required_independent_annotators": REQUIRED_ANNOTATORS,
        "required_independent_reviewer": 1,
    }
    if not _strict_json_equal(scope, expected_scope):
        raise FS09PhaseTruthError("M77 truth scope drifted")
    expected_workbench = {
        "entry": "review.html",
        "annotation_csv": "annotations.csv",
        "adjudication_csv": "adjudications.csv",
        "compiled_events": "compiled/manual-fs09-events.jsonl",
        "validation_report": "compiled/validation-report.json",
        "sealed_candidates_not_loaded_by_ui": True,
        "annotation_execution_allowed": False,
        "public_blind_handoff_required": True,
    }
    workbench = _require_exact_keys(
        manifest.get("workbench"), set(expected_workbench), name="M77 workbench"
    )
    if not _strict_json_equal(workbench, expected_workbench):
        raise FS09PhaseTruthError("M77 workbench contract drifted")
    _require_sha256(
        manifest.get("task_contract_sha256"), name="task_contract_sha256"
    )
    _require_sha256(
        manifest.get("candidate_contract_sha256"), name="candidate_contract_sha256"
    )
    expected_artifacts = {
        "tasks.jsonl",
        "sealed-event-candidates.jsonl",
        "analysis-plan.json",
        "fs09-phase-truth-workbench.js",
        "fs09-phase-truth-workbench.css",
        "review.html",
        "OPERATOR_README.md",
        "media/task-001-browser.mp4",
        "media/task-002-browser.mp4",
    }
    _require_exact_keys(
        manifest.get("artifacts"), expected_artifacts, name="M77 artifacts"
    )
    sources = _require_exact_keys(
        manifest.get("sources"),
        {"analysis_plan", "m74_report", "per_video"},
        name="M77 sources",
    )
    _require_exact_keys(
        sources.get("analysis_plan"), {"path", "sha256"}, name="M77 analysis plan source"
    )
    _require_sha256(
        sources["analysis_plan"].get("sha256"), name="M77 analysis plan source SHA"
    )
    per_video = sources.get("per_video")
    if not isinstance(per_video, list) or len(per_video) != 1:
        raise FS09PhaseTruthError("M77 per-video source scope drifted")
    video_source = _require_exact_keys(
        per_video[0],
        {
            "video_id",
            "view_group",
            "video",
            "m73_report",
            "frames",
            "primary_timeline",
            "events",
            "review_clip_manifest",
            "review_clips",
        },
        name="M77 per-video source",
    )
    if (
        video_source.get("video_id") != expected_scope["video_ids"][0]
        or video_source.get("view_group") != expected_scope["view_groups"][0]
        or not isinstance(video_source.get("review_clips"), list)
        or len(video_source["review_clips"]) != TASK_COUNT
    ):
        raise FS09PhaseTruthError("M77 per-video source identity drifted")
    for index, clip in enumerate(video_source["review_clips"], start=1):
        _require_exact_keys(
            clip,
            {
                "task_id",
                "source_start_ms",
                "source_end_ms",
                "source_time_offset_ms",
                "target_selection_anchor_ms",
                "clip",
                "probe",
            },
            name=f"M77 review clip {index}",
        )
    expected_safety = {
        "candidate_boundaries_embedded_in_annotation_ui": False,
        "candidate_phases_embedded_in_annotation_ui": False,
        "pose_overlay_embedded_in_annotation_ui": False,
        "truth_prefilled_from_candidate": False,
        "ground_contact_or_force_claim": False,
        "independent_adjudication_required": True,
        "partial_annotations_change_runtime": False,
        "production_enabled": False,
        "grade_generated": False,
        "threshold_generated": False,
        "maturity_promoted": False,
        "review_clips_are_browser_h264": True,
        "review_clips_preserve_source_timestamp_mapping": True,
        "authority_pack_may_be_served_or_distributed": False,
        "public_blind_handoff_required": True,
    }
    safety = _require_exact_keys(
        manifest.get("safety"), set(expected_safety), name="M77 manifest safety"
    )
    if not _strict_json_equal(safety, expected_safety):
        raise FS09PhaseTruthError("unsafe M77 manifest declaration")


def _source_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sources = manifest.get("sources", {})
    per_video = sources.get("per_video", [])
    rows = [sources.get("analysis_plan"), sources.get("m74_report")]
    for item in per_video:
        rows.extend(
            item.get(name)
            for name in (
                "video",
                "m73_report",
                "frames",
                "primary_timeline",
                "events",
                "review_clip_manifest",
            )
        )
        rows.extend(clip.get("clip") for clip in item.get("review_clips", []))
    return [row for row in rows if isinstance(row, dict)]


def _verify_pack_sources(
    pack_dir: Path,
    manifest: dict[str, Any],
    *,
    source_artifact_root: Path | None = None,
) -> None:
    validate_fs09_phase_truth_manifest(manifest)
    records = _source_records(manifest)
    if len(records) != 10:
        raise FS09PhaseTruthError("M77 source bindings are incomplete")
    for index, record in enumerate(records, start=1):
        _require_exact_keys(
            record, {"path", "sha256"}, name=f"M77 source binding {index}"
        )
        path = Path(str(record.get("path", ""))).resolve()
        expected_sha = _require_sha256(
            record.get("sha256"), name=f"M77 source binding {index} SHA"
        )
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise FS09PhaseTruthError(f"M77 source SHA mismatch: {path}")
    artifact_paths: dict[str, Path] = {}
    artifact_root = (
        pack_dir.resolve()
        if source_artifact_root is None
        else source_artifact_root.resolve()
    )
    for name, record in manifest.get("artifacts", {}).items():
        _require_exact_keys(
            record, {"path", "sha256"}, name=f"M77 artifact {name}"
        )
        path = Path(str(record.get("path", ""))).resolve()
        expected_path = (artifact_root / Path(name)).resolve()
        expected_sha = _require_sha256(
            record.get("sha256"), name=f"M77 artifact {name} SHA"
        )
        if path != expected_path:
            raise FS09PhaseTruthError(
                f"M77 artifact must point inside the verified artifact root: {name}"
            )
        if not expected_path.is_file() or sha256_file(expected_path) != expected_sha:
            raise FS09PhaseTruthError(f"M77 immutable artifact SHA mismatch: {name}")
        artifact_paths[name] = expected_path
    plan_source_path = Path(manifest["sources"]["analysis_plan"]["path"]).resolve()
    plan_copy_path = pack_dir / "analysis-plan.json"
    analysis_plan, plan_copy_raw, plan_copy_digest = _json_snapshot(plan_copy_path)
    source_plan, source_plan_raw, source_plan_digest = _json_snapshot(plan_source_path)
    if (
        plan_copy_raw != source_plan_raw
        or plan_copy_digest != source_plan_digest
        or plan_copy_digest != ANALYSIS_PLAN_SHA256
        or plan_copy_digest != manifest["sources"]["analysis_plan"]["sha256"]
        or plan_copy_digest != manifest["artifacts"]["analysis-plan.json"]["sha256"]
        or not _strict_json_equal(analysis_plan, source_plan)
    ):
        raise FS09PhaseTruthError("M77 analysis plan source/copy binding mismatch")
    validate_fs09_phase_analysis_plan(analysis_plan)
    tasks = _read_jsonl(pack_dir / "tasks.jsonl")
    candidates = _read_jsonl(pack_dir / "sealed-event-candidates.jsonl")
    if _canonical_sha256(tasks) != manifest.get("task_contract_sha256"):
        raise FS09PhaseTruthError("M77 task contract SHA mismatch")
    if _canonical_sha256(candidates) != manifest.get("candidate_contract_sha256"):
        raise FS09PhaseTruthError("M77 candidate contract SHA mismatch")
    m74_path = Path(manifest["sources"]["m74_report"]["path"]).resolve()
    report = json.loads(m74_path.read_text(encoding="utf-8"))
    expected_tasks, expected_candidates = _expected_contract(
        report, manifest["sources"]["per_video"][0]
    )
    if tasks != expected_tasks or candidates != expected_candidates:
        raise FS09PhaseTruthError("M77 tasks/candidates do not replay bound M74 evidence")
    per_video = manifest["sources"]["per_video"][0]
    clip_manifest_path = Path(per_video["review_clip_manifest"]["path"]).resolve()
    clip_manifest = json.loads(clip_manifest_path.read_text(encoding="utf-8"))
    validate_fs09_phase_review_clip_manifest(clip_manifest)
    if clip_manifest["sources"]["m74_report"] != manifest["sources"]["m74_report"]:
        raise FS09PhaseTruthError("M77 review clips do not bind the pack M74 report")
    if clip_manifest["sources"]["video"] != per_video["video"]:
        raise FS09PhaseTruthError("M77 review clips do not bind the pack source video")
    expected_by_task = {task["task_id"]: task for task in tasks}
    bound_by_task = {clip["task_id"]: clip for clip in per_video["review_clips"]}
    rendered_by_task = {clip["task_id"]: clip for clip in clip_manifest["clips"]}
    _validate_fs09_phase_analysis_plan_binding(
        analysis_plan,
        tasks=tasks,
        candidates=candidates,
        tasks_artifact_sha256=sha256_file(pack_dir / "tasks.jsonl"),
        sealed_candidates_artifact_sha256=sha256_file(
            pack_dir / "sealed-event-candidates.jsonl"
        ),
        m74_report_sha256=manifest["sources"]["m74_report"]["sha256"],
        review_clip_manifest_sha256=per_video["review_clip_manifest"]["sha256"],
        source_video_sha256=per_video["video"]["sha256"],
        review_clip_sha256_by_task={
            str(item["task_id"]): str(item["clip"]["sha256"])
            for item in per_video["review_clips"]
        },
        workbench_artifact_sha256={
            name: sha256_file(artifact_paths[name])
            for name in (
                "fs09-phase-truth-workbench.js",
                "fs09-phase-truth-workbench.css",
                "review.html",
                "OPERATOR_README.md",
            )
        },
    )
    frozen_at = datetime.fromisoformat(
        str(analysis_plan["frozen_at"]).replace("Z", "+00:00")
    )
    review_clip_generated_at = datetime.fromisoformat(
        str(clip_manifest["generated_at"]).replace("Z", "+00:00")
    )
    generated_at = datetime.fromisoformat(
        str(manifest["generated_at"]).replace("Z", "+00:00")
    )
    if not review_clip_generated_at < frozen_at < generated_at:
        raise FS09PhaseTruthError(
            "M77 chronology must be review clips < frozen analysis plan < pack manifest"
        )
    if set(expected_by_task) != set(bound_by_task) or set(expected_by_task) != set(rendered_by_task):
        raise FS09PhaseTruthError("M77 review clip task membership drifted")
    for task_id, task in expected_by_task.items():
        bound = bound_by_task[task_id]
        rendered = rendered_by_task[task_id]
        if (
            rendered["source_start_ms"] != task["review_start_ms"]
            or rendered["source_end_ms"] != task["review_end_ms"]
            or rendered["source_time_offset_ms"] != task["source_time_offset_ms"]
            or rendered["target_selection_anchor_ms"]
            != task["target_selection_anchor_ms"]
            or bound != {
                "task_id": task_id,
                "source_start_ms": rendered["source_start_ms"],
                "source_end_ms": rendered["source_end_ms"],
                "source_time_offset_ms": rendered["source_time_offset_ms"],
                "target_selection_anchor_ms": rendered[
                    "target_selection_anchor_ms"
                ],
                "clip": rendered["clip"],
                "probe": rendered["probe"],
            }
        ):
            raise FS09PhaseTruthError("M77 review clip timestamp/source binding drifted")
        if probe_fs09_phase_review_clip(rendered["clip"]["path"]) != rendered["probe"]:
            raise FS09PhaseTruthError("M77 review clip probe does not replay the bound media")
        local_media_name = f"media/{Path(rendered['clip']['path']).name}"
        if (
            manifest["artifacts"][local_media_name]["sha256"]
            != rendered["clip"]["sha256"]
            or sha256_file(artifact_paths[local_media_name])
            != rendered["clip"]["sha256"]
        ):
            raise FS09PhaseTruthError(
                "M77 local review clip does not match the plan-bound media"
            )


def _workbench_html(bootstrap: dict[str, Any]) -> str:
    data = json.dumps(bootstrap, ensure_ascii=False).replace("</", "<\\/")
    asset_sha = bootstrap["workbench_asset_sha256"]
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RallyMate M77 FS09 阶段真值</title><link rel="stylesheet" href="fs09-phase-truth-workbench.css?v={asset_sha['css']}"></head><body><main>
<h1>M77 FS09 事件与稳定控制阶段盲标</h1><p class="warning">页面不加载候选事件边界、候选阶段或 Pose 骨架。当前 public blind handoff 只证明技术交接完整；在独立外部协议回执与可信锚完成、且其时间早于任何首次作答前，保存、导入与导出保持禁用。私有权威包不得作为标注站点。获授权后，两名标注者必须在不同浏览器配置/设备中独立完成、互不查看导出，再由未参与标注的 reviewer 裁决。“稳定控制”是画面可见的身体控制状态，不是双支撑、足底接触、压力或受力真值。所有角色必须主动选择事件与阶段状态；页面不提供默认正例。</p>
<section><h2>统一观察判据</h2><ul><li><strong>事件起点：</strong>身体整体从移动/接近明确转入制动或恢复控制的第一帧。</li><li><strong>峰速：</strong>可见身体中心平移最快、随后开始减速的时刻；不要用挥拍或单肢速度代替。</li><li><strong>减速峰：</strong>身体中心速度下降最明显、制动最强的可见时刻；它不等于脚接触真值。</li><li><strong>重新稳定开始：</strong>强制动之后，躯干/骨盆摆动开始持续减小并转向恢复平衡的第一帧。</li><li><strong>稳定控制开始：</strong>至少连续 3 帧可见躯干与骨盆已受控，且没有新的大幅纠正步或晃动的第一帧。</li><li><strong>事件终点：</strong>稳定控制已经建立后的第一帧；若画面不足以判断，使用 unobservable 并写原因。</li></ul><p>先按正常速度完整观看，再用逐帧/100 ms 按钮定位。只判断画面可见身体运动；不得查看密封候选文件。</p></section>
<section class="toolbar"><label>模式<select id="mode"><option value="annotate">独立标注</option><option value="adjudicate">独立裁决</option></select></label><label>ID<input id="identity" autocomplete="off"><span>ID 不得包含分号或换行</span></label><label>任务<select id="task"></select></label><button id="previous">上一个</button><button id="next">下一个</button></section>
<section><video id="video" controls playsinline preload="metadata"></video><p id="media-status" role="status">正在验证媒体是否支持完整逐帧定位……</p><p id="window"></p><button id="seek-start">回到审阅窗口起点</button><button id="seek-anchor">跳到预冻结的粗粒度目标提示点</button></section>
<section id="editor"><label>事件判断<select id="event-present"><option value="">请选择（必填）</option><option value="true">存在可标注 FS09 事件</option><option value="false">不存在或不可观测</option></select></label><div class="grid"><label>事件起点 ms<input id="event-start" inputmode="numeric"></label><button data-capture="event-start">取当前视频时间</button><label>事件终点 ms<input id="event-end" inputmode="numeric"></label><button data-capture="event-end">取当前视频时间</button></div><label>事件不存在/不可观测原因<input id="event-reason"></label><div id="phases"></div><label>信心 0–1<input id="confidence" placeholder="必须主动填写"></label><label>备注<textarea id="notes"></textarea></label><button id="save">保存当前草稿</button><p id="status"></p></section>
<section><h2>导入与导出</h2><input id="import" type="file" accept=".csv" multiple><button id="export-annotations">导出当前标注 CSV</button><button id="export-adjudications">导出裁决 CSV</button><button id="reset">清空浏览器草稿</button><p id="progress"></p></section>
<section><h2>交回人工导出</h2><p>A/B 各自交回一份 annotation CSV，C 交回一份 adjudication CSV。不要手工合并或改写 CSV；中央操作员会使用私有权威包与本 handoff manifest 进行原子导入。完整交接步骤见本目录 <code>OPERATOR_README.md</code>。</p></section></main>
<script id="fs09-phase-truth-bootstrap" type="application/json">{data}</script><script src="fs09-phase-truth-workbench.js?v={asset_sha['js']}"></script></body></html>'''


def _operator_guide() -> str:
    return """# FS09 两任务私有权威包

本目录是中央操作员保管的私有权威包，内含密封候选、来源路径和编译证据。**不得把本目录作为 HTTP root，不得复制给 A/B/C，也不得用本目录中的 `review.html` 开始正式标注。** 请只分发由 M88 builder 生成并验证的 public blind handoff。完成两项任务仍不会生成教练等级、评分阈值或 F3/F4 晋升。

## 冻结的分析边界

本包内 `analysis-plan.json` 是在零人工标签状态下按原始字节 SHA 冻结的描述性分析计划，预先限定任务、指标、缺失值处理和允许解释。它不是外部登记、真人签名或生产接受协议，也不含通过阈值。请勿编辑或替换该文件；导入和评测会逐层核验同一字节内容。即使两项任务完成，结果也只可用于流程与边界敏感性诊断，不能自动改变 runtime、F2/F3/F4 或正式 A～E。

## 人工执行使用 public blind handoff

1. 先在中央环境生成 public handoff：`python scripts/build_m88_fs09_phase_blind_handoff.py --source-pack <本私有包> --output <新的-public-handoff>`。此时它只是技术就绪包，不是人工执行授权。
2. **硬停机门禁：**在独立外部协议登记回执及其可信锚尚未绑定本次 handoff manifest 原始 SHA、bundle ID、analysis-plan SHA，或回执时间不能证明早于任何首次作答/保存时，不得让 A/B/C 开始、保存、导入或导出。当前仓库没有这份回执，页面会保持写操作禁用。
3. 本 M88 technical-only handoff **不能**在后来取得回执后原地解锁。未来必须另行实现、生成并验证一个带独立 receipt/trust-anchor 合同的新 authorized bundle/version；当前仓库没有这条转换路径，也没有可执行的 A/B/C 或 intake 正向流程。
4. 未来新 authorized contract 完成后，才可按其版本化说明在隔离设备上启动、分发、导出并由中央建立 intake。不得把下方命令误用于当前 technical-only bundle。

## 统一判据

- 事件起点：身体整体从移动/接近明确转入制动或恢复控制的第一帧。
- 峰速：可见身体中心平移最快、随后开始减速的时刻，不是挥拍或单肢速度。
- 减速峰：身体中心速度下降最明显、制动最强的可见时刻，不等于脚接触真值。
- 重新稳定开始：强制动后，躯干/骨盆摆动开始持续减小并转向恢复平衡的第一帧。
- 稳定控制开始：至少连续 3 帧可见躯干与骨盆已受控、且没有新的大幅纠正步或晃动的第一帧。
- 事件终点：稳定控制已经建立后的第一帧。画面不足时使用 `unobservable`，不可猜测。

先按正常速度完整观看，再逐帧定位。必须主动选择事件存在性、每个阶段状态和 confidence；页面不会默认选择正例。`observed` 才能填写时间；`not_observed` / `unobservable` 必须留空时间并写原因。所有时间由页面换算为原视频绝对 `timestamp_ms`。

## 原子导入与评测

```powershell
$env:PYTHONPATH = "src"
python scripts/compile_m77_fs09_phase_truth.py `
  --pack data/annotations/fs09-phase-truth-m77-v1 `
  --handoff-manifest <public-handoff>/handoff-manifest.json `
  --annotation-export <A.csv> `
  --annotation-export <B.csv> `
  --adjudication-export <reviewer.csv> `
  --session-output <new-session-directory>
python scripts/evaluate_m77_fs09_phase_truth.py `
  --pack <new-session-directory> `
  --output <new-report.json>
```

导入器要求 4 条 annotation、2 条 accepted adjudication、精确任务覆盖、两个不同 annotator、独立 reviewer，以及每条裁决精确引用本任务 A/B 的 annotation ID 与 decision-revision SHA。它逐字节保存三份原始导出，在隐藏 staging 中完成编译与验证，并只用一次 rename 发布最终会话；提交后不再写入。不要手工合并 CSV，也不要覆盖本空白包。

**成功 intake session 仍是私有敏感证据，而不是可分发结果包。** 它包含密封候选、绝对来源路径、A/B/C 身份与备注、人工真值和媒体：不得作为 HTTP root，不得发给标注者或公开分享。它的强回放还依赖原 private source pack 的原路径与原字节；必须把 source pack、public handoff、三份原始导出和 session 按访问控制一起保管。不要移动、改名或改写 session，也不要把评测输出写进其中；如需新路径，请从原始输入重新 intake。保留期限到期时应按项目隐私/审计策略成组删除，而不是只删其中一份。
"""


def build_fs09_phase_truth_pack(
    *,
    m74_report_path: str | Path,
    review_clip_manifest_path: str | Path,
    analysis_plan_path: str | Path,
    output_dir: str | Path,
    asset_directory: str | Path,
) -> dict[str, Any]:
    report_path = Path(m74_report_path).resolve()
    final_dir = Path(output_dir).resolve()
    staging = final_dir.with_name(f".{final_dir.name}.building")
    assets = Path(asset_directory).resolve()
    if final_dir.exists() or staging.exists():
        raise FS09PhaseTruthError(f"output or staging directory already exists: {final_dir}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    items = _phase_items(report)
    m74_source = next(
        row for row in report["sources"]["per_video"] if row["video_id"] == items[0]["video_id"]
    )
    m73_path = Path(m74_source["m73_report"]["path"]).resolve()
    m73 = json.loads(m73_path.read_text(encoding="utf-8"))
    video_path = Path(m73["sources"]["video"]["path"]).resolve()
    clip_manifest_path = Path(review_clip_manifest_path).resolve()
    clip_manifest = json.loads(clip_manifest_path.read_text(encoding="utf-8"))
    validate_fs09_phase_review_clip_manifest(clip_manifest)
    if clip_manifest["sources"]["m74_report"] != _source(report_path):
        raise FS09PhaseTruthError("review clips were not rendered from the bound M74 report")
    if clip_manifest["sources"]["video"] != _source(video_path):
        raise FS09PhaseTruthError("review clips were not rendered from the bound source video")
    per_video = {
        "video_id": items[0]["video_id"],
        "view_group": f"fixed-camera-{items[0]['video_id'][:8]}",
        "video": _source(video_path),
        "m73_report": _source(m73_path),
        "frames": m74_source["frames"],
        "primary_timeline": m74_source["primary_timeline"],
        "events": m74_source["events"],
        "review_clip_manifest": _source(clip_manifest_path),
        "review_clips": [
            {
                "task_id": item["task_id"],
                "source_start_ms": item["source_start_ms"],
                "source_end_ms": item["source_end_ms"],
                "source_time_offset_ms": item["source_time_offset_ms"],
                "target_selection_anchor_ms": item[
                    "target_selection_anchor_ms"
                ],
                "clip": item["clip"],
                "probe": item["probe"],
            }
            for item in clip_manifest["clips"]
        ],
    }
    tasks, candidates = _expected_contract(report, per_video)
    plan_path = Path(analysis_plan_path).resolve()
    analysis_plan, analysis_plan_raw, analysis_plan_digest = _json_snapshot(plan_path)
    if analysis_plan_digest != ANALYSIS_PLAN_SHA256:
        raise FS09PhaseTruthError("M77 analysis plan raw SHA is not the frozen canonical plan")
    validate_fs09_phase_analysis_plan(analysis_plan)
    staging.mkdir(parents=True)
    (staging / "analysis-plan.json").write_bytes(analysis_plan_raw)
    if sha256_file(staging / "analysis-plan.json") != analysis_plan_digest:
        raise FS09PhaseTruthError("portable M77 analysis plan copy mismatch")
    _write_jsonl(staging / "tasks.jsonl", tasks)
    _write_jsonl(staging / "sealed-event-candidates.jsonl", candidates)
    _write_csv(staging / "annotations.csv", ANNOTATION_FIELDS, [])
    _write_csv(staging / "adjudications.csv", ADJUDICATION_FIELDS, [])
    for name in ("fs09-phase-truth-workbench.js", "fs09-phase-truth-workbench.css"):
        shutil.copyfile(assets / name, staging / name)
    media_dir = staging / "media"
    media_dir.mkdir()
    for item in clip_manifest["clips"]:
        clip_source = Path(item["clip"]["path"]).resolve()
        clip_target = media_dir / Path(item["clip"]["path"]).name
        shutil.copyfile(clip_source, clip_target)
        if sha256_file(clip_target) != item["clip"]["sha256"]:
            raise FS09PhaseTruthError("portable M77 review clip copy mismatch")
    (staging / "OPERATOR_README.md").write_text(_operator_guide(), encoding="utf-8")
    bootstrap = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "handoff_bundle_id": None,
        "analysis_plan_sha256": None,
        "annotation_execution_authorized": False,
        "external_protocol_receipt_verified": False,
        "review_clips": {
            item["task_id"]: f"media/{Path(item['clip']['path']).name}"
            for item in clip_manifest["clips"]
        },
        "review_clip_fps": {
            item["task_id"]: item["probe"]["fps"]
            for item in clip_manifest["clips"]
        },
        "workbench_asset_sha256": {
            "js": sha256_file(staging / "fs09-phase-truth-workbench.js"),
            "css": sha256_file(staging / "fs09-phase-truth-workbench.css"),
        },
        "tasks": tasks,
        "phase_keys": list(PHASE_KEYS),
        "annotation_fields": list(ANNOTATION_FIELDS),
        "adjudication_fields": list(ADJUDICATION_FIELDS),
        "required_annotators": REQUIRED_ANNOTATORS,
    }
    (staging / "review.html").write_text(_workbench_html(bootstrap), encoding="utf-8")
    try:
        _validate_fs09_phase_analysis_plan_binding(
            analysis_plan,
            tasks=tasks,
            candidates=candidates,
            tasks_artifact_sha256=sha256_file(staging / "tasks.jsonl"),
            sealed_candidates_artifact_sha256=sha256_file(
                staging / "sealed-event-candidates.jsonl"
            ),
            m74_report_sha256=sha256_file(report_path),
            review_clip_manifest_sha256=sha256_file(clip_manifest_path),
            source_video_sha256=sha256_file(video_path),
            review_clip_sha256_by_task={
                str(item["task_id"]): str(item["clip"]["sha256"])
                for item in clip_manifest["clips"]
            },
            workbench_artifact_sha256={
                name: sha256_file(staging / name)
                for name in (
                    "fs09-phase-truth-workbench.js",
                    "fs09-phase-truth-workbench.css",
                    "review.html",
                    "OPERATOR_README.md",
                )
            },
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    manifest = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": _utc_now(),
        "status": "annotation_required",
        "sources": {
            "analysis_plan": {"path": str(plan_path), "sha256": analysis_plan_digest},
            "m74_report": _source(report_path),
            "per_video": [per_video],
        },
        "scope": {
            "video_count": 1,
            "video_ids": [items[0]["video_id"]],
            "view_groups": [per_video["view_group"]],
            "task_count": len(tasks),
            "indicator_ids": ["FS09-M05"],
            "event_codes": ["FS09"],
            "phase_keys": list(PHASE_KEYS),
            "required_independent_annotators": REQUIRED_ANNOTATORS,
            "required_independent_reviewer": 1,
        },
        "workbench": {
            "entry": "review.html",
            "annotation_csv": "annotations.csv",
            "adjudication_csv": "adjudications.csv",
            "compiled_events": "compiled/manual-fs09-events.jsonl",
            "validation_report": "compiled/validation-report.json",
            "sealed_candidates_not_loaded_by_ui": True,
            "annotation_execution_allowed": False,
            "public_blind_handoff_required": True,
        },
        "task_contract_sha256": _canonical_sha256(tasks),
        "candidate_contract_sha256": _canonical_sha256(candidates),
        "artifacts": {
            name: {"path": str((final_dir / name).resolve()), "sha256": sha256_file(staging / name)}
            for name in (
                "tasks.jsonl",
                "sealed-event-candidates.jsonl",
                "analysis-plan.json",
                "fs09-phase-truth-workbench.js",
                "fs09-phase-truth-workbench.css",
                "review.html",
                "OPERATOR_README.md",
                *(f"media/{Path(item['clip']['path']).name}" for item in clip_manifest["clips"]),
            )
        },
        "safety": {
            "candidate_boundaries_embedded_in_annotation_ui": False,
            "candidate_phases_embedded_in_annotation_ui": False,
            "pose_overlay_embedded_in_annotation_ui": False,
            "truth_prefilled_from_candidate": False,
            "ground_contact_or_force_claim": False,
            "independent_adjudication_required": True,
            "partial_annotations_change_runtime": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
            "review_clips_are_browser_h264": True,
            "review_clips_preserve_source_timestamp_mapping": True,
            "authority_pack_may_be_served_or_distributed": False,
            "public_blind_handoff_required": True,
        },
    }
    try:
        if plan_path.read_bytes() != analysis_plan_raw:
            raise FS09PhaseTruthError("M77 analysis plan changed before pack commit")
        validate_fs09_phase_truth_manifest(manifest)
        _write_json(staging / "manifest.json", manifest)
        staging.replace(final_dir)
        validation = compile_fs09_phase_truth_pack(final_dir)
        if validation.get("status") != "annotation_required":
            details = "; ".join(str(item) for item in validation.get("errors", []))
            raise FS09PhaseTruthError(
                f"new M77 pack failed source/integrity validation: {details}"
            )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        raise
    return manifest


def _parse_decision(row: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    present = _parse_bool(row["event_present"], name="event_present")
    start = _optional_int(row["event_start_ms"], name="event_start_ms")
    end = _optional_int(row["event_end_ms"], name="event_end_ms")
    event_reason = row["event_reason"].strip()
    if present:
        if start is None or end is None or start >= end:
            raise FS09PhaseTruthError("present event requires ordered start/end")
        if start < int(task["review_start_ms"]) or end > int(task["review_end_ms"]):
            raise FS09PhaseTruthError("manual event is outside the blind review window")
    elif start is not None or end is not None or not event_reason:
        raise FS09PhaseTruthError("absent event requires blank boundaries and a reason")
    phases: dict[str, int | None] = {}
    statuses: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for prefix, key in zip(PHASE_PREFIXES, PHASE_KEYS):
        status = row[f"{prefix}_status"].strip().lower()
        if status not in PHASE_STATUS:
            raise FS09PhaseTruthError(f"invalid phase status: {prefix}")
        value = _optional_int(row[f"{prefix}_ms"], name=f"{prefix}_ms")
        reason = row[f"{prefix}_reason"].strip()
        if status == "observed":
            if not present or value is None or value < int(start) or value > int(end):
                raise FS09PhaseTruthError(f"observed phase must be inside event: {prefix}")
        elif value is not None or not reason:
            raise FS09PhaseTruthError(f"unobserved phase requires blank time and reason: {prefix}")
        phases[key] = value
        statuses[key] = status
        reasons[key] = reason
    observed = [value for value in phases.values() if value is not None]
    if observed != sorted(observed):
        raise FS09PhaseTruthError("observed FS09 phases must be nondecreasing")
    return {
        "event_present": present,
        "start_ms": start,
        "end_ms": end,
        "event_reason": event_reason,
        "key_phases_ms": phases,
        "phase_status": statuses,
        "phase_reasons": reasons,
        "confidence": _parse_confidence(row["confidence"]),
        "notes": row["notes"].strip(),
    }


def validate_fs09_phase_truth_intake_manifest(
    pack_dir: str | Path,
    intake: dict[str, Any],
) -> None:
    from rallymate_evaluation.fs09_phase_handoff import (
        validate_fs09_phase_blind_handoff,
    )

    pack = Path(pack_dir).resolve()
    _require_exact_keys(
        intake,
        {
            "schema_version",
            "intake_version",
            "generated_at",
            "status",
            "source_pack",
            "blind_handoff",
            "exports",
            "merged",
            "counts",
            "safety",
        },
        name="M77 intake",
    )
    if (
        intake.get("schema_version") != "1.0.0"
        or intake.get("intake_version") != INTAKE_VERSION
        or intake.get("status") != "ready_for_phase_evaluation"
    ):
        raise FS09PhaseTruthError("unsupported M77 intake contract")
    _require_timestamp(str(intake.get("generated_at", "")), name="generated_at")
    source_pack = _require_exact_keys(
        intake.get("source_pack"),
        {
            "manifest",
            "analysis_plan",
            "pack_version",
            "task_contract_sha256",
            "candidate_contract_sha256",
        },
        name="M77 intake source_pack",
    )
    source_manifest = _require_exact_keys(
        source_pack.get("manifest"), {"path", "sha256"}, name="source manifest"
    )
    source_manifest_path = Path(str(source_manifest.get("path", ""))).resolve()
    copied_manifest_path = pack / "manifest.json"
    if not source_manifest_path.is_file() or not copied_manifest_path.is_file():
        raise FS09PhaseTruthError("M77 intake source-pack manifest mismatch")
    source_manifest_raw = source_manifest_path.read_bytes()
    copied_manifest_raw = copied_manifest_path.read_bytes()
    source_manifest_sha = hashlib.sha256(source_manifest_raw).hexdigest().upper()
    if (
        source_manifest_sha
        != _require_sha256(
            source_manifest.get("sha256"), name="source manifest SHA"
        )
        or source_manifest_raw != copied_manifest_raw
    ):
        raise FS09PhaseTruthError("M77 intake source-pack manifest mismatch")
    try:
        copied_manifest = json.loads(copied_manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("M77 intake copied manifest is invalid") from exc
    validate_fs09_phase_truth_manifest(copied_manifest)
    analysis_plan_record = _require_exact_keys(
        source_pack.get("analysis_plan"),
        {"path", "sha256"},
        name="M77 intake analysis plan",
    )
    analysis_plan_path = pack / "analysis-plan.json"
    analysis_plan, analysis_plan_raw, analysis_plan_digest = _json_snapshot(
        analysis_plan_path
    )
    if (
        _session_path(
            pack,
            str(analysis_plan_record.get("path", "")),
            expected="analysis-plan.json",
            name="M77 intake analysis plan path",
        )
        != analysis_plan_path.resolve()
        or analysis_plan_digest
        != _require_sha256(
            analysis_plan_record.get("sha256"), name="M77 intake analysis plan SHA"
        )
    ):
        raise FS09PhaseTruthError("M77 intake analysis-plan copy mismatch")
    validate_fs09_phase_analysis_plan(analysis_plan)
    _verify_pack_sources(
        pack,
        copied_manifest,
        source_artifact_root=source_manifest_path.parent,
    )
    if (
        source_pack.get("pack_version") != copied_manifest.get("pack_version")
        or source_pack.get("task_contract_sha256")
        != copied_manifest.get("task_contract_sha256")
        or source_pack.get("candidate_contract_sha256")
        != copied_manifest.get("candidate_contract_sha256")
    ):
        raise FS09PhaseTruthError("M77 intake source-pack contract mismatch")
    blind_handoff = _require_exact_keys(
        intake.get("blind_handoff"),
        {
            "manifest",
            "bundle_version",
            "bundle_id",
            "generated_at",
            "content_root_sha256",
            "analysis_plan_sha256",
            "source_pack_manifest_sha256",
        },
        name="M77 intake blind handoff",
    )
    blind_manifest_record = _require_exact_keys(
        blind_handoff.get("manifest"),
        {"path", "sha256"},
        name="M77 intake blind-handoff manifest",
    )
    blind_root = (pack / "blind-handoff").resolve()
    blind_manifest_path = blind_root / "handoff-manifest.json"
    blind_snapshot = validate_fs09_phase_blind_handoff(
        blind_root,
        source_pack_dir=source_manifest_path.parent,
    )
    blind_manifest = blind_snapshot["manifest"]
    blind_safety = blind_manifest.get("safety", {})
    if (
        blind_manifest.get("status")
        != "annotation_authorized_by_verified_external_protocol_receipt"
        or blind_safety.get("annotation_execution_authorized") is not True
        or blind_safety.get("external_protocol_receipt_verified") is not True
    ):
        raise FS09PhaseTruthError(
            "M77 intake is not externally protocol-authorized; technical-only "
            "handoff sessions cannot be compiled or evaluated"
        )
    if (
        _session_path(
            pack,
            str(blind_manifest_record.get("path", "")),
            expected="blind-handoff/handoff-manifest.json",
            name="M77 intake blind-handoff path",
        )
        != blind_manifest_path
        or _require_sha256(
            blind_manifest_record.get("sha256"),
            name="M77 intake blind-handoff manifest SHA",
        )
        != blind_snapshot["manifest_sha256"]
        or blind_handoff.get("bundle_version")
        != blind_snapshot["manifest"].get("bundle_version")
        or blind_handoff.get("bundle_id") != blind_snapshot["bundle_id"]
        or blind_handoff.get("generated_at") != blind_snapshot["generated_at"]
        or _require_sha256(
            blind_handoff.get("content_root_sha256"),
            name="M77 intake blind-handoff content root SHA",
        )
        != blind_snapshot["content_root_sha256"]
        or _require_sha256(
            blind_handoff.get("analysis_plan_sha256"),
            name="M77 intake blind-handoff analysis-plan SHA",
        )
        != analysis_plan_digest
        or _require_sha256(
            blind_handoff.get("source_pack_manifest_sha256"),
            name="M77 intake blind-handoff source-pack manifest SHA",
        )
        != source_manifest_sha
    ):
        raise FS09PhaseTruthError("M77 intake blind-handoff binding mismatch")
    frozen_at = datetime.fromisoformat(
        str(analysis_plan["frozen_at"]).replace("Z", "+00:00")
    )
    generated_at = datetime.fromisoformat(
        str(intake["generated_at"]).replace("Z", "+00:00")
    )
    source_generated_at = _timestamp_value(
        str(copied_manifest["generated_at"]), name="source manifest generated_at"
    )
    handoff_generated_at = _timestamp_value(
        str(blind_snapshot["generated_at"]), name="blind handoff generated_at"
    )
    if not frozen_at < source_generated_at < handoff_generated_at <= generated_at:
        raise FS09PhaseTruthError(
            "M77 intake plan/manifest/handoff chronology is invalid"
        )

    exports = _require_exact_keys(
        intake.get("exports"), {"annotations", "adjudication"}, name="M77 exports"
    )
    annotation_exports = exports.get("annotations")
    adjudication_export = exports.get("adjudication")
    if (
        not isinstance(annotation_exports, list)
        or len(annotation_exports) != REQUIRED_ANNOTATORS
        or not isinstance(adjudication_export, dict)
    ):
        raise FS09PhaseTruthError("M77 intake export membership is incomplete")
    raw_root = (pack / "raw-exports").resolve()
    annotation_snapshots: list[tuple[Path, list[dict[str, str]], bytes, str]] = []
    raw_paths: set[Path] = set()
    for index, value in enumerate(annotation_exports, start=1):
        record = _require_exact_keys(
            value,
            {
                "path",
                "sha256",
                "submitted_name",
                "rows",
                "annotator_id",
                "exported_at",
            },
            name=f"M77 annotation export {index}",
        )
        path = _session_path(
            pack,
            str(record["path"]),
            expected=f"raw-exports/annotation-export-{index:03d}.csv",
            name=f"M77 annotation export {index} path",
        )
        if path in raw_paths:
            raise FS09PhaseTruthError("M77 intake raw export paths must be unique")
        raw_paths.add(path)
        rows, raw, digest = _csv_snapshot(path, ANNOTATION_FIELDS)
        identities = {_require_safe_identity(row["annotator_id"], name="annotator_id") for row in rows}
        if (
            digest
            != _require_sha256(
                record["sha256"], name=f"M77 annotation export {index} SHA"
            )
            or record["rows"] != len(rows)
            or record["rows"] != TASK_COUNT
            or len(identities) != 1
            or record["annotator_id"] != next(iter(identities), None)
            or record["exported_at"]
            != next(iter({row["exported_at"] for row in rows}), None)
            or not str(record["submitted_name"]).strip()
        ):
            raise FS09PhaseTruthError(f"M77 intake raw export SHA mismatch: {path}")
        annotation_snapshots.append((path, rows, raw, digest))

    adjudication_record = _require_exact_keys(
        adjudication_export,
        {
            "path",
            "sha256",
            "submitted_name",
            "rows",
            "reviewer_id",
            "exported_at",
        },
        name="M77 adjudication export",
    )
    adjudication_path = _session_path(
        pack,
        str(adjudication_record["path"]),
        expected="raw-exports/adjudication-export-001.csv",
        name="M77 adjudication export path",
    )
    if adjudication_path in raw_paths:
        raise FS09PhaseTruthError("M77 intake raw export paths must be unique")
    adjudication_rows, adjudication_raw, adjudication_digest = _csv_snapshot(
        adjudication_path, ADJUDICATION_FIELDS
    )
    reviewers = {
        _require_safe_identity(row["reviewer_id"], name="reviewer_id")
        for row in adjudication_rows
    }
    if (
        adjudication_digest
        != _require_sha256(
            adjudication_record["sha256"], name="M77 adjudication export SHA"
        )
        or adjudication_record["rows"] != len(adjudication_rows)
        or adjudication_record["rows"] != TASK_COUNT
        or len(reviewers) != 1
        or adjudication_record["reviewer_id"] != next(iter(reviewers), None)
        or adjudication_record["exported_at"]
        != next(
            iter({row["exported_at"] for row in adjudication_rows}), None
        )
        or not str(adjudication_record["submitted_name"]).strip()
    ):
        raise FS09PhaseTruthError(
            f"M77 intake raw export SHA mismatch: {adjudication_path}"
        )

    tasks, _task_raw, _task_digest = _jsonl_snapshot(pack / "tasks.jsonl")
    expected_annotations, expected_adjudications, _, _ = (
        _validate_fs09_phase_truth_exports(
            tasks=tasks,
            annotation_snapshots=annotation_snapshots,
            adjudication_snapshot=(
                adjudication_path,
                adjudication_rows,
                adjudication_raw,
                adjudication_digest,
            ),
            frozen_at=str(analysis_plan["frozen_at"]),
            manifest_generated_at=str(copied_manifest["generated_at"]),
            handoff_bundle_id=str(blind_snapshot["bundle_id"]),
            analysis_plan_sha256=analysis_plan_digest,
            handoff_generated_at=str(blind_snapshot["generated_at"]),
            intake_generated_at=str(intake["generated_at"]),
        )
    )

    merged = _require_exact_keys(
        intake.get("merged"), {"annotations", "adjudications"}, name="M77 merged"
    )
    expected_merged = {
        "annotations": (pack / "annotations.csv", "annotations.csv", ANNOTATION_FIELDS, expected_annotations),
        "adjudications": (
            pack / "adjudications.csv",
            "adjudications.csv",
            ADJUDICATION_FIELDS,
            expected_adjudications,
        ),
    }
    for name, (path, relative_path, fields, expected_rows) in expected_merged.items():
        record = _require_exact_keys(
            merged.get(name), {"path", "sha256"}, name=f"M77 merged {name}"
        )
        actual_rows, _raw, digest = _csv_snapshot(path, fields)
        if (
            _session_path(
                pack,
                str(record.get("path", "")),
                expected=relative_path,
                name=f"M77 merged {name} path",
            )
            != path.resolve()
            or digest
            != _require_sha256(
                record.get("sha256"), name=f"M77 merged {name} SHA"
            )
            or actual_rows != expected_rows
        ):
            raise FS09PhaseTruthError(f"M77 intake merged {name} mismatch")
    counts = _require_exact_keys(
        intake.get("counts"),
        {
            "tasks",
            "annotation_exports",
            "adjudication_exports",
            "raw_annotations",
            "raw_adjudications",
        },
        name="M77 intake counts",
    )
    if not _strict_json_equal(counts, {
        "tasks": TASK_COUNT,
        "annotation_exports": REQUIRED_ANNOTATORS,
        "adjudication_exports": 1,
        "raw_annotations": TASK_COUNT * REQUIRED_ANNOTATORS,
        "raw_adjudications": TASK_COUNT,
    }):
        raise FS09PhaseTruthError("M77 intake counts drifted")
    safety = _require_exact_keys(
        intake.get("safety"),
        {
            "source_exports_preserved_byte_exact",
            "source_pack_modified",
            "blind_handoff_verified",
            "blind_handoff_snapshot_preserved_byte_exact",
            "stable_decision_times_preserved",
            "candidate_values_used_as_truth",
            "production_enabled",
            "grade_generated",
            "threshold_generated",
            "maturity_promoted",
        },
        name="M77 intake safety",
    )
    expected_safety = {
        "source_exports_preserved_byte_exact": True,
        "source_pack_modified": False,
        "blind_handoff_verified": True,
        "blind_handoff_snapshot_preserved_byte_exact": True,
        "stable_decision_times_preserved": True,
        "candidate_values_used_as_truth": False,
        "production_enabled": False,
        "grade_generated": False,
        "threshold_generated": False,
        "maturity_promoted": False,
    }
    if not _strict_json_equal(safety, expected_safety):
        raise FS09PhaseTruthError("unsafe M77 intake declaration")


def _validate_fs09_phase_truth_exports(
    *,
    tasks: list[dict[str, Any]],
    annotation_snapshots: list[tuple[Path, list[dict[str, str]], bytes, str]],
    adjudication_snapshot: tuple[Path, list[dict[str, str]], bytes, str],
    frozen_at: str,
    manifest_generated_at: str,
    handoff_bundle_id: str,
    analysis_plan_sha256: str,
    handoff_generated_at: str,
    intake_generated_at: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str], str]:
    task_by_id = {str(task["task_id"]): task for task in tasks}
    expected_tasks = set(task_by_id)
    if len(tasks) != TASK_COUNT or len(expected_tasks) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 intake task contract is incomplete")
    if len(annotation_snapshots) != REQUIRED_ANNOTATORS:
        raise FS09PhaseTruthError(
            f"M77 intake requires exactly {REQUIRED_ANNOTATORS} annotation exports"
        )

    frozen = datetime.fromisoformat(
        _require_timestamp(frozen_at, name="analysis plan frozen_at").replace("Z", "+00:00")
    )
    manifest_time = datetime.fromisoformat(
        _require_timestamp(
            manifest_generated_at, name="source manifest generated_at"
        ).replace("Z", "+00:00")
    )
    intake_time = datetime.fromisoformat(
        _require_timestamp(intake_generated_at, name="intake generated_at").replace(
            "Z", "+00:00"
        )
    )
    handoff_time = _timestamp_value(
        handoff_generated_at, name="blind handoff generated_at"
    )
    if not frozen < manifest_time < handoff_time <= intake_time:
        raise FS09PhaseTruthError(
            "M77 plan/manifest/handoff/intake chronology is invalid"
        )
    annotations: list[dict[str, str]] = []
    annotator_ids: list[str] = []
    annotation_ids: set[str] = set()
    annotation_ids_by_task: dict[str, set[str]] = defaultdict(set)
    annotation_export_times_by_id: dict[str, datetime] = {}
    annotation_revision_by_id: dict[str, str] = {}
    for path, rows, _raw, _digest in annotation_snapshots:
        if len(rows) != TASK_COUNT or {row["task_id"].strip() for row in rows} != expected_tasks:
            raise FS09PhaseTruthError(
                f"{path.name} must contain each M77 task exactly once"
            )
        identities = {
            _require_safe_identity(row["annotator_id"], name="annotator_id")
            for row in rows
        }
        if len(identities) != 1 or not next(iter(identities), ""):
            raise FS09PhaseTruthError(
                f"{path.name} must contain one non-empty annotator_id"
            )
        annotator_id = next(iter(identities))
        annotator_ids.append(annotator_id)
        export_times: set[str] = set()
        for row in rows:
            task_id = row["task_id"].strip()
            annotation_id = _require_safe_identity(
                row["annotation_id"], name="annotation_id"
            )
            if (
                annotation_id in annotation_ids
            ):
                raise FS09PhaseTruthError(
                    f"{path.name} contains a missing/duplicate annotation id or time"
                )
            annotation_time, annotation_export_time = _validate_handoff_export_row(
                row,
                handoff_bundle_id=handoff_bundle_id,
                analysis_plan_sha256=analysis_plan_sha256,
                handoff_generated_at=handoff_time,
                decision_time_field="annotated_at",
                intake_generated_at=intake_time,
            )
            _parse_decision(row, task_by_id[task_id])
            revision = _require_sha256(
                row.get("annotation_revision_sha256"),
                name="annotation_revision_sha256",
            )
            if revision != _annotation_revision_sha256(row):
                raise FS09PhaseTruthError(
                    "M77 annotation revision digest does not match its decision"
                )
            annotation_ids.add(annotation_id)
            annotation_ids_by_task[task_id].add(annotation_id)
            annotation_export_times_by_id[annotation_id] = annotation_export_time
            annotation_revision_by_id[annotation_id] = revision
            export_times.add(row["exported_at"])
            annotations.append(row)
        if len(export_times) != 1:
            raise FS09PhaseTruthError(
                f"{path.name} must use one immutable exported_at for the whole export"
            )
    if len({_identity_key(value) for value in annotator_ids}) != REQUIRED_ANNOTATORS:
        raise FS09PhaseTruthError("M77 annotation exports must come from independent annotators")

    adjudication_path, adjudications, _raw, _digest = adjudication_snapshot
    if (
        len(adjudications) != TASK_COUNT
        or {row["task_id"].strip() for row in adjudications} != expected_tasks
    ):
        raise FS09PhaseTruthError(
            f"{adjudication_path.name} must contain each M77 task exactly once"
        )
    reviewers = {
        _require_safe_identity(row["reviewer_id"], name="reviewer_id")
        for row in adjudications
    }
    if len(reviewers) != 1 or not next(iter(reviewers), ""):
        raise FS09PhaseTruthError("M77 adjudication export must contain one reviewer_id")
    reviewer_id = next(iter(reviewers))
    if _identity_key(reviewer_id) in {_identity_key(value) for value in annotator_ids}:
        raise FS09PhaseTruthError("reviewer must be independent")
    adjudication_ids: set[str] = set()
    adjudication_export_times: set[str] = set()
    for row in adjudications:
        task_id = row["task_id"].strip()
        adjudication_id = _require_safe_identity(
            row["adjudication_id"], name="adjudication_id"
        )
        source_values = [
            value.strip()
            for value in row["source_annotation_ids"].split(";")
            if value.strip()
        ]
        source_revisions = [
            value.strip()
            for value in row["source_annotation_revision_sha256s"].split(";")
            if value.strip()
        ]
        if (
            adjudication_id in adjudication_ids
            or row["status"].strip().lower() != "accepted"
        ):
            raise FS09PhaseTruthError(
                "M77 adjudication id/time/status is invalid or duplicate"
            )
        if (
            len(source_values) != REQUIRED_ANNOTATORS
            or len(set(source_values)) != REQUIRED_ANNOTATORS
            or source_values != sorted(annotation_ids_by_task[task_id])
            or len(source_revisions) != REQUIRED_ANNOTATORS
            or any(
                _require_sha256(value, name="source annotation revision SHA")
                != annotation_revision_by_id[source_id]
                for source_id, value in zip(source_values, source_revisions, strict=True)
            )
        ):
            raise FS09PhaseTruthError(
                "M77 adjudication must reference the exact two task annotation revisions"
            )
        adjudication_time, adjudication_export_time = _validate_handoff_export_row(
            row,
            handoff_bundle_id=handoff_bundle_id,
            analysis_plan_sha256=analysis_plan_sha256,
            handoff_generated_at=handoff_time,
            decision_time_field="adjudicated_at",
            intake_generated_at=intake_time,
        )
        latest_annotation = max(
            datetime.fromisoformat(
                row_value["annotated_at"].strip().replace("Z", "+00:00")
            )
            for row_value in annotations
            if row_value["annotation_id"].strip() in source_values
        )
        if adjudication_time <= latest_annotation:
            raise FS09PhaseTruthError("adjudication must be later than source annotations")
        latest_annotation_export = max(
            annotation_export_times_by_id[value] for value in source_values
        )
        if adjudication_time <= latest_annotation_export:
            raise FS09PhaseTruthError(
                "adjudication must be later than the source annotation exports"
            )
        _parse_decision(row, task_by_id[task_id])
        adjudication_ids.add(adjudication_id)
        adjudication_export_times.add(row["exported_at"])
    if len(adjudication_export_times) != 1:
        raise FS09PhaseTruthError(
            f"{adjudication_path.name} must use one immutable exported_at for the whole export"
        )
    return annotations, adjudications, annotator_ids, reviewer_id


def _compile_fs09_phase_truth_rows(
    tasks: list[dict[str, Any]],
    annotations_csv: list[dict[str, str]],
    adjudications_csv: list[dict[str, str]],
    *,
    frozen_at: str | None,
) -> tuple[
    list[str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    int,
]:
    errors: list[str] = []
    if frozen_at is None:
        return ["M77 analysis plan freeze is unavailable"], {}, {}, 0
    try:
        frozen = datetime.fromisoformat(
            _require_timestamp(
                frozen_at, name="analysis plan frozen_at"
            ).replace("Z", "+00:00")
        )
    except Exception as exc:
        return [str(exc)], {}, {}, 0
    task_by_id = {str(task["task_id"]): task for task in tasks}
    if len(task_by_id) != len(tasks):
        errors.append("duplicate M77 task_id")
    annotations: dict[str, dict[str, Any]] = {}
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line, row in enumerate(annotations_csv, start=2):
        try:
            annotation_id = _require_safe_identity(
                row["annotation_id"], name="annotation_id"
            )
            task_id = row["task_id"].strip()
            annotator_id = _require_safe_identity(
                row["annotator_id"], name="annotator_id"
            )
            if annotation_id in annotations:
                raise FS09PhaseTruthError(
                    "annotation_id must be unique"
                )
            if (
                task_id not in task_by_id
            ):
                raise FS09PhaseTruthError("annotation task/annotator/time is invalid")
            if any(
                _identity_key(existing["annotator_id"]) == _identity_key(annotator_id)
                for existing in by_task[task_id]
            ):
                raise FS09PhaseTruthError("one annotator may submit once per task")
            record = {
                "annotation_id": annotation_id,
                "task_id": task_id,
                "annotator_id": annotator_id,
                **_parse_decision(row, task_by_id[task_id]),
                "annotated_at": _require_timestamp(
                    row["annotated_at"], name="annotated_at"
                ),
            }
            if datetime.fromisoformat(
                record["annotated_at"].replace("Z", "+00:00")
            ) <= frozen:
                raise FS09PhaseTruthError(
                    "annotation must be later than the frozen analysis plan"
                )
            annotations[annotation_id] = record
            by_task[task_id].append(record)
        except Exception as exc:
            errors.append(f"annotations.csv row {line}: {exc}")
    accepted: dict[str, dict[str, Any]] = {}
    adjudication_ids: set[str] = set()
    for line, row in enumerate(adjudications_csv, start=2):
        if row.get("status", "").strip().lower() != "accepted":
            continue
        try:
            task_id = row["task_id"].strip()
            if task_id not in task_by_id or task_id in accepted:
                raise FS09PhaseTruthError("accepted adjudication task is invalid or duplicate")
            raw_source_ids = [
                value.strip()
                for value in row["source_annotation_ids"].split(";")
                if value.strip()
            ]
            source_ids = sorted(set(raw_source_ids))
            if (
                len(raw_source_ids) != REQUIRED_ANNOTATORS
                or len(source_ids) != REQUIRED_ANNOTATORS
                or any(value not in annotations for value in source_ids)
            ):
                raise FS09PhaseTruthError(
                    "accepted adjudication requires exactly two source annotations"
                )
            sources = [annotations[value] for value in source_ids]
            if any(item["task_id"] != task_id for item in sources):
                raise FS09PhaseTruthError("adjudication sources must match task")
            task_annotation_ids = {
                item["annotation_id"] for item in by_task.get(task_id, [])
            }
            if set(source_ids) != task_annotation_ids:
                raise FS09PhaseTruthError(
                    "adjudication must reference every and only task annotation"
                )
            annotators = {item["annotator_id"] for item in sources}
            reviewer = _require_safe_identity(
                row["reviewer_id"], name="reviewer_id"
            )
            if (
                len({_identity_key(value) for value in annotators})
                != REQUIRED_ANNOTATORS
                or _identity_key(reviewer)
                in {_identity_key(value) for value in annotators}
            ):
                raise FS09PhaseTruthError("reviewer must be independent")
            adjudication_id = _require_safe_identity(
                row["adjudication_id"], name="adjudication_id"
            )
            if adjudication_id in adjudication_ids:
                raise FS09PhaseTruthError("adjudication_id must be unique")
            adjudicated_at = _require_timestamp(
                row["adjudicated_at"], name="adjudicated_at"
            )
            if datetime.fromisoformat(
                adjudicated_at.replace("Z", "+00:00")
            ) <= frozen:
                raise FS09PhaseTruthError(
                    "adjudication must be later than the frozen analysis plan"
                )
            if datetime.fromisoformat(
                adjudicated_at.replace("Z", "+00:00")
            ) <= max(
                datetime.fromisoformat(
                    item["annotated_at"].replace("Z", "+00:00")
                )
                for item in sources
            ):
                raise FS09PhaseTruthError(
                    "adjudication must be later than source annotations"
                )
            accepted[task_id] = {
                "schema_version": "1.0.0",
                "task_id": task_id,
                "video_id": task_by_id[task_id]["video_id"],
                "view_group": task_by_id[task_id]["view_group"],
                "event_code": "FS09",
                "indicator_id": "FS09-M05",
                **_parse_decision(row, task_by_id[task_id]),
                "source_annotation_ids": source_ids,
                "source_annotator_ids": sorted(annotators),
                "reviewer_id": reviewer,
                "adjudication_id": adjudication_id,
                "adjudicated_at": adjudicated_at,
                "adjudication_status": "accepted",
            }
            adjudication_ids.add(adjudication_id)
        except Exception as exc:
            errors.append(f"adjudications.csv row {line}: {exc}")
    independent = sum(
        len({_identity_key(row["annotator_id"]) for row in values})
        == REQUIRED_ANNOTATORS
        and len(values) == REQUIRED_ANNOTATORS
        for values in by_task.values()
    )
    return errors, annotations, accepted, independent


def _compile_fs09_phase_truth_pack(
    pack_dir: str | Path,
    *,
    allow_existing_intake_recompile: bool,
) -> dict[str, Any]:
    pack = Path(pack_dir).resolve()
    manifest_path = pack / "manifest.json"
    manifest, manifest_raw, manifest_digest = _json_snapshot(manifest_path)
    errors: list[str] = []
    analysis_plan: dict[str, Any] | None = None
    intake_path = pack / "intake-manifest.json"
    if intake_path.is_file() and not allow_existing_intake_recompile:
        raise FS09PhaseTruthError(
            "committed M77 intake sessions are immutable and cannot be recompiled"
        )
    intake_snapshot: tuple[bytes, str] | None = None
    intake: dict[str, Any] | None = None
    blind_handoff_source: dict[str, str] | None = None
    local_snapshots: dict[str, tuple[Path, bytes, str]] = {}
    try:
        tasks_path = pack / "tasks.jsonl"
        candidates_path = pack / "sealed-event-candidates.jsonl"
        annotations_path = pack / "annotations.csv"
        adjudications_path = pack / "adjudications.csv"
        analysis_plan_path = pack / "analysis-plan.json"
        analysis_plan_value, analysis_plan_raw, analysis_plan_digest = _json_snapshot(
            analysis_plan_path
        )
        if not isinstance(analysis_plan_value, dict):
            raise FS09PhaseTruthError("M77 analysis plan must be a JSON object")
        analysis_plan = analysis_plan_value
        validate_fs09_phase_analysis_plan(analysis_plan)
        tasks, tasks_raw, tasks_digest = _jsonl_snapshot(tasks_path)
        _candidates, candidates_raw, candidates_digest = _jsonl_snapshot(candidates_path)
        annotations_csv, annotations_raw, annotations_digest = _csv_snapshot(
            annotations_path, ANNOTATION_FIELDS
        )
        adjudications_csv, adjudications_raw, adjudications_digest = _csv_snapshot(
            adjudications_path, ADJUDICATION_FIELDS
        )
        local_snapshots = {
            "tasks": (tasks_path, tasks_raw, tasks_digest),
            "sealed_candidates": (
                candidates_path,
                candidates_raw,
                candidates_digest,
            ),
            "annotations": (annotations_path, annotations_raw, annotations_digest),
            "adjudications": (
                adjudications_path,
                adjudications_raw,
                adjudications_digest,
            ),
            "analysis_plan": (
                analysis_plan_path,
                analysis_plan_raw,
                analysis_plan_digest,
            ),
        }
        if intake_path.is_file():
            intake, intake_raw, intake_digest = _json_snapshot(intake_path)
            validate_fs09_phase_truth_intake_manifest(pack, intake)
            intake_snapshot = (intake_raw, intake_digest)
            blind_handoff_source = dict(intake["blind_handoff"]["manifest"])
        else:
            _verify_pack_sources(pack, manifest)
    except Exception as exc:
        tasks, annotations_csv, adjudications_csv = [], [], []
        errors.append(str(exc))
    row_errors, annotations, accepted, independent = _compile_fs09_phase_truth_rows(
        tasks,
        annotations_csv,
        adjudications_csv,
        frozen_at=(
            str(analysis_plan["frozen_at"])
            if isinstance(analysis_plan, dict) and "frozen_at" in analysis_plan
            else None
        ),
    )
    errors.extend(row_errors)
    if not intake_path.is_file() and (annotations_csv or adjudications_csv):
        errors.append(
            "non-empty M77 truth requires atomic export intake; direct pack editing is not accepted"
        )
    complete = (
        not errors
        and intake_path.is_file()
        and len(tasks) == TASK_COUNT
        and len(annotations) == TASK_COUNT * REQUIRED_ANNOTATORS
        and len(adjudications_csv) == TASK_COUNT
        and independent == TASK_COUNT
        and len(accepted) == TASK_COUNT
    )
    status = "invalid_annotations" if errors else "ready_for_phase_evaluation" if complete else "annotation_required"
    compiled = pack / "compiled"
    compiled.mkdir(exist_ok=True)
    manual_path = compiled / "manual-fs09-events.jsonl"
    manual = [accepted[key] for key in sorted(accepted)]
    manual_path.write_bytes(_canonical_jsonl_bytes(manual))
    validation = {
        "schema_version": "1.0.0",
        "pack_version": PACK_VERSION,
        "generated_at": _utc_now(),
        "status": status,
        "counts": {
            "tasks": len(tasks),
            "raw_annotations": len(annotations),
            "tasks_with_two_independent_annotators": independent,
            "accepted_adjudications": len(accepted),
            "compiled_manual_events": len(manual),
        },
        "errors": errors,
        "readiness": {
            "two_independent_annotators_per_task": independent == TASK_COUNT,
            "independent_adjudication_per_task": len(accepted) == TASK_COUNT,
            "full_task_coverage": complete,
            "runtime_event_or_feature_change_allowed": False,
        },
        "sources": {
            "manifest": _session_source(
                pack, manifest_path, expected="manifest.json"
            ),
            **{
                name: _session_source(
                    pack,
                    path,
                    expected={
                        "tasks": "tasks.jsonl",
                        "sealed_candidates": "sealed-event-candidates.jsonl",
                        "annotations": "annotations.csv",
                        "adjudications": "adjudications.csv",
                        "analysis_plan": "analysis-plan.json",
                    }[name],
                )
                for name, (path, _raw, digest) in local_snapshots.items()
            },
            **(
                {
                    "intake_manifest": {
                        **_session_source(
                            pack,
                            intake_path,
                            expected="intake-manifest.json",
                        ),
                    }
                }
                if intake_snapshot is not None
                else {}
            ),
            **(
                {"blind_handoff_manifest": blind_handoff_source}
                if blind_handoff_source is not None
                else {}
            ),
        },
        "artifacts": {
            "manual_events": _session_source(
                pack,
                manual_path,
                expected="compiled/manual-fs09-events.jsonl",
            )
        },
        "safety": {
            "ground_truth_complete": complete,
            "candidate_values_used_as_truth": False,
            "partial_annotations_change_runtime": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    if manifest_path.read_bytes() != manifest_raw or any(
        path.read_bytes() != raw for path, raw, _digest in local_snapshots.values()
    ):
        raise FS09PhaseTruthError("M77 pack changed during compilation")
    if intake_snapshot is not None and intake_path.read_bytes() != intake_snapshot[0]:
        raise FS09PhaseTruthError("M77 intake changed during compilation")
    if intake is not None:
        validate_fs09_phase_truth_intake_manifest(pack, intake)
    _write_json(compiled / "validation-report.json", validation)
    return validation


def compile_fs09_phase_truth_pack(pack_dir: str | Path) -> dict[str, Any]:
    return _compile_fs09_phase_truth_pack(
        pack_dir,
        allow_existing_intake_recompile=False,
    )


def ingest_fs09_phase_truth_exports(
    *,
    pack_dir: str | Path,
    annotation_exports: list[str | Path] | tuple[str | Path, ...],
    adjudication_export: str | Path,
    handoff_manifest_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    from rallymate_evaluation.fs09_phase_handoff import (
        validate_fs09_phase_blind_handoff,
    )

    source_pack = Path(pack_dir).resolve()
    final = Path(output_dir).resolve()
    staging = final.with_name(f".{final.name}.building")
    if _paths_overlap(final, source_pack) or final.exists() or staging.exists():
        raise FS09PhaseTruthError(
            f"intake output or staging directory already exists/overlaps source pack: {final}"
        )
    manifest_path = source_pack / "manifest.json"
    manifest, manifest_raw, manifest_digest = _json_snapshot(manifest_path)
    _verify_pack_sources(source_pack, manifest)
    tasks_path = source_pack / "tasks.jsonl"
    candidates_path = source_pack / "sealed-event-candidates.jsonl"
    analysis_plan_path = source_pack / "analysis-plan.json"
    analysis_plan, analysis_plan_raw, analysis_plan_digest = _json_snapshot(
        analysis_plan_path
    )
    validate_fs09_phase_analysis_plan(analysis_plan)
    intake_generated_at = _utc_now()
    tasks, tasks_raw, tasks_digest = _jsonl_snapshot(tasks_path)
    _candidates, candidates_raw, candidates_digest = _jsonl_snapshot(candidates_path)
    if (
        tasks_digest != manifest["artifacts"]["tasks.jsonl"]["sha256"]
        or candidates_digest
        != manifest["artifacts"]["sealed-event-candidates.jsonl"]["sha256"]
    ):
        raise FS09PhaseTruthError("M77 intake source pack changed after verification")

    handoff_manifest_input = Path(handoff_manifest_path).resolve()
    if handoff_manifest_input.name != "handoff-manifest.json":
        raise FS09PhaseTruthError(
            "M77 intake requires the exact public blind-handoff manifest filename"
        )
    handoff_snapshot = validate_fs09_phase_blind_handoff(
        handoff_manifest_input.parent,
        source_pack_dir=source_pack,
    )
    if handoff_snapshot["manifest_sha256"] != hashlib.sha256(
        handoff_snapshot["manifest_raw"]
    ).hexdigest().upper():
        raise FS09PhaseTruthError("M77 blind-handoff snapshot identity mismatch")
    handoff_manifest = handoff_snapshot["manifest"]
    handoff_safety = handoff_manifest.get("safety", {})
    if (
        handoff_manifest.get("status")
        != "annotation_authorized_by_verified_external_protocol_receipt"
        or handoff_safety.get("annotation_execution_authorized") is not True
        or handoff_safety.get("external_protocol_receipt_verified") is not True
    ):
        raise FS09PhaseTruthError(
            "M77 public handoff is technical-only: central intake requires a "
            "separately verified external protocol receipt/trust anchor that binds "
            "the raw handoff manifest, bundle ID, analysis-plan SHA, and predates "
            "every human decision; no such authorization is present"
        )

    annotation_snapshots: list[tuple[Path, list[dict[str, str]], bytes, str]] = []
    for value in annotation_exports:
        path = Path(value).resolve()
        rows, raw, digest = _csv_snapshot(path, ANNOTATION_FIELDS)
        annotation_snapshots.append((path, rows, raw, digest))
    adjudication_path = Path(adjudication_export).resolve()
    rows, raw, digest = _csv_snapshot(adjudication_path, ADJUDICATION_FIELDS)
    adjudication_snapshot = (adjudication_path, rows, raw, digest)
    annotations, adjudications, annotator_ids, reviewer_id = (
        _validate_fs09_phase_truth_exports(
            tasks=tasks,
            annotation_snapshots=annotation_snapshots,
            adjudication_snapshot=adjudication_snapshot,
            frozen_at=str(analysis_plan["frozen_at"]),
            manifest_generated_at=str(manifest["generated_at"]),
            handoff_bundle_id=str(handoff_snapshot["bundle_id"]),
            analysis_plan_sha256=analysis_plan_digest,
            handoff_generated_at=str(handoff_snapshot["generated_at"]),
            intake_generated_at=intake_generated_at,
        )
    )

    def intake_manifest(root: Path) -> dict[str, Any]:
        raw_root = root / "raw-exports"
        return {
            "schema_version": "1.0.0",
            "intake_version": INTAKE_VERSION,
            "generated_at": intake_generated_at,
            "status": "ready_for_phase_evaluation",
            "source_pack": {
                "manifest": {
                    "path": str(manifest_path),
                    "sha256": manifest_digest,
                },
                "analysis_plan": {
                    **_session_source(
                        root,
                        root / "analysis-plan.json",
                        expected="analysis-plan.json",
                    ),
                },
                "pack_version": manifest["pack_version"],
                "task_contract_sha256": manifest["task_contract_sha256"],
                "candidate_contract_sha256": manifest["candidate_contract_sha256"],
            },
            "blind_handoff": {
                "manifest": {
                    **_session_source(
                        root,
                        root / "blind-handoff" / "handoff-manifest.json",
                        expected="blind-handoff/handoff-manifest.json",
                    ),
                },
                "bundle_version": handoff_manifest["bundle_version"],
                "bundle_id": handoff_snapshot["bundle_id"],
                "generated_at": handoff_snapshot["generated_at"],
                "content_root_sha256": handoff_snapshot["content_root_sha256"],
                "analysis_plan_sha256": analysis_plan_digest,
                "source_pack_manifest_sha256": manifest_digest,
            },
            "exports": {
                "annotations": [
                    {
                        **_session_source(
                            root,
                            raw_root / f"annotation-export-{index:03d}.csv",
                            expected=f"raw-exports/annotation-export-{index:03d}.csv",
                        ),
                        "submitted_name": snapshot[0].name,
                        "rows": len(snapshot[1]),
                        "annotator_id": annotator_ids[index - 1],
                        "exported_at": snapshot[1][0]["exported_at"],
                    }
                    for index, snapshot in enumerate(annotation_snapshots, start=1)
                ],
                "adjudication": {
                    **_session_source(
                        root,
                        raw_root / "adjudication-export-001.csv",
                        expected="raw-exports/adjudication-export-001.csv",
                    ),
                    "submitted_name": adjudication_snapshot[0].name,
                    "rows": len(adjudication_snapshot[1]),
                    "reviewer_id": reviewer_id,
                    "exported_at": adjudication_snapshot[1][0]["exported_at"],
                },
            },
            "merged": {
                "annotations": _session_source(
                    root, root / "annotations.csv", expected="annotations.csv"
                ),
                "adjudications": _session_source(
                    root, root / "adjudications.csv", expected="adjudications.csv"
                ),
            },
            "counts": {
                "tasks": TASK_COUNT,
                "annotation_exports": REQUIRED_ANNOTATORS,
                "adjudication_exports": 1,
                "raw_annotations": len(annotations),
                "raw_adjudications": len(adjudications),
            },
            "safety": {
                "source_exports_preserved_byte_exact": True,
                "source_pack_modified": False,
                "blind_handoff_verified": True,
                "blind_handoff_snapshot_preserved_byte_exact": True,
                "stable_decision_times_preserved": True,
                "candidate_values_used_as_truth": False,
                "production_enabled": False,
                "grade_generated": False,
                "threshold_generated": False,
                "maturity_promoted": False,
            },
        }

    committed = False
    try:
        current_handoff = validate_fs09_phase_blind_handoff(
            handoff_manifest_input.parent,
            source_pack_dir=source_pack,
        )
        if (
            manifest_path.read_bytes() != manifest_raw
            or tasks_path.read_bytes() != tasks_raw
            or candidates_path.read_bytes() != candidates_raw
            or analysis_plan_path.read_bytes() != analysis_plan_raw
            or any(path.read_bytes() != raw for path, _rows, raw, _digest in annotation_snapshots)
            or adjudication_path.read_bytes() != adjudication_snapshot[2]
            or current_handoff["manifest_raw"] != handoff_snapshot["manifest_raw"]
            or {
                name: value["raw"]
                for name, value in current_handoff["artifacts"].items()
            }
            != {
                name: value["raw"]
                for name, value in handoff_snapshot["artifacts"].items()
            }
        ):
            raise FS09PhaseTruthError("M77 intake inputs changed before snapshot commit")
        staging.mkdir(parents=True)
        (staging / "manifest.json").write_bytes(manifest_raw)
        (staging / "tasks.jsonl").write_bytes(tasks_raw)
        (staging / "sealed-event-candidates.jsonl").write_bytes(candidates_raw)
        (staging / "analysis-plan.json").write_bytes(analysis_plan_raw)
        if sha256_file(staging / "analysis-plan.json") != analysis_plan_digest:
            raise FS09PhaseTruthError("M77 analysis-plan snapshot copy mismatch")
        handoff_target_root = staging / "blind-handoff"
        handoff_target_root.mkdir()
        (handoff_target_root / "handoff-manifest.json").write_bytes(
            handoff_snapshot["manifest_raw"]
        )
        for relative_path, artifact in handoff_snapshot["artifacts"].items():
            target = handoff_target_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(artifact["raw"])
        staged_handoff = validate_fs09_phase_blind_handoff(
            handoff_target_root,
            source_pack_dir=source_pack,
        )
        if (
            staged_handoff["manifest_raw"] != handoff_snapshot["manifest_raw"]
            or {
                name: value["raw"]
                for name, value in staged_handoff["artifacts"].items()
            }
            != {
                name: value["raw"]
                for name, value in handoff_snapshot["artifacts"].items()
            }
        ):
            raise FS09PhaseTruthError("M77 blind-handoff snapshot copy mismatch")
        raw_root = staging / "raw-exports"
        raw_root.mkdir()
        for index, (_path, _rows, snapshot_raw, snapshot_digest) in enumerate(
            annotation_snapshots, start=1
        ):
            target = raw_root / f"annotation-export-{index:03d}.csv"
            target.write_bytes(snapshot_raw)
            if sha256_file(target) != snapshot_digest:
                raise FS09PhaseTruthError("M77 annotation export snapshot copy mismatch")
        adjudication_target = raw_root / "adjudication-export-001.csv"
        adjudication_target.write_bytes(adjudication_snapshot[2])
        if sha256_file(adjudication_target) != adjudication_snapshot[3]:
            raise FS09PhaseTruthError("M77 adjudication export snapshot copy mismatch")
        _write_csv(staging / "annotations.csv", ANNOTATION_FIELDS, annotations)
        _write_csv(staging / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)
        _write_json(staging / "intake-manifest.json", intake_manifest(staging))
        preview = _compile_fs09_phase_truth_pack(
            staging,
            allow_existing_intake_recompile=True,
        )
        if preview.get("status") != "ready_for_phase_evaluation":
            details = "; ".join(str(item) for item in preview.get("errors", []))
            raise FS09PhaseTruthError(f"M77 intake failed compilation: {details}")
        staging.replace(final)
        committed = True
        committed_handoff = validate_fs09_phase_blind_handoff(
            final / "blind-handoff",
            source_pack_dir=source_pack,
        )
        if committed_handoff["manifest_raw"] != handoff_snapshot["manifest_raw"]:
            raise FS09PhaseTruthError("M77 committed blind-handoff snapshot mismatch")
        committed_intake, _committed_intake_raw, _committed_intake_sha = _json_snapshot(
            final / "intake-manifest.json"
        )
        validate_fs09_phase_truth_intake_manifest(final, committed_intake)
        report, _report_raw, _report_sha = _json_snapshot(
            final / "compiled" / "validation-report.json"
        )
        _verify_compilation(final, report)
        if report.get("status") != "ready_for_phase_evaluation":
            details = "; ".join(str(item) for item in report.get("errors", []))
            raise FS09PhaseTruthError(f"M77 committed intake failed compilation: {details}")
        return report
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if committed and final.exists():
            shutil.rmtree(final)
        raise


def _verify_compilation(
    pack: Path, validation: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _require_exact_keys(
        validation,
        {
            "schema_version",
            "pack_version",
            "generated_at",
            "status",
            "counts",
            "errors",
            "readiness",
            "sources",
            "artifacts",
            "safety",
        },
        name="compiled M77 validation",
    )
    if (
        validation.get("schema_version") != "1.0.0"
        or validation.get("pack_version") != PACK_VERSION
    ):
        raise FS09PhaseTruthError("unsupported compiled M77 validation contract")
    _require_timestamp(str(validation.get("generated_at", "")), name="generated_at")
    intake_path = pack / "intake-manifest.json"
    expected_source_names = {
        "manifest",
        "analysis_plan",
        "tasks",
        "sealed_candidates",
        "annotations",
        "adjudications",
    }
    if intake_path.is_file():
        expected_source_names.update(
            {"intake_manifest", "blind_handoff_manifest"}
        )
    sources = _require_exact_keys(
        validation.get("sources"), expected_source_names, name="compiled M77 sources"
    )
    paths = {
        "manifest": pack / "manifest.json",
        "analysis_plan": pack / "analysis-plan.json",
        "tasks": pack / "tasks.jsonl",
        "sealed_candidates": pack / "sealed-event-candidates.jsonl",
        "annotations": pack / "annotations.csv",
        "adjudications": pack / "adjudications.csv",
    }
    relative_paths = {
        "manifest": "manifest.json",
        "analysis_plan": "analysis-plan.json",
        "tasks": "tasks.jsonl",
        "sealed_candidates": "sealed-event-candidates.jsonl",
        "annotations": "annotations.csv",
        "adjudications": "adjudications.csv",
    }
    snapshots: dict[str, tuple[bytes, str]] = {}
    intake_generated_at: str | None = None
    for name, path in paths.items():
        record = _require_exact_keys(
            sources.get(name), {"path", "sha256"}, name=f"compiled M77 {name} source"
        )
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest().upper()
        snapshots[name] = (raw, digest)
        if (
            _session_path(
                pack,
                str(record.get("path", "")),
                expected=relative_paths[name],
                name=f"compiled M77 {name} path",
            )
            != path.resolve()
            or digest
            != _require_sha256(
                record.get("sha256"), name=f"compiled M77 {name} SHA"
            )
        ):
            raise FS09PhaseTruthError(f"compiled M77 lineage mismatch: {name}")
    if intake_path.is_file():
        record = _require_exact_keys(
            sources["intake_manifest"],
            {"path", "sha256"},
            name="compiled M77 intake source",
        )
        intake, intake_raw, intake_digest = _json_snapshot(intake_path)
        if (
            _session_path(
                pack,
                str(record.get("path", "")),
                expected="intake-manifest.json",
                name="compiled M77 intake path",
            )
            != intake_path.resolve()
            or intake_digest
            != _require_sha256(
                record.get("sha256"), name="compiled M77 intake SHA"
            )
        ):
            raise FS09PhaseTruthError("compiled M77 intake lineage mismatch")
        validate_fs09_phase_truth_intake_manifest(pack, intake)
        intake_generated_at = str(intake["generated_at"])
        handoff_record = _require_exact_keys(
            sources["blind_handoff_manifest"],
            {"path", "sha256"},
            name="compiled M77 blind-handoff source",
        )
        if not _strict_json_equal(
            handoff_record, intake["blind_handoff"]["manifest"]
        ):
            raise FS09PhaseTruthError(
                "compiled M77 blind-handoff lineage mismatch"
            )
        if intake_path.read_bytes() != intake_raw:
            raise FS09PhaseTruthError("compiled M77 intake changed during verification")

    try:
        analysis_plan = json.loads(snapshots["analysis_plan"][0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("compiled M77 analysis plan is invalid JSON") from exc
    if not isinstance(analysis_plan, dict):
        raise FS09PhaseTruthError("compiled M77 analysis plan must be a JSON object")
    validate_fs09_phase_analysis_plan(analysis_plan)
    try:
        compiled_manifest = json.loads(snapshots["manifest"][0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("compiled M77 manifest is invalid JSON") from exc
    lower_bounds = [
        datetime.fromisoformat(str(analysis_plan["frozen_at"]).replace("Z", "+00:00")),
        datetime.fromisoformat(str(compiled_manifest["generated_at"]).replace("Z", "+00:00")),
    ]
    if intake_generated_at is not None:
        lower_bounds.append(
            datetime.fromisoformat(intake_generated_at.replace("Z", "+00:00"))
        )
    if datetime.fromisoformat(
        str(validation["generated_at"]).replace("Z", "+00:00")
    ) < max(lower_bounds):
        raise FS09PhaseTruthError(
            "compiled M77 validation predates its plan/manifest/intake sources"
        )

    try:
        tasks = [
            json.loads(line)
            for line in snapshots["tasks"][0].decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("compiled M77 tasks are invalid JSONL") from exc
    annotations_csv, annotation_raw, annotation_digest = _csv_snapshot(
        paths["annotations"], ANNOTATION_FIELDS
    )
    adjudications_csv, adjudication_raw, adjudication_digest = _csv_snapshot(
        paths["adjudications"], ADJUDICATION_FIELDS
    )
    if (
        annotation_raw != snapshots["annotations"][0]
        or annotation_digest != snapshots["annotations"][1]
        or adjudication_raw != snapshots["adjudications"][0]
        or adjudication_digest != snapshots["adjudications"][1]
    ):
        raise FS09PhaseTruthError("compiled M77 CSV changed during verification")

    errors, annotations, accepted, independent = _compile_fs09_phase_truth_rows(
        tasks,
        annotations_csv,
        adjudications_csv,
        frozen_at=str(analysis_plan["frozen_at"]),
    )
    if not intake_path.is_file() and (annotations_csv or adjudications_csv):
        errors.append(
            "non-empty M77 truth requires atomic export intake; direct pack editing is not accepted"
        )
    complete = (
        not errors
        and intake_path.is_file()
        and len(tasks) == TASK_COUNT
        and len(annotations) == TASK_COUNT * REQUIRED_ANNOTATORS
        and len(adjudications_csv) == TASK_COUNT
        and independent == TASK_COUNT
        and len(accepted) == TASK_COUNT
    )
    expected_status = (
        "invalid_annotations"
        if errors
        else "ready_for_phase_evaluation"
        if complete
        else "annotation_required"
    )
    expected_counts = {
        "tasks": len(tasks),
        "raw_annotations": len(annotations),
        "tasks_with_two_independent_annotators": independent,
        "accepted_adjudications": len(accepted),
        "compiled_manual_events": len(accepted),
    }
    expected_readiness = {
        "two_independent_annotators_per_task": independent == TASK_COUNT,
        "independent_adjudication_per_task": len(accepted) == TASK_COUNT,
        "full_task_coverage": complete,
        "runtime_event_or_feature_change_allowed": False,
    }
    expected_safety = {
        "ground_truth_complete": complete,
        "candidate_values_used_as_truth": False,
        "partial_annotations_change_runtime": False,
        "production_enabled": False,
        "grade_generated": False,
        "threshold_generated": False,
        "maturity_promoted": False,
    }
    if (
        validation.get("status") != expected_status
        or not _strict_json_equal(validation.get("errors"), errors)
        or not _strict_json_equal(validation.get("counts"), expected_counts)
        or not _strict_json_equal(validation.get("readiness"), expected_readiness)
        or not _strict_json_equal(validation.get("safety"), expected_safety)
    ):
        raise FS09PhaseTruthError("compiled M77 validation does not replay source CSVs")

    manual = pack / "compiled" / "manual-fs09-events.jsonl"
    artifacts = _require_exact_keys(
        validation.get("artifacts"), {"manual_events"}, name="compiled M77 artifacts"
    )
    record = _require_exact_keys(
        artifacts.get("manual_events"),
        {"path", "sha256"},
        name="compiled M77 manual artifact",
    )
    manual_rows, _manual_raw, manual_digest = _jsonl_snapshot(manual)
    expected_manual = [accepted[key] for key in sorted(accepted)]
    if (
        _session_path(
            pack,
            str(record.get("path", "")),
            expected="compiled/manual-fs09-events.jsonl",
            name="compiled M77 manual artifact path",
        )
        != manual.resolve()
        or manual_digest
        != _require_sha256(
            record.get("sha256"), name="compiled M77 manual-event SHA"
        )
        or _manual_raw != _canonical_jsonl_bytes(expected_manual)
    ):
        raise FS09PhaseTruthError("compiled M77 manual-event lineage mismatch")
    try:
        candidates = [
            json.loads(line)
            for line in snapshots["sealed_candidates"][0].decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("compiled M77 candidates are invalid JSONL") from exc
    return candidates, expected_manual


def _event_interval(candidate: dict[str, Any]) -> EventInterval:
    return EventInterval(
        event_id=str(candidate["event_id"]),
        event_code="FS09",
        start_ms=int(candidate["start_ms"]),
        end_ms=int(candidate["end_ms"]),
        person_track_id=int(candidate["person_track_id"]),
        key_phases=candidate["key_phases_ms"],
    )


def _aggregate_numeric(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = np.asarray([row[field] for row in rows if row.get(field) is not None], dtype=np.float64)
    return {
        "count": int(values.size),
        "mae": round(float(np.mean(np.abs(values))), 8) if values.size else None,
        "p95_absolute_error": round(float(np.quantile(np.abs(values), 0.95)), 8) if values.size else None,
        "bias_candidate_minus_truth": round(float(np.mean(values)), 8) if values.size else None,
    }


def _require_report_number_or_none(value: Any, *, name: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise FS09PhaseTruthError(f"{name} must be a finite number or null")


def _validate_numeric_aggregate(value: Any, *, name: str) -> None:
    aggregate = _require_exact_keys(
        value,
        {"count", "mae", "p95_absolute_error", "bias_candidate_minus_truth"},
        name=name,
    )
    if (
        not isinstance(aggregate["count"], int)
        or isinstance(aggregate["count"], bool)
        or not 0 <= aggregate["count"] <= TASK_COUNT
    ):
        raise FS09PhaseTruthError(f"{name} count is invalid")
    for field in ("mae", "p95_absolute_error", "bias_candidate_minus_truth"):
        _require_report_number_or_none(aggregate[field], name=f"{name} {field}")
    if aggregate["count"] == 0 and any(
        aggregate[field] is not None
        for field in ("mae", "p95_absolute_error", "bias_candidate_minus_truth")
    ):
        raise FS09PhaseTruthError(f"{name} empty aggregate must use null numerics")
    if aggregate["count"] > 0 and any(
        aggregate[field] is None
        for field in ("mae", "p95_absolute_error", "bias_candidate_minus_truth")
    ):
        raise FS09PhaseTruthError(f"{name} non-empty aggregate requires numerics")


def evaluate_fs09_phase_truth(pack_dir: str | Path) -> dict[str, Any]:
    pack = Path(pack_dir).resolve()
    manifest_path = pack / "manifest.json"
    manifest, manifest_raw, manifest_digest = _json_snapshot(manifest_path)
    intake_path = pack / "intake-manifest.json"
    intake_raw: bytes | None = None
    if intake_path.is_file():
        intake, intake_raw, _intake_digest = _json_snapshot(intake_path)
        validate_fs09_phase_truth_intake_manifest(pack, intake)
    else:
        _verify_pack_sources(pack, manifest)
    analysis_plan_path = pack / "analysis-plan.json"
    analysis_plan, analysis_plan_raw, analysis_plan_digest = _json_snapshot(
        analysis_plan_path
    )
    validate_fs09_phase_analysis_plan(analysis_plan)
    validation_path = pack / "compiled" / "validation-report.json"
    if not validation_path.is_file():
        raise FS09PhaseTruthError("compile M77 pack before evaluation")
    validation, validation_raw, validation_digest = _json_snapshot(validation_path)
    candidate_rows, manual_rows = _verify_compilation(pack, validation)

    def reverify_evaluation_sources() -> None:
        if (
            manifest_path.read_bytes() != manifest_raw
            or analysis_plan_path.read_bytes() != analysis_plan_raw
            or validation_path.read_bytes() != validation_raw
        ):
            raise FS09PhaseTruthError("M77 source changed during evaluation")
        if intake_raw is not None:
            if not intake_path.is_file() or intake_path.read_bytes() != intake_raw:
                raise FS09PhaseTruthError("M77 intake changed during evaluation")
            current_intake, current_intake_raw, _current_intake_digest = _json_snapshot(
                intake_path
            )
            validate_fs09_phase_truth_intake_manifest(pack, current_intake)
            if current_intake_raw != intake_raw:
                raise FS09PhaseTruthError("M77 intake changed during evaluation")
        else:
            if intake_path.exists():
                raise FS09PhaseTruthError("M77 intake appeared during evaluation")
            _verify_pack_sources(pack, manifest)
        replay_candidates, replay_manual = _verify_compilation(pack, validation)
        if not _strict_json_equal(replay_candidates, candidate_rows) or not _strict_json_equal(
            replay_manual, manual_rows
        ):
            raise FS09PhaseTruthError("M77 compiled sources changed during evaluation")
        if (
            manifest_path.read_bytes() != manifest_raw
            or analysis_plan_path.read_bytes() != analysis_plan_raw
            or validation_path.read_bytes() != validation_raw
            or (intake_raw is not None and intake_path.read_bytes() != intake_raw)
        ):
            raise FS09PhaseTruthError("M77 source changed during evaluation")

    if validation.get("errors"):
        raise FS09PhaseTruthError("M77 compiled truth contains errors")
    base = {
        "schema_version": "1.0.0",
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": _utc_now(),
        "status": "annotation_required",
        "scope": {
            "video_count": 1,
            "task_count": TASK_COUNT,
            "indicator_ids": ["FS09-M05"],
            "phase_keys": list(PHASE_KEYS),
            "required_features": list(REQUIRED_FEATURES),
            "target_unavailable_feature": TARGET_FEATURE,
        },
        "sources": {
            "manifest": {
                "path": str(manifest_path.resolve()),
                "sha256": manifest_digest,
            },
            "validation": {
                "path": str(validation_path.resolve()),
                "sha256": validation_digest,
            },
            "analysis_plan": {
                "path": str(analysis_plan_path.resolve()),
                "sha256": analysis_plan_digest,
            },
            "m74_report": manifest["sources"]["m74_report"],
            "per_video": manifest["sources"]["per_video"],
        },
        "counts": {
            "accepted_manual_events": 0,
            "manual_event_present": 0,
            "manual_event_absent": 0,
            "candidate_feature_records": 0,
            "manual_boundary_conditioned_feature_records": 0,
        },
        "event_metrics": None,
        "phase_metrics": None,
        "feature_boundary_conditioning": None,
        "details": [],
        "acceptance": {
            "status": "external_preregistered_protocol_required",
            "analysis_plan_verified": True,
            "thresholds": None,
            "runtime_event_change_allowed": False,
            "runtime_feature_change_allowed": False,
            "f2_to_f3_allowed": False,
        },
        "safety": {
            "full_manual_truth_provided": False,
            "candidate_boundaries_are_ground_truth": False,
            "manual_stable_control_is_ground_contact_or_force_truth": False,
            "feature_boundary_difference_is_total_feature_error": False,
            "production_enabled": False,
            "grade_generated": False,
            "threshold_generated": False,
            "maturity_promoted": False,
        },
    }
    if validation["status"] == "annotation_required":
        validate_fs09_phase_truth_report(base)
        reverify_evaluation_sources()
        return base
    if validation["status"] != "ready_for_phase_evaluation":
        raise FS09PhaseTruthError("M77 truth is not evaluation-ready")

    candidates = {row["task_id"]: row for row in candidate_rows}
    manual = {row["task_id"]: row for row in manual_rows}
    if set(candidates) != set(manual) or len(candidates) != TASK_COUNT:
        raise FS09PhaseTruthError("M77 evaluated task membership drifted")
    source = manifest["sources"]["per_video"][0]
    sequence = pose_sequence_from_records(_read_jsonl(source["frames"]["path"]), _read_jsonl(source["primary_timeline"]["path"]))
    details: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    phase_rows: list[dict[str, Any]] = []
    for task_id in sorted(candidates):
        candidate = candidates[task_id]
        truth = manual[task_id]
        candidate_event = _event_interval(candidate)
        try:
            candidate_features = {
                result.feature_name: result.to_dict()
                for result in compute_event_features(
                    sequence, candidate_event, list(REQUIRED_FEATURES)
                )
            }
            if (
                _snapshot(candidate_features[TARGET_FEATURE])
                != candidate["target_feature_before"]
            ):
                raise FS09PhaseTruthError(
                    "current feature code does not replay M74 target feature"
                )
        except Exception:
            clear_feature_cache(sequence)
            raise
        manual_features: dict[str, dict[str, Any]] | None = None
        if truth["event_present"]:
            manual_event = EventInterval(
                event_id=f"manual:{task_id}",
                event_code="FS09",
                start_ms=int(truth["start_ms"]),
                end_ms=int(truth["end_ms"]),
                person_track_id=int(candidate["person_track_id"]),
                key_phases=truth["key_phases_ms"],
            )
            try:
                manual_features = {
                    result.feature_name: result.to_dict()
                    for result in compute_event_features(
                        sequence, manual_event, list(REQUIRED_FEATURES)
                    )
                }
            except Exception:
                clear_feature_cache(sequence)
                raise
            for name in REQUIRED_FEATURES:
                left = _snapshot(candidate_features[name])
                right = _snapshot(manual_features[name])
                comparable = (
                    left["valid"]
                    and right["valid"]
                    and isinstance(left["value"], (int, float))
                    and isinstance(right["value"], (int, float))
                    and math.isfinite(float(left["value"]))
                    and math.isfinite(float(right["value"]))
                )
                signed = round(float(left["value"] - right["value"]), 8) if comparable else None
                feature_rows.append(
                    {
                        "task_id": task_id,
                        "feature_name": name,
                        "candidate": left,
                        "manual_boundary_conditioned": right,
                        "comparison_status": "comparable" if comparable else "unavailable",
                        "signed_difference_candidate_minus_manual_boundary": signed,
                        "absolute_difference": abs(signed) if signed is not None else None,
                        "semantics": "event_boundary_conditioning_not_pose_truth_or_feature_accuracy",
                    }
                )
        for key in PHASE_KEYS:
            candidate_value = candidate["key_phases_ms"].get(key)
            truth_value = truth["key_phases_ms"].get(key)
            status = truth["phase_status"][key]
            signed = (
                int(candidate_value) - int(truth_value)
                if status == "observed" and candidate_value is not None and truth_value is not None
                else None
            )
            phase_rows.append(
                {
                    "task_id": task_id,
                    "phase_key": key,
                    "truth_status": status,
                    "candidate_available": candidate_value is not None,
                    "truth_ms": truth_value,
                    "candidate_ms": candidate_value,
                    "signed_error_candidate_minus_truth_ms": signed,
                }
            )
        manual_delta = (
            int(truth["key_phases_ms"]["stable_control_onset_ms"])
            - int(truth["key_phases_ms"]["deceleration_peak_ms"])
            if truth["phase_status"]["stable_control_onset_ms"] == "observed"
            and truth["phase_status"]["deceleration_peak_ms"] == "observed"
            else None
        )
        candidate_phase_delta = int(candidate["key_phases_ms"]["stable_control_onset_ms"] - candidate["key_phases_ms"]["deceleration_peak_ms"])
        candidate_proxy = candidate_features[TARGET_FEATURE]["value"] if candidate_features[TARGET_FEATURE]["valid"] else None
        manual_proxy = (
            manual_features[TARGET_FEATURE]["value"]
            if manual_features is not None and manual_features[TARGET_FEATURE]["valid"]
            else None
        )
        details.append(
            {
                "task_id": task_id,
                "video_id": truth["video_id"],
                "candidate_event_id": candidate["event_id"],
                "manual_event_present": truth["event_present"],
                "candidate_event": {"start_ms": candidate["start_ms"], "end_ms": candidate["end_ms"]},
                "manual_event": {"start_ms": truth["start_ms"], "end_ms": truth["end_ms"], "reason": truth["event_reason"]},
                "manual_deceleration_to_stable_control_ms": manual_delta,
                "candidate_event_phase_deceleration_to_stable_control_ms": candidate_phase_delta,
                "candidate_low_ankle_motion_proxy_ms": candidate_proxy,
                "manual_boundary_conditioned_low_ankle_motion_proxy_ms": manual_proxy,
                "proxy_to_manual_stable_control_difference_ms": (
                    int(manual_proxy - manual_delta)
                    if manual_proxy is not None and manual_delta is not None
                    else None
                ),
                "proxy_comparison_semantics": (
                    "low_ankle_motion_pose_proxy_vs_visual_stable_control_reference;"
                    "diagnostic_disagreement_not_same-observable_feature_MAE_or_ground_contact"
                ),
                "source_annotation_ids": truth["source_annotation_ids"],
                "reviewer_id": truth["reviewer_id"],
            }
        )
    clear_feature_cache(sequence)

    present_details = [row for row in details if row["manual_event_present"]]
    event_signed: list[dict[str, int]] = []
    ious: list[float] = []
    for row in present_details:
        c = row["candidate_event"]
        t = row["manual_event"]
        intersection = max(0, min(c["end_ms"], t["end_ms"]) - max(c["start_ms"], t["start_ms"]))
        union = max(c["end_ms"], t["end_ms"]) - min(c["start_ms"], t["start_ms"])
        ious.append(intersection / union if union > 0 else 0.0)
        event_signed.append({"start": c["start_ms"] - t["start_ms"], "end": c["end_ms"] - t["end_ms"]})
    start_errors = np.asarray([row["start"] for row in event_signed], dtype=np.float64)
    end_errors = np.asarray([row["end"] for row in event_signed], dtype=np.float64)
    phase_by_key = {
        key: _aggregate_numeric([row for row in phase_rows if row["phase_key"] == key], "signed_error_candidate_minus_truth_ms")
        for key in PHASE_KEYS
    }
    feature_by_name = {
        name: _aggregate_numeric([row for row in feature_rows if row["feature_name"] == name], "signed_difference_candidate_minus_manual_boundary")
        for name in REQUIRED_FEATURES
    }
    base.update(
        {
            "status": "evaluated_external_acceptance_protocol_required",
            "counts": {
                "accepted_manual_events": len(manual),
                "manual_event_present": len(present_details),
                "manual_event_absent": len(details) - len(present_details),
                "candidate_feature_records": len(feature_rows),
                "manual_boundary_conditioned_feature_records": len(feature_rows),
            },
            "event_metrics": {
                "candidate_count": TASK_COUNT,
                "truth_present_count": len(present_details),
                "truth_absent_count": TASK_COUNT - len(present_details),
                "segment_iou_mean": round(float(np.mean(ious)), 8) if ious else None,
                "boundary_start_mae_ms": round(float(np.mean(np.abs(start_errors))), 8) if start_errors.size else None,
                "boundary_end_mae_ms": round(float(np.mean(np.abs(end_errors))), 8) if end_errors.size else None,
                "boundary_start_bias_candidate_minus_truth_ms": round(float(np.mean(start_errors)), 8) if start_errors.size else None,
                "boundary_end_bias_candidate_minus_truth_ms": round(float(np.mean(end_errors)), 8) if end_errors.size else None,
            },
            "phase_metrics": {
                "by_phase": phase_by_key,
                "details": phase_rows,
            },
            "feature_boundary_conditioning": {
                "semantics": "candidate-boundary_vs_manual-boundary_with_same_model_pose_not_total_feature_error",
                "by_feature": feature_by_name,
                "details": feature_rows,
                "candidate_target_feature_valid_count": sum(row["candidate_low_ankle_motion_proxy_ms"] is not None for row in details),
                "manual_boundary_conditioned_target_feature_valid_count": sum(row["manual_boundary_conditioned_low_ankle_motion_proxy_ms"] is not None for row in details),
            },
            "details": details,
            "safety": {**base["safety"], "full_manual_truth_provided": True},
        }
    )
    validate_fs09_phase_truth_report(base)
    reverify_evaluation_sources()
    return base


def validate_fs09_phase_truth_report(report: dict[str, Any]) -> None:
    _require_exact_keys(
        report,
        {
            "schema_version",
            "evaluator_version",
            "generated_at",
            "status",
            "scope",
            "sources",
            "counts",
            "event_metrics",
            "phase_metrics",
            "feature_boundary_conditioning",
            "details",
            "acceptance",
            "safety",
        },
        name="M77 evaluation report",
    )
    if report.get("schema_version") != "1.0.0" or report.get("evaluator_version") != EVALUATOR_VERSION:
        raise FS09PhaseTruthError("unsupported M77 report contract")
    _require_timestamp(str(report.get("generated_at", "")), name="generated_at")
    if report.get("status") not in {"annotation_required", "evaluated_external_acceptance_protocol_required"}:
        raise FS09PhaseTruthError("unsupported M77 report status")
    scope = _require_exact_keys(
        report.get("scope"),
        {
            "video_count",
            "task_count",
            "indicator_ids",
            "phase_keys",
            "required_features",
            "target_unavailable_feature",
        },
        name="M77 report scope",
    )
    if (
        scope.get("video_count") != 1
        or scope.get("task_count") != TASK_COUNT
        or scope.get("indicator_ids") != ["FS09-M05"]
        or scope.get("phase_keys") != list(PHASE_KEYS)
        or tuple(scope.get("required_features", [])) != REQUIRED_FEATURES
        or scope.get("target_unavailable_feature") != TARGET_FEATURE
    ):
        raise FS09PhaseTruthError("M77 report scope drifted")
    acceptance = _require_exact_keys(
        report.get("acceptance"),
        {
            "status",
            "analysis_plan_verified",
            "thresholds",
            "runtime_event_change_allowed",
            "runtime_feature_change_allowed",
            "f2_to_f3_allowed",
        },
        name="M77 report acceptance",
    )
    if acceptance != {
        "status": "external_preregistered_protocol_required",
        "analysis_plan_verified": True,
        "thresholds": None,
        "runtime_event_change_allowed": False,
        "runtime_feature_change_allowed": False,
        "f2_to_f3_allowed": False,
    }:
        raise FS09PhaseTruthError("M77 report cannot change runtime or maturity")
    safety = _require_exact_keys(
        report.get("safety"),
        {
            "full_manual_truth_provided",
            "candidate_boundaries_are_ground_truth",
            "manual_stable_control_is_ground_contact_or_force_truth",
            "feature_boundary_difference_is_total_feature_error",
            "production_enabled",
            "grade_generated",
            "threshold_generated",
            "maturity_promoted",
        },
        name="M77 report safety",
    )
    for name in (
        "candidate_boundaries_are_ground_truth",
        "manual_stable_control_is_ground_contact_or_force_truth",
        "feature_boundary_difference_is_total_feature_error",
        "production_enabled",
        "grade_generated",
        "threshold_generated",
        "maturity_promoted",
    ):
        if safety.get(name) is not False:
            raise FS09PhaseTruthError(f"unsafe M77 report field: {name}")
    sources = _require_exact_keys(
        report.get("sources"),
        {"manifest", "validation", "analysis_plan", "m74_report", "per_video"},
        name="M77 report sources",
    )
    if not isinstance(sources["per_video"], list) or len(sources["per_video"]) != 1:
        raise FS09PhaseTruthError("M77 report per-video source scope drifted")
    counts = _require_exact_keys(
        report.get("counts"),
        {
            "accepted_manual_events",
            "manual_event_present",
            "manual_event_absent",
            "candidate_feature_records",
            "manual_boundary_conditioned_feature_records",
        },
        name="M77 report counts",
    )
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts.values()):
        raise FS09PhaseTruthError("M77 report counts must be non-negative integers")
    if report["status"] == "annotation_required":
        if any(report.get(name) is not None for name in ("event_metrics", "phase_metrics", "feature_boundary_conditioning")) or report.get("details") != []:
            raise FS09PhaseTruthError("empty M77 report exposes metrics")
        if any(int(value) != 0 for value in report.get("counts", {}).values()):
            raise FS09PhaseTruthError("empty M77 report contains evaluated counts")
        if safety.get("full_manual_truth_provided") is not False:
            raise FS09PhaseTruthError("empty M77 report claims truth")
    else:
        if report.get("event_metrics") is None or report.get("phase_metrics") is None or report.get("feature_boundary_conditioning") is None:
            raise FS09PhaseTruthError("evaluated M77 report lacks metrics")
        if len(report.get("details", [])) != TASK_COUNT or safety.get("full_manual_truth_provided") is not True:
            raise FS09PhaseTruthError("evaluated M77 report lacks complete truth details")
        if (
            counts["accepted_manual_events"] != TASK_COUNT
            or counts["manual_event_present"] + counts["manual_event_absent"]
            != TASK_COUNT
            or counts["candidate_feature_records"]
            != counts["manual_event_present"] * len(REQUIRED_FEATURES)
            or counts["manual_boundary_conditioned_feature_records"]
            != counts["candidate_feature_records"]
        ):
            raise FS09PhaseTruthError("evaluated M77 report counts do not reconcile")
        task_ids = [str(item.get("task_id", "")) for item in report["details"]]
        if len(set(task_ids)) != TASK_COUNT or any(not value for value in task_ids):
            raise FS09PhaseTruthError("evaluated M77 report task membership drifted")
        manual_present_by_task: dict[str, bool] = {}
        for item in report["details"]:
            if not isinstance(item, dict) or not isinstance(
                item.get("manual_event_present"), bool
            ):
                raise FS09PhaseTruthError("evaluated M77 report task truth drifted")
            manual_present_by_task[str(item.get("task_id", ""))] = item[
                "manual_event_present"
            ]
        event_metrics = report["event_metrics"]
        event_metrics = _require_exact_keys(
            event_metrics,
            {
                "candidate_count",
                "truth_present_count",
                "truth_absent_count",
                "segment_iou_mean",
                "boundary_start_mae_ms",
                "boundary_end_mae_ms",
                "boundary_start_bias_candidate_minus_truth_ms",
                "boundary_end_bias_candidate_minus_truth_ms",
            },
            name="evaluated M77 event metrics",
        )
        if (
            event_metrics.get("candidate_count") != TASK_COUNT
            or event_metrics.get("truth_present_count")
            != counts["manual_event_present"]
            or event_metrics.get("truth_absent_count") != counts["manual_event_absent"]
        ):
            raise FS09PhaseTruthError("evaluated M77 event metrics do not reconcile")
        for field in (
            "segment_iou_mean",
            "boundary_start_mae_ms",
            "boundary_end_mae_ms",
            "boundary_start_bias_candidate_minus_truth_ms",
            "boundary_end_bias_candidate_minus_truth_ms",
        ):
            _require_report_number_or_none(
                event_metrics[field], name=f"evaluated M77 event {field}"
            )
        if counts["manual_event_present"] == 0 and any(
            event_metrics[field] is not None
            for field in (
                "segment_iou_mean",
                "boundary_start_mae_ms",
                "boundary_end_mae_ms",
                "boundary_start_bias_candidate_minus_truth_ms",
                "boundary_end_bias_candidate_minus_truth_ms",
            )
        ):
            raise FS09PhaseTruthError("empty M77 event population must use null metrics")
        if counts["manual_event_present"] > 0 and any(
            event_metrics[field] is None
            for field in (
                "segment_iou_mean",
                "boundary_start_mae_ms",
                "boundary_end_mae_ms",
                "boundary_start_bias_candidate_minus_truth_ms",
                "boundary_end_bias_candidate_minus_truth_ms",
            )
        ):
            raise FS09PhaseTruthError("non-empty M77 event population requires metrics")

        phase_metrics = _require_exact_keys(
            report["phase_metrics"], {"by_phase", "details"}, name="M77 phase metrics"
        )
        feature_metrics = _require_exact_keys(
            report["feature_boundary_conditioning"],
            {
                "semantics",
                "by_feature",
                "details",
                "candidate_target_feature_valid_count",
                "manual_boundary_conditioned_target_feature_valid_count",
            },
            name="M77 feature-boundary metrics",
        )
        if (
            set(phase_metrics.get("by_phase", {})) != set(PHASE_KEYS)
            or len(phase_metrics.get("details", [])) != TASK_COUNT * len(PHASE_KEYS)
            or set(feature_metrics.get("by_feature", {})) != set(REQUIRED_FEATURES)
            or len(feature_metrics.get("details", []))
            != counts["candidate_feature_records"]
        ):
            raise FS09PhaseTruthError("evaluated M77 phase/feature metrics do not reconcile")
        for key, aggregate in phase_metrics["by_phase"].items():
            _validate_numeric_aggregate(aggregate, name=f"M77 phase aggregate {key}")
        for index, row in enumerate(phase_metrics["details"], start=1):
            detail = _require_exact_keys(
                row,
                {
                    "task_id",
                    "phase_key",
                    "truth_status",
                    "candidate_available",
                    "truth_ms",
                    "candidate_ms",
                    "signed_error_candidate_minus_truth_ms",
                },
                name=f"M77 phase detail {index}",
            )
            if (
                detail["phase_key"] not in PHASE_KEYS
                or detail["truth_status"] not in PHASE_STATUS
                or not isinstance(detail["candidate_available"], bool)
            ):
                raise FS09PhaseTruthError("evaluated M77 phase detail semantics drifted")
            for field in (
                "truth_ms",
                "candidate_ms",
                "signed_error_candidate_minus_truth_ms",
            ):
                _require_report_number_or_none(
                    detail[field], name=f"M77 phase detail {field}"
                )
            expected_signed = (
                int(detail["candidate_ms"]) - int(detail["truth_ms"])
                if detail["truth_status"] == "observed"
                and detail["candidate_available"]
                and detail["truth_ms"] is not None
                and detail["candidate_ms"] is not None
                else None
            )
            if (
                (detail["truth_status"] == "observed")
                != (detail["truth_ms"] is not None)
                or detail["candidate_available"]
                != (detail["candidate_ms"] is not None)
                or detail["signed_error_candidate_minus_truth_ms"]
                != expected_signed
                or (
                    not manual_present_by_task.get(str(detail["task_id"]), False)
                    and detail["truth_status"] == "observed"
                )
            ):
                raise FS09PhaseTruthError("evaluated M77 phase missingness drifted")
        for key in PHASE_KEYS:
            expected = _aggregate_numeric(
                [
                    row
                    for row in phase_metrics["details"]
                    if row["phase_key"] == key
                ],
                "signed_error_candidate_minus_truth_ms",
            )
            if not _strict_json_equal(phase_metrics["by_phase"][key], expected):
                raise FS09PhaseTruthError("evaluated M77 phase aggregate drifted")
        expected_phase_membership = {
            (task_id, key) for task_id in task_ids for key in PHASE_KEYS
        }
        if {
            (str(row["task_id"]), str(row["phase_key"]))
            for row in phase_metrics["details"]
        } != expected_phase_membership:
            raise FS09PhaseTruthError("evaluated M77 phase detail membership drifted")

        if (
            feature_metrics["semantics"]
            != "candidate-boundary_vs_manual-boundary_with_same_model_pose_not_total_feature_error"
            or not all(
                isinstance(feature_metrics[name], int)
                and not isinstance(feature_metrics[name], bool)
                and 0 <= feature_metrics[name] <= TASK_COUNT
                for name in (
                    "candidate_target_feature_valid_count",
                    "manual_boundary_conditioned_target_feature_valid_count",
                )
            )
        ):
            raise FS09PhaseTruthError("evaluated M77 feature semantics drifted")
        for name, aggregate in feature_metrics["by_feature"].items():
            _validate_numeric_aggregate(
                aggregate, name=f"M77 feature aggregate {name}"
            )
        snapshot_fields = {
            "feature_name",
            "feature_version",
            "value",
            "unit",
            "confidence",
            "valid",
            "reason",
            "source_frames",
        }
        for index, row in enumerate(feature_metrics["details"], start=1):
            detail = _require_exact_keys(
                row,
                {
                    "task_id",
                    "feature_name",
                    "candidate",
                    "manual_boundary_conditioned",
                    "comparison_status",
                    "signed_difference_candidate_minus_manual_boundary",
                    "absolute_difference",
                    "semantics",
                },
                name=f"M77 feature detail {index}",
            )
            if (
                detail["feature_name"] not in REQUIRED_FEATURES
                or detail["comparison_status"] not in {"comparable", "unavailable"}
                or detail["semantics"]
                != "event_boundary_conditioning_not_pose_truth_or_feature_accuracy"
            ):
                raise FS09PhaseTruthError("evaluated M77 feature detail semantics drifted")
            for side in ("candidate", "manual_boundary_conditioned"):
                snapshot = _require_exact_keys(
                    detail[side], snapshot_fields, name=f"M77 feature detail {side}"
                )
                if (
                    snapshot["feature_name"] != detail["feature_name"]
                    or not isinstance(snapshot["valid"], bool)
                    or not isinstance(snapshot["source_frames"], list)
                    or any(
                        not isinstance(value, int) or isinstance(value, bool)
                        for value in snapshot["source_frames"]
                    )
                ):
                    raise FS09PhaseTruthError("evaluated M77 feature snapshot drifted")
                _require_report_number_or_none(
                    snapshot["value"], name="M77 feature snapshot value"
                )
                _require_report_number_or_none(
                    snapshot["confidence"], name="M77 feature snapshot confidence"
                )
            for field in (
                "signed_difference_candidate_minus_manual_boundary",
                "absolute_difference",
            ):
                _require_report_number_or_none(
                    detail[field], name=f"M77 feature detail {field}"
                )
            signed = detail["signed_difference_candidate_minus_manual_boundary"]
            expected_feature_signed = (
                round(
                    float(detail["candidate"]["value"])
                    - float(detail["manual_boundary_conditioned"]["value"]),
                    8,
                )
                if detail["comparison_status"] == "comparable"
                and detail["candidate"]["value"] is not None
                and detail["manual_boundary_conditioned"]["value"] is not None
                else None
            )
            if (
                detail["comparison_status"] == "comparable"
                and (
                    signed is None
                    or signed != expected_feature_signed
                    or detail["absolute_difference"] != abs(signed)
                    or detail["candidate"]["valid"] is not True
                    or detail["manual_boundary_conditioned"]["valid"] is not True
                    or detail["candidate"]["value"] is None
                    or detail["manual_boundary_conditioned"]["value"] is None
                )
            ) or (
                detail["comparison_status"] == "unavailable"
                and (
                    signed is not None or detail["absolute_difference"] is not None
                )
            ):
                raise FS09PhaseTruthError("evaluated M77 feature missingness drifted")
        for name in REQUIRED_FEATURES:
            expected = _aggregate_numeric(
                [
                    row
                    for row in feature_metrics["details"]
                    if row["feature_name"] == name
                ],
                "signed_difference_candidate_minus_manual_boundary",
            )
            if not _strict_json_equal(feature_metrics["by_feature"][name], expected):
                raise FS09PhaseTruthError("evaluated M77 feature aggregate drifted")
        present_task_ids = {
            str(row["task_id"])
            for row in report["details"]
            if row["manual_event_present"]
        }
        expected_feature_membership = {
            (task_id, name)
            for task_id in present_task_ids
            for name in REQUIRED_FEATURES
        }
        if {
            (str(row["task_id"]), str(row["feature_name"]))
            for row in feature_metrics["details"]
        } != expected_feature_membership:
            raise FS09PhaseTruthError("evaluated M77 feature detail membership drifted")

        expected_detail_fields = {
            "task_id",
            "video_id",
            "candidate_event_id",
            "manual_event_present",
            "candidate_event",
            "manual_event",
            "manual_deceleration_to_stable_control_ms",
            "candidate_event_phase_deceleration_to_stable_control_ms",
            "candidate_low_ankle_motion_proxy_ms",
            "manual_boundary_conditioned_low_ankle_motion_proxy_ms",
            "proxy_to_manual_stable_control_difference_ms",
            "proxy_comparison_semantics",
            "source_annotation_ids",
            "reviewer_id",
        }
        for index, row in enumerate(report["details"], start=1):
            detail = _require_exact_keys(
                row, expected_detail_fields, name=f"M77 evaluation detail {index}"
            )
            if (
                not isinstance(detail["manual_event_present"], bool)
                or detail["proxy_comparison_semantics"]
                != (
                    "low_ankle_motion_pose_proxy_vs_visual_stable_control_reference;"
                    "diagnostic_disagreement_not_same-observable_feature_MAE_or_ground_contact"
                )
                or not isinstance(detail["source_annotation_ids"], list)
                or len(detail["source_annotation_ids"]) != REQUIRED_ANNOTATORS
            ):
                raise FS09PhaseTruthError("evaluated M77 detail semantics drifted")
            candidate_event = _require_exact_keys(
                detail["candidate_event"], {"start_ms", "end_ms"}, name="candidate event"
            )
            manual_event = _require_exact_keys(
                detail["manual_event"], {"start_ms", "end_ms", "reason"}, name="manual event"
            )
            if (
                not all(
                    isinstance(candidate_event[field], int)
                    and not isinstance(candidate_event[field], bool)
                    for field in ("start_ms", "end_ms")
                )
                or candidate_event["start_ms"] >= candidate_event["end_ms"]
                or not isinstance(manual_event["reason"], str)
            ):
                raise FS09PhaseTruthError("evaluated M77 event detail drifted")
            if detail["manual_event_present"]:
                if (
                    not all(
                        isinstance(manual_event[field], int)
                        and not isinstance(manual_event[field], bool)
                        for field in ("start_ms", "end_ms")
                    )
                    or manual_event["start_ms"] >= manual_event["end_ms"]
                ):
                    raise FS09PhaseTruthError("evaluated M77 manual event drifted")
            elif manual_event["start_ms"] is not None or manual_event["end_ms"] is not None:
                raise FS09PhaseTruthError("absent M77 manual event must use null boundaries")
            for field in (
                "manual_deceleration_to_stable_control_ms",
                "candidate_event_phase_deceleration_to_stable_control_ms",
                "candidate_low_ankle_motion_proxy_ms",
                "manual_boundary_conditioned_low_ankle_motion_proxy_ms",
                "proxy_to_manual_stable_control_difference_ms",
            ):
                _require_report_number_or_none(
                    detail[field], name=f"M77 evaluation detail {field}"
                )
        present_details = [
            row for row in report["details"] if row["manual_event_present"]
        ]
        target_feature_by_task = {
            str(row["task_id"]): row
            for row in feature_metrics["details"]
            if row["feature_name"] == TARGET_FEATURE
        }
        for row in report["details"]:
            target = target_feature_by_task.get(str(row["task_id"]))
            expected_candidate_proxy = (
                target["candidate"]["value"]
                if target is not None and target["candidate"]["valid"]
                else None
            )
            expected_manual_proxy = (
                target["manual_boundary_conditioned"]["value"]
                if target is not None
                and target["manual_boundary_conditioned"]["valid"]
                else None
            )
            expected_proxy_difference = (
                int(
                    expected_manual_proxy
                    - row["manual_deceleration_to_stable_control_ms"]
                )
                if expected_manual_proxy is not None
                and row["manual_deceleration_to_stable_control_ms"] is not None
                else None
            )
            if (
                row["candidate_low_ankle_motion_proxy_ms"]
                != expected_candidate_proxy
                or row["manual_boundary_conditioned_low_ankle_motion_proxy_ms"]
                != expected_manual_proxy
                or row["proxy_to_manual_stable_control_difference_ms"]
                != expected_proxy_difference
            ):
                raise FS09PhaseTruthError("evaluated M77 target proxy trace drifted")
        if (
            feature_metrics["candidate_target_feature_valid_count"]
            != sum(
                row["candidate_low_ankle_motion_proxy_ms"] is not None
                for row in report["details"]
            )
            or feature_metrics[
                "manual_boundary_conditioned_target_feature_valid_count"
            ]
            != sum(
                row["manual_boundary_conditioned_low_ankle_motion_proxy_ms"]
                is not None
                for row in report["details"]
            )
        ):
            raise FS09PhaseTruthError("evaluated M77 target proxy counts drifted")
        start_errors = np.asarray(
            [
                row["candidate_event"]["start_ms"]
                - row["manual_event"]["start_ms"]
                for row in present_details
            ],
            dtype=np.float64,
        )
        end_errors = np.asarray(
            [
                row["candidate_event"]["end_ms"]
                - row["manual_event"]["end_ms"]
                for row in present_details
            ],
            dtype=np.float64,
        )
        ious: list[float] = []
        for row in present_details:
            candidate_event = row["candidate_event"]
            manual_event = row["manual_event"]
            intersection = max(
                0,
                min(candidate_event["end_ms"], manual_event["end_ms"])
                - max(candidate_event["start_ms"], manual_event["start_ms"]),
            )
            union = max(candidate_event["end_ms"], manual_event["end_ms"]) - min(
                candidate_event["start_ms"], manual_event["start_ms"]
            )
            ious.append(intersection / union if union > 0 else 0.0)
        expected_event_metrics = {
            "candidate_count": TASK_COUNT,
            "truth_present_count": len(present_details),
            "truth_absent_count": TASK_COUNT - len(present_details),
            "segment_iou_mean": round(float(np.mean(ious)), 8) if ious else None,
            "boundary_start_mae_ms": round(float(np.mean(np.abs(start_errors))), 8)
            if start_errors.size
            else None,
            "boundary_end_mae_ms": round(float(np.mean(np.abs(end_errors))), 8)
            if end_errors.size
            else None,
            "boundary_start_bias_candidate_minus_truth_ms": round(
                float(np.mean(start_errors)), 8
            )
            if start_errors.size
            else None,
            "boundary_end_bias_candidate_minus_truth_ms": round(
                float(np.mean(end_errors)), 8
            )
            if end_errors.size
            else None,
        }
        if not _strict_json_equal(event_metrics, expected_event_metrics):
            raise FS09PhaseTruthError("evaluated M77 event metrics drifted from details")


def validate_fs09_phase_truth_report_sources(report: dict[str, Any]) -> None:
    validate_fs09_phase_truth_report(report)
    records = [
        report.get("sources", {}).get(name)
        for name in ("manifest", "validation", "analysis_plan", "m74_report")
    ]
    records.extend(
        item.get(name)
        for item in report.get("sources", {}).get("per_video", [])
        for name in ("video", "m73_report", "frames", "primary_timeline", "events", "review_clip_manifest")
    )
    records.extend(
        clip.get("clip")
        for item in report.get("sources", {}).get("per_video", [])
        for clip in item.get("review_clips", [])
    )
    if any(not isinstance(record, dict) for record in records):
        raise FS09PhaseTruthError("M77 report source bindings are incomplete")
    source_raw: list[bytes] = []
    for index, record in enumerate(records, start=1):
        _require_exact_keys(
            record, {"path", "sha256"}, name=f"M77 report source {index}"
        )
        path = Path(str(record.get("path", ""))).resolve()
        expected_sha = _require_sha256(
            record.get("sha256"), name=f"M77 report source {index} SHA"
        )
        if not path.is_file():
            raise FS09PhaseTruthError(f"M77 report source SHA mismatch: {path}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest().upper() != expected_sha:
            raise FS09PhaseTruthError(f"M77 report source SHA mismatch: {path}")
        source_raw.append(raw)
    try:
        source_manifest = json.loads(source_raw[0].decode("utf-8"))
        source_validation = json.loads(source_raw[1].decode("utf-8"))
        source_plan = json.loads(source_raw[2].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FS09PhaseTruthError("M77 report chronology sources are invalid JSON") from exc
    report_time = datetime.fromisoformat(
        str(report["generated_at"]).replace("Z", "+00:00")
    )
    lower_bound = max(
        datetime.fromisoformat(str(source_plan["frozen_at"]).replace("Z", "+00:00")),
        datetime.fromisoformat(str(source_manifest["generated_at"]).replace("Z", "+00:00")),
        datetime.fromisoformat(str(source_validation["generated_at"]).replace("Z", "+00:00")),
    )
    if report_time < lower_bound:
        raise FS09PhaseTruthError(
            "M77 evaluation predates its plan/manifest/validation sources"
        )
    pack = Path(report["sources"]["manifest"]["path"]).resolve().parent
    replay = evaluate_fs09_phase_truth(pack)
    for name in (
        "status",
        "scope",
        "sources",
        "counts",
        "event_metrics",
        "phase_metrics",
        "feature_boundary_conditioning",
        "details",
        "acceptance",
        "safety",
    ):
        if replay.get(name) != report.get(name):
            raise FS09PhaseTruthError(f"M77 source replay mismatch: {name}")
