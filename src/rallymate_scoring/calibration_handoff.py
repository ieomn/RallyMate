from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from rallymate_annotation.scoring_truth_refresh import (
    load_latest_scoring_truth_refresh,
    validate_scoring_truth_refresh_manifest,
)
from rallymate_scoring.calibration_dataset import compile_calibration_dataset
from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    validate_prepared_dataset,
)
from rallymate_scoring.manual_event_features import build_manual_event_features


SCHEMA_VERSION = "1.0.0"
HANDOFF_VERSION = "scoring-truth-calibration-handoff-v1.0.0"
FIT_READINESS_VERSION = "calibration-fit-readiness-v1.0.0"
LATEST_VERSION = "scoring-truth-calibration-handoff-latest-v1.0.0"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record must be an object: {path}:{line_number}")
            records.append(value)
    return records


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256_file(path.resolve())}


def _copy_bound(source: Path, destination: Path, expected_sha256: str) -> None:
    if not source.is_file():
        raise ValueError(f"bound source is missing: {source}")
    actual = _sha256_file(source)
    if actual != str(expected_sha256).upper():
        raise ValueError(f"bound source SHA mismatch: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if _sha256_file(destination) != actual:
        raise ValueError(f"source snapshot copy mismatch: {destination}")


def _first_pose_topology(frames_path: Path) -> tuple[str | None, int | None]:
    for record in _read_jsonl(frames_path):
        poses = record.get("poses")
        if not isinstance(poses, list):
            continue
        for pose in poses:
            if not isinstance(pose, dict):
                continue
            keypoints = pose.get("keypoints")
            count = len(keypoints) if isinstance(keypoints, list) else None
            keypoint_format = pose.get("keypoint_format")
            return (
                str(keypoint_format) if isinstance(keypoint_format, str) else None,
                count,
            )
    return None, None


def _fit_readiness(
    *,
    dataset_manifest: Mapping[str, Any],
    prepared_paths: Mapping[str, str],
    output_path: Path,
) -> dict[str, Any]:
    indicators: list[dict[str, Any]] = []
    for indicator_id in dataset_manifest["registry"]["indicator_ids"]:
        path = Path(prepared_paths[indicator_id]).resolve()
        payload = _read_json(path)
        audit = validate_prepared_dataset(payload, require_fit_ready=False)
        fit_ready = True
        blocker: str | None = None
        try:
            validate_prepared_dataset(payload, require_fit_ready=True)
        except CalibrationFitError as exc:
            fit_ready = False
            blocker = str(exc)
        indicators.append(
            {
                "indicator_id": indicator_id,
                "prepared_path": str(path),
                "prepared_sha256": _sha256_file(path),
                "contract_valid": True,
                "fit_ready": fit_ready,
                "fit_blocker": blocker,
                "record_counts": audit["record_counts"],
            }
        )
    ready_count = sum(item["fit_ready"] for item in indicators)
    if ready_count == len(indicators):
        status = "all_indicators_prepared_requires_external_protocol"
    elif ready_count:
        status = "partially_prepared_requires_more_truth"
    else:
        status = "annotation_required"
    report = {
        "schema_version": SCHEMA_VERSION,
        "fit_readiness_version": FIT_READINESS_VERSION,
        "dataset_id": dataset_manifest["dataset_id"],
        "dataset_version": dataset_manifest["dataset_version"],
        "status": status,
        "indicator_count": len(indicators),
        "fit_ready_indicator_count": ready_count,
        "indicators": indicators,
        "safety": {
            "fit_executed": False,
            "candidate_written": False,
            "thresholds_generated": False,
            "model_trained": False,
            "grades_generated": False,
            "automatic_F3_or_F4_promotion": False,
        },
    }
    _write_json(output_path, report)
    return report


def _render_index(manifest: Mapping[str, Any], output_path: Path) -> None:
    counts = manifest["counts"]
    artifacts = manifest["artifacts"]
    indicator_rows = "".join(
        "<tr><td><code>{}</code></td><td>{}</td><td>{}</td></tr>".format(
            item["indicator_id"],
            "yes" if item["fit_ready"] else "no",
            item["fit_blocker"] or "external versioned fit protocol still required",
        )
        for item in manifest["indicator_readiness"]
    )
    refresh_href = os.path.relpath(
        artifacts["refresh_manifest"]["path"], output_path.parent
    ).replace("\\", "/")
    feature_href = os.path.relpath(
        artifacts["manual_event_features"]["summary"]["path"], output_path.parent
    ).replace("\\", "/")
    readiness_href = os.path.relpath(
        artifacts["calibration_dataset"]["readiness"]["path"], output_path.parent
    ).replace("\\", "/")
    fit_href = os.path.relpath(
        artifacts["fit_readiness"]["path"], output_path.parent
    ).replace("\\", "/")
    html = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>RallyMate M48 标定交接</title>
<style>body{{font:15px/1.55 system-ui;margin:28px;max-width:1180px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:6px;text-align:left}}code{{word-break:break-all}}.warn{{background:#fff4d6;padding:12px}}</style></head><body>
<h1>RallyMate M48：真值刷新 → 标定数据集交接</h1>
<p class=\"warn\">状态：<code>{manifest['status']}</code>。当前只验证数据契约与可追溯交接；没有生成阈值、模型或 A～E，也没有推进 F3/F4。</p>
<p>上游 refresh：<a href=\"{refresh_href}\"><code>{manifest['source']['truth_refresh']['refresh_id']}</code></a>；人工事件 {counts['accepted_manual_events']}，人工边界特征 {counts['manual_feature_records']}，标定 samples {counts['calibration_samples']}，fit-ready 指标 {counts['fit_ready_indicators']}/{counts['indicators']}。</p>
<p><a href=\"{feature_href}\">人工事件特征摘要</a> · <a href=\"{readiness_href}\">标定 readiness</a> · <a href=\"{fit_href}\">逐指标拟合前门禁</a></p>
<table><thead><tr><th>指标</th><th>fit-ready</th><th>阻断</th></tr></thead><tbody>{indicator_rows}</tbody></table>
</body></html>"""
    output_path.write_text(html, encoding="utf-8")


def build_scoring_truth_calibration_handoff(
    *,
    truth_refresh_latest_path: str | Path,
    output_root: str | Path,
    handoff_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    latest_path = Path(truth_refresh_latest_path).resolve()
    output_root = Path(output_root).resolve()
    refresh_manifest = load_latest_scoring_truth_refresh(latest_path)
    latest = _read_json(latest_path)
    refresh_binding = latest["manifest"]
    refresh_manifest_path = Path(refresh_binding["path"]).resolve()
    refresh_manifest_sha = _sha256_file(refresh_manifest_path)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    handoff_id = handoff_id or f"m48-{refresh_manifest['refresh_id']}"
    if not isinstance(handoff_id, str) or not handoff_id:
        raise ValueError("handoff_id must be a non-empty string")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in handoff_id):
        raise ValueError("handoff_id contains unsupported characters")
    run_dir = output_root / handoff_id
    if run_dir.exists():
        raise FileExistsError(f"calibration handoff already exists: {run_dir}")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=False)
    try:
        sources_dir = run_dir / "sources"
        sources_dir.mkdir()
        upstream_artifacts = refresh_manifest["artifacts"]
        snapshots = {
            "registry": sources_dir / "metric-feasibility-pose-wave-v2.json",
            "manual_events": sources_dir / "manual-events.jsonl",
            "manual_semantics": sources_dir / "manual-semantics.jsonl",
            "coach_labels": sources_dir / "coach-labels.jsonl",
            "truth_validation": sources_dir / "truth-validation-report.json",
            "truth_manifest": sources_dir / "truth-manifest.json",
        }
        for name in (
            "registry_snapshot",
            "manual_events",
            "manual_semantics",
            "coach_labels",
            "truth_validation",
        ):
            destination_name = "registry" if name == "registry_snapshot" else name
            binding = upstream_artifacts[name]
            _copy_bound(
                Path(binding["path"]).resolve(),
                snapshots[destination_name],
                binding["sha256"],
            )
        truth_manifest_source = (
            Path(refresh_manifest["source"]["truth_pack"]["path"]).resolve()
            / "manifest.json"
        )
        _copy_bound(
            truth_manifest_source,
            snapshots["truth_manifest"],
            refresh_manifest["source"]["truth_pack"]["files"]["manifest.json"],
        )

        truth_validation = _read_json(snapshots["truth_validation"])
        scoring_binding = truth_validation.get("scoring_source_binding")
        if not isinstance(scoring_binding, dict):
            raise ValueError("truth validation lacks scoring_source_binding")
        video_id = scoring_binding.get("video_id")
        if not isinstance(video_id, str) or not video_id:
            raise ValueError("scoring_source_binding.video_id is required")
        frames_binding = refresh_manifest["source"]["frames"]
        timeline_binding = refresh_manifest["source"]["primary_timeline"]
        frames_path = Path(frames_binding["path"]).resolve()
        timeline_path = Path(timeline_binding["path"]).resolve()
        if _sha256_file(frames_path) != str(frames_binding["sha256"]).upper():
            raise ValueError("truth refresh frames SHA mismatch")
        if _sha256_file(timeline_path) != str(timeline_binding["sha256"]).upper():
            raise ValueError("truth refresh primary timeline SHA mismatch")

        all_events = _read_jsonl(snapshots["manual_events"])
        video_events = [item for item in all_events if item.get("video_id") == video_id]
        filtered_events_path = sources_dir / f"{video_id}-manual-events.jsonl"
        _write_jsonl(filtered_events_path, video_events)
        topology, topology_count = _first_pose_topology(frames_path)
        profile = str(scoring_binding.get("pose_profile", "unknown"))
        pose_model = {
            "backend": "rtmpose" if profile.startswith("rtmpose") else "unknown",
            "runtime": "unknown",
            "profile": profile,
            "model_sha256": None,
            "native_keypoint_format": topology,
            "native_keypoint_count": topology_count,
        }
        manual_feature_dir = run_dir / "manual-event-features" / video_id
        manual_result = build_manual_event_features(
            frames_path=frames_path,
            primary_timeline_path=timeline_path,
            manual_events_path=filtered_events_path,
            feasibility_registry_path=snapshots["registry"],
            video_id=video_id,
            output_dir=manual_feature_dir,
            pose_model=pose_model,
            source_provenance={
                "truth_refresh_id": refresh_manifest["refresh_id"],
                "truth_refresh_manifest_sha256": refresh_manifest_sha,
                "truth_refresh_source_fingerprint_sha256": refresh_manifest[
                    "source_fingerprint_sha256"
                ],
            },
        )

        calibration_dir = run_dir / "calibration-dataset"
        dataset_manifest = compile_calibration_dataset(
            feasibility_registry_path=snapshots["registry"],
            indicator_feature_paths=[manual_feature_dir / "indicator-features.jsonl"],
            manual_events_path=snapshots["manual_events"],
            manual_semantics_path=snapshots["manual_semantics"],
            coach_labels_path=snapshots["coach_labels"],
            truth_manifest_path=snapshots["truth_manifest"],
            truth_validation_report_path=snapshots["truth_validation"],
            output_dir=calibration_dir,
            generated_at=generated_at,
        )
        fit_readiness_path = run_dir / "fit-readiness.json"
        fit_readiness = _fit_readiness(
            dataset_manifest=dataset_manifest,
            prepared_paths=dataset_manifest["outputs"]["prepared_by_indicator"],
            output_path=fit_readiness_path,
        )
        registry = _read_json(snapshots["registry"])
        indicator_ids = [item["indicator_id"] for item in registry["indicators"]]
        missing_source_events = [
            item for item in all_events if item.get("video_id") != video_id
        ]
        status = (
            "annotation_required"
            if dataset_manifest["status"] == "annotation_required"
            else fit_readiness["status"]
        )
        source_snapshot_artifacts = {
            name: _artifact(path) for name, path in snapshots.items()
        }
        source_snapshot_artifacts["filtered_manual_events"] = _artifact(
            filtered_events_path
        )
        manual_artifacts = {
            "summary": _artifact(manual_feature_dir / "summary.json"),
            "events": _artifact(manual_feature_dir / "events.jsonl"),
            "features": _artifact(manual_feature_dir / "features.jsonl"),
            "indicator_features": _artifact(
                manual_feature_dir / "indicator-features.jsonl"
            ),
            "scores": _artifact(manual_feature_dir / "scores.jsonl"),
        }
        calibration_artifacts = {
            "manifest": _artifact(calibration_dir / "manifest.json"),
            "readiness": _artifact(calibration_dir / "readiness-report.json"),
            "samples": _artifact(calibration_dir / "samples.jsonl"),
            "split_manifest": _artifact(calibration_dir / "split-manifest.json"),
        }
        prepared_artifacts = {
            indicator_id: _artifact(
                Path(dataset_manifest["outputs"]["prepared_by_indicator"][indicator_id])
            )
            for indicator_id in indicator_ids
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "handoff_version": HANDOFF_VERSION,
            "handoff_id": handoff_id,
            "generated_at": generated_at,
            "status": status,
            "source_fingerprint_sha256": _canonical_sha256(
                {
                    "truth_refresh_manifest_sha256": refresh_manifest_sha,
                    "frames_sha256": str(frames_binding["sha256"]).upper(),
                    "primary_timeline_sha256": str(timeline_binding["sha256"]).upper(),
                    "registry_sha256": source_snapshot_artifacts["registry"]["sha256"],
                    "manual_events_sha256": source_snapshot_artifacts["manual_events"]["sha256"],
                    "manual_semantics_sha256": source_snapshot_artifacts["manual_semantics"]["sha256"],
                    "coach_labels_sha256": source_snapshot_artifacts["coach_labels"]["sha256"],
                }
            ),
            "source": {
                "truth_refresh": {
                    "refresh_id": refresh_manifest["refresh_id"],
                    "manifest": _artifact(refresh_manifest_path),
                },
                "measurement_source": {
                    "video_id": video_id,
                    "frames": _artifact(frames_path),
                    "primary_timeline": _artifact(timeline_path),
                    "pose_profile": profile,
                    "native_keypoint_format": topology,
                    "native_keypoint_count": topology_count,
                    "accepted_manual_event_count": len(video_events),
                },
            },
            "registry": {
                "version": registry["registry_version"],
                "indicator_ids": indicator_ids,
                "indicator_count": len(indicator_ids),
            },
            "states": {
                "manual_event_features": manual_result["summary"]["status"],
                "calibration_dataset": dataset_manifest["status"],
                "fit_readiness": fit_readiness["status"],
            },
            "counts": {
                "indicators": len(indicator_ids),
                "accepted_manual_events": len(all_events),
                "measurement_source_manual_events": len(video_events),
                "manual_events_without_measurement_source": len(missing_source_events),
                "manual_feature_records": len(manual_result["features"]),
                "manual_indicator_records": len(manual_result["indicator_records"]),
                "calibration_samples": dataset_manifest["counts"]["samples"],
                "prepared_indicators": dataset_manifest["counts"][
                    "prepared_indicator_files"
                ],
                "fit_ready_indicators": fit_readiness["fit_ready_indicator_count"],
            },
            "indicator_readiness": fit_readiness["indicators"],
            "artifacts": {
                "refresh_manifest": _artifact(refresh_manifest_path),
                "source_snapshots": source_snapshot_artifacts,
                "manual_event_features": manual_artifacts,
                "calibration_dataset": calibration_artifacts,
                "prepared_by_indicator": prepared_artifacts,
                "fit_readiness": _artifact(fit_readiness_path),
            },
            "safety": {
                "candidate_event_detector_used": False,
                "candidate_boundaries_promoted_to_truth": False,
                "fit_executed": False,
                "candidate_written": False,
                "thresholds_generated": False,
                "model_trained": False,
                "grades_generated": False,
                "automatic_F3_or_F4_promotion": False,
                "empty_truth_interpreted_as_grade_E_or_zero": False,
                "maximum_status_without_external_calibration": "calibration_required",
            },
        }
        index_path = run_dir / "index.html"
        manifest["artifacts"]["index_html"] = {
            "path": str(index_path.resolve()),
            "sha256": "0" * 64,
        }
        _render_index(manifest, index_path)
        manifest["artifacts"]["index_html"] = _artifact(index_path)
        manifest_path = run_dir / "handoff-manifest.json"
        _write_json(manifest_path, manifest)
        validate_scoring_truth_calibration_handoff(manifest, verify_sources=True)
        persisted = _read_json(manifest_path)
        validate_scoring_truth_calibration_handoff(persisted, verify_sources=True)

        latest_payload = {
            "schema_version": SCHEMA_VERSION,
            "latest_version": LATEST_VERSION,
            "updated_at": generated_at,
            "handoff_id": handoff_id,
            "manifest": _artifact(manifest_path),
        }
        temporary_latest = output_root / f".latest-{handoff_id}.tmp"
        _write_json(temporary_latest, latest_payload)
        os.replace(temporary_latest, output_root / "latest.json")
        return manifest
    except Exception:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        raise


def _verify_artifact(binding: Mapping[str, Any], name: str) -> Path:
    path = Path(str(binding.get("path", ""))).resolve()
    expected = str(binding.get("sha256", "")).upper()
    if not path.is_file() or _sha256_file(path) != expected:
        raise ValueError(f"calibration handoff artifact SHA mismatch: {name}")
    return path


def validate_scoring_truth_calibration_handoff(
    manifest: Mapping[str, Any], *, verify_sources: bool = False
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get(
        "handoff_version"
    ) != HANDOFF_VERSION:
        raise ValueError("unsupported calibration handoff version")
    if manifest.get("status") not in {
        "annotation_required",
        "partially_prepared_requires_more_truth",
        "all_indicators_prepared_requires_external_protocol",
    }:
        raise ValueError("calibration handoff status is invalid")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping):
        raise ValueError("calibration handoff safety block is missing")
    for field in (
        "candidate_event_detector_used",
        "candidate_boundaries_promoted_to_truth",
        "fit_executed",
        "candidate_written",
        "thresholds_generated",
        "model_trained",
        "grades_generated",
        "automatic_F3_or_F4_promotion",
        "empty_truth_interpreted_as_grade_E_or_zero",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe calibration handoff field: {field}")
    if safety.get("maximum_status_without_external_calibration") != "calibration_required":
        raise ValueError("calibration handoff maximum uncalibrated status is invalid")
    registry_section = manifest.get("registry")
    if not isinstance(registry_section, Mapping):
        raise ValueError("calibration handoff registry section is missing")
    indicator_ids = registry_section.get("indicator_ids")
    if (
        not isinstance(indicator_ids, list)
        or not indicator_ids
        or len(set(indicator_ids)) != len(indicator_ids)
        or registry_section.get("indicator_count") != len(indicator_ids)
    ):
        raise ValueError("calibration handoff indicator set is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("calibration handoff artifacts are missing")
    if verify_sources:
        refresh_path = _verify_artifact(artifacts["refresh_manifest"], "refresh_manifest")
        refresh_manifest = _read_json(refresh_path)
        validate_scoring_truth_refresh_manifest(refresh_manifest, verify_sources=True)
        source_refresh = manifest["source"]["truth_refresh"]
        if source_refresh["refresh_id"] != refresh_manifest["refresh_id"]:
            raise ValueError("calibration handoff refresh ID mismatch")
        if source_refresh["manifest"] != artifacts["refresh_manifest"]:
            raise ValueError("calibration handoff refresh binding mismatch")

        snapshot_paths = {
            name: _verify_artifact(binding, f"source_snapshots.{name}")
            for name, binding in artifacts["source_snapshots"].items()
        }
        upstream = refresh_manifest["artifacts"]
        expected_snapshot_hashes = {
            "registry": upstream["registry_snapshot"]["sha256"],
            "manual_events": upstream["manual_events"]["sha256"],
            "manual_semantics": upstream["manual_semantics"]["sha256"],
            "coach_labels": upstream["coach_labels"]["sha256"],
            "truth_validation": upstream["truth_validation"]["sha256"],
            "truth_manifest": refresh_manifest["source"]["truth_pack"]["files"][
                "manifest.json"
            ],
        }
        for name, expected in expected_snapshot_hashes.items():
            if _sha256_file(snapshot_paths[name]) != str(expected).upper():
                raise ValueError(f"calibration handoff snapshot differs from refresh: {name}")
        registry = _read_json(snapshot_paths["registry"])
        expected_ids = [item["indicator_id"] for item in registry["indicators"]]
        if expected_ids != indicator_ids or registry["registry_version"] != registry_section.get(
            "version"
        ):
            raise ValueError("calibration handoff registry contents mismatch")
        measurement = manifest["source"]["measurement_source"]
        frames_path = _verify_artifact(measurement["frames"], "measurement.frames")
        timeline_path = _verify_artifact(
            measurement["primary_timeline"], "measurement.primary_timeline"
        )
        if measurement["frames"] != refresh_manifest["source"]["frames"]:
            raise ValueError("calibration handoff frames binding differs from refresh")
        if measurement["primary_timeline"] != refresh_manifest["source"][
            "primary_timeline"
        ]:
            raise ValueError("calibration handoff timeline binding differs from refresh")
        del frames_path, timeline_path
        all_events = _read_jsonl(snapshot_paths["manual_events"])
        filtered = _read_jsonl(snapshot_paths["filtered_manual_events"])
        video_id = measurement["video_id"]
        expected_filtered = [item for item in all_events if item.get("video_id") == video_id]
        if filtered != expected_filtered:
            raise ValueError("filtered manual events differ from refresh truth")

        manual_paths = {
            name: _verify_artifact(binding, f"manual_event_features.{name}")
            for name, binding in artifacts["manual_event_features"].items()
        }
        manual_summary = _read_json(manual_paths["summary"])
        if manual_summary["video_id"] != video_id:
            raise ValueError("manual feature video_id mismatch")
        if (
            manual_summary["registry_version"] != registry["registry_version"]
            or manual_summary["indicator_count"] != len(indicator_ids)
        ):
            raise ValueError("manual feature registry binding mismatch")
        if manual_summary["source_files"]["frames"]["sha256"].upper() != measurement[
            "frames"
        ]["sha256"].upper():
            raise ValueError("manual feature frames lineage mismatch")
        if manual_summary["source_files"]["primary_timeline"]["sha256"].upper() != measurement[
            "primary_timeline"
        ]["sha256"].upper():
            raise ValueError("manual feature timeline lineage mismatch")
        if manual_summary["source_files"]["manual_events"]["sha256"].upper() != artifacts[
            "source_snapshots"
        ]["filtered_manual_events"]["sha256"].upper():
            raise ValueError("manual feature event lineage mismatch")
        if manual_summary["safety"]["candidate_event_detector_used"] is not False:
            raise ValueError("manual feature build used candidate detector")
        expected_manual_artifacts = {
            "events_jsonl": manual_paths["events"],
            "features_jsonl": manual_paths["features"],
            "indicator_features_jsonl": manual_paths["indicator_features"],
            "scores_jsonl": manual_paths["scores"],
        }
        for name, path in expected_manual_artifacts.items():
            if Path(manual_summary["artifacts"][name]).resolve() != path:
                raise ValueError(f"manual feature artifact path mismatch: {name}")
        scores = _read_jsonl(manual_paths["scores"])
        if any(item.get("grade") is not None for item in scores):
            raise ValueError("uncalibrated manual feature output contains a grade")
        if any(item.get("status") not in {"calibration_required", "unavailable"} for item in scores):
            raise ValueError("uncalibrated manual feature output has unsafe status")

        calibration_paths = {
            name: _verify_artifact(binding, f"calibration_dataset.{name}")
            for name, binding in artifacts["calibration_dataset"].items()
        }
        dataset_manifest = _read_json(calibration_paths["manifest"])
        if dataset_manifest["registry"]["indicator_ids"] != indicator_ids:
            raise ValueError("calibration dataset indicator set mismatch")
        expected_dataset_sources = {
            "feasibility_registry": artifacts["source_snapshots"]["registry"],
            "manual_events": artifacts["source_snapshots"]["manual_events"],
            "manual_semantics": artifacts["source_snapshots"]["manual_semantics"],
            "coach_labels": artifacts["source_snapshots"]["coach_labels"],
            "truth_manifest": artifacts["source_snapshots"]["truth_manifest"],
            "truth_validation_report": artifacts["source_snapshots"]["truth_validation"],
        }
        for name, expected in expected_dataset_sources.items():
            actual = dataset_manifest["source_files"][name]
            if actual["path"] != expected["path"] or actual["sha256"].upper() != expected[
                "sha256"
            ].upper():
                raise ValueError(f"calibration dataset source mismatch: {name}")
        indicator_sources = dataset_manifest["source_files"]["indicator_features"]
        expected_indicator_source = artifacts["manual_event_features"]["indicator_features"]
        if len(indicator_sources) != 1 or indicator_sources[0]["path"] != expected_indicator_source[
            "path"
        ] or indicator_sources[0]["sha256"].upper() != expected_indicator_source[
            "sha256"
        ].upper():
            raise ValueError("calibration dataset manual feature source mismatch")
        dataset_safety = dataset_manifest["safety"]
        for field in (
            "generated_thresholds",
            "trained_scoring_model",
            "automatic_F3_or_F4_promotion",
            "candidate_events_promoted_to_truth",
            "majority_or_median_grade_resolution",
            "acceptance_thresholds_applied",
        ):
            if dataset_safety.get(field) is not False:
                raise ValueError("calibration dataset contains an unsafe claim")
        if "prepared_artifact_scope" in dataset_safety:
            if (
                dataset_safety.get("prepared_artifact_scope")
                != "unverified_truth_diagnostic_input"
                or dataset_safety.get("verified_authorized_truth_intake") is not False
            ):
                raise ValueError(
                    "calibration handoff must retain unverified diagnostic truth scope"
                )
        elif dataset_manifest.get("status") != "annotation_required":
            raise ValueError(
                "legacy calibration handoff without truth authority must be annotation_required"
            )
        expected_dataset_outputs = {
            "samples": calibration_paths["samples"],
            "split_manifest": calibration_paths["split_manifest"],
            "readiness_report": calibration_paths["readiness"],
        }
        for name, path in expected_dataset_outputs.items():
            if Path(dataset_manifest["outputs"][name]).resolve() != path:
                raise ValueError(f"calibration dataset output path mismatch: {name}")

        prepared_bindings = artifacts["prepared_by_indicator"]
        if set(prepared_bindings) != set(indicator_ids):
            raise ValueError("calibration handoff prepared indicator set mismatch")
        replay: list[dict[str, Any]] = []
        for indicator_id in indicator_ids:
            prepared_path = _verify_artifact(
                prepared_bindings[indicator_id], f"prepared.{indicator_id}"
            )
            payload = _read_json(prepared_path)
            audit = validate_prepared_dataset(payload, require_fit_ready=False)
            fit_ready = True
            blocker: str | None = None
            try:
                validate_prepared_dataset(payload, require_fit_ready=True)
            except CalibrationFitError as exc:
                fit_ready = False
                blocker = str(exc)
            replay.append(
                {
                    "indicator_id": indicator_id,
                    "prepared_path": str(prepared_path),
                    "prepared_sha256": _sha256_file(prepared_path),
                    "contract_valid": True,
                    "fit_ready": fit_ready,
                    "fit_blocker": blocker,
                    "record_counts": audit["record_counts"],
                }
            )
        if replay != manifest.get("indicator_readiness"):
            raise ValueError("calibration handoff fit readiness differs from replay")
        fit_path = _verify_artifact(artifacts["fit_readiness"], "fit_readiness")
        fit_report = _read_json(fit_path)
        if fit_report["indicators"] != replay:
            raise ValueError("fit-readiness artifact differs from replay")
        if any(fit_report["safety"].values()):
            raise ValueError("fit-readiness artifact contains an unsafe claim")
        expected_fit_status = (
            "all_indicators_prepared_requires_external_protocol"
            if all(item["fit_ready"] for item in replay)
            else "partially_prepared_requires_more_truth"
            if any(item["fit_ready"] for item in replay)
            else "annotation_required"
        )
        if (
            fit_report["status"] != expected_fit_status
            or fit_report["fit_ready_indicator_count"]
            != sum(item["fit_ready"] for item in replay)
        ):
            raise ValueError("fit-readiness status differs from replay")
        expected_states = {
            "manual_event_features": manual_summary["status"],
            "calibration_dataset": dataset_manifest["status"],
            "fit_readiness": fit_report["status"],
        }
        if manifest.get("states") != expected_states:
            raise ValueError("calibration handoff states differ from source replay")
        expected_status = (
            "annotation_required"
            if dataset_manifest["status"] == "annotation_required"
            else fit_report["status"]
        )
        if manifest.get("status") != expected_status:
            raise ValueError("calibration handoff status differs from source replay")
        index_path = _verify_artifact(artifacts["index_html"], "index_html")
        del index_path
        counts = manifest["counts"]
        expected_counts = {
            "indicators": len(indicator_ids),
            "accepted_manual_events": len(all_events),
            "measurement_source_manual_events": len(filtered),
            "manual_events_without_measurement_source": len(all_events) - len(filtered),
            "manual_feature_records": len(_read_jsonl(manual_paths["features"])),
            "manual_indicator_records": len(_read_jsonl(manual_paths["indicator_features"])),
            "calibration_samples": len(_read_jsonl(calibration_paths["samples"])),
            "prepared_indicators": len(prepared_bindings),
            "fit_ready_indicators": sum(item["fit_ready"] for item in replay),
        }
        if counts != expected_counts:
            raise ValueError("calibration handoff counts differ from source replay")
        expected_fingerprint = _canonical_sha256(
            {
                "truth_refresh_manifest_sha256": artifacts["refresh_manifest"]["sha256"],
                "frames_sha256": measurement["frames"]["sha256"],
                "primary_timeline_sha256": measurement["primary_timeline"]["sha256"],
                "registry_sha256": artifacts["source_snapshots"]["registry"]["sha256"],
                "manual_events_sha256": artifacts["source_snapshots"]["manual_events"]["sha256"],
                "manual_semantics_sha256": artifacts["source_snapshots"]["manual_semantics"]["sha256"],
                "coach_labels_sha256": artifacts["source_snapshots"]["coach_labels"]["sha256"],
            }
        )
        if manifest.get("source_fingerprint_sha256") != expected_fingerprint:
            raise ValueError("calibration handoff source fingerprint mismatch")


def load_latest_scoring_truth_calibration_handoff(latest_path: str | Path) -> dict[str, Any]:
    latest_path = Path(latest_path).resolve()
    latest = _read_json(latest_path)
    if latest.get("schema_version") != SCHEMA_VERSION or latest.get(
        "latest_version"
    ) != LATEST_VERSION:
        raise ValueError("unsupported calibration handoff latest pointer")
    binding = latest.get("manifest")
    if not isinstance(binding, Mapping):
        raise ValueError("calibration handoff latest manifest binding missing")
    manifest_path = _verify_artifact(binding, "latest.manifest")
    manifest = _read_json(manifest_path)
    if manifest.get("handoff_id") != latest.get("handoff_id"):
        raise ValueError("calibration handoff latest ID mismatch")
    validate_scoring_truth_calibration_handoff(manifest, verify_sources=True)
    return manifest


__all__ = [
    "FIT_READINESS_VERSION",
    "HANDOFF_VERSION",
    "LATEST_VERSION",
    "build_scoring_truth_calibration_handoff",
    "load_latest_scoring_truth_calibration_handoff",
    "validate_scoring_truth_calibration_handoff",
]
