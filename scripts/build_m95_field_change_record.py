from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "reports"
    / "m95-model-candidate-finetune-readiness"
    / "field-change-record.json"
)
M94_RECORD = ROOT / "reports" / "m94-api-demo-model-shadow" / "field-change-record.json"
M94_RECORD_SHA256 = "8E4D9CDAE0725C1BE64C2C61EC65DFED515F283B28A3726D06EA336F33DFAC77"
M94_DEPLOYMENT_REGISTRY_SHA256 = (
    "A95B7B5F225C025371157C22A9A60397874DE03C85B4EAC512B3B9C443F14C69"
)
M95_SHADOW_REGISTRY_SHA256 = (
    "11B656E536964E8D84415719D82CE5BB5451C4071CDEA295211962323AB2E81B"
)
X_CHECKPOINT_SHA256 = (
    "7FB6E239601082A06CA8442FEB7A9774B8D77A1C37911B2AD0F27C1E84BE4D21"
)
X_CONFIG_SHA256 = (
    "961E5704E4983F27173BC008C17F08E8C2C5907FDB104EA8669AD06C4C68B678"
)
SMOKE_REPORT_SHA256 = (
    "E82F20B47196B5DD09BD0F9C45A926A110165EC6EFAFBFB0B777F696D16DD124"
)
READINESS_REPORT_SHA256 = (
    "397DEC0D11E898C8E61ACA15B94BE6682A1127A30A254071C413D921627A8223"
)

ARTIFACT_PATHS = (
    "README.md",
    "models/rtmpose/deployment-presets.json",
    "models/rtmpose/m95-shadow-candidates.json",
    "models/rtmpose/rtmpose-x_halpe26_384x288.pth",
    "runtime/rtmpose/.venv/Lib/site-packages/mmpose/.mim/configs/body_2d_keypoint/rtmpose/body8/rtmpose-x_8xb256-700e_body8-halpe26-384x288.py",
    "src/rallymate_evaluation/pose_shadow_smoke.py",
    "scripts/run_m95_rtmpose_x_shadow_smoke.py",
    "tests/test_pose_shadow_smoke.py",
    "reports/m95-rtmpose-x-shadow/smoke-report.json",
    "src/rallymate_training/pose_finetune_readiness.py",
    "scripts/audit_pose_finetune_readiness.py",
    "tests/test_pose_finetune_readiness.py",
    "reports/m95-pose-finetune-readiness/readiness.json",
    "src/rallymate_training/mmpose_dataset.py",
    "scripts/export_mmpose_halpe26_dataset.py",
    "tests/test_mmpose_dataset.py",
    "training/configs/rtmpose_x_halpe26_384x288_rallymate.py",
    "src/rallymate_training/mmpose_finetune.py",
    "scripts/run_rtmpose_finetune.py",
    "tests/test_mmpose_finetune.py",
    "docs/RTMPOSE_X_AND_FINETUNE_READINESS_M95.md",
    "docs/RallyMate产品说明书_当前态_v1.0.md",
    "docs/RallyMate技术架构与实现说明书_当前态_v1.0.md",
    "docs/RallyMate接口与端到端链路_当前态_v1.0.md",
    "docs/RallyMate当前项目文档导航.md",
    "docs/RallyMate推理评分系统技术设计与维护手册_v1.0.md",
    "docs/diagrams/RallyMate完整系统架构_当前态_v1.0.drawio",
    "scripts/build_current_product_technical_docs.ps1",
    "reports/rallymate-current-docs/index.html",
    "reports/rallymate-current-docs/product.html",
    "reports/rallymate-current-docs/technical.html",
    "reports/rallymate-current-docs/api.html",
    "reports/rallymate-current-docs/RallyMate完整系统架构_当前态_v1.0.drawio",
    "scripts/build_m95_field_change_record.py",
)


class M95RecordError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise M95RecordError(f"expected JSON object: {path}")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise M95RecordError(message)


def _document_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if match is None:
        raise M95RecordError(f"cannot find document version: {path}")
    return match.group(1)


def _drawio_validation(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    pages = root.findall("diagram")
    dangling: list[str] = []
    for page in pages:
        cells = page.findall("./mxGraphModel/root/mxCell")
        ids = {cell.get("id") for cell in cells if cell.get("id")}
        for cell in cells:
            for attribute in ("parent", "source", "target"):
                target = cell.get(attribute)
                if target and target not in ids:
                    dangling.append(
                        f"{page.get('name')}:{cell.get('id')}:{attribute}={target}"
                    )
    return {
        "xml_parse_passed": True,
        "page_count": len(pages),
        "page_names": [str(page.get("name")) for page in pages],
        "dangling_reference_count": len(dangling),
        "dangling_references": dangling,
    }


def _artifact(path_text: str) -> dict[str, Any]:
    path = ROOT / path_text
    _require(path.is_file(), f"missing artifact: {path_text}")
    return {
        "path": path_text,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the exact M95 field/change and verification record."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--focused-test-count", type=int, required=True)
    parser.add_argument("--focused-test-seconds", type=float, required=True)
    parser.add_argument("--focused-wall-seconds", type=float, required=True)
    parser.add_argument("--independent-focused-test-count", type=int, required=True)
    parser.add_argument("--independent-focused-test-seconds", type=float, required=True)
    parser.add_argument("--independent-focused-wall-seconds", type=float, required=True)
    parser.add_argument("--full-test-count", type=int, required=True)
    parser.add_argument("--full-test-seconds", type=float, required=True)
    parser.add_argument("--full-tests-passed", action="store_true")
    parser.add_argument("--python-compile-passed", action="store_true")
    parser.add_argument("--document-build-passed", action="store_true")
    parser.add_argument("--independent-review-passed", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = args.output.resolve()
    _require(not output.exists(), f"refusing to overwrite record: {output}")
    _require(args.focused_test_count > 0, "focused test count must be positive")
    _require(args.focused_test_seconds >= 0, "focused test seconds must be non-negative")
    _require(args.focused_wall_seconds >= 0, "focused wall seconds must be non-negative")
    _require(
        args.independent_focused_test_count == args.focused_test_count,
        "independent focused test count must match the primary run",
    )
    _require(
        args.independent_focused_test_seconds >= 0,
        "independent focused test seconds must be non-negative",
    )
    _require(
        args.independent_focused_wall_seconds >= 0,
        "independent focused wall seconds must be non-negative",
    )
    _require(args.full_test_count > 0, "full test count must be positive")
    _require(args.full_test_seconds >= 0, "full test seconds must be non-negative")
    _require(args.full_tests_passed, "full test suite must pass")
    _require(args.python_compile_passed, "Python compile must pass")
    _require(args.document_build_passed, "document build must pass")
    _require(args.independent_review_passed, "independent review must pass")

    deployment_path = ROOT / "models" / "rtmpose" / "deployment-presets.json"
    registry_path = ROOT / "models" / "rtmpose" / "m95-shadow-candidates.json"
    checkpoint_path = ROOT / "models" / "rtmpose" / "rtmpose-x_halpe26_384x288.pth"
    config_path = (
        ROOT
        / "runtime"
        / "rtmpose"
        / ".venv"
        / "Lib"
        / "site-packages"
        / "mmpose"
        / ".mim"
        / "configs"
        / "body_2d_keypoint"
        / "rtmpose"
        / "body8"
        / "rtmpose-x_8xb256-700e_body8-halpe26-384x288.py"
    )
    smoke_path = ROOT / "reports" / "m95-rtmpose-x-shadow" / "smoke-report.json"
    readiness_path = (
        ROOT / "reports" / "m95-pose-finetune-readiness" / "readiness.json"
    )

    _require(_sha256(M94_RECORD) == M94_RECORD_SHA256, "M94 record changed")
    _require(
        _sha256(deployment_path) == M94_DEPLOYMENT_REGISTRY_SHA256,
        "M94 deployment registry changed",
    )
    _require(_sha256(registry_path) == M95_SHADOW_REGISTRY_SHA256, "M95 registry drift")
    _require(_sha256(checkpoint_path) == X_CHECKPOINT_SHA256, "X checkpoint drift")
    _require(checkpoint_path.stat().st_size == 200_397_852, "X checkpoint size drift")
    _require(_sha256(config_path) == X_CONFIG_SHA256, "X base config drift")
    _require(_sha256(smoke_path) == SMOKE_REPORT_SHA256, "X smoke report drift")
    _require(
        _sha256(readiness_path) == READINESS_REPORT_SHA256,
        "fine-tune readiness report drift",
    )

    m94_record = _load_json(M94_RECORD)
    registry = _load_json(registry_path)
    smoke = _load_json(smoke_path)
    readiness = _load_json(readiness_path)
    candidates = registry.get("candidates")
    _require(isinstance(candidates, list) and len(candidates) == 1, "candidate count drift")
    candidate = candidates[0]
    _require(
        candidate.get("candidate_id") == "rtmpose-x-halpe26-384x288-m95-shadow",
        "candidate id drift",
    )
    _require(
        registry.get("production_default_binding", {}).get("preset_id")
        == "rtmpose-m-halpe26-online",
        "production default drift",
    )
    _require(smoke.get("status") == "smoke_passed_ground_truth_required", "smoke status drift")
    _require(
        smoke.get("registry", {}).get("sha256") == M95_SHADOW_REGISTRY_SHA256,
        "smoke registry binding drift",
    )
    _require(
        smoke.get("protocol", {}).get("ground_truth_used") is False,
        "smoke unexpectedly used ground truth",
    )
    for field in (
        "RallyMate_accuracy_improved",
        "candidate_promoted",
        "production_default_changed",
        "ground_truth_accuracy_measured",
        "F3_or_F4_promoted",
        "grade_or_threshold_generated",
    ):
        _require(smoke.get("claims", {}).get(field) is False, f"unsafe smoke claim: {field}")
    _require(
        smoke.get("protocol", {}).get("sealed_holdout_opened") is False,
        "sealed holdout was opened",
    )
    _require(readiness.get("report_version") == "pose-finetune-readiness-v1.2.0", "readiness version drift")
    _require(readiness.get("status") == "annotation_required", "unexpected readiness status")
    _require(readiness.get("accuracy_claim") is False, "readiness accuracy claim drift")
    _require(readiness.get("counts", {}).get("accepted_frames") == 0, "accepted frame count is not zero")
    _require(readiness.get("counts", {}).get("accepted_joint_values") == 0, "accepted joint count is not zero")
    _require(readiness.get("readiness", {}).get("dataset_export_allowed") is False, "dataset export unexpectedly allowed")
    _require(readiness.get("training_started") is False, "training unexpectedly started")
    _require(
        readiness.get("sealed_holdout_guard", {}).get("registry_sha256")
        == M95_SHADOW_REGISTRY_SHA256,
        "readiness holdout registry binding drift",
    )
    _require(
        readiness.get("sealed_holdout_guard", {}).get("use_during_development")
        is False,
        "readiness allows sealed holdout during development",
    )

    product_version = _document_version(
        ROOT / "docs" / "RallyMate产品说明书_当前态_v1.0.md",
        r"文档版本：([^\s]+)",
    )
    technical_version = _document_version(
        ROOT / "docs" / "RallyMate技术架构与实现说明书_当前态_v1.0.md",
        r"文档版本：([^\s]+)",
    )
    api_version = _document_version(
        ROOT / "docs" / "RallyMate接口与端到端链路_当前态_v1.0.md",
        r"文档修订：([^\s]+)",
    )
    _require(product_version == "1.5.4", "product document version drift")
    _require(technical_version == "1.5.5", "technical document version drift")
    _require(api_version == "1.0.5", "API document version drift")

    drawio_path = ROOT / "docs" / "diagrams" / "RallyMate完整系统架构_当前态_v1.0.drawio"
    drawio = _drawio_validation(drawio_path)
    _require(drawio["page_count"] == 5, "Draw.io page count drift")
    _require(drawio["dangling_reference_count"] == 0, "Draw.io has dangling references")
    center_drawio = ROOT / "reports" / "rallymate-current-docs" / drawio_path.name
    _require(_sha256(center_drawio) == _sha256(drawio_path), "document-center Draw.io copy drift")

    artifacts = [_artifact(path) for path in ARTIFACT_PATHS]
    smoke_totals = smoke["totals"]
    held = readiness["sealed_holdout_guard"]
    record: dict[str, Any] = {
        "schema_version": "1.0.0",
        "record_version": "m95-model-candidate-finetune-readiness-v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "milestone": "M95",
        "snapshot_semantics": (
            "This record identifies the exact M95 RTMPose-X offline shadow candidate, "
            "local GPU smoke, fail-closed human-keypoint readiness audit, Halpe26 COCO "
            "exporter, gated MMEngine training adapter, living documentation, editable "
            "Draw.io, and verification bytes. It preserves the M94 deployment registry "
            "and default M256 preset. It does not create human labels, claim RallyMate "
            "accuracy, open the sealed holdout, export a real training dataset, start "
            "training, create a trained checkpoint, promote a model or indicator, or "
            "authorize formal A-E scoring."
        ),
        "model_candidate": {
            "shadow_registry_version": registry["registry_version"],
            "shadow_registry_sha256": M95_SHADOW_REGISTRY_SHA256,
            "candidate_id": candidate["candidate_id"],
            "role": candidate["role"],
            "native_keypoint_format": candidate["native_keypoint_format"],
            "input_size_hw": candidate["input_size_hw"],
            "checkpoint_path": candidate["model_relative_path"],
            "checkpoint_bytes": checkpoint_path.stat().st_size,
            "checkpoint_sha256": X_CHECKPOINT_SHA256,
            "config_path": candidate["config_runtime_relative_path"],
            "config_sha256": X_CONFIG_SHA256,
            "official_body8_metrics": candidate["official_body8_metrics"],
            "registry_snapshot_semantics": "immutable_pre_smoke_input_and_protocol",
            "registry_candidate_status_at_registration": candidate["promotion_status"],
            "current_smoke_status": smoke["status"],
            "post_smoke_status_authority": "reports/m95-rtmpose-x-shadow/smoke-report.json",
            "production_default_before": "rtmpose-m-halpe26-online",
            "production_default_after": "rtmpose-m-halpe26-online",
            "added_to_http_deployment_presets": False,
            "automatically_selected": False,
            "candidate_promoted": False,
        },
        "local_gpu_smoke": {
            "report_version": smoke["report_version"],
            "report_sha256": SMOKE_REPORT_SHA256,
            "status": smoke["status"],
            "device": smoke["environment"]["device"],
            "development_video_count": len(smoke["inputs"]),
            "sample_calls": smoke_totals["sample_calls"],
            "sample_rois": smoke_totals["sample_rois"],
            "sample_poses": smoke_totals["sample_poses"],
            "pose_return_rate": smoke_totals["pose_return_rate"],
            "latency_ms": smoke_totals["latency_ms"],
            "latency_per_roi_ms": smoke_totals["latency_per_roi_ms"],
            "cuda_peak_allocated_bytes": smoke_totals["cuda_peak_allocated_bytes"],
            "cuda_peak_reserved_bytes": smoke_totals["cuda_peak_reserved_bytes"],
            "halpe26_shape_and_finite_all": all(
                bool(call["halpe26_shape_and_finite"]) for call in smoke["calls"]
            ),
            "ground_truth_used": False,
            "latency_is_comparative_benchmark": False,
            "RallyMate_accuracy_improved": False,
            "sealed_holdout_opened": False,
        },
        "finetune_readiness": {
            "report_path": "reports/m95-pose-finetune-readiness/readiness.json",
            "report_sha256": READINESS_REPORT_SHA256,
            "report_version": readiness["report_version"],
            "status": readiness["status"],
            "input_fingerprint_sha256": readiness["inputs"]["input_fingerprint_sha256"],
            "truth_pack_count": readiness["counts"]["pack_count"],
            "truth_task_count": sum(pack["counts"]["tasks"] for pack in readiness["packs"]),
            "accepted_human_keypoint_frames": readiness["counts"]["accepted_frames"],
            "accepted_human_keypoint_joint_values": readiness["counts"]["accepted_joint_values"],
            "governance_csv_provided": readiness["governance"]["provided"],
            "dataset_export_allowed": readiness["readiness"]["dataset_export_allowed"],
            "training_started": readiness["training_started"],
            "topology": readiness["topology"],
            "sealed_holdout_guard": {
                "registry_version": held["registry_version"],
                "registry_sha256": held["registry_sha256"],
                "video_id": held["video_id"],
                "video_sha256": held["video_sha256"],
                "use_during_development": held["use_during_development"],
                "opened_by_M95": False,
            },
        },
        "dataset_export_contract": {
            "manifest_version": "rallymate-mmpose-halpe26-dataset-v1.0.0",
            "output_status_if_ready": "ready_for_mmpose_training",
            "current_real_dataset_exported": False,
            "train_and_val_required_nonempty": True,
            "test_split_allowed_during_development": False,
            "governance_split_wire_values": ["train", "val"],
            "governance_split_case_sensitive": True,
            "train_split_all_26_joints_visible_supervision_required": True,
            "compiled_validation_status_required": "ready_for_keypoint_error_evaluation",
            "each_train_val_frame_positive_visible_keypoint_required": True,
            "visible_normalized_coordinate_interval": "[0,1)",
            "visible_pixel_coordinate_bounds": "0<=x<width and 0<=y<height",
            "visible_keypoint_must_be_inside_task_bbox": True,
            "source_path_and_sha_unique_across_all_video_ids": True,
            "cross_truth_pack_same_video_frame_allowed": False,
            "split_isolation_dimensions": [
                "video_id",
                "source_resolved_path",
                "source_video_sha256",
                "subject_id",
                "session_id",
                "extracted_jpeg_raw_sha256",
                "decoded_mmpose_imread_color_pixel_sha256",
            ],
            "sealed_holdout_rejection_dimensions": ["video_id", "resolved_path", "sha256"],
            "keypoint_state_wire_values": [
                "adjudicated_visible_coordinate",
                "adjudicated_invisible_no_coordinate",
                "not_annotated",
            ],
            "keypoint_state_semantic_groups": {
                "visible": "adjudicated_visible_coordinate",
                "invisible": "adjudicated_invisible_no_coordinate",
                "not_annotated": "not_annotated",
            },
            "visible_coco_value": 2,
            "invisible_or_not_annotated_coco_value": 0,
            "bbox_ground_truth_claim": False,
            "overwrite_allowed": False,
            "second_readiness_fingerprint_check": True,
        },
        "training_adapter_contract": {
            "plan_version": "rallymate-rtmpose-finetune-plan-v1.0.0",
            "default_mode": "dry_run",
            "explicit_execute_required": True,
            "fixed_model": "RTMPose-X Halpe26 384x288",
            "runner": "mmengine.Runner",
            "current_real_run_created": False,
            "training_started": False,
            "new_trained_checkpoint_created": False,
            "accuracy_claim": False,
            "promotion_claim": False,
            "lifecycle_statuses": [
                "execution_blocked",
                "dry_run_validated",
                "dry_run_blocked",
                "validated_ready_to_start",
                "runner_initialized",
                "training_running",
                "training_completed",
                "runner_initialization_interrupted",
                "runner_initialization_failed",
                "runner_start_interrupted",
                "runner_start_failed",
                "training_interrupted",
                "training_failed",
            ],
            "training_started_semantics": "true only after the before_train hook is entered",
        },
        "documentation": {
            "product_document_version": product_version,
            "technical_document_version": technical_version,
            "api_document_version": api_version,
            "documentation_date": "2026-09-04",
            "m95_special_document_created": True,
            "drawio": drawio,
            "rendered_html": ["product.html", "technical.html", "api.html"],
            "document_center_updated": True,
        },
        "field_changes": [
            {
                "area": "offline pose candidate",
                "fields": [
                    "m95-shadow-candidates.registry_version",
                    "production_default_binding path/version/SHA",
                    "development video path/SHA/frozen-frame SHA",
                    "sealed holdout id/path/SHA/use_during_development",
                    "candidate id/role/checkpoint/config/runtime/topology/input size",
                    "official model-zoo background metrics and license status",
                ],
            },
            {
                "area": "local GPU smoke",
                "fields": [
                    "exact input/model/config/registry bindings",
                    "sample frame/ROI/pose counts",
                    "Halpe26 shape and finite-value result",
                    "latency and CUDA memory observations",
                    "accuracy/promotion/default/holdout negative claims",
                ],
            },
            {
                "area": "fine-tune readiness",
                "fields": [
                    "two annotators plus independent adjudicator lineage",
                    "compiled/source/task/topology bindings",
                    "seven governance fields",
                    "train-only Halpe26 visible supervision",
                    "video/path/SHA/subject/session split isolation",
                    "sealed holdout registry and ID/path/SHA deny binding",
                ],
            },
            {
                "area": "MMPose dataset export",
                "fields": [
                    "immutable COCO train/val and JPEG extraction",
                    "adjudicated_visible_coordinate/adjudicated_invisible_no_coordinate/not_annotated wire values",
                    "image/artifact SHA and byte manifests",
                    "cross-split JPEG raw-byte and decoded-pixel isolation",
                    "second readiness fingerprint replay",
                    "no test split, overwrite, path escape, duplicate source or holdout",
                ],
            },
            {
                "area": "RTMPose-X training adapter",
                "fields": [
                    "fixed checkpoint/base-config/template bindings",
                    "resolved MMPose config and real dataset construction audit",
                    "dry-run default and explicit execute",
                    "Runner lifecycle status and training_started semantics",
                    "no automatic accuracy, promotion or production claim",
                ],
            },
            {
                "area": "living documentation",
                "fields": [
                    "product Markdown/HTML 1.5.4",
                    "technical Markdown/HTML 1.5.5",
                    "API Markdown/HTML 1.0.5",
                    "M95 detailed model/readiness document",
                    "five-page editable Draw.io and document-center copy",
                    "navigation, README and maintenance record",
                ],
            },
        ],
        "verification": {
            "focused_test_command": (
                "$env:PYTHONPATH='src'; "
                ".\\.venv\\Scripts\\python.exe -m unittest "
                "tests.test_pose_shadow_smoke tests.test_pose_finetune_readiness "
                "tests.test_mmpose_dataset tests.test_mmpose_finetune tests.test_pose_presets"
            ),
            "focused_test_count": args.focused_test_count,
            "focused_unittest_seconds": args.focused_test_seconds,
            "focused_process_wall_seconds": args.focused_wall_seconds,
            "focused_tests_passed": True,
            "independent_focused_test_count": args.independent_focused_test_count,
            "independent_focused_unittest_seconds": args.independent_focused_test_seconds,
            "independent_focused_process_wall_seconds": args.independent_focused_wall_seconds,
            "independent_focused_tests_passed": True,
            "full_test_command": "scripts/run_tests.ps1",
            "full_test_count": args.full_test_count,
            "full_test_seconds": args.full_test_seconds,
            "full_tests_passed": True,
            "full_test_failures_or_errors": 0,
            "python_compile_passed": True,
            "document_build_passed": True,
            "independent_review_passed": True,
            "drawio_xml_parse_passed": True,
            "drawio_page_count": drawio["page_count"],
            "drawio_dangling_reference_count": drawio["dangling_reference_count"],
            "field_change_artifact_count": len(artifacts),
            "field_change_artifact_hash_mismatch_count": 0,
        },
        "unchanged_frozen_authority": {
            "m94_field_record_path": "reports/m94-api-demo-model-shadow/field-change-record.json",
            "m94_field_record_sha256": M94_RECORD_SHA256,
            "m94_record_version": m94_record["record_version"],
            "m94_deployment_registry_sha256": M94_DEPLOYMENT_REGISTRY_SHA256,
            **m94_record["unchanged_frozen_authority"],
            "m94_record_rewritten_by_M95": False,
            "m94_deployment_registry_rewritten_by_M95": False,
        },
        "product_state": {
            "registered_pose_only_indicators": 13,
            "maturity": "F2",
            "formal_A_to_E_enabled": False,
            "formal_A_to_E_created": 0,
            "calibration_authorized": False,
            "promotion_authorized": False,
            "production_scoring_enabled": False,
        },
        "artifacts": artifacts,
        "safety_assertions": {
            "human_labels_created_by_M95": 0,
            "model_predictions_used_as_training_truth": False,
            "sealed_holdout_opened_by_M95": False,
            "real_training_dataset_exported": False,
            "training_started": False,
            "new_trained_checkpoint_created": False,
            "unverified_accuracy_claimed": False,
            "default_pose_changed": False,
            "F3_or_F4_claimed": False,
            "formal_A_to_E_created": 0,
            "calibration_authorized": False,
            "promotion_authorized": False,
            "production_scoring_authorized": False,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "created",
                "path": str(output),
                "bytes": output.stat().st_size,
                "sha256": _sha256(output),
                "artifact_count": len(artifacts),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
