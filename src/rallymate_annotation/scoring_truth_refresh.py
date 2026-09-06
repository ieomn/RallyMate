from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rallymate_annotation.scoring_action_readiness import (
    SATISFIED,
    build_scoring_truth_action_readiness,
    validate_scoring_truth_action_readiness,
)
from rallymate_annotation.scoring_action_worklist import (
    validate_scoring_truth_action_worklist,
)
from rallymate_annotation.scoring_evidence_plan import (
    build_scoring_truth_evidence_plan,
    validate_scoring_truth_evidence_plan_sources,
    write_scoring_truth_evidence_plan,
)
from rallymate_annotation.truth_pack import compile_truth_pack
from rallymate_evaluation.pose_diagnostic_truth import (
    evaluate_pose_diagnostic_truth,
    validate_pose_diagnostic_evaluation,
    validate_pose_diagnostic_truth_pack_sources,
)
from rallymate_evaluation.scoring_truth import build_scoring_truth_evaluation


SCHEMA_VERSION = "1.0.0"
REFRESH_VERSION = "scoring-truth-evidence-refresh-v1.0.0"
LATEST_VERSION = "scoring-truth-evidence-refresh-latest-v1.0.0"
SOURCE_CSV_FILES = (
    "event-annotations.csv",
    "keypoint-annotations.csv",
    "semantic-annotations.csv",
    "coach-labels.csv",
    "full-video-review-completion.csv",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def _preflight_truth_pack(pack_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="rallymate-truth-preflight-") as directory:
        staged_pack = Path(directory) / "pack"
        shutil.copytree(pack_dir, staged_pack)
        report = compile_truth_pack(staged_pack)
        if report.get("errors"):
            raise ValueError(
                "truth-pack preflight failed: " + "; ".join(report["errors"][:10])
            )


def _validated_compiled_truth_files(
    source: Path,
) -> tuple[dict[str, Any], set[str]]:
    source_report = _read_json(source / "validation-report.json")
    by_video = source_report.get("outputs", {}).get("by_video")
    if not isinstance(by_video, Mapping):
        raise ValueError("truth-pack validation report lacks by-video outputs")
    global_names = (
        "validation-report.json",
        "manual-events.jsonl",
        "manual-keypoints.jsonl",
        "manual-semantics.jsonl",
        "coach-labels.jsonl",
    )
    per_video_names = (
        "manual-events.jsonl",
        "manual-keypoints.jsonl",
        "manual-semantics.jsonl",
        "coach-labels.jsonl",
    )
    expected_files = set(global_names)
    for video_id in sorted(by_video):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", str(video_id)):
            raise ValueError("truth-pack validation report has an unsafe video_id")
        expected_files.update(
            f"by-video/{video_id}/{name}" for name in per_video_names
        )
    actual_files: set[str] = set()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("compiled truth must not contain symbolic links")
        if path.is_file():
            actual_files.add(path.relative_to(source).as_posix())
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ValueError(
            f"compiled truth topology mismatch: missing={missing}, extra={extra}"
        )
    return source_report, expected_files


def _snapshot_compiled_truth(
    *, pack_dir: Path, output_dir: Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    source = pack_dir / "compiled"
    _, expected_files = _validated_compiled_truth_files(source)
    if output_dir.exists():
        raise ValueError("compiled truth snapshot output already exists")
    output_dir.mkdir(parents=True)
    for relative in sorted(expected_files):
        destination = output_dir / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / Path(relative)).read_bytes())
    validation_path = output_dir / "validation-report.json"
    report = _read_json(validation_path)
    global_paths = {
        "manual_events": output_dir / "manual-events.jsonl",
        "manual_keypoints": output_dir / "manual-keypoints.jsonl",
        "manual_semantics": output_dir / "manual-semantics.jsonl",
        "coach_labels": output_dir / "coach-labels.jsonl",
    }
    report["outputs"].update(
        {name: str(path.resolve()) for name, path in global_paths.items()}
    )
    for video_id, outputs in report["outputs"]["by_video"].items():
        video_dir = output_dir / "by-video" / video_id
        outputs.update(
            {
                "manual_events": str((video_dir / "manual-events.jsonl").resolve()),
                "manual_keypoints": str(
                    (video_dir / "manual-keypoints.jsonl").resolve()
                ),
                "manual_semantics": str(
                    (video_dir / "manual-semantics.jsonl").resolve()
                ),
                "coach_labels": str((video_dir / "coach-labels.jsonl").resolve()),
            }
        )
    _write_json(validation_path, report)
    return report, {"validation": validation_path, **global_paths}


def _snapshot_pose_truth(
    *, manifest_path: Path, output_dir: Path
) -> tuple[dict[str, Any], Path, Path, Path]:
    manifest = _read_json(manifest_path)
    validate_pose_diagnostic_truth_pack_sources(manifest)
    source_coverage = Path(manifest["annotation_files"]["coverage"]["path"])
    source_positives = Path(manifest["annotation_files"]["positives"]["path"])
    output_dir.mkdir(parents=True, exist_ok=True)
    coverage = output_dir / "pose-diagnostic-coverage.csv"
    positives = output_dir / "pose-diagnostic-positives.csv"
    shutil.copy2(source_coverage, coverage)
    shutil.copy2(source_positives, positives)
    manifest = copy.deepcopy(manifest)
    manifest["annotation_files"]["coverage"]["path"] = str(coverage.resolve())
    manifest["annotation_files"]["positives"]["path"] = str(positives.resolve())
    snapshot_manifest = output_dir / "manifest.json"
    _write_json(snapshot_manifest, manifest)
    return manifest, snapshot_manifest, coverage, positives


def _snapshot_worklist_inputs(
    *,
    worklist_path: Path,
    registry_path: Path,
    reference_context_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], Path, Path, Path]:
    worklist = _read_json(worklist_path)
    validate_scoring_truth_action_worklist(worklist)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_snapshot = output_dir / "metric-feasibility-pose-wave-v2.json"
    reference_snapshot = output_dir / "scoring-reference-context.json"
    shutil.copy2(registry_path, registry_snapshot)
    shutil.copy2(reference_context_path, reference_snapshot)
    worklist = copy.deepcopy(worklist)
    registry_source = worklist["source"]["feasibility_registry"]
    if str(registry_source["sha256"]).upper() != sha256_file(registry_path):
        raise ValueError("worklist does not bind the supplied registry")
    registry_source["path"] = str(registry_snapshot.resolve())
    registry_source["sha256"] = sha256_file(registry_snapshot)
    context_source = worklist["source"]["scoring_reference_context"]
    if str(context_source["sha256"]).upper() != sha256_file(reference_context_path):
        raise ValueError("worklist does not bind the supplied reference context")
    context_source["path"] = str(reference_snapshot.resolve())
    context_source["sha256"] = sha256_file(reference_snapshot)
    snapshot_path = output_dir / "worklist.json"
    _write_json(snapshot_path, worklist)
    validate_scoring_truth_action_worklist(worklist)
    return worklist, snapshot_path, registry_snapshot, reference_snapshot


def _render_index(manifest: Mapping[str, Any], *, output_path: Path) -> None:
    states = manifest["states"]
    counts = manifest["counts"]
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RallyMate M47 真值刷新</title><style>body{{font:16px/1.6 system-ui;max-width:1050px;margin:28px auto;padding:0 18px;color:#172033}}code{{background:#eef2f7;padding:2px 5px}}.warn{{background:#fff3cd;border-left:4px solid #b7791f;padding:12px}}li{{margin:8px 0}}</style></head><body><h1>M47 真值证据刷新</h1><p class="warn">本页只发布 hash-bound 真值与 readiness 快照，不生成等级、阈值或成熟度晋级。没有教练标定时最高仍为 calibration_required。</p><p>刷新 ID：<code>{html.escape(str(manifest['refresh_id']))}</code></p><ul><li>truth pack：<code>{html.escape(str(states['truth_pack']))}</code></li><li>事件/特征评测：<code>{html.escape(str(states['scoring_truth_evaluation']))}</code></li><li>Pose 诊断评测：<code>{html.escape(str(states['pose_diagnostic_evaluation']))}</code></li><li>M45：<code>{html.escape(str(states['action_readiness']))}</code>，满足 {counts['satisfied_work_items']}/{counts['work_items']} work items</li><li>M46：<code>{html.escape(str(states['evidence_plan']))}</code>，满足 {counts['satisfied_evidence_units']}/{counts['evidence_units']} evidence units</li></ul><p><a href="refresh-manifest.json">刷新 manifest</a> · <a href="scoring-truth-evaluation.json">事件/特征误差</a> · <a href="pose-diagnostic-evaluation.json">Pose 诊断误差</a> · <a href="action-readiness.json">M45 readiness</a> · <a href="evidence-plan/index.html">M46 共享证据计划</a></p></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def build_scoring_truth_refresh(
    *,
    pack_dir: Path,
    frames_path: Path,
    primary_timeline_path: Path,
    predicted_events_path: Path,
    registry_path: Path,
    worklist_path: Path,
    pose_truth_manifest_path: Path,
    reference_context_path: Path,
    output_root: Path,
    refresh_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    supplied = {
        "pack_dir": pack_dir,
        "frames": frames_path,
        "primary_timeline": primary_timeline_path,
        "predicted_events": predicted_events_path,
        "registry": registry_path,
        "worklist": worklist_path,
        "pose_truth_manifest": pose_truth_manifest_path,
        "reference_context": reference_context_path,
    }
    pack_dir = pack_dir.resolve()
    for name, path in supplied.items():
        resolved = path.resolve()
        expected = resolved.is_dir() if name == "pack_dir" else resolved.is_file()
        if not expected:
            raise ValueError(f"truth refresh input is missing: {name}={resolved}")
    frames_path = frames_path.resolve()
    primary_timeline_path = primary_timeline_path.resolve()
    predicted_events_path = predicted_events_path.resolve()
    registry_path = registry_path.resolve()
    worklist_path = worklist_path.resolve()
    pose_truth_manifest_path = pose_truth_manifest_path.resolve()
    reference_context_path = reference_context_path.resolve()
    output_root = output_root.resolve()

    pose_source_manifest = _read_json(pose_truth_manifest_path)
    validate_pose_diagnostic_truth_pack_sources(pose_source_manifest)
    pose_coverage_source = Path(
        str(pose_source_manifest["annotation_files"]["coverage"]["path"])
    ).resolve()
    pose_positives_source = Path(
        str(pose_source_manifest["annotation_files"]["positives"]["path"])
    ).resolve()
    for name, path in (
        ("pose_truth_coverage", pose_coverage_source),
        ("pose_truth_positives", pose_positives_source),
    ):
        if not path.is_file():
            raise ValueError(f"truth refresh input is missing: {name}={path}")

    raw_sources = [pack_dir / "manifest.json", *(pack_dir / name for name in SOURCE_CSV_FILES)]
    for path in raw_sources:
        if not path.is_file():
            raise ValueError(f"truth-pack source is missing: {path}")
    source_fingerprint = canonical_hash(
        {
            "truth_pack": {path.name: sha256_file(path) for path in raw_sources},
            "inputs": {
                name: sha256_file(path.resolve())
                for name, path in supplied.items()
                if name != "pack_dir"
            }
            | {
                "pose_truth_coverage": sha256_file(pose_coverage_source),
                "pose_truth_positives": sha256_file(pose_positives_source),
            },
        }
    )
    now = generated_at or datetime.now(timezone.utc).isoformat()
    if refresh_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        refresh_id = f"refresh-{stamp}-{source_fingerprint[:12].lower()}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", refresh_id):
        raise ValueError("refresh_id contains unsupported characters")

    _preflight_truth_pack(pack_dir)
    canonical_validation = compile_truth_pack(pack_dir)
    if canonical_validation.get("errors"):
        raise ValueError("truth-pack compilation produced validation errors")
    _validated_compiled_truth_files(pack_dir / "compiled")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = (output_root / refresh_id).resolve()
    if run_dir.parent != output_root or run_dir.exists():
        raise ValueError("truth refresh output already exists or escapes output_root")
    run_dir.mkdir()
    marker = run_dir / ".inprogress.json"
    _write_json(
        marker,
        {
            "refresh_id": refresh_id,
            "generated_at": now,
            "source_fingerprint_sha256": source_fingerprint,
        },
    )
    try:
        truth_validation, compiled = _snapshot_compiled_truth(
            pack_dir=pack_dir, output_dir=run_dir / "compiled-truth"
        )
        worklist, worklist_snapshot, registry_snapshot, reference_snapshot = (
            _snapshot_worklist_inputs(
                worklist_path=worklist_path,
                registry_path=registry_path,
                reference_context_path=reference_context_path,
                output_dir=run_dir / "sources",
            )
        )
        pose_manifest, pose_manifest_snapshot, coverage, positives = (
            _snapshot_pose_truth(
                manifest_path=pose_truth_manifest_path,
                output_dir=run_dir / "pose-truth",
            )
        )

        scoring_evaluation = build_scoring_truth_evaluation(
            frames_path=frames_path,
            primary_timeline_path=primary_timeline_path,
            predicted_events_path=predicted_events_path,
            registry_path=registry_snapshot,
            manual_events_path=compiled["manual_events"],
            manual_keypoints_path=compiled["manual_keypoints"],
            manual_semantics_path=compiled["manual_semantics"],
        )
        scoring_evaluation_path = run_dir / "scoring-truth-evaluation.json"
        _write_json(scoring_evaluation_path, scoring_evaluation)

        pose_evaluation = evaluate_pose_diagnostic_truth(
            manifest=pose_manifest,
            coverage_csv_text=coverage.read_text(encoding="utf-8"),
            positives_csv_text=positives.read_text(encoding="utf-8"),
            coverage_path=coverage,
            positives_path=positives,
            generated_at=now,
        )
        validate_pose_diagnostic_evaluation(pose_evaluation)
        pose_evaluation_path = run_dir / "pose-diagnostic-evaluation.json"
        _write_json(pose_evaluation_path, pose_evaluation)

        readiness = build_scoring_truth_action_readiness(
            worklist_path=worklist_snapshot,
            truth_validation_path=compiled["validation"],
            manual_events_path=compiled["manual_events"],
            manual_keypoints_path=compiled["manual_keypoints"],
            manual_semantics_path=compiled["manual_semantics"],
            scoring_truth_evaluation_path=scoring_evaluation_path,
            pose_truth_manifest_path=pose_manifest_snapshot,
            pose_truth_evaluation_path=pose_evaluation_path,
            reference_context_path=reference_snapshot,
            generated_at=now,
        )
        readiness_path = run_dir / "action-readiness.json"
        _write_json(readiness_path, readiness)

        evidence_plan = build_scoring_truth_evidence_plan(
            readiness_path=readiness_path, generated_at=now
        )
        evidence_outputs = write_scoring_truth_evidence_plan(
            evidence_plan, output_dir=run_dir / "evidence-plan"
        )

        artifacts = {
            "truth_validation": _artifact(compiled["validation"]),
            "manual_events": _artifact(compiled["manual_events"]),
            "manual_keypoints": _artifact(compiled["manual_keypoints"]),
            "manual_semantics": _artifact(compiled["manual_semantics"]),
            "coach_labels": _artifact(compiled["coach_labels"]),
            "worklist_snapshot": _artifact(worklist_snapshot),
            "registry_snapshot": _artifact(registry_snapshot),
            "reference_context_snapshot": _artifact(reference_snapshot),
            "pose_truth_manifest": _artifact(pose_manifest_snapshot),
            "pose_truth_coverage": _artifact(coverage),
            "pose_truth_positives": _artifact(positives),
            "scoring_truth_evaluation": _artifact(scoring_evaluation_path),
            "pose_diagnostic_evaluation": _artifact(pose_evaluation_path),
            "action_readiness": _artifact(readiness_path),
            "evidence_plan": _artifact(evidence_outputs["json"]),
            "evidence_plan_csv": _artifact(evidence_outputs["csv"]),
            "evidence_plan_html": _artifact(evidence_outputs["html"]),
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "refresh_version": REFRESH_VERSION,
            "refresh_id": refresh_id,
            "generated_at": now,
            "status": "published_fail_closed_truth_evidence_snapshot",
            "source_fingerprint_sha256": source_fingerprint,
            "source": {
                "truth_pack": {
                    "path": str(pack_dir),
                    "files": {path.name: sha256_file(path) for path in raw_sources},
                },
                "frames": _artifact(frames_path),
                "primary_timeline": _artifact(primary_timeline_path),
                "predicted_events": _artifact(predicted_events_path),
                "registry": _artifact(registry_path),
                "worklist": _artifact(worklist_path),
                "pose_truth_manifest": _artifact(pose_truth_manifest_path),
                "pose_truth_coverage": _artifact(pose_coverage_source),
                "pose_truth_positives": _artifact(pose_positives_source),
                "reference_context": _artifact(reference_context_path),
            },
            "states": {
                "truth_pack": truth_validation["status"],
                "scoring_truth_evaluation": scoring_evaluation["status"],
                "pose_diagnostic_evaluation": pose_evaluation["status"],
                "action_readiness": readiness["status"],
                "evidence_plan": evidence_plan["status"],
            },
            "counts": {
                "manual_events": truth_validation["counts"]["manual_events"],
                "manual_keypoint_frames": truth_validation["counts"][
                    "accepted_keypoint_frames"
                ],
                "manual_semantics": truth_validation["counts"][
                    "semantic_truth_records"
                ],
                "coach_labels": truth_validation["counts"]["coach_labels"],
                "work_items": readiness["counts"]["work_items"],
                "satisfied_work_items": readiness["counts"]["by_status"][SATISFIED],
                "indicator_instances": readiness["counts"]["indicator_instances"],
                "satisfied_indicator_instances": readiness["counts"][
                    "indicator_instances_by_status"
                ][SATISFIED],
                "evidence_units": evidence_plan["counts"]["evidence_units"],
                "satisfied_evidence_units": evidence_plan["counts"]["by_status"][
                    SATISFIED
                ],
            },
            "artifacts": artifacts,
            "safety": {
                "refresh_manifest_written_before_validation": False,
                "latest_pointer_updated_before_validation": False,
                "candidate_events_promoted_to_truth": False,
                "quality_gate_modified": False,
                "scoring_state_modified": False,
                "grades_generated": False,
                "thresholds_generated": False,
                "maturity_promoted": False,
                "coverage_is_accuracy": False,
                "completion_requires_explicit_scoring_recompute": True,
                "maximum_status_without_calibration": "calibration_required",
            },
        }
        index_path = run_dir / "index.html"
        manifest["artifacts"]["index_html"] = {
            "path": str(index_path.resolve()),
            "sha256": "PENDING_UNTIL_RENDERED",
        }
        _render_index(manifest, output_path=index_path)
        manifest["artifacts"]["index_html"] = _artifact(index_path)
        validate_scoring_truth_refresh_manifest(manifest, verify_sources=True)
        manifest_path = run_dir / "refresh-manifest.json"
        _write_json(manifest_path, manifest)
        validate_scoring_truth_refresh_manifest(
            _read_json(manifest_path), verify_sources=True
        )
        marker.unlink()
        latest = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": now,
            "refresh_id": refresh_id,
            "manifest": _artifact(manifest_path),
        }
        temporary_latest = output_root / f".latest-{refresh_id}.tmp"
        _write_json(temporary_latest, latest)
        os.replace(temporary_latest, output_root / "latest.json")
        return manifest
    except Exception as exc:
        _write_json(
            run_dir / ".failed.json",
            {
                "refresh_id": refresh_id,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latest_pointer_updated": False,
            },
        )
        if marker.exists():
            marker.unlink()
        raise


def validate_scoring_truth_refresh_manifest(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported truth refresh schema")
    if manifest.get("refresh_version") != REFRESH_VERSION:
        raise ValueError("unsupported truth refresh version")
    if manifest.get("status") != "published_fail_closed_truth_evidence_snapshot":
        raise ValueError("truth refresh is not a published snapshot")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("truth refresh artifacts are missing")
    required_artifacts = {
        "truth_validation",
        "manual_events",
        "manual_keypoints",
        "manual_semantics",
        "coach_labels",
        "worklist_snapshot",
        "registry_snapshot",
        "reference_context_snapshot",
        "pose_truth_manifest",
        "pose_truth_coverage",
        "pose_truth_positives",
        "scoring_truth_evaluation",
        "pose_diagnostic_evaluation",
        "action_readiness",
        "evidence_plan",
        "evidence_plan_csv",
        "evidence_plan_html",
        "index_html",
    }
    if set(artifacts) != required_artifacts:
        raise ValueError("truth refresh artifact set mismatch")
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("truth refresh source bindings are missing")
    truth_pack = source.get("truth_pack")
    if not isinstance(truth_pack, Mapping) or not isinstance(
        truth_pack.get("files"), Mapping
    ):
        raise ValueError("truth refresh truth-pack binding is missing")
    fingerprint_names = (
        "frames",
        "primary_timeline",
        "predicted_events",
        "registry",
        "worklist",
        "pose_truth_manifest",
        "reference_context",
        "pose_truth_coverage",
        "pose_truth_positives",
    )
    fingerprint_inputs: dict[str, str] = {}
    for name in fingerprint_names:
        binding = source.get(name)
        if not isinstance(binding, Mapping) or not isinstance(
            binding.get("sha256"), str
        ):
            raise ValueError(f"truth refresh source binding is missing: {name}")
        fingerprint_inputs[name] = str(binding["sha256"]).upper()
    expected_fingerprint = canonical_hash(
        {
            "truth_pack": {
                str(name): str(digest).upper()
                for name, digest in truth_pack["files"].items()
            },
            "inputs": fingerprint_inputs,
        }
    )
    if manifest.get("source_fingerprint_sha256") != expected_fingerprint:
        raise ValueError("truth refresh source fingerprint mismatch")
    if verify_sources:
        for name, binding in artifacts.items():
            if not isinstance(binding, Mapping):
                raise ValueError(f"truth refresh artifact binding invalid: {name}")
            path = Path(str(binding.get("path", ""))).resolve()
            if not path.is_file() or sha256_file(path) != str(
                binding.get("sha256", "")
            ).upper():
                raise ValueError(f"truth refresh artifact SHA mismatch: {name}")
        pack_dir = Path(str(truth_pack.get("path", ""))).resolve()
        for name, digest in truth_pack.get("files", {}).items():
            path = pack_dir / name
            if not path.is_file() or sha256_file(path) != str(digest).upper():
                raise ValueError(f"truth refresh raw source SHA mismatch: {name}")
        for name in (
            "frames",
            "primary_timeline",
            "predicted_events",
            "registry",
            "worklist",
            "pose_truth_manifest",
            "pose_truth_coverage",
            "pose_truth_positives",
            "reference_context",
        ):
            binding = source.get(name)
            path = Path(str(binding.get("path", ""))).resolve() if isinstance(binding, Mapping) else Path()
            if not path.is_file() or sha256_file(path) != str(
                binding.get("sha256", "")
            ).upper():
                raise ValueError(f"truth refresh source SHA mismatch: {name}")

        readiness = _read_json(Path(artifacts["action_readiness"]["path"]))
        validate_scoring_truth_action_readiness(readiness)
        replayed_readiness = build_scoring_truth_action_readiness(
            worklist_path=Path(artifacts["worklist_snapshot"]["path"]),
            truth_validation_path=Path(artifacts["truth_validation"]["path"]),
            manual_events_path=Path(artifacts["manual_events"]["path"]),
            manual_keypoints_path=Path(artifacts["manual_keypoints"]["path"]),
            manual_semantics_path=Path(artifacts["manual_semantics"]["path"]),
            scoring_truth_evaluation_path=Path(
                artifacts["scoring_truth_evaluation"]["path"]
            ),
            pose_truth_manifest_path=Path(artifacts["pose_truth_manifest"]["path"]),
            pose_truth_evaluation_path=Path(
                artifacts["pose_diagnostic_evaluation"]["path"]
            ),
            reference_context_path=Path(
                artifacts["reference_context_snapshot"]["path"]
            ),
            generated_at=str(readiness["generated_at"]),
        )
        if readiness != replayed_readiness:
            raise ValueError("truth refresh action readiness differs from source replay")
        plan = _read_json(Path(artifacts["evidence_plan"]["path"]))
        validate_scoring_truth_evidence_plan_sources(plan)
        truth_validation = _read_json(Path(artifacts["truth_validation"]["path"]))
        scoring_evaluation = _read_json(
            Path(artifacts["scoring_truth_evaluation"]["path"])
        )
        pose_evaluation = _read_json(
            Path(artifacts["pose_diagnostic_evaluation"]["path"])
        )
        validate_pose_diagnostic_evaluation(pose_evaluation)
        states = manifest.get("states", {})
        if states.get("action_readiness") != readiness.get("status"):
            raise ValueError("truth refresh action-readiness state mismatch")
        if states.get("evidence_plan") != plan.get("status"):
            raise ValueError("truth refresh evidence-plan state mismatch")
        if states.get("truth_pack") != truth_validation.get("status"):
            raise ValueError("truth refresh truth-pack state mismatch")
        if states.get("scoring_truth_evaluation") != scoring_evaluation.get("status"):
            raise ValueError("truth refresh scoring-evaluation state mismatch")
        if states.get("pose_diagnostic_evaluation") != pose_evaluation.get("status"):
            raise ValueError("truth refresh Pose-evaluation state mismatch")
        counts = manifest.get("counts", {})
        expected_counts = {
            "manual_events": truth_validation["counts"]["manual_events"],
            "manual_keypoint_frames": truth_validation["counts"][
                "accepted_keypoint_frames"
            ],
            "manual_semantics": truth_validation["counts"][
                "semantic_truth_records"
            ],
            "coach_labels": truth_validation["counts"]["coach_labels"],
            "work_items": readiness["counts"]["work_items"],
            "satisfied_work_items": readiness["counts"]["by_status"][SATISFIED],
            "indicator_instances": readiness["counts"]["indicator_instances"],
            "satisfied_indicator_instances": readiness["counts"][
                "indicator_instances_by_status"
            ][SATISFIED],
            "evidence_units": plan["counts"]["evidence_units"],
            "satisfied_evidence_units": plan["counts"]["by_status"][SATISFIED],
        }
        if any(counts.get(name) != value for name, value in expected_counts.items()):
            raise ValueError("truth refresh readiness counts mismatch")
    safety = manifest.get("safety")
    false_fields = (
        "refresh_manifest_written_before_validation",
        "latest_pointer_updated_before_validation",
        "candidate_events_promoted_to_truth",
        "quality_gate_modified",
        "scoring_state_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
        "coverage_is_accuracy",
    )
    if (
        not isinstance(safety, Mapping)
        or any(safety.get(name) is not False for name in false_fields)
        or safety.get("completion_requires_explicit_scoring_recompute") is not True
        or safety.get("maximum_status_without_calibration") != "calibration_required"
    ):
        raise ValueError("truth refresh safety invariant failed")


def load_latest_scoring_truth_refresh(latest_path: Path) -> dict[str, Any]:
    latest = _read_json(latest_path)
    if latest.get("schema_version") != SCHEMA_VERSION or latest.get(
        "latest_version"
    ) != LATEST_VERSION:
        raise ValueError("unsupported truth refresh latest pointer")
    manifest_binding = latest.get("manifest")
    if not isinstance(manifest_binding, Mapping):
        raise ValueError("truth refresh latest manifest binding missing")
    manifest_path = Path(str(manifest_binding.get("path", ""))).resolve()
    if not manifest_path.is_file() or sha256_file(manifest_path) != str(
        manifest_binding.get("sha256", "")
    ).upper():
        raise ValueError("truth refresh latest manifest SHA mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("refresh_id") != latest.get("refresh_id"):
        raise ValueError("truth refresh latest ID mismatch")
    validate_scoring_truth_refresh_manifest(manifest, verify_sources=True)
    return manifest
