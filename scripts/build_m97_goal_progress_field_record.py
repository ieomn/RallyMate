#!/usr/bin/env python3
"""Build or validate the immutable M97 goal-progress evidence bundle.

The builder intentionally uses only Python's standard library.  It never opens,
stats, or hashes the sealed holdout video.  A normal invocation creates exactly
``summary.md`` and ``field-change-record.json`` in a new output directory; an
existing destination is an error.  ``--validate-existing`` is read-only and
recomputes every recorded artifact byte count and SHA-256.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "1.0.0"
RECORD_VERSION = "m97-goal-progress-field-change-record-v1.0.0"
DEFAULT_OUTPUT_DIRECTORY = Path("reports/m97-goal-progress")

SEALED_HOLDOUT_ID = "c235227fffcd3290b60572d0c3f9cc85"
SEALED_HOLDOUT_RELATIVE_PATH = f"FULL-TEST/{SEALED_HOLDOUT_ID}.mp4"
SEALED_HOLDOUT_DECLARED_SHA256 = (
    "41AE2B5C12A92E8883B159F0963741451D42A515BB82F11194DEC1F4FB1C05D6"
)

PRODUCTION_REGISTRY_PATH = "models/rtmpose/deployment-presets.json"
PRODUCTION_REGISTRY_SHA256 = (
    "A95B7B5F225C025371157C22A9A60397874DE03C85B4EAC512B3B9C443F14C69"
)
PRODUCTION_DEFAULT_PRESET = "rtmpose-m-halpe26-online"

IMMUTABLE_FIELD_RECORDS: tuple[tuple[str, str, str], ...] = (
    (
        "m95_model_candidate_finetune_readiness",
        "reports/m95-model-candidate-finetune-readiness/field-change-record.json",
        "EC9CF606069422E53234F43647350C0E1C16106195741CBDA7B15D0078FFD938",
    ),
    (
        "m96_same_frame_diagnostic",
        "reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json",
        "EBAA52D305B963719093BE2663240C11F32146D1AEDA347E3D2977A0225ED1DD",
    ),
    (
        "m96_user_demo_v1_1",
        "reports/m96-user-demo-v1.1/field-change-record.json",
        "6D2F8763DEEB595EEB3DA9EC7D455DF093878B026A3BB27BE18883A0C689B731",
    ),
    (
        "m96_pose_pilot",
        "reports/m96-pose-pilot/field-change-record.json",
        "877851936E18B03A8088AD0E4DDE9BA6B5C0D11B516FAF0893D586EBDB6DD465",
    ),
    (
        "m97_operational_residual",
        "reports/measurement-recovery-m97/field-change-record.json",
        "87647B58D804CBF280C7C293CF20F3A5EA1126EC29AB9CBF6C89A63DE802C2BA",
    ),
)

DOCUMENT_SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "product",
        "docs/RallyMate产品说明书_当前态_v1.0.md",
        "1.5.5",
    ),
    (
        "technical",
        "docs/RallyMate技术架构与实现说明书_当前态_v1.0.md",
        "1.5.6",
    ),
    (
        "api",
        "docs/RallyMate接口与端到端链路_当前态_v1.0.md",
        "1.0.6",
    ),
    (
        "m96_same_frame",
        "docs/RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md",
        "special_document",
    ),
    (
        "m97_operational",
        "docs/RTMPOSE_X_OPERATIONAL_COMPARISON_M97.md",
        "special_document",
    ),
)

EXPECTED_DRAWIO_PAGES = (
    "01 用户上传到结果",
    "02 服务 API 与作业状态",
    "03 推理测量与评分门禁",
    "04 人工标注审核与发布",
    "05 模型候选训练与验证",
)

EXPECTED_HTTP_ROUTES = (
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/"),
    ("GET", "/v1/model-capabilities"),
    ("GET", "/v1/jobs"),
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs/{job_id}"),
    ("GET", "/v1/jobs/{job_id}/demo-result"),
    ("GET", "/v1/jobs/{job_id}/artifacts/{artifact_name}"),
)

EXPECTED_ARTIFACT_WHITELIST = frozenset(
    {
        "summary.json",
        "frames.jsonl",
        "annotated.mp4",
        "preview.jpg",
        "scoring-readiness.json",
        "analysis-report.html",
        "primary-player.jsonl",
        "primary-player-summary.json",
        "events.jsonl",
        "features.jsonl",
        "indicator-features.jsonl",
        "scores.jsonl",
        "event-feature-errors.json",
        "scoring-loop-summary.json",
        "scoring-loop-report.html",
        "calculation-readiness.json",
        "indicator-measurement-portfolio.json",
        "scoring-cycle-measurement.json",
    }
)

EXPECTED_CURRENT_DOC_HTML_FILES = (
    "api.html",
    "index.html",
    "m96-model-diagnostic.html",
    "m97-model-comparison.html",
    "product.html",
    "technical.html",
)

EXPECTED_PILOT_SOURCE_FILE_COUNT = 23

# The generated record carries hashes for these files, all 23 Pilot source-snapshot
# files, the exact current rendered HTML roster, and every existing current-docs
# evidence copy. Large checkpoints and videos are deliberately omitted; the five
# immutable source records retain their provenance.
BASE_ARTIFACT_SPECS: tuple[tuple[str, str], ...] = (
    ("src/rallymate_service/user_demo.py", "Demo v1.1 result implementation"),
    ("src/rallymate_service/api.py", "HTTP service routes and artifact allowlist"),
    ("src/rallymate_service/assets/user-demo.html", "Demo v1.1 page"),
    ("src/rallymate_service/assets/user-demo.js", "Demo v1.1 batch and recovery UI"),
    ("src/rallymate_service/assets/user-demo.css", "Demo v1.1 styles"),
    (
        "src/rallymate_evaluation/pose_same_frame_diagnostic.py",
        "M96 same-frame diagnostic implementation",
    ),
    (
        "src/rallymate_evaluation/pose_x_operational.py",
        "M97 operational residual implementation",
    ),
    ("src/rallymate_evaluation/m96_pose_pilot.py", "M96 evaluation-only pilot"),
    (
        "src/rallymate_training/pose_finetune_readiness.py",
        "fine-tune readiness fail-closed implementation",
    ),
    ("scripts/build_m95_field_change_record.py", "M95 field record builder"),
    (
        "scripts/run_m96_rtmpose_same_frame_diagnostic.py",
        "M96 same-frame diagnostic runner",
    ),
    (
        "scripts/build_m96_same_frame_field_record.py",
        "M96 same-frame field record builder",
    ),
    (
        "scripts/build_m96_user_demo_field_record.py",
        "Demo v1.1 field record builder",
    ),
    ("scripts/build_m96_pose_pilot.py", "M96 pilot builder"),
    ("scripts/build_m96_pose_pilot_audit.py", "M96 pilot audit builder"),
    (
        "scripts/run_m97_x_pose_operational_extension.py",
        "M97 operational runner",
    ),
    (
        "scripts/build_m97_x_pose_operational_summary.py",
        "M97 operational summary builder",
    ),
    (
        "scripts/build_m97_x_operational_field_record.py",
        "M97 operational field record builder",
    ),
    (
        "scripts/build_m97_goal_progress_field_record.py",
        "M97 goal-progress refuse-overwrite builder",
    ),
    (
        "scripts/build_current_product_technical_docs.ps1",
        "five-document HTML build script",
    ),
    ("scripts/serve_current_docs.ps1", "allowlisted current-document launcher"),
    ("scripts/range_http_server.py", "allowlisted local HTTP server"),
    ("tests/test_pose_finetune_readiness.py", "M95 readiness tests"),
    ("tests/test_pose_same_frame_diagnostic.py", "M96 same-frame tests"),
    ("tests/test_user_demo.py", "Demo v1.1 unit and browser-contract tests"),
    ("tests/test_service.py", "Demo v1.1 HTTP integration tests"),
    ("tests/test_m96_pose_pilot.py", "M96 pilot tests"),
    ("tests/test_range_http_server.py", "allowlisted range-server tests"),
    ("tests/test_m97_x_pose_operational.py", "M97 operational tests"),
    (
        "tests/test_m97_x_pose_operational_summary.py",
        "M97 summary and field-record tests",
    ),
    (
        "reports/m95-model-candidate-finetune-readiness/field-change-record.json",
        "immutable M95 field record",
    ),
    (
        "reports/m95-pose-finetune-readiness/readiness.json",
        "M95 fine-tune readiness report",
    ),
    (
        "reports/m96-rtmpose-same-frame-diagnostic/field-change-record.json",
        "immutable M96 same-frame field record",
    ),
    (
        "reports/m96-rtmpose-same-frame-diagnostic/diagnostic-report.json",
        "M96 raw same-frame diagnostic report",
    ),
    (
        "reports/m96-rtmpose-same-frame-diagnostic/summary.md",
        "M96 same-frame human summary",
    ),
    (
        "reports/m96-user-demo-v1.1/field-change-record.json",
        "immutable Demo v1.1 field record",
    ),
    ("reports/m96-user-demo-v1.1/demo-contract.json", "Demo v1.1 contract"),
    ("reports/m96-user-demo-v1.1/summary.md", "Demo v1.1 implementation summary"),
    (
        "reports/m96-pose-pilot/field-change-record.json",
        "immutable M96 pilot field record",
    ),
    ("reports/m96-pose-pilot/pilot-contract.json", "M96 pilot audit contract"),
    ("reports/m96-pose-pilot/summary.md", "M96 pilot human summary"),
    (
        "data/annotations/m96-halpe26-development-pilot-v1/validation-report.json",
        "M96 blank-pilot validation",
    ),
    (
        "data/annotations/m96-halpe26-development-pilot-v1/tasks.jsonl",
        "M96 evaluation-only task list with no holdout",
    ),
    (
        "reports/measurement-recovery-m97/field-change-record.json",
        "immutable M97 operational field record",
    ),
    (
        "reports/measurement-recovery-m97/summary/report.json",
        "M97 aggregate operational report",
    ),
    (
        "reports/measurement-recovery-m97/summary/summary.md",
        "M97 human-readable operational summary",
    ),
    ("models/rtmpose/deployment-presets.json", "unchanged production registry"),
    ("models/rtmpose/m95-shadow-candidates.json", "M95 candidate registry"),
    (
        "models/rtmpose/m96-same-frame-diagnostic.json",
        "M96 immutable same-frame protocol",
    ),
    (
        "models/rtmpose/m97-x-operational-protocol.json",
        "M97 immutable operational protocol",
    ),
    ("docs/RallyMate产品说明书_当前态_v1.0.md", "product document v1.5.5"),
    (
        "docs/RallyMate技术架构与实现说明书_当前态_v1.0.md",
        "technical document v1.5.6",
    ),
    (
        "docs/RallyMate接口与端到端链路_当前态_v1.0.md",
        "API and end-to-end document v1.0.6",
    ),
    (
        "docs/RTMPOSE_MLX_SAME_FRAME_DIAGNOSTIC_M96.md",
        "M96 detailed diagnostic document",
    ),
    (
        "docs/RTMPOSE_X_OPERATIONAL_COMPARISON_M97.md",
        "M97 detailed comparison document",
    ),
    (
        "docs/RallyMate当前项目文档导航.md",
        "current-project document navigation",
    ),
    (
        "docs/RallyMate推理评分系统技术设计与维护手册_v1.0.md",
        "scoring-system technical design and maintenance manual",
    ),
    ("README.md", "repository navigation"),
    (
        "docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio",
        "editable five-page Draw.io source",
    ),
    (
        "reports/rallymate-current-docs/assets/docs.css",
        "current-document HTML stylesheet",
    ),
    (
        "reports/rallymate-current-docs/RallyMate完整系统架构_当前态_v1.0.drawio",
        "current-document Draw.io copy",
    ),
)


class GoalProgressRecordError(ValueError):
    """Raised when evidence is missing, inconsistent, or would be overwritten."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise GoalProgressRecordError(message)


def _inside(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise GoalProgressRecordError(f"{label} must stay inside the workspace")
    return resolved


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _reject_holdout_path(relative_path: str, label: str) -> None:
    normalized = relative_path.replace("\\", "/").casefold()
    if SEALED_HOLDOUT_ID.casefold() in normalized:
        raise GoalProgressRecordError(
            f"{label} must not open, stat, or hash the sealed holdout: {relative_path}"
        )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _sha256_file(root: Path, path: Path, label: str) -> str:
    relative = _relative_path(root, path)
    _reject_holdout_path(relative, label)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _load_json(root: Path, value: str | Path, label: str) -> dict[str, Any]:
    path = _inside(root, value, label)
    relative = _relative_path(root, path)
    _reject_holdout_path(relative, label)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoalProgressRecordError(f"{label} is not valid readable JSON: {path}") from exc
    if not isinstance(parsed, dict):
        raise GoalProgressRecordError(f"{label} must contain a JSON object")
    return parsed


def _required(mapping: Mapping[str, Any], *keys: str, label: str) -> Any:
    value: Any = mapping
    walked: list[str] = []
    for key in keys:
        walked.append(key)
        if not isinstance(value, Mapping) or key not in value:
            raise GoalProgressRecordError(
                f"{label} is missing required field: {'.'.join(walked)}"
            )
        value = value[key]
    return value


def _expect_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise GoalProgressRecordError(
            f"{label} drifted: expected {expected!r}, observed {actual!r}"
        )


def _artifact(root: Path, relative_path: str, role: str) -> dict[str, Any]:
    _reject_holdout_path(relative_path, "artifact inventory")
    path = _inside(root, relative_path, role)
    if not path.is_file():
        raise GoalProgressRecordError(f"artifact is missing: {relative_path}")
    return {
        "relative_path": _relative_path(root, path),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(root, path, role),
    }


def _validate_pilot_source_snapshot(
    root: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    source_snapshot = _required(
        contract, "source_snapshot", label="M96 pilot contract"
    )
    _expect(
        isinstance(source_snapshot, Mapping),
        "M96 pilot source snapshot must be an object",
    )
    _expect_equal(
        source_snapshot.get("file_count"),
        EXPECTED_PILOT_SOURCE_FILE_COUNT,
        "M96 pilot source snapshot file_count",
    )
    source_files = source_snapshot.get("files")
    _expect(isinstance(source_files, list), "M96 pilot source snapshot files must be a list")
    _expect_equal(
        len(source_files),
        EXPECTED_PILOT_SOURCE_FILE_COUNT,
        "M96 pilot source snapshot file roster length",
    )
    inventory_contract_sha256 = source_snapshot.get("inventory_contract_sha256")
    _expect(
        isinstance(inventory_contract_sha256, str)
        and re.fullmatch(r"[0-9A-F]{64}", inventory_contract_sha256) is not None,
        "M96 pilot source snapshot inventory_contract_sha256 must be uppercase SHA-256",
    )

    seen: set[str] = set()
    validated_files: list[dict[str, Any]] = []
    for index, item in enumerate(source_files):
        label = f"M96 pilot source snapshot file[{index}]"
        _expect(isinstance(item, Mapping), f"{label} must be an object")
        relative_path = item.get("path")
        category = item.get("category")
        expected_bytes = item.get("bytes")
        expected_sha256 = item.get("raw_sha256")
        _expect(
            isinstance(relative_path, str) and bool(relative_path),
            f"{label}.path must be non-empty text",
        )
        _expect(
            isinstance(category, str) and bool(category),
            f"{label}.category must be non-empty text",
        )
        _expect(
            isinstance(expected_bytes, int)
            and not isinstance(expected_bytes, bool)
            and expected_bytes >= 0,
            f"{label}.bytes must be a non-negative integer",
        )
        _expect(
            isinstance(expected_sha256, str)
            and re.fullmatch(r"[0-9A-F]{64}", expected_sha256) is not None,
            f"{label}.raw_sha256 must be uppercase SHA-256",
        )

        normalized = Path(relative_path).as_posix()
        _reject_holdout_path(normalized, label)
        folded = normalized.casefold()
        _expect(folded not in seen, f"duplicate M96 pilot source snapshot path: {normalized}")
        seen.add(folded)
        current = _artifact(root, normalized, f"M96 pilot source snapshot ({category})")
        _expect_equal(current["relative_path"], normalized, f"{label}.path")
        _expect_equal(current["bytes"], expected_bytes, f"{label}.bytes")
        _expect_equal(current["sha256"], expected_sha256, f"{label}.raw_sha256")
        validated_files.append(
            {
                "relative_path": normalized,
                "category": category,
                "bytes": expected_bytes,
                "raw_sha256": expected_sha256,
            }
        )

    return {
        "status": "all_source_snapshot_files_match_bytes_and_raw_sha256",
        "file_count": len(validated_files),
        "inventory_contract_sha256": inventory_contract_sha256,
        "files": validated_files,
    }


def _load_immutable_field_records(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    records: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    for key, relative_path, expected_sha256 in IMMUTABLE_FIELD_RECORDS:
        path = _inside(root, relative_path, f"{key} immutable record")
        if not path.is_file():
            raise GoalProgressRecordError(
                f"immutable field record is missing: {relative_path}"
            )
        observed_sha256 = _sha256_file(root, path, f"{key} immutable record")
        _expect_equal(observed_sha256, expected_sha256, f"{key} SHA-256")
        record = _load_json(root, relative_path, f"{key} immutable record")
        records[key] = record
        bindings.append(
            {
                "key": key,
                "relative_path": relative_path,
                "bytes": path.stat().st_size,
                "sha256": observed_sha256,
                "schema_version": record.get("schema_version"),
                "record_version": record.get("record_version"),
                "status": record.get("status"),
            }
        )
    return records, bindings


def _extract_demo_v1_1(record: Mapping[str, Any]) -> dict[str, Any]:
    _expect_equal(record.get("schema_version"), "1.1.0", "Demo schema_version")
    result_contract = _required(record, "result_contract", label="Demo record")
    final_score = _required(
        result_contract, "final_demo_score", label="Demo result contract"
    )
    _expect_equal(
        final_score.get("relationship"),
        "primary_information_formation_reference",
        "final_demo_score relationship",
    )
    _expect_equal(
        final_score.get("semantics"),
        "recognizable_motion_outline_and_amplitude_information_formation_only",
        "final_demo_score semantics",
    )
    _expect_equal(
        final_score.get("range"),
        {"minimum": 0, "maximum": 100},
        "final_demo_score range",
    )
    for field in (
        "is_formal_technique_score",
        "is_coach_score",
        "is_recognition_accuracy",
    ):
        _expect_equal(final_score.get(field), False, f"final_demo_score.{field}")

    analysis_quality = _required(
        result_contract, "analysis_quality", label="Demo result contract"
    )
    _expect_equal(
        analysis_quality.get("relationship"),
        "separate_analysis_completeness_measure",
        "analysis_quality relationship",
    )
    display_score = _required(
        result_contract, "display_score", label="Demo result contract"
    )
    _expect_equal(
        display_score,
        {
            "relationship": "backward_compatible_alias",
            "compatibility_alias_for": "analysis_quality",
        },
        "display_score compatibility relation",
    )
    expected_action_fields = [
        "status",
        "label_zh",
        "reference_score_0_to_100",
        "meaning_zh",
        "explanation_zh",
        "components",
        "component_weights",
        "score_version",
    ]
    _expect_equal(
        result_contract.get("per_action_formation_assessment_fields"),
        expected_action_fields,
        "formation_assessment fields",
    )

    browser = _required(record, "browser_batch_and_recovery", label="Demo record")
    expected_allowlist = [
        "version",
        "submitted_job_ids",
        "completed_job_ids",
        "failed_job_ids",
        "active_job_id",
        "current_index",
        "total",
    ]
    _expect_equal(
        browser.get("local_storage_allowlist"),
        expected_allowlist,
        "Demo localStorage allowlist",
    )
    for field in (
        "token_persisted",
        "files_or_filenames_persisted",
        "result_payloads_persisted",
    ):
        _expect_equal(browser.get(field), False, f"Demo browser.{field}")
    _expect_equal(
        browser.get("submission"),
        "multiple_files_submitted_sequentially_as_single_job_API_calls",
        "Demo multi-video submission",
    )
    _expect_equal(
        browser.get("recovery"),
        "submitted_job_ids_are_reloaded_and_polled_after_refresh",
        "Demo refresh recovery",
    )

    legacy = _required(record, "legacy_job_behavior", label="Demo record")
    _expect_equal(legacy.get("http_status"), 409, "legacy Demo HTTP status")
    _expect_equal(
        legacy.get("code"),
        "legacy_job_requires_reanalysis",
        "legacy Demo error code",
    )
    _expect_equal(
        legacy.get("condition"),
        "succeeded_job_missing_indicator-features.jsonl",
        "legacy Demo condition",
    )
    _expect_equal(legacy.get("returns_http_500"), False, "legacy Demo 500 boundary")

    formal = _required(record, "formal_scoring", label="Demo record")
    _expect_equal(formal.get("available"), False, "formal_scoring.available")
    _expect_equal(formal.get("score_0_to_100"), None, "formal scoring score")
    _expect_equal(formal.get("grade"), None, "formal scoring grade")
    _expect_equal(formal.get("A_to_E_generated"), False, "formal A-E boundary")
    claim_boundaries = _required(record, "claim_boundaries", label="Demo record")
    _expect(
        all(value is False for value in claim_boundaries.values()),
        "all Demo claim-boundary flags must remain false",
    )
    source_checks = _required(
        record,
        "verification",
        "source_contract_checks",
        label="Demo record",
    )
    for field in (
        "formation_assessment_present",
        "multiple_input_present",
        "sequential_submission_present",
        "refresh_recovery_present",
        "summary_zh_rendered",
        "local_storage_allowlist_excludes_sensitive_values",
    ):
        _expect_equal(source_checks.get(field), True, f"Demo source check {field}")
    return {
        "schema_version": record["schema_version"],
        "result_contract": result_contract,
        "browser_batch_and_recovery": browser,
        "legacy_job_behavior": legacy,
        "formal_scoring": formal,
        "claim_boundaries": claim_boundaries,
        "summary_zh_ui_requirement": {
            "displayed": source_checks["summary_zh_rendered"],
            "artifact_bound_by_inventory": "src/rallymate_service/assets/user-demo.js",
        },
    }


def _extract_m96_same_frame(record: Mapping[str, Any]) -> dict[str, Any]:
    sample = _required(record, "sample_binding", label="M96 same-frame record")
    _expect_equal(sample.get("sample_count"), 45, "M96 sample count")
    _expect_equal(sample.get("development_video_count"), 3, "M96 video count")
    _expect_equal(sample.get("frames_per_window"), 5, "M96 frames per window")
    _expect_equal(sample.get("windows_per_video"), 3, "M96 windows per video")
    _expect_equal(
        sample.get("same_decoded_frames_and_frozen_ROIs_across_models"),
        True,
        "M96 same-input binding",
    )
    observed = _required(record, "observed_snapshot", label="M96 same-frame record")
    expected = {
        "M": {
            "input_size_hw": [256, 192],
            "pose_return_rate": 1.0,
            "coverage_0_5": 0.734188,
            "latency_p50_ms": 9.1244,
        },
        "L": {
            "input_size_hw": [384, 288],
            "pose_return_rate": 1.0,
            "coverage_0_5": 0.747863,
            "latency_p50_ms": 10.6427,
        },
        "X": {
            "input_size_hw": [384, 288],
            "pose_return_rate": 1.0,
            "coverage_0_5": 0.769231,
            "latency_p50_ms": 11.7265,
        },
    }
    models: dict[str, Any] = {}
    for family in ("M", "L", "X"):
        source = _required(observed, family, label="M96 observed snapshot")
        coverage = _required(
            source,
            "confidence_threshold_coverage_including_missing_poses",
            label=f"M96 {family}",
        )
        latency = _required(
            source,
            "latency_ms_per_single_roi_call",
            label=f"M96 {family}",
        )
        _expect_equal(source.get("input_size_hw"), expected[family]["input_size_hw"], f"M96 {family} input")
        _expect_equal(source.get("pose_return_rate"), expected[family]["pose_return_rate"], f"M96 {family} pose return")
        _expect_equal(coverage.get("0.5"), expected[family]["coverage_0_5"], f"M96 {family} confidence coverage")
        _expect_equal(latency.get("p50"), expected[family]["latency_p50_ms"], f"M96 {family} latency p50")
        _expect_equal(latency.get("count"), 45, f"M96 {family} latency count")
        models[family] = {
            "candidate_id": source.get("candidate_id"),
            "input_size_hw": source.get("input_size_hw"),
            "pose_return_rate": source.get("pose_return_rate"),
            "confidence_threshold_coverage_including_missing_poses": coverage,
            "latency_ms_per_single_roi_call": latency,
            "cuda_steady_state_peak": source.get("cuda_steady_state_peak"),
            "same_input_repeat_normalized_coordinate_delta": source.get(
                "same_input_repeat_normalized_coordinate_delta"
            ),
            "temporal_normalized_coordinate_displacement": source.get(
                "temporal_normalized_coordinate_displacement"
            ),
        }
    claims = _required(record, "claims", label="M96 same-frame record")
    _expect_equal(claims.get("RallyMate_accuracy_measured"), False, "M96 accuracy boundary")
    _expect_equal(claims.get("candidate_promoted"), False, "M96 promotion boundary")
    return {
        "scope": sample,
        "models": models,
        "semantics": "same_frame_observability_only_not_ground_truth_or_accuracy",
        "ground_truth_used": False,
        "candidate_promoted": False,
    }


def _extract_m97_operational(record: Mapping[str, Any]) -> dict[str, Any]:
    scope = _required(record, "scope", label="M97 operational record")
    result = _required(record, "result", label="M97 operational record")
    _expect_equal(scope.get("target_frame_count"), 258, "M97 target frames")
    _expect_equal(scope.get("video_count"), 3, "M97 video count")
    _expect_equal(scope.get("indicator_instances"), 2366, "M97 indicator instances")
    _expect_equal(
        _required(result, "X_projection", "operational_measured", label="M97 result"),
        2331,
        "M97 X operational measured",
    )
    _expect_equal(
        _required(result, "baseline_M70", "operational_measured", label="M97 result"),
        2327,
        "M97 M70 operational measured",
    )
    _expect_equal(
        _required(result, "baseline_M71", "operational_measured", label="M97 result"),
        2333,
        "M97 M71 operational measured",
    )
    comparison_m70 = _required(
        result,
        "comparison_to_M70",
        "operational_measurement",
        label="M97 result",
    )
    comparison_m71 = _required(
        result,
        "comparison_to_M71",
        "operational_measurement",
        label="M97 result",
    )
    _expect_equal(comparison_m70.get("additional_recovery_count"), 4, "M97 X vs M70 additional")
    _expect_equal(comparison_m70.get("lost_reference_recovery_count"), 0, "M97 X vs M70 lost")
    _expect_equal(comparison_m71.get("additional_recovery_count"), 1, "M97 X vs M71 additional")
    _expect_equal(comparison_m71.get("lost_reference_recovery_count"), 3, "M97 X vs M71 lost")
    _expect_equal(
        result.get("status"),
        "experimental_x_extension_regression_free_to_m70_but_does_not_preserve_m71_recovery",
        "M97 decision status",
    )
    preserved = _required(record, "preserved_state", label="M97 operational record")
    _expect_equal(preserved.get("candidate_promoted"), False, "M97 promotion boundary")
    _expect_equal(preserved.get("ground_truth_used"), False, "M97 ground-truth boundary")
    return {
        "scope": scope,
        "result": result,
        "decision": {
            "candidate_promoted": False,
            "production_default_changed": False,
            "reason": "X adds four operational recoveries over M70 with no loss, but adds one and loses three relative to M71",
        },
        "semantics": "operational_observability_not_ground_truth_accuracy",
    }


def _extract_pilot(
    root: Path,
    field_record: Mapping[str, Any],
    m95_record: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    contract_path = Path("reports/m96-pose-pilot/pilot-contract.json")
    validation_path = Path(
        "data/annotations/m96-halpe26-development-pilot-v1/validation-report.json"
    )
    tasks_path = _inside(
        root,
        "data/annotations/m96-halpe26-development-pilot-v1/tasks.jsonl",
        "M96 pilot task list",
    )
    validation = _load_json(root, validation_path, "M96 pilot validation")
    binding = _required(field_record, "pilot_binding", label="M96 pilot field record")
    observed_contract_sha = _sha256_file(
        root, _inside(root, contract_path, "M96 pilot contract"), "M96 pilot contract"
    )
    _expect_equal(
        observed_contract_sha,
        binding.get("pilot_contract_report_raw_sha256"),
        "M96 pilot contract SHA-256",
    )
    source_snapshot_validation = _validate_pilot_source_snapshot(root, contract)
    _expect_equal(
        source_snapshot_validation["inventory_contract_sha256"],
        binding.get("source_inventory_contract_sha256"),
        "M96 pilot source inventory contract SHA-256",
    )
    scope = _required(contract, "scope", label="M96 pilot contract")
    _expect_equal(scope.get("development_videos"), 3, "M96 pilot videos")
    _expect_equal(scope.get("frames"), 24, "M96 pilot frames")
    _expect_equal(scope.get("frames_per_video"), 8, "M96 pilot frames per video")
    _expect_equal(scope.get("joint_tasks"), 624, "M96 pilot joint tasks")
    _expect_equal(scope.get("keypoints_per_frame"), 26, "M96 pilot keypoints")
    _expect_equal(scope.get("mode"), "evaluation_only", "M96 pilot mode")
    outcomes = _required(contract, "outcomes", label="M96 pilot contract")
    _expect_equal(outcomes.get("human_annotation_rows"), 0, "M96 human rows")
    _expect_equal(outcomes.get("adjudication_rows"), 0, "M96 adjudication rows")
    _expect_equal(outcomes.get("accuracy"), None, "M96 pilot accuracy")
    counts = _required(validation, "counts", label="M96 pilot validation")
    _expect_equal(counts.get("frames"), 24, "M96 validation frames")
    _expect_equal(counts.get("joint_tasks"), 624, "M96 validation joint tasks")
    _expect_equal(counts.get("human_annotation_rows"), 0, "M96 validation human rows")
    _expect_equal(counts.get("accuracy_metrics"), 0, "M96 validation accuracy metrics")
    checks = _required(validation, "checks", label="M96 pilot validation")
    _expect_equal(checks.get("sealed_holdout_excluded"), True, "M96 pilot holdout exclusion")
    if not tasks_path.is_file():
        raise GoalProgressRecordError("M96 pilot task list is missing")
    task_text = tasks_path.read_text(encoding="utf-8")
    _expect(
        SEALED_HOLDOUT_ID.casefold() not in task_text.casefold(),
        "sealed holdout must not appear in the M96 pilot task list",
    )
    readiness = _required(
        m95_record, "finetune_readiness", label="M95 readiness record"
    )
    _expect_equal(readiness.get("truth_task_count"), 1976, "historical truth task count")
    _expect_equal(readiness.get("accepted_human_keypoint_frames"), 0, "accepted human frames")
    _expect_equal(readiness.get("dataset_export_allowed"), False, "dataset export gate")
    _expect_equal(readiness.get("training_started"), False, "training start gate")
    return {
        "scope": scope,
        "outcomes": outcomes,
        "validation_counts": counts,
        "status": validation.get("status"),
        "safety": contract.get("safety"),
        "source_snapshot_validation": source_snapshot_validation,
        "human_governance": {
            "annotator_A_complete": False,
            "annotator_B_complete": False,
            "adjudicator_C_complete": False,
            "human_annotation_rows": 0,
            "adjudication_rows": 0,
        },
        "historical_context": {
            "m95_truth_task_count": 1976,
            "is_current_training_roster": False,
            "real_dataset_exported": False,
            "training_started": False,
        },
    }


def _extract_documents(root: Path) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    for key, relative_path, expected_version in DOCUMENT_SOURCES:
        path = _inside(root, relative_path, f"{key} document")
        if not path.is_file():
            raise GoalProgressRecordError(f"document is missing: {relative_path}")
        text = path.read_text(encoding="utf-8")
        if key == "product":
            match = re.search(r"文档版本[：:]\s*([0-9.]+)", text)
        elif key == "technical":
            match = re.search(r"文档版本[：:]\s*([0-9.]+)", text)
        elif key == "api":
            match = re.search(r"文档修订[：:]\s*([0-9.]+)", text)
        else:
            match = None
        observed_version = match.group(1) if match else "special_document"
        _expect_equal(observed_version, expected_version, f"{key} document version")
        documents.append(
            {
                "key": key,
                "relative_path": relative_path,
                "version": observed_version,
            }
        )

    build_script = (
        _inside(
            root,
            "scripts/build_current_product_technical_docs.ps1",
            "document build script",
        )
        .read_text(encoding="utf-8")
    )
    for _, relative_path, _ in DOCUMENT_SOURCES:
        filename = Path(relative_path).name
        _expect(
            filename in build_script,
            f"document build script does not include {filename}",
        )
    serve_script = (
        _inside(root, "scripts/serve_current_docs.ps1", "document launcher")
        .read_text(encoding="utf-8")
    )
    _expect(
        "reports/rallymate-current-docs" in serve_script
        and '"--allow"' in serve_script
        and '"127.0.0.1"' in serve_script,
        "current-document launcher must remain loopback-bound and allowlisted",
    )
    return {
        "source_count": len(documents),
        "sources": documents,
        "versions": {item["key"]: item["version"] for item in documents[:3]},
        "build_script": "scripts/build_current_product_technical_docs.ps1",
        "launcher": "scripts/serve_current_docs.ps1",
        "launcher_boundary": "loopback_and_reports/rallymate-current-docs_allowlist",
    }


def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_drawio(root: Path) -> dict[str, Any]:
    relative_path = "docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio"
    path = _inside(root, relative_path, "Draw.io source")
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise GoalProgressRecordError("Draw.io source is not valid XML") from exc
    pages = [node for node in tree.getroot().iter() if _tag_name(node.tag) == "diagram"]
    names = [str(page.get("name", "")) for page in pages]
    _expect_equal(tuple(names), EXPECTED_DRAWIO_PAGES, "Draw.io page names")
    edge_count = 0
    dangling: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages, start=1):
        cells = [node for node in page.iter() if _tag_name(node.tag) == "mxCell"]
        ids = {cell.get("id") for cell in cells if cell.get("id")}
        for cell in cells:
            if cell.get("edge") != "1":
                continue
            edge_count += 1
            source = cell.get("source")
            target = cell.get("target")
            missing = [
                endpoint
                for endpoint, value in (("source", source), ("target", target))
                if not value or value not in ids
            ]
            if missing:
                dangling.append(
                    {
                        "page_index": page_index,
                        "page_name": names[page_index - 1],
                        "edge_id": cell.get("id"),
                        "missing": missing,
                    }
                )
    _expect_equal(len(pages), 5, "Draw.io page count")
    _expect_equal(dangling, [], "Draw.io dangling edges")
    return {
        "relative_path": relative_path,
        "xml_parse_passed": True,
        "page_count": len(pages),
        "page_names": names,
        "edge_count": edge_count,
        "dangling_edge_count": 0,
        "dangling_edges": [],
    }


def _extract_service_contract(root: Path) -> dict[str, Any]:
    relative_path = "src/rallymate_service/api.py"
    path = _inside(root, relative_path, "service API source")
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise GoalProgressRecordError("service API source is not valid Python") from exc

    routes: list[tuple[int, str, str]] = []
    methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            function = decorator.func
            if not (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "app"
                and function.attr in methods
            ):
                continue
            route_arg = decorator.args[0]
            if not isinstance(route_arg, ast.Constant) or not isinstance(
                route_arg.value, str
            ):
                raise GoalProgressRecordError("HTTP route path must be a string literal")
            routes.append((decorator.lineno, function.attr.upper(), route_arg.value))
    routes.sort()
    route_pairs = tuple((method, route) for _, method, route in routes)
    _expect_equal(route_pairs, EXPECTED_HTTP_ROUTES, "HTTP route roster")

    allowlist: Any = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ALLOWED_ARTIFACTS"
            for target in node.targets
        ):
            allowlist = ast.literal_eval(node.value)
            break
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "ALLOWED_ARTIFACTS"
        ):
            allowlist = ast.literal_eval(node.value)
            break
    _expect(isinstance(allowlist, (set, frozenset)), "ALLOWED_ARTIFACTS must be a set literal")
    normalized_allowlist = frozenset(str(value) for value in allowlist)
    _expect_equal(normalized_allowlist, EXPECTED_ARTIFACT_WHITELIST, "artifact whitelist")
    return {
        "source": relative_path,
        "http_route_count": len(route_pairs),
        "http_routes": [
            {"method": method, "path": route} for method, route in route_pairs
        ],
        "artifact_whitelist_count": len(normalized_allowlist),
        "artifact_whitelist": sorted(normalized_allowlist),
    }


def _extract_production_state(
    root: Path,
    records: Mapping[str, Mapping[str, Any]],
    pilot_contract: Mapping[str, Any],
) -> dict[str, Any]:
    path = _inside(root, PRODUCTION_REGISTRY_PATH, "production registry")
    observed_sha = _sha256_file(root, path, "production registry")
    _expect_equal(observed_sha, PRODUCTION_REGISTRY_SHA256, "production registry SHA-256")
    registry = _load_json(root, PRODUCTION_REGISTRY_PATH, "production registry")
    _expect_equal(registry.get("default_preset"), PRODUCTION_DEFAULT_PRESET, "default preset")
    presets = registry.get("presets")
    _expect(isinstance(presets, list), "production registry presets must be a list")
    default_entries = [
        item
        for item in presets
        if isinstance(item, Mapping) and item.get("preset_id") == PRODUCTION_DEFAULT_PRESET
    ]
    _expect_equal(len(default_entries), 1, "default preset registry entry count")
    default_entry = default_entries[0]
    _expect_equal(default_entry.get("input_size_hw"), [256, 192], "default M input size")

    m95 = records["m95_model_candidate_finetune_readiness"]
    m96 = records["m96_same_frame_diagnostic"]
    pilot = records["m96_pose_pilot"]
    m97 = records["m97_operational_residual"]
    m95_candidate = _required(m95, "model_candidate", label="M95 record")
    _expect_equal(m95_candidate.get("production_default_before"), PRODUCTION_DEFAULT_PRESET, "M95 default before")
    _expect_equal(m95_candidate.get("production_default_after"), PRODUCTION_DEFAULT_PRESET, "M95 default after")
    _expect_equal(m95_candidate.get("candidate_promoted"), False, "M95 promotion")
    m96_state = _required(m96, "preserved_state", label="M96 record")
    _expect_equal(m96_state.get("production_default_preset"), PRODUCTION_DEFAULT_PRESET, "M96 default")
    _expect_equal(m96_state.get("production_registry_sha256"), PRODUCTION_REGISTRY_SHA256, "M96 registry SHA")
    _expect_equal(m96_state.get("production_default_changed"), False, "M96 default change")
    pilot_disconnected = _required(pilot, "systems_not_connected", label="M96 pilot record")
    _expect_equal(pilot_disconnected.get("production_default"), True, "pilot production isolation")
    pilot_safety = _required(pilot_contract, "safety", label="M96 pilot contract")
    _expect_equal(pilot_safety.get("production_default_changed"), False, "pilot default change")
    m97_state = _required(m97, "preserved_state", label="M97 record")
    _expect_equal(m97_state.get("production_default_preset"), PRODUCTION_DEFAULT_PRESET, "M97 default")
    _expect_equal(m97_state.get("production_registry_sha256"), PRODUCTION_REGISTRY_SHA256, "M97 registry SHA")
    _expect_equal(m97_state.get("production_default_changed"), False, "M97 default change")
    _expect_equal(m97_state.get("candidate_promoted"), False, "M97 candidate promotion")
    return {
        "registry_relative_path": PRODUCTION_REGISTRY_PATH,
        "registry_version": registry.get("registry_version"),
        "registry_bytes": path.stat().st_size,
        "registry_sha256": observed_sha,
        "default_preset": PRODUCTION_DEFAULT_PRESET,
        "default_family_and_input": "RTMPose-M Halpe26 256x192 (M256)",
        "unchanged_from_m95_through_m97": True,
        "candidate_promoted": False,
    }


def _extract_holdout_boundary(
    records: Mapping[str, Mapping[str, Any]], artifact_paths: Iterable[str]
) -> dict[str, Any]:
    m95_guard = _required(
        records["m95_model_candidate_finetune_readiness"],
        "finetune_readiness",
        "sealed_holdout_guard",
        label="M95 record",
    )
    _expect_equal(m95_guard.get("video_id"), SEALED_HOLDOUT_ID, "M95 holdout id")
    _expect_equal(m95_guard.get("video_sha256"), SEALED_HOLDOUT_DECLARED_SHA256, "M95 holdout declared SHA")
    _expect_equal(m95_guard.get("use_during_development"), False, "M95 holdout development use")
    _expect_equal(m95_guard.get("opened_by_M95"), False, "M95 holdout open flag")
    m96_state = _required(
        records["m96_same_frame_diagnostic"], "preserved_state", label="M96 record"
    )
    _expect_equal(m96_state.get("sealed_holdout_file_read_or_hashed"), False, "M96 holdout read/hash")
    m97_holdout = _required(
        records["m97_operational_residual"],
        "preserved_state",
        "sealed_holdout",
        label="M97 record",
    )
    _expect_equal(m97_holdout.get("video_id"), SEALED_HOLDOUT_ID, "M97 holdout id")
    _expect_equal(m97_holdout.get("declared_relative_path"), SEALED_HOLDOUT_RELATIVE_PATH, "M97 holdout path")
    _expect_equal(m97_holdout.get("declared_sha256"), SEALED_HOLDOUT_DECLARED_SHA256, "M97 holdout SHA declaration")
    _expect_equal(m97_holdout.get("file_opened_or_hashed"), False, "M97 holdout open/hash")
    _expect_equal(m97_holdout.get("declared_hash_reverified_in_M97"), False, "M97 holdout reverify")
    for relative_path in artifact_paths:
        _expect(
            SEALED_HOLDOUT_ID.casefold() not in relative_path.casefold(),
            "sealed holdout must not enter the goal-progress artifact inventory",
        )
    return {
        "video_id": SEALED_HOLDOUT_ID,
        "declared_relative_path": SEALED_HOLDOUT_RELATIVE_PATH,
        "declared_sha256": SEALED_HOLDOUT_DECLARED_SHA256,
        "included_in_pilot_task_list": False,
        "included_in_artifact_inventory": False,
        "opened_statted_or_hashed_by_this_builder": False,
        "declaration_only": True,
    }


def _artifact_specs(
    root: Path, pilot_source_files: Iterable[Mapping[str, Any]]
) -> list[tuple[str, str]]:
    specs = list(BASE_ARTIFACT_SPECS)
    existing_paths = {Path(path).as_posix().casefold() for path, _ in specs}
    pilot_paths: set[str] = set()
    for item in pilot_source_files:
        relative_path = item.get("relative_path")
        category = item.get("category")
        _expect(
            isinstance(relative_path, str) and isinstance(category, str),
            "validated M96 pilot source snapshot entry is malformed",
        )
        normalized = Path(relative_path).as_posix()
        pilot_paths.add(normalized)
        folded = normalized.casefold()
        if folded not in existing_paths:
            specs.append((normalized, f"M96 pilot source snapshot ({category})"))
            existing_paths.add(folded)

    rendered_root = _inside(
        root, "reports/rallymate-current-docs", "current rendered document root"
    )
    if not rendered_root.is_dir():
        raise GoalProgressRecordError("current rendered document directory is missing")
    html_paths = sorted(rendered_root.glob("*.html"), key=lambda item: item.name.casefold())
    _expect_equal(
        tuple(path.name for path in html_paths),
        EXPECTED_CURRENT_DOC_HTML_FILES,
        "current rendered HTML file roster",
    )
    for path in html_paths:
        specs.append((_relative_path(root, path), "current rendered HTML"))

    evidence_root = _inside(
        root,
        "reports/rallymate-current-docs/evidence",
        "current rendered evidence root",
    )
    if not evidence_root.is_dir():
        raise GoalProgressRecordError("current rendered evidence directory is missing")
    evidence_paths: list[Path] = []
    for candidate in sorted(
        evidence_root.rglob("*"),
        key=lambda item: item.relative_to(evidence_root).as_posix().casefold(),
    ):
        lexical_relative = _relative_path(root, candidate)
        _reject_holdout_path(lexical_relative, "current rendered evidence")
        _expect(
            not candidate.is_symlink(),
            f"current rendered evidence must not be a symlink: {lexical_relative}",
        )
        if candidate.is_file():
            evidence_paths.append(candidate)
    _expect(bool(evidence_paths), "current rendered evidence directory is empty")
    for path in evidence_paths:
        specs.append((_relative_path(root, path), "current rendered evidence copy"))

    deduplicated: dict[str, str] = {}
    for relative_path, role in specs:
        normalized = Path(relative_path).as_posix()
        _reject_holdout_path(normalized, "artifact specification")
        if normalized in deduplicated and deduplicated[normalized] != role:
            raise GoalProgressRecordError(
                f"artifact has conflicting roles: {normalized}"
            )
        deduplicated[normalized] = role
    _expect_equal(
        len(pilot_paths),
        EXPECTED_PILOT_SOURCE_FILE_COUNT,
        "M96 pilot source paths added to artifact roster",
    )
    _expect(
        all(path in deduplicated for path in pilot_paths),
        "every M96 pilot source snapshot file must be in the artifact roster",
    )
    return sorted(deduplicated.items(), key=lambda item: item[0].casefold())


def _build_artifact_inventory(
    root: Path, pilot_source_files: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    artifacts = [
        _artifact(root, path, role)
        for path, role in _artifact_specs(root, pilot_source_files)
    ]
    _expect(len(artifacts) >= 50, "artifact inventory is unexpectedly incomplete")
    return artifacts


def _collect_snapshot(root: Path) -> dict[str, Any]:
    records, immutable_bindings = _load_immutable_field_records(root)
    pilot_contract = _load_json(
        root, "reports/m96-pose-pilot/pilot-contract.json", "M96 pilot contract"
    )
    pilot = _extract_pilot(
        root,
        records["m96_pose_pilot"],
        records["m95_model_candidate_finetune_readiness"],
        pilot_contract,
    )
    pilot_source_files = _required(
        pilot,
        "source_snapshot_validation",
        "files",
        label="M96 pilot extracted snapshot",
    )
    artifacts = _build_artifact_inventory(root, pilot_source_files)
    artifact_paths = [str(item["relative_path"]) for item in artifacts]
    return {
        "immutable_field_records": immutable_bindings,
        "demo_v1_1": _extract_demo_v1_1(records["m96_user_demo_v1_1"]),
        "m96_same_frame_observability": _extract_m96_same_frame(
            records["m96_same_frame_diagnostic"]
        ),
        "m97_operational_residual": _extract_m97_operational(
            records["m97_operational_residual"]
        ),
        "m96_evaluation_only_pilot": pilot,
        "documentation": _extract_documents(root),
        "drawio": _extract_drawio(root),
        "service_contract": _extract_service_contract(root),
        "production_state": _extract_production_state(root, records, pilot_contract),
        "sealed_holdout": _extract_holdout_boundary(records, artifact_paths),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
    }


def _full_suite_record(tests: int, seconds: float, skipped: int) -> dict[str, Any]:
    if tests < 1:
        raise GoalProgressRecordError("--full-suite-tests must be at least 1")
    if not math.isfinite(seconds) or seconds <= 0:
        raise GoalProgressRecordError(
            "--full-suite-seconds must be a finite positive number"
        )
    if skipped < 0 or skipped >= tests:
        raise GoalProgressRecordError(
            "--full-suite-skipped must be at least 0 and less than --full-suite-tests"
        )
    return {
        "status": "passed_as_supplied_after_external_run",
        "passed": True,
        "tests": tests,
        "seconds": seconds,
        "skipped": skipped,
        "failures": 0,
        "errors": 0,
        "source": "required_command_line_values_supplied_by_the_caller",
        "builder_executes_test_suite": False,
        "is_model_accuracy_evidence": False,
    }


def _render_summary(snapshot: Mapping[str, Any], full_suite: Mapping[str, Any]) -> str:
    m96 = snapshot["m96_same_frame_observability"]
    models = m96["models"]
    m97 = snapshot["m97_operational_residual"]["result"]
    pilot = snapshot["m96_evaluation_only_pilot"]
    docs = snapshot["documentation"]["versions"]
    demo = snapshot["demo_v1_1"]
    return "\n".join(
        [
            "# RallyMate M97 目标进度冻结摘要",
            "",
            "> 本摘要只汇总已存在且经过 SHA/结构校验的开发证据。所有模型数字都只用于可观测性，不是真值、技术分或识别准确率。",
            "",
            "## 已完成",
            "",
            "- Demo v1.1 已把 `final_demo_score` 固定为 0–100 的“当前视频中可识别动作轮廓与幅度信息成型程度”参考分；`analysis_quality` 独立，`display_score` 只兼容指向 `analysis_quality`。",
            f"- 每动作已冻结 `formation_assessment` 八字段；中文 `summary_zh` 进入界面；正式评分仍为 `available={str(demo['formal_scoring']['available']).lower()}`、分数与等级为空。",
            "- 多视频按选择顺序逐个调用原单任务 API；页面显示 1-based 当前序号/总数并保留可切换摘要。刷新只恢复已提交 job id 和批次状态；localStorage 不保存 token、文件、文件名或结果载荷。",
            "- 旧成功任务若缺少 `indicator-features.jsonl`，Demo 结果端点返回 HTTP 409 `legacy_job_requires_reanalysis`，提示重新上传分析，不返回 500。",
            f"- M96 同帧范围为 {m96['scope']['sample_count']} 帧；M/L/X 的 pose return rate 均为 1.0，置信度 0.5 覆盖分别为 {models['M']['confidence_threshold_coverage_including_missing_poses']['0.5']:.6f}/{models['L']['confidence_threshold_coverage_including_missing_poses']['0.5']:.6f}/{models['X']['confidence_threshold_coverage_including_missing_poses']['0.5']:.6f}，单 ROI 延迟 P50 为 {models['M']['latency_ms_per_single_roi_call']['p50']:.4f}/{models['L']['latency_ms_per_single_roi_call']['p50']:.4f}/{models['X']['latency_ms_per_single_roi_call']['p50']:.4f} ms。",
            f"- M97 在 {snapshot['m97_operational_residual']['scope']['target_frame_count']} 个残差帧、{snapshot['m97_operational_residual']['scope']['indicator_instances']} 个指标实例上：X={m97['X_projection']['operational_measured']}、M70={m97['baseline_M70']['operational_measured']}、M71={m97['baseline_M71']['operational_measured']}；X 相对 M70 为 +{m97['comparison_to_M70']['operational_measurement']['additional_recovery_count']}/lost {m97['comparison_to_M70']['operational_measurement']['lost_reference_recovery_count']}，相对 M71 为 +{m97['comparison_to_M71']['operational_measurement']['additional_recovery_count']}/lost {m97['comparison_to_M71']['operational_measurement']['lost_reference_recovery_count']}。因此 X 不晋级，默认仍为 M256。",
            f"- M96 evaluation-only 人工 pilot 已生成 {pilot['scope']['frames']} 帧 × {pilot['scope']['keypoints_per_frame']} 关键点 = {pilot['scope']['joint_tasks']} 个任务；当前 human rows={pilot['outcomes']['human_annotation_rows']}。M95 的 1976 条历史 truth task 不是本轮训练清单。",
            f"- 当前产品/技术/API 文档版本分别为 {docs['product']}/{docs['technical']}/{docs['api']}；Draw.io 为 {snapshot['drawio']['page_count']} 页且悬空边为 {snapshot['drawio']['dangling_edge_count']}；HTTP 路由 {snapshot['service_contract']['http_route_count']} 条，artifact whitelist {snapshot['service_contract']['artifact_whitelist_count']} 项。",
            f"- 最终全量回归由调用方在外部运行后提供：{full_suite['tests']} tests，{full_suite['seconds']} s，skipped={full_suite['skipped']}，failures=0，errors=0；本构建脚本不运行测试。",
            "",
            "## 未完成",
            "",
            "- A/B 独立人工标注与 C 分歧裁决尚未完成；human annotation rows 与 adjudication rows 均为 0。",
            "- 尚未形成可用于真实训练的数据清单，未导出真实训练集，未开始微调，也没有新 checkpoint。",
            "- 尚无人工真值，因此没有 RallyMate 关键点准确率或技术水平准确率结论。",
            "- X 候选未晋级，生产默认 preset 与 registry SHA 均未改变。",
            "- 正式评分、阈值校准和 A–E 等级仍关闭。",
            "- sealed holdout 未进入 pilot 或 artifact 清单，本脚本不读取、不 stat、不哈希该视频。",
            "",
            "## 可审计边界",
            "",
            f"- 五份不可变字段记录均通过固定 SHA-256；本记录额外覆盖 {snapshot['artifact_count']} 个源码、脚本、测试、报告、文档与当前 HTML artifact。",
            "- `--validate-existing` 只读重算所有 artifact 的 bytes/SHA，并重提取上述事实；任何漂移都会失败。",
            "",
        ]
    )


def _summary_binding(
    summary_bytes: bytes,
    relative_path: str = "reports/m97-goal-progress/summary.md",
) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "bytes": len(summary_bytes),
        "sha256": _sha256_bytes(summary_bytes),
    }


def _build_record(
    snapshot: Mapping[str, Any],
    full_suite: Mapping[str, Any],
    summary_bytes: bytes,
    summary_relative_path: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_version": RECORD_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "status": "goal_progress_snapshot_verified",
        "purpose": "freeze_M95_through_M97_goal_progress_and_remaining_gaps",
        **snapshot,
        "completed": [
            "Demo_v1_1_information_formation_result_and_safe_batch_recovery",
            "M96_45_frame_same_input_M_L_X_observability",
            "M97_258_residual_frame_X_operational_comparison",
            "M96_24_frame_624_joint_evaluation_only_pilot_scaffold",
            "current_documents_drawio_routes_and_download_allowlist",
            "external_full_suite_regression_recorded_from_required_arguments",
        ],
        "not_completed": [
            "independent_human_annotation_A",
            "independent_human_annotation_B",
            "disagreement_adjudication_C",
            "real_training_roster_and_dataset_export",
            "model_finetuning_or_new_checkpoint",
            "ground_truth_accuracy_evaluation",
            "candidate_promotion_or_production_default_change",
            "formal_scoring_thresholds_or_A_to_E_grades",
        ],
        "verification": {
            "full_suite": full_suite,
            "immutable_field_record_count": len(IMMUTABLE_FIELD_RECORDS),
            "all_artifact_bytes_and_sha_recomputable": True,
            "sealed_holdout_opened_statted_or_hashed": False,
        },
        "summary_binding": _summary_binding(summary_bytes, summary_relative_path),
        "claim_boundaries": {
            "accuracy_claimed": False,
            "technical_score_claimed": False,
            "coach_score_claimed": False,
            "finetuning_claimed": False,
            "candidate_promoted": False,
            "formal_A_to_E_generated": False,
            "observability_is_ground_truth": False,
        },
        "self_hash_note": (
            "The raw SHA-256 of field-change-record.json is reported externally; "
            "a file cannot contain its own raw-file hash."
        ),
    }


def _json_bytes(record: Mapping[str, Any]) -> bytes:
    text = json.dumps(
        record,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def _validated_generated_at(value: Any) -> str:
    _expect(isinstance(value, str) and bool(value), "generated_at must be non-empty text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GoalProgressRecordError("generated_at must be valid ISO-8601") from exc
    _expect(parsed.tzinfo is not None, "generated_at must include a timezone")
    _expect_equal(
        parsed.utcoffset(),
        timezone.utc.utcoffset(parsed),
        "generated_at UTC offset",
    )
    _expect_equal(value, parsed.isoformat(), "generated_at canonical ISO-8601 text")
    return value


def _write_new_bundle(
    root: Path,
    output_directory: Path,
    summary_bytes: bytes,
    record_bytes: bytes,
) -> None:
    output = _inside(root, output_directory, "goal-progress output directory")
    _expect(output != root, "output directory cannot be the workspace root")
    if output.exists():
        raise GoalProgressRecordError(
            f"immutable goal-progress destination already exists: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".m97-goal-progress-staging-", dir=output.parent)
    )
    try:
        (staging / "summary.md").write_bytes(summary_bytes)
        (staging / "field-change-record.json").write_bytes(record_bytes)
        if output.exists():
            raise GoalProgressRecordError(
                f"immutable goal-progress destination appeared during build: {output}"
            )
        staging.rename(output)
    finally:
        if staging.exists():
            resolved_staging = staging.resolve()
            _expect(
                resolved_staging.parent == output.parent.resolve()
                and resolved_staging.name.startswith(".m97-goal-progress-staging-"),
                "refusing to clean an unexpected staging directory",
            )
            shutil.rmtree(resolved_staging)


def build_goal_progress_bundle(
    *,
    workspace: str | Path,
    output_directory: str | Path,
    full_suite_tests: int,
    full_suite_seconds: float,
    full_suite_skipped: int,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = _inside(root, output_directory, "goal-progress output directory")
    if output.exists():
        raise GoalProgressRecordError(
            f"immutable goal-progress destination already exists: {output}"
        )
    full_suite = _full_suite_record(
        full_suite_tests, full_suite_seconds, full_suite_skipped
    )
    snapshot = _collect_snapshot(root)
    summary_bytes = _render_summary(snapshot, full_suite).encode("utf-8")
    summary_relative_path = _relative_path(root, output / "summary.md")
    record = _build_record(
        snapshot, full_suite, summary_bytes, summary_relative_path
    )
    record_bytes = _json_bytes(record)
    _write_new_bundle(root, Path(output_directory), summary_bytes, record_bytes)
    return {
        "status": "created",
        "output_directory": str(output),
        "summary": {
            "bytes": len(summary_bytes),
            "sha256": _sha256_bytes(summary_bytes),
        },
        "field_change_record": {
            "bytes": len(record_bytes),
            "sha256": _sha256_bytes(record_bytes),
        },
        "artifact_count": snapshot["artifact_count"],
    }


def _verify_recorded_artifacts(
    root: Path, recorded: Any, expected_current: list[dict[str, Any]]
) -> None:
    _expect(isinstance(recorded, list), "recorded artifacts must be a list")
    seen: set[str] = set()
    recomputed: list[dict[str, Any]] = []
    for item in recorded:
        _expect(isinstance(item, Mapping), "each recorded artifact must be an object")
        relative_path = item.get("relative_path")
        role = item.get("role")
        _expect(isinstance(relative_path, str), "artifact relative_path must be text")
        _expect(isinstance(role, str), "artifact role must be text")
        _expect(relative_path not in seen, f"duplicate artifact: {relative_path}")
        seen.add(relative_path)
        recomputed_item = _artifact(root, relative_path, role)
        _expect_equal(recomputed_item["bytes"], item.get("bytes"), f"{relative_path} bytes")
        _expect_equal(recomputed_item["sha256"], item.get("sha256"), f"{relative_path} SHA-256")
        recomputed.append(recomputed_item)
    _expect_equal(recomputed, expected_current, "artifact inventory roster and values")


def validate_existing_bundle(
    *,
    workspace: str | Path,
    output_directory: str | Path,
    full_suite_tests: int,
    full_suite_seconds: float,
    full_suite_skipped: int,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = _inside(root, output_directory, "goal-progress output directory")
    if not output.is_dir():
        raise GoalProgressRecordError(
            f"goal-progress destination does not exist for validation: {output}"
        )
    present = {path.name for path in output.iterdir()}
    _expect_equal(
        present,
        {"summary.md", "field-change-record.json"},
        "goal-progress output file roster",
    )
    record_path = output / "field-change-record.json"
    summary_path = output / "summary.md"
    record = _load_json(root, record_path, "existing goal-progress field record")
    _expect_equal(record.get("schema_version"), SCHEMA_VERSION, "goal-progress schema")
    _expect_equal(record.get("record_version"), RECORD_VERSION, "goal-progress record version")
    full_suite = _full_suite_record(
        full_suite_tests, full_suite_seconds, full_suite_skipped
    )
    _expect_equal(
        _required(record, "verification", "full_suite", label="goal-progress record"),
        full_suite,
        "recorded full-suite result",
    )
    snapshot = _collect_snapshot(root)
    for key, expected in snapshot.items():
        if key == "artifacts":
            continue
        _expect_equal(record.get(key), expected, f"goal-progress snapshot.{key}")
    _verify_recorded_artifacts(root, record.get("artifacts"), snapshot["artifacts"])

    summary_bytes = summary_path.read_bytes()
    expected_summary = _render_summary(snapshot, full_suite).encode("utf-8")
    _expect_equal(summary_bytes, expected_summary, "summary.md rendered content")
    _expect_equal(
        record.get("summary_binding"),
        _summary_binding(summary_bytes, _relative_path(root, summary_path)),
        "summary.md binding",
    )
    generated_at = _validated_generated_at(record.get("generated_at"))
    expected_record = _build_record(
        snapshot,
        full_suite,
        summary_bytes,
        _relative_path(root, summary_path),
        generated_at=generated_at,
    )
    _expect_equal(
        record,
        expected_record,
        "complete goal-progress field record",
    )
    record_bytes = record_path.read_bytes()
    _expect_equal(
        record_bytes,
        _json_bytes(expected_record),
        "field-change-record.json canonical bytes",
    )
    return {
        "status": "validated",
        "output_directory": str(output),
        "summary": {
            "bytes": len(summary_bytes),
            "sha256": _sha256_bytes(summary_bytes),
        },
        "field_change_record": {
            "bytes": len(record_bytes),
            "sha256": _sha256_bytes(record_bytes),
        },
        "artifact_count": snapshot["artifact_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument("--full-suite-tests", type=int, required=True)
    parser.add_argument("--full-suite-seconds", type=float, required=True)
    parser.add_argument("--full-suite-skipped", type=int, required=True)
    parser.add_argument(
        "--validate-existing",
        action="store_true",
        help="read-only validation; recompute all source facts and artifact hashes",
    )
    args = parser.parse_args()
    try:
        if args.validate_existing:
            result = validate_existing_bundle(
                workspace=args.root,
                output_directory=args.output_directory,
                full_suite_tests=args.full_suite_tests,
                full_suite_seconds=args.full_suite_seconds,
                full_suite_skipped=args.full_suite_skipped,
            )
        else:
            result = build_goal_progress_bundle(
                workspace=args.root,
                output_directory=args.output_directory,
                full_suite_tests=args.full_suite_tests,
                full_suite_seconds=args.full_suite_seconds,
                full_suite_skipped=args.full_suite_skipped,
            )
    except GoalProgressRecordError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
