#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rallymate_evaluation.m96_pose_pilot import (  # noqa: E402
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
    SAFETY,
    validate_m96_pose_pilot,
)


AUDIT_VERSION = "m96-pose-pilot-audit-v1.0.0"
EXPECTED_PILOT_MANIFEST_RAW_SHA256 = (
    "5995F83285607E64EBA0CC9BBDA137CE46A891938198B9FB8CE270A83DDCAE4C"
)
EXPECTED_TASK_CONTRACT_SHA256 = (
    "1248575A51D469539D2F5CAFD5A6D204BD5FC493C04886D4CDB7FD4432342175"
)
DEFAULT_PILOT = "data/annotations/m96-halpe26-development-pilot-v1"
DEFAULT_OUTPUT = "reports/m96-pose-pilot"
OUTPUT_FILES = ("summary.md", "pilot-contract.json", "field-change-record.json")
IMPLEMENTATION_FILES = (
    ("implementation", "src/rallymate_evaluation/m96_pose_pilot.py"),
    ("script", "scripts/build_m96_pose_pilot.py"),
    ("script", "scripts/build_m96_pose_pilot_adjudication.py"),
    ("script", "scripts/validate_m96_pose_pilot_submission.py"),
    ("script", "scripts/serve_m96_pose_pilot.py"),
    ("script", "scripts/build_m96_pose_pilot_audit.py"),
    ("ui", "src/rallymate_annotation/assets/m96-pose-pilot-workbench.js"),
    ("ui", "src/rallymate_annotation/assets/m96-pose-pilot-workbench.css"),
    ("test", "tests/test_m96_pose_pilot.py"),
)


class M96PilotAuditError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _snapshot(path: Path, *, name: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise M96PilotAuditError(f"{name} must be a regular file: {path}")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise M96PilotAuditError(f"{name} changed while being read: {path}")
    return raw


def _parse_object(raw: bytes, *, name: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise M96PilotAuditError(f"{name} repeats JSON key: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise M96PilotAuditError(f"{name} contains invalid JSON constant: {value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M96PilotAuditError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise M96PilotAuditError(f"{name} must be a JSON object")
    return parsed


def _relative_path(root: Path, path: Path, *, name: str) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise M96PilotAuditError(f"{name} must remain inside the workspace") from exc
    return relative


def _inventory(root: Path, pilot: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category, relative in IMPLEMENTATION_FILES:
        path = root / Path(*relative.split("/"))
        raw = _snapshot(path, name=f"{category} source")
        if relative in seen:
            raise M96PilotAuditError(f"duplicate inventory path: {relative}")
        seen.add(relative)
        records.append(
            {
                "category": category,
                "path": relative,
                "bytes": len(raw),
                "raw_sha256": _sha256(raw),
            }
        )
    for path in sorted(pilot.rglob("*")):
        if not path.is_file():
            continue
        relative = _relative_path(root, path, name="blank pilot file")
        raw = _snapshot(path, name="blank pilot file")
        if relative in seen:
            raise M96PilotAuditError(f"duplicate inventory path: {relative}")
        seen.add(relative)
        records.append(
            {
                "category": "blank_pilot",
                "path": relative,
                "bytes": len(raw),
                "raw_sha256": _sha256(raw),
            }
        )
    return sorted(records, key=lambda item: (item["category"], item["path"]))


def _assert_frozen_pilot(validated: Mapping[str, Any]) -> None:
    manifest = validated["manifest"]
    if validated["manifest_raw_sha256"] != EXPECTED_PILOT_MANIFEST_RAW_SHA256:
        raise M96PilotAuditError("pilot manifest raw SHA-256 does not match the frozen M96 audit target")
    if manifest["task_contract_sha256"] != EXPECTED_TASK_CONTRACT_SHA256:
        raise M96PilotAuditError("task contract SHA-256 does not match the frozen M96 audit target")
    if manifest["scope"] != {
        "video_count": 3,
        "frames_per_video": 8,
        "frame_count": 24,
        "keypoint_format": "halpe26",
        "keypoint_count": 26,
        "joint_task_count": 624,
    }:
        raise M96PilotAuditError("pilot scope is not the frozen 3x8x26 contract")
    report = _parse_object(
        validated["snapshots"]["validation-report.json"],
        name="pilot validation report",
    )
    if (
        report.get("counts", {}).get("human_annotation_rows") != 0
        or report.get("counts", {}).get("accuracy_metrics") != 0
        or manifest.get("safety") != SAFETY
    ):
        raise M96PilotAuditError("pilot is no longer an unlabeled evaluation-only package")


def _pilot_contract(
    *,
    generated_at: str,
    validated: Mapping[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = validated["manifest"]
    roles = validated["roles"]
    protocol = _parse_object(validated["snapshots"]["protocol.json"], name="pilot protocol")
    return {
        "schema_version": "1.0.0",
        "audit_version": AUDIT_VERSION,
        "generated_at": generated_at,
        "status": "blank_pilot_verified_human_annotation_required",
        "pilot_binding": {
            "path": DEFAULT_PILOT,
            "manifest_raw_sha256": EXPECTED_PILOT_MANIFEST_RAW_SHA256,
            "task_contract_sha256": EXPECTED_TASK_CONTRACT_SHA256,
            "pilot_status": manifest["status"],
        },
        "scope": {
            "mode": "evaluation_only",
            "development_videos": 3,
            "frames_per_video": 8,
            "frames": 24,
            "keypoint_format": "halpe26",
            "keypoints_per_frame": 26,
            "joint_tasks": 624,
            "independent_frame_units": 24,
        },
        "outcomes": {
            "human_annotation_rows": 0,
            "adjudication_rows": 0,
            "accuracy": None,
            "accuracy_claim": False,
            "real_human_annotation_complete": False,
        },
        "role_boundaries": {
            "A": {
                "kind": "independent_annotator",
                "bundle_id": roles["A"]["bundle_id"],
                "cannot_view": ["annotator_B_results", "reviewer_C_results", "model_values"],
            },
            "B": {
                "kind": "independent_annotator",
                "bundle_id": roles["B"]["bundle_id"],
                "cannot_view": ["annotator_A_results", "reviewer_C_results", "model_values"],
            },
            "C": {
                "kind": "disagreement_only_adjudicator",
                "entry_generation_gate": "complete_valid_A_and_B_raw_CSV_intake",
                "visible_input": "only_A_B_human_disagreement_values_and_required_video_frames",
                "cannot_view": ["model_values", "sealed_holdout", "repository_other_files"],
                "role_id_must_differ_from_A_and_B": True,
            },
            "role_ids_case_insensitive_non_reuse": True,
        },
        "governance_boundary": {
            "template_path": f"{DEFAULT_PILOT}/governance.template.csv",
            "template_contains_only_known_video_ids_and_REPLACE_WITH_placeholders": True,
            "not_inferred": ["consent", "subject", "session", "split", "usage_scope", "retention_policy"],
            "real_values_present": False,
            "operator_must_supply_real_authorized_values_separately": True,
        },
        "sampling": protocol["sampling"],
        "safety": {
            **SAFETY,
            "training_allowed": False,
            "dataset_export_allowed": False,
            "promotion_allowed": False,
        },
        "source_snapshot": {
            "file_count": len(inventory),
            "inventory_contract_sha256": _sha256(_canonical_bytes(inventory)),
            "files": inventory,
        },
    }


def _field_change_record(
    *,
    generated_at: str,
    pilot_contract_raw_sha256: str,
    inventory_contract_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "record_version": "m96-pose-pilot-field-change-record-v1.0.0",
        "generated_at": generated_at,
        "status": "contract_fields_recorded_no_human_values_added",
        "pilot_binding": {
            "manifest_raw_sha256": EXPECTED_PILOT_MANIFEST_RAW_SHA256,
            "task_contract_sha256": EXPECTED_TASK_CONTRACT_SHA256,
            "pilot_contract_report_raw_sha256": pilot_contract_raw_sha256,
            "source_inventory_contract_sha256": inventory_contract_sha256,
        },
        "protocol_fields": [
            "schema_version",
            "protocol_version",
            "purpose",
            "scope",
            "sampling",
            "target_definition",
            "coordinate_definition",
            "roles",
            "source_bindings",
            "selected_frames",
            "joint_schema",
            "task_contract_sha256",
            "tasks_raw_sha256",
            "safety",
        ],
        "task_fields": [
            "task_id",
            "frame_id",
            "video_id",
            "sample_ordinal",
            "source_frame_index",
            "processed_index",
            "timestamp_ms",
            "frame_width",
            "frame_height",
            "joint_index",
            "joint_name",
            "downstream_joint_id",
            "target_instruction",
        ],
        "annotation_submission_fields": list(ANNOTATION_FIELDS),
        "adjudication_submission_fields": list(ADJUDICATION_FIELDS),
        "governance_template_fields": [
            "video_id",
            "consent_status",
            "consent_record_reference",
            "subject_id",
            "session_id",
            "split_assignment",
            "usage_scope",
            "retention_policy",
        ],
        "gates_added": [
            "A_and_B_complete_exact_624_rows",
            "A_and_B_role_ids_distinct_case_insensitive",
            "raw_CSV_bytes_SHA256_preserved",
            "semantic_submission_revision_SHA256_preserved",
            "C_created_only_after_verified_A_B_intake",
            "C_contains_only_human_disagreements",
            "C_role_id_distinct_from_A_and_B",
            "loopback_exact_file_allowlist",
            "refuse_overwrite_for_pilot_and_C_bundles",
        ],
        "values_deliberately_not_added": {
            "human_annotation_rows": 0,
            "accuracy": None,
            "consent": "REPLACE_WITH placeholder only",
            "subject": "REPLACE_WITH placeholder only",
            "session": "REPLACE_WITH placeholder only",
            "split": "REPLACE_WITH placeholder only",
        },
        "systems_not_connected": {
            "dataset_export": True,
            "training": True,
            "promotion": True,
            "production_default": True,
        },
        "documentation_files_modified_by_this_audit": [],
    }


def _summary(
    *,
    generated_at: str,
    pilot_contract_sha256: str,
    field_change_sha256: str,
    inventory_count: int,
) -> bytes:
    text = f"""# M96 Halpe26 人工评测 pilot 审计摘要

- 审计时间：`{generated_at}`
- 状态：空白 pilot 已验证，仍需真人 A/B 标注；不得声称真人标注完成。
- pilot manifest 原始字节 SHA-256：`{EXPECTED_PILOT_MANIFEST_RAW_SHA256}`
- task contract SHA-256：`{EXPECTED_TASK_CONTRACT_SHA256}`
- 范围：3 个开发视频，每个 8 帧，共 24 帧；每帧 Halpe26 全部 26 点，共 624 个关节点任务。
- 当前人工行数：`0`
- 当前准确率：`null`

## 角色与治理边界

A 与 B 使用不同角色包独立完成，各自不能看到另一方结果或模型值。只有两份完整、绑定正确且角色 ID 不复用的 A/B 原始 CSV 通过原子 intake 后，才能生成 C 入口。C 只看到需要裁决的 A/B 人工分歧和必要视频帧，不看到模型值、密封 holdout 或仓库其他文件。

治理模板只保存已知 video ID 与 `REPLACE_WITH_*` 占位；真实 consent、subject、session、split、usage scope 和 retention policy 均未推断、未填写。

## 安全结论

- `training_allowed=false`
- `dataset_export_allowed=false`
- `promotion_allowed=false`
- `accuracy_claim_generated=false`
- 本审计没有生成标签、准确率、等级、阈值或晋级结论。

## 完整性

- `pilot-contract.json` 原始字节 SHA-256：`{pilot_contract_sha256}`
- `field-change-record.json` 原始字节 SHA-256：`{field_change_sha256}`
- 已保存源码、脚本、UI、测试和空白 pilot 关键文件的 bytes + SHA-256：`{inventory_count}` 个文件。

构建脚本拒绝覆盖既有目录：

```powershell
python scripts/build_m96_pose_pilot_audit.py
python scripts/build_m96_pose_pilot_audit.py --validate-existing
```
"""
    return text.encode("utf-8")


def _expected_output(
    root: Path,
    pilot: Path,
    *,
    generated_at: str,
) -> dict[str, bytes]:
    validated = validate_m96_pose_pilot(root, pilot)
    _assert_frozen_pilot(validated)
    inventory = _inventory(root, pilot)
    contract = _pilot_contract(
        generated_at=generated_at,
        validated=validated,
        inventory=inventory,
    )
    contract_raw = _pretty_bytes(contract)
    changes = _field_change_record(
        generated_at=generated_at,
        pilot_contract_raw_sha256=_sha256(contract_raw),
        inventory_contract_sha256=contract["source_snapshot"]["inventory_contract_sha256"],
    )
    changes_raw = _pretty_bytes(changes)
    summary_raw = _summary(
        generated_at=generated_at,
        pilot_contract_sha256=_sha256(contract_raw),
        field_change_sha256=_sha256(changes_raw),
        inventory_count=len(inventory),
    )
    return {
        "summary.md": summary_raw,
        "pilot-contract.json": contract_raw,
        "field-change-record.json": changes_raw,
    }


def validate_existing(root: Path, pilot: Path, output: Path) -> dict[str, Any]:
    if output.is_symlink() or not output.is_dir():
        raise M96PilotAuditError("audit output must be a regular directory")
    actual_names = {path.name for path in output.iterdir() if path.is_file()}
    if actual_names != set(OUTPUT_FILES) or any(path.is_dir() for path in output.iterdir()):
        raise M96PilotAuditError("audit output tree must contain exactly the three declared files")
    existing = {
        name: _snapshot(output / name, name=f"audit {name}") for name in OUTPUT_FILES
    }
    contract = _parse_object(existing["pilot-contract.json"], name="pilot-contract.json")
    generated_at = contract.get("generated_at")
    if not isinstance(generated_at, str):
        raise M96PilotAuditError("pilot contract generated_at is missing")
    expected = _expected_output(root, pilot, generated_at=generated_at)
    for name in OUTPUT_FILES:
        if existing[name] != expected[name]:
            raise M96PilotAuditError(f"audit file drifted from exact replay: {name}")
    return {
        "status": "valid_immutable_M96_pilot_audit",
        "generated_at": generated_at,
        "files": {
            name: {"bytes": len(existing[name]), "raw_sha256": _sha256(existing[name])}
            for name in OUTPUT_FILES
        },
        "pilot_manifest_raw_sha256": EXPECTED_PILOT_MANIFEST_RAW_SHA256,
        "task_contract_sha256": EXPECTED_TASK_CONTRACT_SHA256,
    }


def build(root: Path, pilot: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise M96PilotAuditError(
            f"refusing to overwrite existing immutable audit output: {output}"
        )
    if not output.parent.is_dir():
        raise M96PilotAuditError(f"audit output parent does not exist: {output.parent}")
    generated_at = _now_utc()
    raw_by_name = _expected_output(root, pilot, generated_at=generated_at)
    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise M96PilotAuditError("unique staging directory unexpectedly exists")
    try:
        staging.mkdir()
        for name, raw in raw_by_name.items():
            with (staging / name).open("xb") as handle:
                handle.write(raw)
        validate_existing(root, pilot, staging)
        for attempt in range(4):
            try:
                staging.rename(output)
                break
            except PermissionError:
                if output.exists() or attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if staging.exists() and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
    return validate_existing(root, pilot, output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate the immutable three-file M96 pose-pilot audit"
    )
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args()
    root = args.workspace.resolve()
    pilot = (args.pilot or (root / DEFAULT_PILOT)).resolve()
    output = (args.output or (root / DEFAULT_OUTPUT)).resolve()
    try:
        result = (
            validate_existing(root, pilot, output)
            if args.validate_existing
            else build(root, pilot, output)
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    except (M96PilotAuditError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
