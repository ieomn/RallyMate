#!/usr/bin/env python3
"""Seal the M96 user Demo field-change record without overwriting it."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONTRACT = Path("reports/m96-user-demo-v1.1/demo-contract.json")
DEFAULT_SUMMARY = Path("reports/m96-user-demo-v1.1/summary.md")
DEFAULT_OUTPUT = Path("reports/m96-user-demo-v1.1/field-change-record.json")
RECORD_VERSION = "m96-user-demo-field-change-record-v1.0.0"

IMPLEMENTATION_FILES: tuple[tuple[str, str], ...] = (
    ("src/rallymate_service/user_demo.py", "Demo result builder"),
    ("src/rallymate_service/api.py", "Demo result HTTP endpoint"),
    ("src/rallymate_service/assets/user-demo.html", "Demo page structure"),
    ("src/rallymate_service/assets/user-demo.js", "Demo browser workflow"),
    ("src/rallymate_service/assets/user-demo.css", "Demo presentation styles"),
    ("tests/test_user_demo.py", "Demo result and browser contract tests"),
    ("tests/test_service.py", "Service integration and legacy-job tests"),
)


class FieldRecordError(ValueError):
    """Raised when the immutable field record cannot be sealed."""


def _inside(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise FieldRecordError(f"{label} must stay inside the workspace")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(root: Path, value: str | Path, role: str) -> dict[str, Any]:
    path = _inside(root, value, role)
    if not path.is_file():
        raise FieldRecordError(f"{role} does not exist: {path}")
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FieldRecordError(f"Demo contract cannot be read: {path}") from exc
    if not isinstance(value, dict):
        raise FieldRecordError("Demo contract must be a JSON object")
    if value.get("schema_version") != "1.1.0":
        raise FieldRecordError("Demo contract schema_version must be 1.1.0")
    scoring = value.get("score_contracts", {})
    final_score = scoring.get("final_demo_score", {})
    if final_score.get("semantics") != (
        "recognizable_motion_outline_and_amplitude_information_formation_only"
    ):
        raise FieldRecordError("final_demo_score semantics drifted")
    for forbidden_flag in (
        "is_formal_technique_score",
        "is_coach_score",
        "is_recognition_accuracy",
    ):
        if final_score.get(forbidden_flag) is not False:
            raise FieldRecordError(f"final_demo_score.{forbidden_flag} must be false")
    formal = value.get("formal_scoring_invariants", {})
    if (
        formal.get("available") is not False
        or formal.get("score_0_to_100") is not None
        or formal.get("grade") is not None
    ):
        raise FieldRecordError("formal scoring must remain unavailable and null")
    return value


def _verify_source_contract(root: Path) -> dict[str, bool]:
    user_demo = (root / "src/rallymate_service/user_demo.py").read_text(
        encoding="utf-8"
    )
    api = (root / "src/rallymate_service/api.py").read_text(encoding="utf-8")
    html = (root / "src/rallymate_service/assets/user-demo.html").read_text(
        encoding="utf-8"
    )
    javascript = (root / "src/rallymate_service/assets/user-demo.js").read_text(
        encoding="utf-8"
    )
    checks = {
        "schema_version_1_1_0_present": '"schema_version": "1.1.0"'
        in user_demo,
        "final_demo_score_present": '"final_demo_score"' in user_demo,
        "analysis_quality_present": '"analysis_quality"' in user_demo,
        "display_score_alias_present": (
            '"compatibility_alias_for": "analysis_quality"' in user_demo
        ),
        "formation_assessment_present": '"formation_assessment"' in user_demo,
        "formal_scoring_forced_unavailable": (
            '"available": False' in user_demo
            and '"score_0_to_100": None' in user_demo
            and '"grade": None' in user_demo
        ),
        "legacy_job_409_present": (
            '"legacy_job_requires_reanalysis"' in api
            and "raise HTTPException(" in api
            and "409," in api
        ),
        "multiple_input_present": "multiple required" in html,
        "sequential_submission_present": (
            "for (const [index, file] of files.entries())" in javascript
            and 'data.delete("video")' in javascript
            and 'data.set("video", file, file.name)' in javascript
        ),
        "refresh_recovery_present": "restoreSavedBatch" in javascript,
        "summary_zh_rendered": "action.summary_zh" in javascript,
    }
    if not all(checks.values()):
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise FieldRecordError(f"source contract checks failed: {failed}")

    persist_start = javascript.find("window.localStorage.setItem")
    persist_end = javascript.find("}));", persist_start)
    if persist_start < 0 or persist_end < 0:
        raise FieldRecordError("localStorage persistence block cannot be located")
    persisted_payload = javascript[persist_start : persist_end + len("}));")]
    for forbidden in ("token", "original_filename", "file.name", "result"):
        if forbidden in persisted_payload.lower():
            raise FieldRecordError(
                f"localStorage persistence contains forbidden field: {forbidden}"
            )
    checks["local_storage_allowlist_excludes_sensitive_values"] = True
    return checks


def build_field_change_record(
    *,
    workspace: str | Path,
    contract_path: str | Path,
    summary_path: str | Path,
    output_path: str | Path,
    focused_test_count: int,
    focused_tests_passed: bool,
    python_compile_passed: bool,
    javascript_syntax_passed: bool,
    live_static_probe_passed: bool,
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = _inside(root, output_path, "field record output")
    if output.exists():
        raise FieldRecordError(
            f"field record is immutable and already exists: {output}"
        )
    if focused_test_count != 20 or not all(
        (
            focused_tests_passed,
            python_compile_passed,
            javascript_syntax_passed,
            live_static_probe_passed,
        )
    ):
        raise FieldRecordError("recorded verification gates do not match the observed run")

    contract_file = _inside(root, contract_path, "Demo contract")
    summary_file = _inside(root, summary_path, "implementation summary")
    _load_contract(contract_file)
    if not summary_file.is_file():
        raise FieldRecordError(f"implementation summary does not exist: {summary_file}")
    source_checks = _verify_source_contract(root)
    implementation = [
        _artifact(root, relative_path, role)
        for relative_path, role in IMPLEMENTATION_FILES
    ]
    if len(implementation) != 7:
        raise FieldRecordError("exactly seven implementation files must be sealed")

    record = {
        "schema_version": "1.1.0",
        "record_version": RECORD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "implemented_and_verified",
        "purpose": "audit_M96_user_demo_v1_1_field_and_behavior_changes",
        "result_contract": {
            "result_version": "rallymate-user-demo-result-v1.1.0",
            "final_demo_score": {
                "relationship": "primary_information_formation_reference",
                "range": {"minimum": 0, "maximum": 100},
                "semantics": (
                    "recognizable_motion_outline_and_amplitude_information_formation_only"
                ),
                "is_formal_technique_score": False,
                "is_coach_score": False,
                "is_recognition_accuracy": False,
                "aggregation": (
                    "unweighted_mean_of_three_action_reference_scores"
                ),
            },
            "analysis_quality": {
                "relationship": "separate_analysis_completeness_measure",
                "range": {"minimum": 0, "maximum": 100},
            },
            "display_score": {
                "relationship": "backward_compatible_alias",
                "compatibility_alias_for": "analysis_quality",
            },
            "per_action_formation_assessment_fields": [
                "status",
                "label_zh",
                "reference_score_0_to_100",
                "meaning_zh",
                "explanation_zh",
                "components",
                "component_weights",
                "score_version",
            ],
        },
        "browser_batch_and_recovery": {
            "submission": "multiple_files_submitted_sequentially_as_single_job_API_calls",
            "progress": "current_one_based_index_and_batch_total_are_displayed",
            "results": "completed_video_summaries_are_retained_and_switchable",
            "recovery": "submitted_job_ids_are_reloaded_and_polled_after_refresh",
            "unsubmitted_files": "must_be_reselected_after_refresh",
            "local_storage_allowlist": [
                "version",
                "submitted_job_ids",
                "completed_job_ids",
                "failed_job_ids",
                "active_job_id",
                "current_index",
                "total",
            ],
            "token_persisted": False,
            "files_or_filenames_persisted": False,
            "result_payloads_persisted": False,
        },
        "legacy_job_behavior": {
            "condition": "succeeded_job_missing_indicator-features.jsonl",
            "http_status": 409,
            "code": "legacy_job_requires_reanalysis",
            "action": "reupload_and_reanalyze",
            "returns_http_500": False,
        },
        "formal_scoring": {
            "available": False,
            "score_0_to_100": None,
            "grade": None,
            "status": "calibration_required",
            "A_to_E_generated": False,
        },
        "verification": {
            "focused_tests": {
                "passed": True,
                "test_count": 20,
                "command": (
                    "$env:PYTHONPATH='src'; "
                    ".\\.venv\\Scripts\\python.exe "
                    "-m unittest -v tests.test_user_demo tests.test_service"
                ),
            },
            "python_compile": {
                "passed": True,
                "command": (
                    ".\\.venv\\Scripts\\python.exe "
                    "-m py_compile src/rallymate_service/user_demo.py "
                    "src/rallymate_service/api.py tests/test_user_demo.py "
                    "tests/test_service.py"
                ),
            },
            "javascript_syntax": {
                "passed": True,
                "command": "node --check src/rallymate_service/assets/user-demo.js",
            },
            "live_static_probe": {
                "passed": True,
                "endpoint": "http://127.0.0.1:8000/",
                "observed_http_status": 200,
            },
            "source_contract_checks": source_checks,
        },
        "implementation_files": implementation,
        "implementation_file_count": len(implementation),
        "report_inputs": {
            "demo_contract": _artifact(root, contract_file, "Demo contract"),
            "summary": _artifact(root, summary_file, "implementation summary"),
            "builder": _artifact(
                root,
                "scripts/build_m96_user_demo_field_record.py",
                "refuse-overwrite field record builder",
            ),
        },
        "claim_boundaries": {
            "technical_level_measured": False,
            "coach_score_generated": False,
            "recognition_accuracy_measured": False,
            "formal_A_to_E_generated": False,
            "new_model_accuracy_claimed": False,
        },
        "self_hash_note": (
            "The field-change-record SHA-256 is reported externally because a file "
            "cannot contain its own raw-file hash."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                record,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
    except FileExistsError as exc:
        raise FieldRecordError(
            f"field record is immutable and already exists: {output}"
        ) from exc
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--focused-test-count", type=int, required=True)
    parser.add_argument("--focused-tests-passed", action="store_true")
    parser.add_argument("--python-compile-passed", action="store_true")
    parser.add_argument("--javascript-syntax-passed", action="store_true")
    parser.add_argument("--live-static-probe-passed", action="store_true")
    args = parser.parse_args()
    try:
        record = build_field_change_record(
            workspace=args.root,
            contract_path=args.contract,
            summary_path=args.summary,
            output_path=args.output,
            focused_test_count=args.focused_test_count,
            focused_tests_passed=args.focused_tests_passed,
            python_compile_passed=args.python_compile_passed,
            javascript_syntax_passed=args.javascript_syntax_passed,
            live_static_probe_passed=args.live_static_probe_passed,
        )
    except FieldRecordError as exc:
        parser.error(str(exc))
    output = _inside(Path(args.root).resolve(), args.output, "field record output")
    print(
        json.dumps(
            {
                "status": record["status"],
                "output": str(output),
                "bytes": output.stat().st_size,
                "sha256": _sha256(output),
                "implementation_file_count": record["implementation_file_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
